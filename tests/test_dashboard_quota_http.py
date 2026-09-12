from __future__ import annotations

import json
import threading
import urllib.request
import subprocess
import shutil
import os
from pathlib import Path
from http.server import ThreadingHTTPServer

import pytest

import dashboard.quota_server as quota_server


class _FakeQuotaService:
    def read_all(self) -> dict[str, object]:
        return {
            "ts": 1000,
            "degraded": True,
            "providers": [
                {
                    "provider": "codex",
                    "status": "ok",
                    "source": "fixture",
                    "observed_at": 999,
                    "buckets": [
                        {
                            "id": "5h",
                            "label": "5h",
                            "scope": "account",
                            "used_percent": 25.0,
                            "remaining_percent": 75.0,
                            "window_seconds": 18000,
                            "resets_at": 1200,
                            "quality": "exact",
                        }
                    ],
                },
                {
                    "provider": "antigravity",
                    "status": "unavailable",
                    "source": "fixture",
                    "observed_at": 1000,
                    "buckets": [],
                    "reason": "offline",
                },
            ],
        }


def test_api_quotas_returns_partial_failure_as_200(monkeypatch):
    monkeypatch.setattr(quota_server, "QUOTA_SERVICE", _FakeQuotaService())
    server = ThreadingHTTPServer(("127.0.0.1", 0), quota_server.Handler)
    worker = threading.Thread(target=server.handle_request, daemon=True)
    worker.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/api/quotas",
            timeout=5,
        ) as response:
            assert response.status == 200
            assert response.headers["Cache-Control"] == "no-store"
            payload = json.loads(response.read())
    finally:
        worker.join(timeout=6)
        server.server_close()

    assert not worker.is_alive()
    assert payload["degraded"] is True
    assert [provider["status"] for provider in payload["providers"]] == [
        "ok",
        "unavailable",
    ]
    assert payload["providers"][0]["buckets"][0]["remaining_percent"] == 75.0


def test_usage_script_executes_demo_privacy_and_escapes_provider_content():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the UI runtime regression")
    script = quota_server._USAGE_SCRIPT.split(">", 1)[1].rsplit("</script>", 1)[0]
    harness = r"""
const vm=require('node:vm');
const assert=require('node:assert/strict');
const script=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
async function run(search, force){
  let requests=0;
  const root={innerHTML:''};
  const context={document:{getElementById:()=>root},location:{search},
    window:{AGENTSTACK_DEMO_FORCE:force},URLSearchParams,setInterval:()=>{},
    fetch:async()=>{requests++;return {ok:true,json:async()=>({providers:[{
      provider:'<script>secret</script>',status:'stale',source:'fixture',buckets:[
        {id:'good',label:'<img src=x onerror=alert(1)>',remaining_percent:25},
        {id:'missing',label:'missing',remaining_percent:null},
      ]}]})};}};
  vm.runInNewContext(script,context);
  await new Promise(resolve=>setImmediate(resolve));
  return {requests,html:root.innerHTML};
}
(async()=>{
  for(const [search,force] of [['?demo=1',false],['',true]]){
    const result=await run(search,force);
    assert.equal(result.requests,0);
    assert.match(result.html,/demo-fixture/);
  }
  const live=await run('',false);
  assert.equal(live.requests,1);
  assert.match(live.html,/25%/);
  assert.match(live.html,/stale/);
  assert.match(live.html,/&lt;img/);
  assert.ok(!live.html.includes('<script>secret'));
  assert.ok(!live.html.includes('>missing<'));
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run([node, "-e", harness], input=json.dumps(script),
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_real_dashboard_injection_preserves_existing_script_bodies():
    import re
    source = Path(quota_server.legacy.INDEX_HTML).read_bytes()
    injected = quota_server.inject_usage_ui(source)
    scripts = lambda html: re.findall(rb"<script\b[^>]*>(.*?)</script\s*>", html, re.S)
    original_scripts = scripts(source)
    result_scripts = scripts(injected)
    assert result_scripts[:-1] == original_scripts
    assert b"refreshQuota" in result_scripts[-1]
    assert injected.rfind(b'id="agentstack-quota-script"') > injected.find(b"doc.write(")


def test_served_demo_guard_blocks_api_even_without_demo_assets():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the UI runtime regression")
    source = quota_server.inject_served_demo(b"<html><head></head><body></body></html>")
    guard = source.split(b"<script>", 1)[1].split(b"</script>", 1)[0].decode()
    harness = r"""
