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
  padding:12px;font-family:"IBM Plex Mono",ui-monospace,monospace;min-width:0}
body[data-view="net"] .usage-strip{display:none}
.usage-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:9px}
.usage-title{font-size:9px;letter-spacing:2.6px;color:var(--bone-dim)}
.usage-providers{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;min-width:0}
.usage-card{border:1px solid var(--line-soft);background:var(--bg);padding:10px;min-width:0}
.usage-card-head{display:flex;flex-wrap:wrap;align-items:center;gap:7px;min-width:0;margin-bottom:9px}
.usage-provider-name{min-width:0;font-size:12px;letter-spacing:1.4px;color:var(--bone);
  text-transform:uppercase;overflow-wrap:anywhere}
.usage-status{font-size:8px;letter-spacing:.8px;color:var(--amber);text-transform:uppercase}
.usage-observed{margin-left:auto;font-size:8px;letter-spacing:.6px;color:var(--bone-dim);opacity:.68;
  font-variant-numeric:tabular-nums;white-space:nowrap}
.usage-card-scope{margin:-4px 0 8px;font-size:10px;line-height:1.35;letter-spacing:.5px;color:var(--bone-dim)}
.usage-buckets{display:grid;gap:7px;min-width:0}
.usage-bucket{display:grid;grid-template-columns:minmax(0,1fr) 52px 56px;align-items:center;
  gap:6px;font-size:9px;color:var(--bone-dim);min-width:0}
.usage-bucket-label{font-size:11px;color:var(--bone);line-height:1.35;overflow-wrap:anywhere}
.usage-meter{width:52px;height:3px;background:var(--line-soft);overflow:hidden;display:inline-block}
.usage-meter>i{display:block;height:100%;background:var(--amber);transition:width .4s ease}
.usage-meter[data-unknown="true"]>i{width:100%!important;background:var(--bone-dim);opacity:.22}
.usage-pct{font-size:12px;text-align:right;color:var(--bone);font-variant-numeric:tabular-nums;white-space:nowrap}
.usage-pct[data-unknown="true"]{letter-spacing:.3px;color:var(--bone-dim)}
.usage-reset{grid-column:1/-1;font-size:10px;line-height:1.35;letter-spacing:.35px;color:var(--bone-dim);
  font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.usage-via{color:var(--amber);white-space:nowrap}
.usage-state{min-width:0;font-size:9px;color:var(--bone-dim);letter-spacing:.35px;overflow-wrap:anywhere}
.usage-card[data-status="stale"]{opacity:.68}
.usage-card[data-status="unavailable"]{opacity:.5}
.usage-card[data-status="degraded"] .usage-provider-name{color:var(--amber)}
.usage-additional{margin-top:12px;min-width:0}
.usage-additional-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:7px}
.usage-additional-title{font-size:8px;letter-spacing:1.7px;color:var(--bone-dim)}
.usage-scroll-controls{display:flex;gap:5px}
.usage-scroll-button{width:26px;height:24px;border:1px solid var(--line);background:var(--bg);color:var(--bone);
  font:12px/1 "IBM Plex Mono",ui-monospace,monospace;cursor:pointer}
.usage-scroll-button[aria-disabled="true"]{cursor:default;opacity:.28}
.usage-scroll-button:focus-visible,.usage-additional-viewport:focus-visible{outline:1px solid var(--amber);outline-offset:2px}
.usage-additional-shell{position:relative;min-width:0}
.usage-additional-shell::before,.usage-additional-shell::after{content:"";position:absolute;z-index:1;
  top:0;bottom:0;width:28px;pointer-events:none;opacity:0;transition:opacity .16s ease}
