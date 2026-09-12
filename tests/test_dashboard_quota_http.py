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
  const roots=Object.fromEntries(['usage-providers','usage-additional','usage-additional-shell',
    'usage-additional-track','usage-additional-viewport','usage-additional-prev','usage-additional-next']
    .map(id=>[id,{innerHTML:'',hidden:false,dataset:{},scrollLeft:0,scrollWidth:900,clientWidth:300,
      addEventListener:()=>{},setAttribute:()=>{},scrollBy(){this.scrollLeft+=240}}]));
  const context={document:{getElementById:id=>roots[id]??null},location:{search},
    window:{AGENTSTACK_DEMO_FORCE:force},URLSearchParams,setInterval:()=>{},
    fetch:async()=>{requests++;return {ok:true,json:async()=>({providers:[{
      provider:'<script>secret</script>',status:'stale',source:'fixture',buckets:[
        {id:'good',label:'<img src=x onerror=alert(1)>',remaining_percent:25},
        {id:'missing',label:'missing',remaining_percent:null},
      ]}]})};}};
  vm.runInNewContext(script,context);
  await new Promise(resolve=>setImmediate(resolve));
  return {requests,main:roots['usage-providers'].innerHTML,
    additional:roots['usage-additional-track'].innerHTML+roots['usage-additional-viewport'].innerHTML};
}
(async()=>{
  for(const [search,force] of [['?demo=1',false],['',true]]){
    const result=await run(search,force);
    assert.equal(result.requests,0);
    assert.match(result.main+result.additional,/demo-fixture/);
  }
  const live=await run('',false);
  assert.equal(live.requests,1);
  assert.match(live.additional,/25%/);
  assert.match(live.additional,/stale/);
  assert.match(live.additional,/&lt;img/);
  assert.ok(!live.additional.includes('<script>secret'));
  assert.match(live.additional,/UNKNOWN|unknown/);
  assert.ok(!live.additional.includes('>0%<')&&!live.additional.includes('>100%<'));
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run([node, "-e", harness], input=json.dumps(script),
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_usage_cards_separate_main_pools_resets_scopes_and_overflow_controls():
    """Execute the injected UI and exercise its rendered DOM contracts.

    This intentionally uses a tiny DOM implementation instead of inspecting the
    JavaScript source.  It is sufficient for the dashboard's server-rendered
    fragment and lets this regression test remain dependency-free (jsdom is not
    a dashboard runtime dependency).
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the UI runtime regression")
    for node_id in ("usage-providers", "usage-additional", "usage-additional-shell",
                    "usage-additional-viewport", "usage-additional-track",
                    "usage-additional-prev", "usage-additional-next"):
        assert f'id="{node_id}"' in quota_server._USAGE_HTML
    script = quota_server._USAGE_SCRIPT.split(">", 1)[1].rsplit("</script>", 1)[0]
    harness = r'''
const vm=require('node:vm'),assert=require('node:assert/strict');
const source=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
class El {
  constructor(tag,attrs){this.tagName=tag;this.attrs=attrs;this.dataset={};
    for(const [k,v] of Object.entries(attrs)){if(k.startsWith('data-'))
      this.dataset[k.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=v??'';}
    this.listeners={};this.scrollLeft=0;this.scrollWidth=1000;this.clientWidth=400;}
  addEventListener(type,fn){(this.listeners[type]??=[]).push(fn)}
  dispatch(type,extra={}){if(typeof this['on'+type]==='function')this['on'+type]({target:this,...extra});
    for(const fn of this.listeners[type]??[])fn({target:this,...extra})}
  setAttribute(k,v){this.attrs[k]=String(v)}
  removeAttribute(k){delete this.attrs[k]}
  getAttribute(k){return this.attrs[k]??null}
  matches(sel){
    if(sel[0]==='#')return this.attrs.id===sel.slice(1);
    const m=sel.match(/^\[data-([\w-]+)(?:="([^"]*)")?\]$/);
    if(m)return this.dataset[m[1].replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]!==undefined &&
      (m[2]===undefined||this.dataset[m[1].replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]===m[2]);
    if(sel[0]==='.')return (this.attrs.class||'').split(/\s+/).includes(sel.slice(1));
    return false;
  }
}
class Root extends El {
  constructor(){super('div',{});this._html='';this.children=[];this.scrollByCalls=[]}
  set innerHTML(v){this._html=String(v);this.children=[];
    const re=/<([a-z][\w-]*)(?:\s+([^>]*?))?\s*\/?>/gi;let m;
    while((m=re.exec(this._html))){const attrs={};
      for(const a of (m[2]||'').matchAll(/([\w-]+)(?:="([^"]*)")?/g))attrs[a[1]]=a[2]??'';
      this.children.push(new El(m[1],attrs));}
  }
  get innerHTML(){return this._html}
  querySelectorAll(sel){return this.children.filter(e=>e.matches(sel))}
  querySelector(sel){return this.querySelectorAll(sel)[0]??null}
  getElementById(id){return this.children.find(e=>e.attrs.id===id)??null}
  scrollBy(opts){this.scrollByCalls.push(opts);this.scrollLeft+=Number(opts.left||0)}
  scrollTo(opts){this.scrollBy(opts)}
}
async function run(payload){
  const ids=['usage-providers','usage-additional','usage-additional-shell','usage-additional-track',
    'usage-additional-viewport','usage-additional-prev','usage-additional-next'];
  const nodes=Object.fromEntries(ids.map(id=>[id,new Root()]));let requests=0;
  nodes['usage-additional-viewport'].clientWidth=300;nodes['usage-additional-viewport'].scrollWidth=900;
  nodes['usage-additional-prev'].innerHTML='<button id="usage-additional-prev" data-overflow-arrow="left"></button>';
  nodes['usage-additional-next'].innerHTML='<button id="usage-additional-next" data-overflow-arrow="right"></button>';
  const document={getElementById:id=>{
    if(id==='usage-additional-prev'||id==='usage-additional-next')return nodes[id].children[0];
    return nodes[id]??null;},
    querySelectorAll:sel=>Object.values(nodes).flatMap(n=>n.querySelectorAll(sel)),
    querySelector:sel=>Object.values(nodes).flatMap(n=>n.querySelectorAll(sel))[0]??null};
  let resizeCallback=null;
  const windowListeners={};
  const window={addEventListener:(type,fn)=>(windowListeners[type]??=[]).push(fn),
    dispatchEvent:event=>(windowListeners[event.type]??[]).forEach(fn=>fn(event))};
  class CustomEvent{constructor(type,init){this.type=type;this.detail=init?.detail}}
  class ResizeObserver{constructor(fn){this.fn=fn;resizeCallback=fn}observe(){this.fn()}disconnect(){}}
  const context={document,location:{search:''},window,ResizeObserver,CustomEvent,URLSearchParams,setInterval:()=>{},
    fetch:async()=>{requests++;return {ok:true,json:async()=>payload}}};
  vm.runInNewContext(source,context); await new Promise(resolve=>setImmediate(resolve));
  return {nodes,requests,window,resizeCallback};
}
(async()=>{
  const result=await run({providers:[
    {provider:'codex',status:'ok',buckets:[
      {id:'codex_bengalfox-300m',label:'Spark · 5h',scope:'account',remaining_percent:11,resets_at:1800000000},
      {id:'codex_bengalfox-10080m',label:'Extra · 7d',scope:'account',remaining_percent:22,resets_at:'invalid'},
      {id:'codex-10080m',label:'7d',scope:'account',remaining_percent:77,resets_at:1800000000},
      {id:'codex-other',label:'unrecognised',scope:'account',remaining_percent:null,resets_at:null}]},
    {provider:'claude',status:'ok',buckets:[
      {id:'claude-main',label:'5h',scope:'account',remaining_percent:80,resets_at:1800000000}]},
    {provider:'antigravity',status:'ok',buckets:[
      {id:'gemini-weekly',label:'Gemini Models · 7d',scope:'account',remaining_percent:50,resets_at:1800000000},
      {id:'3p-weekly',label:'GPT',scope:'account',remaining_percent:40,resets_at:1800000000}]}
  ]});
  assert.equal(result.requests,1);
  const main=result.nodes['usage-providers'].innerHTML;
  const additional=result.nodes['usage-additional-track'].innerHTML;
  assert.match(main,/codex-10080m/);
  assert.doesNotMatch(main,/codex_bengalfox|codex-other/,'non-main limits must not enter main root');
  assert.doesNotMatch(additional,/codex_bengalfox/);
  assert.match(additional,/codex-other/,'unknown Codex limits may remain safely in additional area');
  assert.match(additional,/data-pool="other"/);
  assert.doesNotMatch(additional,/Spark|Extra/);
  assert.match(additional,/<span class="usage-pct" data-unknown="true">UNKNOWN<\/span>/);
  assert.match(additional,/role="img"[^>]*aria-label="[^"]*unknown/i);
  assert.match(main+additional,/1\/15 08:00 reset/);
  assert.match(main+additional,/RESET UNKNOWN/);
  assert.match(main+additional,/data-provider="antigravity"[\s\S]*data-scope="antigravity"/);
  assert.match(main,/gemini-weekly/);
  assert.doesNotMatch(main,/3p-weekly|via Antigravity/);
  assert.doesNotMatch(main,/<div class="usage-card-scope">/);
  const arrows=Object.values(result.nodes).flatMap(n=>n.querySelectorAll('[data-overflow-arrow]'));
  assert.equal(arrows.length,2,'both overflow arrows must always be rendered');
  const track=result.nodes['usage-additional-viewport'];
  const disabled=a=>a.getAttribute('aria-disabled')==='true'||a.getAttribute('disabled')!==null;
  const state=()=>arrows.map(disabled);
  assert.equal(state()[0],true); assert.equal(state()[1],false);
  const leftBefore=track.scrollLeft; arrows[0].dispatch('click');
  assert.equal(track.scrollLeft,leftBefore,'disabled left arrow must be a no-op');
  arrows[1].dispatch('click'); assert.ok(track.scrollByCalls.length||track.scrollLeft>0);
  track.scrollLeft=300; track.dispatch('scroll');
  assert.equal(state()[0],false); assert.equal(state()[1],false);
  track.scrollLeft=800; track.dispatch('scroll'); assert.equal(state()[1],true);
  assert.equal(state()[0],false);
  track.clientWidth=1000; result.resizeCallback();
  assert.equal(state()[0],true); assert.equal(state()[1],true);
  assert.equal(result.nodes['usage-additional-shell'].dataset.overflowLeft,'false');
  assert.equal(result.nodes['usage-additional-shell'].dataset.overflowRight,'false');
  const absent=await run({providers:[
    {provider:'claude',status:'unavailable',buckets:[]},
    {provider:'codex',status:'ok',buckets:[
      {id:'codex_bengalfox-300m',label:'Spark',remaining_percent:12}]},
    {provider:'antigravity',status:'unavailable',buckets:[]}
  ]});
  const absentMain=absent.nodes['usage-providers'].innerHTML;
  const absentAdditional=absent.nodes['usage-additional-track'].innerHTML;
  const identities=[...absentMain.matchAll(/data-provider="([^"]+)"/g)].map(m=>m[1]);
  assert.deepEqual(identities,['claude','codex','antigravity']);
  assert.match(absentMain,/unavailable/i);
  assert.equal(absentAdditional,'');
  assert.equal(absent.nodes['usage-additional'].hidden,true);
  assert.doesNotMatch(absentMain,/Spark/);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    result = subprocess.run([node, "-e", harness], input=json.dumps(script),
                            text=True, capture_output=True, timeout=10,
                            env={**os.environ, "TZ":"UTC"})
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
