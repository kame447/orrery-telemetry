"""Reproduction: legacy fetch_inbox returns no body unless include_bodies=true."""
from types import SimpleNamespace

from agentstack_mail import app


def test_message_payload_omits_body_unless_requested():
    message = SimpleNamespace(id=1, project_id=1, sender_id=2, thread_id=None, topic=None,
                              subject="s", importance="normal", ack_required=False,
                              created_ts=None, attachments=[], body_md="full body")
    assert "body_md" not in app._message_to_dict(message, include_body=False)
    assert app._message_to_dict(message, include_body=True)["body_md"] == "full body"
