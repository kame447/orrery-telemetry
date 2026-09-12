#!/usr/bin/env python3
"""Quota-aware dashboard wrapper.

The existing dashboard server stays untouched. This module adds a read-only
/api/quotas endpoint and injects a compact provider-usage strip into the root
HTML, then delegates every other route and action to dashboard.server.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    from dashboard import server as legacy
    from dashboard.quotas import build_default_service
except ModuleNotFoundError:  # direct `python dashboard/quota_server.py`
    import server as legacy
    from quotas import build_default_service


QUOTA_SERVICE = build_default_service()

_USAGE_STYLE = r"""
<style id="agentstack-quota-styles">
.usage-strip{margin:16px 26px 0;border:1px solid var(--line);background:var(--panel);
  padding:9px 12px;display:flex;align-items:center;gap:14px;min-height:42px;
  font-family:"IBM Plex Mono",ui-monospace,monospace}
body[data-view="net"] .usage-strip{display:none}
.usage-title{flex:none;font-size:9px;letter-spacing:2.6px;color:var(--bone-dim)}
.usage-providers{display:flex;align-items:center;gap:18px;min-width:0;flex:1;overflow-x:auto}
.usage-provider{display:flex;align-items:center;gap:9px;white-space:nowrap;flex:none}
.usage-provider-name{font-size:10px;letter-spacing:1.4px;color:var(--bone);text-transform:uppercase}
.usage-status{font-size:8px;letter-spacing:.8px;color:var(--amber);text-transform:uppercase}
.usage-observed{font-size:8px;letter-spacing:.6px;color:var(--bone-dim);opacity:.68;font-variant-numeric:tabular-nums}
.usage-bucket{display:inline-flex;align-items:center;gap:5px;font-size:9px;color:var(--bone-dim)}
.usage-bucket-label{max-width:160px;overflow:hidden;text-overflow:ellipsis}
.usage-meter{width:42px;height:3px;background:var(--line-soft);overflow:hidden;display:inline-block}
.usage-meter>i{display:block;height:100%;background:var(--amber);transition:width .4s ease}
.usage-pct{min-width:30px;text-align:right;color:var(--bone);font-variant-numeric:tabular-nums}
.usage-state{font-size:9px;color:var(--bone-dim)}
.usage-provider[data-status="stale"]{opacity:.62}
.usage-provider[data-status="unavailable"]{opacity:.42}
.usage-provider[data-status="degraded"] .usage-provider-name{color:var(--amber)}
@media(max-width:760px){.usage-strip{margin-left:14px;margin-right:14px;align-items:flex-start;flex-direction:column;gap:8px}
  .usage-providers{gap:13px;width:100%}.usage-provider{align-items:flex-start;flex-direction:column;gap:4px}}
</style>
"""

_USAGE_HTML = r"""
<section id="usage-strip" class="usage-strip" aria-label="Provider account usage">
  <span class="usage-title">USAGE · LEFT</span>
  <div id="usage-providers" class="usage-providers">
    <span class="usage-state">ACQUIRING QUOTA</span>
  </div>
