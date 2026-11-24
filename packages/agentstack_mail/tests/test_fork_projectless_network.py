"""Fork acceptance: two real CLI working directories, one Mail and graph."""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from agentstack_mail.fresh_client import FreshBoundClient
from dashboard.fresh_backend import Backend
from test_fresh_guard import live

ROOT = Path(__file__).resolve().parents[3]


def test_cross_folder_cli_mail_and_network(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tests"))
    from test_fresh_dashboard import dashboard
    async def run():
        a, b = tmp_path / "repo-a", tmp_path / "repo-b"
        a.mkdir()
        b.mkdir()
        async with live(tmp_path) as (runtime, people, alpha, _):
            beta = await FreshBoundClient.prepare(
                runtime.root / "config.json", people[1]["binding_path"],
                alpha.context["endpoint"], client_id=str(uuid4())
            )
            async def call(client, cwd, tool, arguments):
                env = dict(os.environ, AGENTSTACK_PROJECT_KEY=str(cwd),
                           AGENTSTACK_RUNTIME_MODE="fresh",
                           PYTHONPATH=os.pathsep.join(map(str, (ROOT, ROOT / "packages/agentstack_mail/src"))))
                result = await asyncio.to_thread(
                    subprocess.run,
                    [sys.executable, "-m", "agentstack_mail.fresh_client",
                     "call-json", "--context", str(client.path), "--tool", tool],
                    cwd=cwd, env=env, input=json.dumps(arguments),
                    capture_output=True, text=True, timeout=45
                )
                assert result.returncode == 0, "fixture client failed"
                return json.loads(result.stdout)

            aid, bid = (c.context["mail_agent_id"] for c in (alpha, beta))
            await call(alpha, a, "send_message", dict(
                recipients=[{"target_agent_id": bid}], subject="A to B", body_md="fixture A"))
            await call(beta, b, "send_message", dict(
                recipients=[{"target_agent_id": aid}], subject="B to A", body_md="fixture B"))
            ainbox, binbox = await asyncio.gather(
                call(alpha, a, "fetch_inbox", {}),
                call(beta, b, "fetch_inbox", {}))
            assert any(m["sender_id"] == bid and m["subject"] == "B to A" for m in ainbox)
            assert any(m["sender_id"] == aid and m["subject"] == "A to B" for m in binbox)
            backend = Backend(alpha.path, ROOT)
            with dashboard(alpha, backend, monkeypatch, tmp_path) as (_, request):
                ac, roster = await asyncio.to_thread(request, "/api/agents")
                gc, graph = await asyncio.to_thread(request, "/api/graph?all=1")
                assert ac == gc == 200
                identity = lambda row: (row["mail_instance_id"], row["mail_agent_id"])
                assert {identity(r) for r in roster["agents"]} == {identity(n) for n in graph["nodes"]}
                assert {(runtime.config["instance_id"], i) for i in (aid, bid)} <= {
                    identity(n) for n in graph["nodes"]}
                assert {(aid, bid), (bid, aid)} <= {
                    (e["source_agent_id"], e["target_agent_id"]) for e in graph["edges"]}
                assert graph["degraded"] is False
    asyncio.run(run())
