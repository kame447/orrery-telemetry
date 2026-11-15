"""Read a sender's committed send outcome without replay, writes or body exposure."""
from __future__ import annotations

import hmac
import json
import re
from uuid import UUID

from sqlalchemy import select

from . import storage_origin_directory as directory
from .models import Message, MessageRecipient
from .storage_origin_contract import B1Error, digest

SEND_FIELDS = frozenset({'recipients', 'subject', 'body_md', 'thread_id', 'topic', 'importance', 'ack_required'})
CONTENT_FIELDS = ('subject', 'body_md', 'importance', 'ack_required', 'topic')


def receipt_query(request_id: str, arguments: dict) -> dict:
    """Normalize the published send handler's defaults, not legacy defaults."""
    if (not isinstance(arguments, dict) or set(arguments) - SEND_FIELDS
            or not {'recipients', 'subject', 'body_md'} <= set(arguments)):
        raise B1Error('FRESH_REQUEST_INVALID')
    intent = dict(body_md=arguments['body_md'], recipients=arguments['recipients'],
                  subject=arguments['subject'], thread_id=arguments.get('thread_id'),
                  topic=arguments.get('topic'), importance=arguments.get('importance', 'normal'),
                  ack_required=arguments.get('ack_required', False), message_id=None)
    query = dict(send_request_id=request_id, expected_intent_sha256=digest(intent),
                 expected_content_sha256=digest({key: intent[key] for key in CONTENT_FIELDS}))
    validate_query(**query)
    return query


def validate_query(*, send_request_id, expected_intent_sha256, expected_content_sha256):
    try:
        if str(UUID(send_request_id)) != send_request_id:
            raise ValueError
        if any(not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value)
               for value in (expected_intent_sha256, expected_content_sha256)):
            raise ValueError
    except (TypeError, ValueError, AttributeError):
        raise B1Error('FRESH_RECEIPT_QUERY_INVALID') from None


async def read(core, *, expected_server_instance_id, auth, send_request_id,
               expected_intent_sha256, expected_content_sha256):
    validate_query(send_request_id=send_request_id, expected_intent_sha256=expected_intent_sha256,
                   expected_content_sha256=expected_content_sha256)
    # One read snapshot pins current identity, credentials and result references.
    async with core._transaction() as session:
        state, records, keys = await core._context(session, expected_server_instance_id)
        actor = core._verify(auth, records, keys)
        selector = dict(instance_id=state['instance_id'], actor_id=actor.id,
                        operation='fresh-send', request_id=send_request_id)
        saved = (await session.execute(select(directory.intents).where(
            *[directory.intents.c[key] == value for key, value in selector.items()]
        ))).mappings().first()
        result = dict(contract='orrery-fresh-message-receipt-v1', mail_instance_id=state['instance_id'],
                      sender_id=actor.id, request_id=send_request_id, status='not_observed',
                      receipt=None, refusal_code=None)
        if saved is None:
            # This is not evidence that a concurrent in-flight send can never commit.
            return result
        if not hmac.compare_digest(saved['intent_hash'], expected_intent_sha256):
            raise B1Error('REQUEST_INTENT_CONFLICT')
        outcome = json.loads(saved['outcome'])
        if outcome.get('status') == 'refused':
            code = outcome.get('code')
            if not isinstance(code, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,127}', code):
                raise B1Error('FRESH_RECEIPT_INVALID')
            return dict(result, status='refused', refusal_code=code)
        if outcome.get('status') != 'accepted' or not isinstance(outcome.get('result'), dict):
            raise B1Error('FRESH_RECEIPT_INVALID')
        receipt = outcome['result']
        if (receipt.get('mail_instance_id') != state['instance_id'] or receipt.get('sender_id') != actor.id
                or type(receipt.get('message_id')) is not int or receipt['message_id'] <= 0
                or receipt.get('decision_only') is not False or not isinstance(receipt.get('recipients'), list)):
            raise B1Error('FRESH_RECEIPT_INVALID')
        message = await session.get(Message, receipt['message_id'])
        pairs = (await session.execute(select(MessageRecipient).where(
            MessageRecipient.message_id == receipt['message_id']))).scalars().all()
        try:
            expected = {(row['agent_id'], row['kind']) for row in receipt['recipients']}
        except (TypeError, KeyError):
            raise B1Error('FRESH_RECEIPT_INVALID') from None
        if (message is None or message.sender_id != actor.id or not expected
                or len(expected) != len(receipt['recipients'])
                or {(row.agent_id, row.kind) for row in pairs} != expected
                or message.thread_id != receipt.get('thread_id')):
            raise B1Error('RESULT_REFERENCE_MISSING')
        if not hmac.compare_digest(digest({key: getattr(message, key) for key in CONTENT_FIELDS}),
                                   expected_content_sha256):
            raise B1Error('RESULT_CONTENT_MISMATCH')
        # No stored body, project, credential, path or arbitrary error detail is exposed.
        public = {key: receipt[key] for key in ('mail_instance_id', 'message_id', 'sender_id',
                                               'thread_id', 'created_ts', 'decision_only', 'delivery_status')}
        public['recipients'] = [{'agent_id': row['agent_id'], 'kind': row['kind']} for row in receipt['recipients']]
        return dict(result, status='accepted', receipt=public)
