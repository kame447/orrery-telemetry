"""Receipt reads authenticate the sender and leave all persistent state unchanged."""
import asyncio
import json
import sqlite3
from uuid import uuid4

import pytest
from sqlalchemy import select

from agentstack_mail.fresh_message_receipts import receipt_query
from agentstack_mail.models import Message, MessageRecipient
from agentstack_mail.storage_origin_contract import B1Error
from test_fresh_client import prepare, message
from test_fresh_mail import state, snapshot


def test_lost_response_receipt_is_read_only_and_never_sends_again(tmp_path):
    async def run():
        async with state(tmp_path) as (runtime, people):
            sender = await prepare(runtime, people[0]);real=sender.dispatch;calls=[]
            async def lose(endpoint, tool, args):
                calls.append(tool)
                result=await real(endpoint,tool,args)
                if tool=='send_message':raise B1Error('FRESH_TRANSPORT_UNAVAILABLE')
                return result
            sender.dispatch=lose;request=str(uuid4())
            with pytest.raises(B1Error):await sender.call('send_message',message(),request_id=request)
            before=await snapshot(runtime)
            result=await sender.receipt(request)
            assert result['status']=='accepted' and result['request_id']==request
            assert result['receipt']['sender_id']==people[0]['agent_id']
            assert 'body_md' not in json.dumps(result) and 'registration_token' not in json.dumps(result)
            assert calls==['send_message','get_message_receipt']
            assert await snapshot(runtime)==before
            assert await sender.receipt(request)==result
    asyncio.run(run())


def test_uncommitted_and_foreign_requests_are_not_observed_without_effects(tmp_path):
    async def run():
        async with state(tmp_path) as (runtime,people):
            sender,other=[await prepare(runtime,p) for p in people[:2]]
            request=str(uuid4());query=receipt_query(request,message())
            before=await snapshot(runtime)
            assert (await sender.call('get_message_receipt',query))['status']=='not_observed'
            assert await snapshot(runtime)==before
            await sender.call('send_message',message(),request_id=request)
            before=await snapshot(runtime)
            absent=await other.call('get_message_receipt',query)
            assert absent['status']=='not_observed' and absent['receipt'] is None
            assert await snapshot(runtime)==before
    asyncio.run(run())


def test_committed_refusal_is_readable_and_stable(tmp_path):
    async def run():
        async with state(tmp_path) as (runtime,people):
            sender=await prepare(runtime,people[0]);request=str(uuid4())
            await runtime.core.retire(people[1]['agent_id'])
            with pytest.raises(B1Error,match='TARGET_RETIRED'):
                await sender.call('send_message',message(),request_id=request)
            before=await snapshot(runtime)
            result=await sender.receipt(request)
            assert result['status']=='refused' and result['refusal_code']=='TARGET_RETIRED'
            assert result['receipt'] is None and 'candidates' not in result
            assert await snapshot(runtime)==before
    asyncio.run(run())


@pytest.mark.parametrize('field,value',[('send_request_id','../bad'),('send_request_id',True),
    ('expected_intent_sha256','bad'),('expected_content_sha256','x'*64)])
def test_invalid_receipt_queries_are_refused(tmp_path,field,value):
    async def run():
        async with state(tmp_path) as (runtime,people):
            sender=await prepare(runtime,people[0]);query=receipt_query(str(uuid4()),message())
            before=await snapshot(runtime)
            with pytest.raises(B1Error):await sender.call('get_message_receipt',{**query,field:value})
            assert await snapshot(runtime)==before
    asyncio.run(run())


@pytest.mark.parametrize('tamper',['body','subject','recipient','thread','delete'])
def test_missing_or_changed_committed_content_is_never_accepted(tmp_path,tamper):
    async def run():
        async with state(tmp_path) as (runtime,people):
            sender=await prepare(runtime,people[0]);request=str(uuid4())
            sent=await sender.call('send_message',message(),request_id=request)
            if tamper=='delete':
                # Deliberate corruption of this fixture's private store, not a product deletion.
                with sqlite3.connect(runtime.root/'storage.sqlite3') as db:
                    db.execute('PRAGMA foreign_keys=OFF')
                    db.execute('DELETE FROM messages WHERE id=?',(sent['message_id'],))
                before=await snapshot(runtime)
                with pytest.raises(B1Error):await sender.receipt(request)
                assert await snapshot(runtime)==before
                return
            async with runtime.sessions() as session:
                msg=await session.get(Message,sent['message_id'])
                if tamper=='body':msg.body_md='changed'
                elif tamper=='subject':msg.subject='changed'
                elif tamper=='thread':msg.thread_id='changed'
                else:
                    pairs=(await session.execute(select(MessageRecipient).where(MessageRecipient.message_id==msg.id))).scalars().all()
                    for row in pairs:await session.delete(row)
                await session.commit()
            before=await snapshot(runtime)
            with pytest.raises(B1Error):await sender.receipt(request)
            assert await snapshot(runtime)==before
    asyncio.run(run())


def test_retired_sender_and_wrong_instance_cannot_read_receipts(tmp_path):
    async def run():
        async with state(tmp_path) as (runtime,people):
            sender=await prepare(runtime,people[0]);request=str(uuid4())
            await sender.call('send_message',message(),request_id=request)
            query=receipt_query(request,message())
            with pytest.raises(B1Error,match='INSTANCE_MISMATCH'):
                await runtime.core.message_receipt(expected_server_instance_id='f'*32,auth=people[0]['auth'],**query)
            await runtime.core.retire(people[0]['agent_id'])
            with pytest.raises(B1Error,match='ACTOR_RETIRED'):await sender.receipt(request)
    asyncio.run(run())


def test_wrong_saved_intent_hash_and_wrong_content_hash_fail_closed(tmp_path):
    async def run():
        async with state(tmp_path) as (runtime,people):
            sender=await prepare(runtime,people[0]);request=str(uuid4())
            await sender.call('send_message',message(),request_id=request)
            query=receipt_query(request,message())
            for field in ('expected_intent_sha256','expected_content_sha256'):
                with pytest.raises(B1Error):await sender.call('get_message_receipt',{**query,field:'0'*64})
    asyncio.run(run())
