"""Rendering a tool-call panel to text must not print it (it carries message bodies)."""

from __future__ import annotations

from agentstack_mail import rich_logger


def test_render_tool_call_panel_returns_text_without_printing(capfd) -> None:
    ctx = rich_logger.ToolCallContext(tool_name="send_message", args=[], kwargs={}, project="p", agent="a")
    ctx.start_time = 1.0
    ctx.end_time = 2.0
    ctx.success = True
    ctx.result = {"deliveries": [{"payload": {"body_md": "BODY-MARKER-7f3a"}}]}

    text = rich_logger.render_tool_call_panel(ctx)

    assert "BODY-MARKER-7f3a" in text
    out, err = capfd.readouterr()
    assert "BODY-MARKER-7f3a" not in out + err