</section>
"""

_USAGE_SCRIPT = r"""
<script id="agentstack-quota-script">
(()=>{
  const root=document.getElementById('usage-providers');
  if(!root)return;
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const pct=v=>{
    if(v===null||v===undefined||typeof v==='boolean'||v==='')return null;
    const n=Number(v);
    return Number.isFinite(n)?Math.max(0,Math.min(100,n)):null;
  };
  const resetText=ts=>{
    if(!ts)return '';
    const date=new Date(Number(ts)*1000);
    return Number.isNaN(date.getTime())?'':`reset ${date.toLocaleString()}`;
  };
  const observedText=ts=>{
    if(!ts)return '';
    const date=new Date(Number(ts)*1000);
    if(Number.isNaN(date.getTime()))return '';
    return date.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
  };
  function bucket(b){
    const remaining=pct(b.remaining_percent);
    if(remaining===null)return '';
    const title=['remaining quota',b.scope,b.quality,resetText(b.resets_at)].filter(Boolean).join(' · ');
    return `<span class="usage-bucket" title="${esc(title)}">`+
      `<span class="usage-bucket-label">${esc(b.label||b.id)}</span>`+
      `<span class="usage-meter"><i style="width:${remaining.toFixed(1)}%"></i></span>`+
      `<span class="usage-pct">${Math.round(remaining)}%</span></span>`;
  }
  function provider(p){
    const status=p.status||'unavailable';
    const name=esc(p.provider||'provider');
    const items=Array.isArray(p.buckets)?p.buckets:[];
    const rendered=items.map(bucket).filter(Boolean);
    const detail=rendered.length?rendered.join(''):`<span class="usage-state">${esc(status)}</span>`;
    const statusHtml=rendered.length&&status!=='ok'?`<span class="usage-status">${esc(status)}</span>`:'';
    const observed=observedText(p.observed_at);
    const observedHtml=observed?`<span class="usage-observed" aria-label="last update ${esc(observed)}">${esc(observed)}</span>`:'';
    const title=[p.source,p.reason,p.observed_at?`observed ${new Date(Number(p.observed_at)*1000).toLocaleString()}`:''].filter(Boolean).join(' · ');
    return `<div class="usage-provider" data-status="${esc(status)}" title="${esc(title)}">`+
      `<span class="usage-provider-name">${name}</span>${statusHtml}${observedHtml}${detail}</div>`;
  }
  function renderQuota(data){
    const providers=Array.isArray(data&&data.providers)?data.providers:[];
    root.innerHTML=providers.length?providers.map(provider).join(''):'<span class="usage-state">NO QUOTA SIGNAL</span>';
  }
  const params=new URLSearchParams(location.search);
  const demo=params.get('demo')==='1'||Boolean(window.AGENTSTACK_DEMO_FORCE);
  const demoQuota={providers:[
    {provider:'claude',status:'ok',source:'demo-fixture',observed_at:null,buckets:[
      {id:'five_hour',label:'5h',scope:'account',remaining_percent:76,quality:'exact'},
      {id:'seven_day',label:'7d',scope:'account',remaining_percent:59,quality:'exact'}]},
    {provider:'codex',status:'ok',source:'demo-fixture',observed_at:null,buckets:[
      {id:'codex-300m',label:'5h',scope:'account',remaining_percent:43,quality:'exact'},
      {id:'codex-10080m',label:'7d',scope:'account',remaining_percent:68,quality:'exact'}]},
    {provider:'antigravity',status:'ok',source:'demo-fixture',observed_at:null,buckets:[
      {id:'demo-5h',label:'5h',scope:'account',remaining_percent:82,quality:'exact'},
      {id:'demo-weekly',label:'7d',scope:'account',remaining_percent:58,quality:'exact'}]}
  ]};
  async function refreshQuota(){
    // Demo mode has a strict no-real-machine contract. Never fall through to
    // the live quota endpoint when the rest of the dashboard is synthetic.
    if(demo){renderQuota(demoQuota);return;}
    try{
      const res=await fetch('/api/quotas',{cache:'no-store'});
      if(!res.ok)throw new Error(`HTTP ${res.status}`);
      renderQuota(await res.json());
    }catch(err){
      root.innerHTML=`<span class="usage-state" title="${esc(err)}">QUOTA UNAVAILABLE</span>`;
    }
  }
  refreshQuota();
  setInterval(refreshQuota,60000);
})();
</script>
"""


def inject_usage_ui(source: bytes) -> bytes:
    """Inject the quota strip without modifying the 1 MB dashboard HTML source."""

    text = source.decode("utf-8")
    if 'id="agentstack-quota-script"' in text:
        return source
    if "</head>" not in text or "</body>" not in text:
        return source
    text = text.replace("</head>", _USAGE_STYLE + "\n</head>", 1)
    marker = '<main id="wrap">'
    if marker in text:
        text = text.replace(marker, _USAGE_HTML + "\n" + marker, 1)
    else:
        text = text.replace("<body>", "<body>\n" + _USAGE_HTML, 1)
    # The real dashboard contains a document.write('...<body></body>...')
    # string inside its main script. Replacing the first closing body would
    # inject a literal </script> there and break the entire dashboard script.
    before, closing, after = text.rpartition("</body>")
    text = before + _USAGE_SCRIPT + "\n" + closing + after
    return text.encode("utf-8")


class Handler(legacy.Handler):
    def do_GET(self):
        path = urlparse(self.path).path
        demo_assets = {f"/demo/{name}": Path(legacy.HERE) / "demo" / name for name in (
            "demo_api.js", "demo_tour.js", "story_bugreport.js", "story_research.js",
        )}
        if path in demo_assets:
            try:
                self._send(200, demo_assets[path].read_bytes(), "text/javascript; charset=utf-8")
            except OSError:
                self._send(404, b"demo asset missing", "text/plain")
            return
        # Only bundled public portraits, never runtime/custom portrait files.
        if path.startswith("/portraits_64/"):
            name = path.removeprefix("/portraits_64/")
            if name.endswith(".png") and name[:-4].isalpha() and name.isascii():
                try:
                    self._send(200, (Path(legacy.PORT_64) / name).read_bytes(), "image/png")
                except OSError:
                    self._send(404, b"portrait missing", "text/plain")
                return
        if path == "/api/quotas":
            # A GET can start an authenticated provider process on a cache
            # miss. Reject cross-origin browser triggers before any refresh;
            # local CLI clients and direct navigation remain usable.
            origin = (self.headers.get("Origin") or "").strip()
            fetch_site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
            expected_origin = f"http://{self.headers.get('Host', '')}"
            if ((origin and origin != expected_origin)
                    or fetch_site not in {"", "none", "same-origin"}):
                self._send(403, b'{"error":"cross_origin_quota_request"}', "application/json")
                return
            body = json.dumps(
                QUOTA_SERVICE.read_all(),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        if path == "/":
            try:
                with open(legacy.INDEX_HTML, "rb") as handle:
                    source = inject_usage_ui(handle.read())
                demo = parse_qs(urlparse(self.path).query, keep_blank_values=True).get("demo", [""])[0]
                if demo == "1":
                    source = inject_served_demo(source)
                body = legacy._render_dashboard_index(source)
            except OSError as exc:
                self._send(500, str(exc).encode("utf-8"), "text/plain; charset=utf-8")
                return
            self._send(200, body, "text/html; charset=utf-8")
            return
        super().do_GET()


def inject_served_demo(source: bytes) -> bytes:
    # Install a fail-closed transport BEFORE any page script. If a demo engine
    # or story asset is missing, no live API read/write may escape. The demo
    # engine then wraps this guard with its synthetic responses.
    guard = b"""<script>
(()=>{const original=window.fetch.bind(window);window.fetch=(input,init)=>{
const url=new URL(typeof input==='string'?input:input.url,location.href);
if(url.pathname.startsWith('/api/'))return Promise.reject(new Error('demo API blocked'));
return original(input,init);};})();
</script>
<script src="demo/story_bugreport.js"></script>
<script src="demo/story_research.js"></script>
"""
    return source.replace(b"<head>", b"<head>" + guard, 1)


def main() -> None:
    # legacy.main resolves Handler at runtime, so swapping only this module-level
    # reference preserves its cleanup, watchdog and HTTP lifecycle unchanged.
    legacy.Handler = Handler
    legacy.main()


if __name__ == "__main__":
    main()