const vm=require('node:vm'),assert=require('node:assert/strict');
let requests=0;
const context={URL,location:{href:'http://localhost:8770/?demo=1'},
  window:{fetch:()=>{requests++;return Promise.resolve({});}}};
vm.runInNewContext(JSON.parse(require('node:fs').readFileSync(0,'utf8')),context);
(async()=>{
  for(const path of ['/api/agents','/api/quotas','/api/spawn','/api/future-endpoint']){
    await assert.rejects(context.window.fetch(path,{method:'POST'}),/demo API blocked/);
  }
  assert.equal(requests,0);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run([node, "-e", harness], input=json.dumps(guard),
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_served_demo_html_loads_stories_and_guard_before_dashboard_scripts(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), quota_server.Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with urllib.request.urlopen(base + "/?demo=1", timeout=5) as response:
            body = response.read()
        assert body.index(b"demo API blocked") < body.index(b'demo/demo_api.js')
        assert body.index(b'demo/story_bugreport.js') < body.index(b'demo/demo_api.js')
        for name in ("demo_api.js", "demo_tour.js", "story_bugreport.js", "story_research.js"):
            with urllib.request.urlopen(base + "/demo/" + name, timeout=3) as response:
                assert response.status == 200
                assert b"<html" not in response.read()[:20]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


@pytest.mark.skipif(os.name == "nt", reason="agentctl is the macOS/WSL shell entrypoint")
@pytest.mark.parametrize("has_wrapper", [True, False])
def test_foreground_start_selects_quota_wrapper_with_legacy_fallback(tmp_path, has_wrapper):
    dashboard = tmp_path / "dashboard"
    dashboard.mkdir()
    original = Path(quota_server.__file__).with_name("agentctl.sh")
    shutil.copyfile(original, dashboard / "agentctl.sh")
    if has_wrapper:
        (dashboard / "quota_server.py").touch()
    result = subprocess.run(
        ["/bin/bash", str(dashboard / "agentctl.sh"), "fg"],
        env={**os.environ, "AGENTSTACK_ENV_FILE": str(tmp_path / "absent"),
             "AGENTSTACK_PYTHON": "/bin/echo"},
        text=True, capture_output=True, timeout=5,
    )
    assert result.returncode == 0, result.stderr
    expected = "quota_server.py" if has_wrapper else "server.py"
    assert result.stdout.strip() == str(dashboard / expected)


def test_agent_api_does_not_wait_for_quota_refresh(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    class SlowQuotaService:
        def read_all(self):
            entered.set()
            assert release.wait(timeout=5)
            return {"providers": []}
    monkeypatch.setattr(quota_server, "QUOTA_SERVICE", SlowQuotaService())
    monkeypatch.setattr(quota_server.legacy, "build_agents", lambda: [{"name": "fixture"}])
    server = ThreadingHTTPServer(("127.0.0.1", 0), quota_server.Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    errors = []
    def request_quota():
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/quotas", timeout=6) as response:
                response.read()
        except Exception as exc:
            errors.append(exc)
    poll = threading.Thread(target=request_quota, daemon=True)
    poll.start()
    try:
        assert entered.wait(timeout=2)
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/agents", timeout=2) as response:
            assert json.loads(response.read())["agents"] == [{"name": "fixture"}]
        assert poll.is_alive()
    finally:
        release.set()
        poll.join(timeout=6)
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
    assert errors == []


@pytest.mark.parametrize("headers", [
    {"Origin": "https://untrusted.example"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
])
def test_cross_origin_quota_request_cannot_start_provider(monkeypatch, headers):
    class NeverRead:
        def read_all(self):
            pytest.fail("cross-origin GET must not start a quota provider")
    monkeypatch.setattr(quota_server, "QUOTA_SERVICE", NeverRead())
    server = ThreadingHTTPServer(("127.0.0.1", 0), quota_server.Handler)
    worker = threading.Thread(target=server.handle_request, daemon=True)
    worker.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/quotas", headers=headers,
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=3)
        assert error.value.code == 403
    finally:
        worker.join(timeout=4)
        server.server_close()