.usage-additional-shell::before{left:0;background:linear-gradient(90deg,var(--panel),transparent)}
.usage-additional-shell::after{right:0;background:linear-gradient(270deg,var(--panel),transparent)}
.usage-additional-shell[data-overflow-left="true"]::before,
.usage-additional-shell[data-overflow-right="true"]::after{opacity:1}
.usage-additional-viewport{overflow-x:auto;overscroll-behavior-inline:contain;scrollbar-width:thin;min-width:0}
.usage-additional-track{display:flex;gap:10px;width:max-content;min-width:100%}
.usage-additional-track .usage-card{width:min(310px,calc(100vw - 84px));flex:none}
@media(max-width:980px){.usage-providers{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:620px){.usage-strip{margin-left:14px;margin-right:14px}.usage-providers{grid-template-columns:minmax(0,1fr)}
  .usage-bucket{grid-template-columns:minmax(0,1fr) 48px 56px}.usage-meter{width:48px}}
</style>
"""

_USAGE_HTML = r"""
<section id="usage-strip" class="usage-strip" aria-label="Provider account usage">
  <div class="usage-heading"><span class="usage-title">USAGE · LEFT</span></div>
  <div id="usage-providers" class="usage-providers">
    <span class="usage-state">ACQUIRING QUOTA</span>
  </div>
  <section id="usage-additional" class="usage-additional" aria-label="Additional usage limits" hidden>
    <div class="usage-additional-head">
      <span class="usage-additional-title">ADDITIONAL LIMITS</span>
      <span class="usage-scroll-controls">
        <button id="usage-additional-prev" class="usage-scroll-button" type="button" data-overflow-arrow="left" aria-label="Scroll additional limits left" aria-disabled="true">&larr;</button>
        <button id="usage-additional-next" class="usage-scroll-button" type="button" data-overflow-arrow="right" aria-label="Scroll additional limits right" aria-disabled="true">&rarr;</button>
      </span>
    </div>
    <div id="usage-additional-shell" class="usage-additional-shell" data-overflow-left="false" data-overflow-right="false">
      <div id="usage-additional-viewport" class="usage-additional-viewport" tabindex="0" aria-label="Scrollable additional limits">
        <div id="usage-additional-track" class="usage-additional-track"></div>
      </div>
    </div>
  </section>
</section>
"""

_USAGE_SCRIPT = r"""
<script id="agentstack-quota-script">
(()=>{
  const root=document.getElementById('usage-providers');
  if(!root)return;
  const additional=document.getElementById('usage-additional');
  const additionalShell=document.getElementById('usage-additional-shell');
  const additionalViewport=document.getElementById('usage-additional-viewport');
  const additionalTrack=document.getElementById('usage-additional-track');
  const previousButton=document.getElementById('usage-additional-prev');
  const nextButton=document.getElementById('usage-additional-next');
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const pct=v=>{
    if(typeof v!=='number'||!Number.isFinite(v)||v<0||v>100)return null;
    return v;
  };
  const resetText=ts=>{
    if(typeof ts!=='number'||!Number.isFinite(ts))return 'RESET UNKNOWN';
    const date=new Date(ts*1000);
    if(Number.isNaN(date.getTime()))return 'RESET UNKNOWN';
    const pad=value=>String(value).padStart(2,'0');
    return `${date.getMonth()+1}/${date.getDate()} ${pad(date.getHours())}:${pad(date.getMinutes())} reset`;
  };
  const observedText=ts=>{
    if(!ts)return '';
    const date=new Date(Number(ts)*1000);
    if(Number.isNaN(date.getTime()))return '';
    return date.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
  };
  const statusOf=p=>String(p&&p.status||'unavailable');
  const bucketLabel=(b,prefix)=>{
    const label=String(b&&b.label||b&&b.id||'limit');
    return prefix&&label.startsWith(`${prefix} · `)?label.slice(prefix.length+3):label;
  };
  const antigravityScope=b=>{
    const explicit=String(b&&b.scope||'').toLowerCase();
    if(explicit==='claude'||explicit==='gpt')return explicit;
    const label=String(b&&b.label||'');
    if(/claude/i.test(label)&&/gpt/i.test(label))return 'claude-gpt';
    if(/claude/i.test(label))return 'claude';
    if(/gpt/i.test(label))return 'gpt';
    if(/gemini/i.test(label))return 'gemini';
    return 'antigravity';
  };
  function bucket(b,options={}){
    b=b&&typeof b==='object'?b:{};
    const remaining=pct(b.remaining_percent);
    const label=bucketLabel(b,options.labelPrefix);
    const reset=resetText(b.resets_at);
    const scope=options.antigravityScope?antigravityScope(b):String(b&&b.scope||'');
    const via=options.antigravityScope&&/(?:claude|gpt)/.test(scope)?` <span class="usage-via">· via Antigravity</span>`:'';
    const title=['remaining quota',b.scope,b.quality,reset,via?'via Antigravity':''].filter(Boolean).join(' · ');
    const meterUnknown=remaining===null?'true':'false';
    const meterLabel=remaining===null?`${label}: remaining quota unknown`:`${label}: ${Math.round(remaining)}% remaining`;
    const meterAttrs=remaining===null
      ?`role="img" aria-label="${esc(meterLabel)}"`
      :`role="meter" aria-label="${esc(meterLabel)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${remaining.toFixed(1)}"`;
    const percent=remaining===null?'UNKNOWN':`${Math.round(remaining)}%`;
    const width=remaining===null?'0':remaining.toFixed(1);
    const scopeAttr=scope?` data-scope="${esc(scope)}"`:'';
    return `<div class="usage-bucket" title="${esc(title)}" data-bucket-id="${esc(b.id||'limit')}"${scopeAttr}>`+
      `<span class="usage-bucket-label">${esc(label)}</span>`+
      `<span class="usage-meter" ${meterAttrs} data-unknown="${meterUnknown}"><i style="width:${width}%"></i></span>`+
      `<span class="usage-pct" data-unknown="${meterUnknown}">${percent}</span>`+
      `<span class="usage-reset">${esc(reset)}${via}</span></div>`;
  }
  function provider(p,options={}){
    const status=statusOf(p);
    const providerName=String(options.name||p.provider||'provider');
    const name=esc(providerName);
    const items=Array.isArray(options.buckets)?options.buckets:(Array.isArray(p.buckets)?p.buckets:[]);
    const rendered=items.map(item=>bucket(item,{labelPrefix:options.labelPrefix,
      antigravityScope:Boolean(options.antigravityScope)}));
    const state=options.emptyState||p.reason||status;
    const detail=rendered.length?rendered.join(''):`<span class="usage-state">${esc(state)}</span>`;
    const statusHtml=status!=='ok'?`<span class="usage-status">${esc(status)}</span>`:'';
    const observed=observedText(p.observed_at);
    const observedHtml=observed?`<span class="usage-observed" aria-label="last update ${esc(observed)}">${esc(observed)}</span>`:'';
    const title=[p.source,p.reason,p.observed_at?`observed ${new Date(Number(p.observed_at)*1000).toLocaleString()}`:''].filter(Boolean).join(' · ');
    const cardId=options.id?` id="${esc(options.id)}"`:'';
    const scope=options.scope?`<div class="usage-card-scope">${esc(options.scope)}</div>`:'';
    const role=options.role||'additional';
    const pool=options.pool||'';
    const poolAttr=pool?` data-pool="${esc(pool)}"`:'';
    const scopeAttr=options.cardScope?` data-scope="${esc(options.cardScope)}"`:'';
    return `<article${cardId} class="usage-card usage-provider" data-provider="${esc(options.providerKey||p.provider||'provider')}" data-role="${esc(role)}"${poolAttr}${scopeAttr} data-status="${esc(status)}" title="${esc(title)}">`+
      `<div class="usage-card-head"><span class="usage-provider-name">${name}</span>${statusHtml}${observedHtml}</div>${scope}`+
      `<div class="usage-buckets">${detail}</div></article>`;
  }
  const emptyProvider=name=>({provider:name,status:'unavailable',source:'',observed_at:null,buckets:[],reason:'NO QUOTA SIGNAL'});
  const codexPool=id=>{
    const match=String(id||'').match(/^(.+)-(\d+)m$/i);
    return match?match[1].toLowerCase():null;
  };
  function splitCodex(p){
    const main=[];
    const extra=new Map();
    const items=Array.isArray(p&&p.buckets)?p.buckets:[];
    for(const item of items){
      const pool=codexPool(item&&item.id);
      const id=String(item&&item.id||'');
      if(pool==='codex'&&/^codex-\d+m$/i.test(id)){main.push(item);continue;}
      // Unknown IDs stay visible as additional information, but are never
      // guessed to be the primary Codex pool.
      const extraPool=pool&&pool!=='codex'?pool:'other';
      if(!extra.has(extraPool))extra.set(extraPool,[]);
      extra.get(extraPool).push(item);
    }
    return {main,extra};
  }
  const poolName=(pool,items)=>{
    const label=String(items[0]&&items[0].label||'');
    const marker=label.indexOf(' · ');
    if(marker>0)return label.slice(0,marker);
    return pool==='other'?'Other Codex limits':pool;
  };
  function updateOverflow(){
    if(!additionalViewport||!additionalShell||!previousButton||!nextButton)return;
    const max=Math.max(0,(Number(additionalViewport.scrollWidth)||0)-(Number(additionalViewport.clientWidth)||0));
    const left=Number(additionalViewport.scrollLeft)||0;
    const hasLeft=max>1&&left>1;
    const hasRight=max>1&&left<max-1;
    additionalShell.dataset.overflowLeft=String(hasLeft);
    additionalShell.dataset.overflowRight=String(hasRight);
    if(typeof previousButton.setAttribute==='function')previousButton.setAttribute('aria-disabled',String(!hasLeft));
    if(typeof nextButton.setAttribute==='function')nextButton.setAttribute('aria-disabled',String(!hasRight));
  }
  function scrollAdditional(direction){
    if(!additionalViewport)return;
    const button=direction<0?previousButton:nextButton;
    if(button&&typeof button.getAttribute==='function'&&button.getAttribute('aria-disabled')==='true')return;
    const amount=Math.max(220,Math.floor((Number(additionalViewport.clientWidth)||300)*.8));
    if(typeof additionalViewport.scrollBy==='function')additionalViewport.scrollBy({left:direction*amount,behavior:'smooth'});
    else additionalViewport.scrollLeft=(Number(additionalViewport.scrollLeft)||0)+direction*amount;
    if(typeof setTimeout==='function')setTimeout(updateOverflow,250);
    else updateOverflow();
  }
  function renderQuota(data){
    const providers=Array.isArray(data&&data.providers)?data.providers:[];
    const byName=new Map(providers.map(item=>[String(item&&item.provider||'').toLowerCase(),item]));
    const claude=byName.get('claude')||emptyProvider('claude');
    const codex=byName.get('codex')||emptyProvider('codex');
    const antigravity=byName.get('antigravity')||emptyProvider('antigravity');
    const codexGroups=splitCodex(codex);
    const codexMain=codexGroups.main.length?codex:{...codex,status:'unavailable',reason:'NO CODEX POOL SIGNAL'};
    root.innerHTML=[
      provider(claude,{id:'usage-card-claude',providerKey:'claude',name:'Claude',role:'main',pool:'main'}),
      provider(codexMain,{id:'usage-card-codex',providerKey:'codex',name:'Codex',role:'main',pool:'main',
        buckets:codexGroups.main,labelPrefix:poolName('codex',codexGroups.main),emptyState:'NO CODEX POOL SIGNAL'}),
      provider(antigravity,{id:'usage-card-antigravity',providerKey:'antigravity',name:'Antigravity',
        role:'main',pool:'main',cardScope:'antigravity',scope:'Claude / GPT limits are scoped via Antigravity',
        antigravityScope:true})
    ].join('');
    const cards=[];
    for(const [pool,items] of codexGroups.extra){
      const name=poolName(pool,items);
      cards.push(provider(codex,{providerKey:'codex',role:'additional',pool,name,buckets:items,labelPrefix:name,
        scope:'Separate Codex usage pool'}));
    }
    for(const item of providers){
      const name=String(item&&item.provider||'');
      if(!name||['claude','codex','antigravity'].includes(name.toLowerCase()))continue;
      cards.push(provider(item,{providerKey:name,role:'additional',pool:name,name}));
    }
    if(additional&&additionalTrack){
      additionalTrack.innerHTML=cards.join('');
      additional.hidden=cards.length===0;
      if(additionalViewport)additionalViewport.scrollLeft=0;
      if(typeof requestAnimationFrame==='function')requestAnimationFrame(updateOverflow);
      else updateOverflow();
    }
    if(typeof document.dispatchEvent==='function'&&typeof CustomEvent==='function'){
      document.dispatchEvent(new CustomEvent('agentstack:quota-rendered',{detail:{additionalPools:codexGroups.extra.size}}));
    }
  }
  const params=new URLSearchParams(location.search);
  const demo=params.get('demo')==='1'||Boolean(window.AGENTSTACK_DEMO_FORCE);
  const demoQuota={providers:[
    {provider:'claude',status:'ok',source:'demo-fixture',observed_at:null,buckets:[
      {id:'five_hour',label:'5h',scope:'account',remaining_percent:76,quality:'exact',resets_at:1893492000},
      {id:'seven_day',label:'7d',scope:'account',remaining_percent:59,quality:'exact',resets_at:1893888000}]},
    {provider:'codex',status:'ok',source:'demo-fixture',observed_at:null,buckets:[
      {id:'codex-300m',label:'codex · 5h',scope:'account',remaining_percent:43,quality:'exact',resets_at:1893495600},
      {id:'codex-10080m',label:'codex · 7d',scope:'account',remaining_percent:68,quality:'exact',resets_at:1893974400},
      {id:'codex_bengalfox-300m',label:'GPT-5.3-Codex-Spark · 5h',scope:'account',remaining_percent:88,quality:'exact',resets_at:1893495600},
      {id:'codex_bengalfox-10080m',label:'GPT-5.3-Codex-Spark · 7d',scope:'account',remaining_percent:91,quality:'exact',resets_at:1893974400},
      {id:'demo_extra-10080m',label:'Demo extra pool · 7d',scope:'account',remaining_percent:64,quality:'exact',resets_at:1894060800}]},
    {provider:'antigravity',status:'ok',source:'demo-fixture',observed_at:null,buckets:[
      {id:'gemini-5h',label:'Gemini Models · 5h',scope:'account',remaining_percent:82,quality:'exact',resets_at:1893499200},
      {id:'claude-gpt-weekly',label:'Claude and GPT models · 7d',scope:'account',remaining_percent:58,quality:'exact',resets_at:1894060800}]}
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
      renderQuota({providers:[]});
    }
  }
  refreshQuota();
  setInterval(refreshQuota,60000);
  if(additionalViewport&&typeof additionalViewport.addEventListener==='function'){
    additionalViewport.addEventListener('scroll',updateOverflow,{passive:true});
  }
  if(previousButton&&typeof previousButton.addEventListener==='function')previousButton.addEventListener('click',()=>scrollAdditional(-1));
  if(nextButton&&typeof nextButton.addEventListener==='function')nextButton.addEventListener('click',()=>scrollAdditional(1));
  if(typeof ResizeObserver==='function'&&additionalViewport)new ResizeObserver(updateOverflow).observe(additionalViewport);
  else if(typeof window.addEventListener==='function')window.addEventListener('resize',updateOverflow);
  if(typeof MutationObserver==='function'&&document.body){
    new MutationObserver(updateOverflow).observe(document.body,{attributes:true,attributeFilter:['data-view']});
  }
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
