"""Usage presentation must not discard API data or invent observation times."""
from __future__ import annotations

import ast
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from dashboard.quotas.base import QuotaBucket, QuotaSnapshot
from dashboard.quotas.claude import ClaudeQuotaProvider
from dashboard.quotas.service import QuotaService


def _write_observation(path: Path, observed_at: int, used: int = 25) -> None:
    path.write_text(json.dumps({"observed_at": observed_at, "rate_limits": {
        "five_hour": {"used_percentage": used, "resets_at": observed_at + 18000},
    }}), encoding="utf-8")


def test_expiry_preserves_observation_and_separates_last_check(tmp_path):
    path = tmp_path / "claude.json"
    _write_observation(path, 1000)
    now = [1599.0]
    service = QuotaService([ClaudeQuotaProvider(path, clock=lambda: now[0])], clock=lambda: now[0])
    fresh = service.read_all()["providers"][0]
    assert fresh["last_observed_at"] == 1000
    assert fresh["checked_at"] == 1599

    now[0] = 1601.0  # cache still valid, observation no longer valid
    expired = service.read_all()["providers"][0]
    assert expired["status"] == "unavailable"
    assert expired["reason"] == "observation_expired"
    assert expired["buckets"] == []
    assert expired["observed_at"] == expired["last_observed_at"] == 1000
    assert expired["checked_at"] == 1599

    now[0] = 1630.0  # underlying provider is checked again
    again = service.read_all()["providers"][0]
    assert again["observed_at"] == again["last_observed_at"] == 1000
    assert again["checked_at"] == 1630
    assert again["buckets"] == []

    _write_observation(path, 1631, used=50)
    now[0] = 1661.0
    recovered = service.read_all()["providers"][0]
    assert recovered["status"] == "ok"
    assert recovered["last_observed_at"] == 1631
    assert recovered["checked_at"] == 1661
    assert recovered["buckets"][0]["remaining_percent"] == 50


def test_cold_start_retains_time_from_expired_observation(tmp_path):
    path = tmp_path / "claude.json"
    _write_observation(path, 1000)
    service = QuotaService([ClaudeQuotaProvider(path, clock=lambda: 2000)], clock=lambda: 2000)
    expired = service.read_all()["providers"][0]
    assert expired["reason"] == "observation_stale"
    assert expired["observed_at"] == expired["last_observed_at"] == 1000
    assert expired["checked_at"] == 2000
    assert expired["buckets"] == []


def test_newer_expired_file_is_not_replaced_by_older_in_memory_observation(tmp_path):
    path = tmp_path / "claude.json"
    _write_observation(path, 1000)
    now = [1000.0]
    service = QuotaService([ClaudeQuotaProvider(path, clock=lambda: now[0])], clock=lambda: now[0])
    service.read_all()
    _write_observation(path, 1100)
    now[0] = 1800.0
    expired = service.read_all()["providers"][0]
    assert expired["last_observed_at"] == expired["observed_at"] == 1100
    assert expired["buckets"] == []


@pytest.mark.parametrize("payload", [None, {"observed_at": 3000, "rate_limits": {}},
                                      {"observed_at": 1000, "rate_limits": {}}])
def test_missing_future_or_empty_observation_has_no_success_time(tmp_path, payload):
    path = tmp_path / "claude.json"
    if payload is not None:
        path.write_text(json.dumps(payload), encoding="utf-8")
    service = QuotaService([ClaudeQuotaProvider(path, clock=lambda: 2000)], clock=lambda: 2000)
    unavailable = service.read_all()["providers"][0]
    assert unavailable["status"] == "unavailable"
    assert unavailable["last_observed_at"] is None
    assert unavailable["checked_at"] == 2000
    assert unavailable["buckets"] == []


@dataclass
class _Provider:
    provider_name: str
    source_name: str
    result: QuotaSnapshot | Exception
    ttl_seconds: int = 1

    def read(self) -> QuotaSnapshot:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_failure_keeps_last_success_time_but_not_expired_balance():
    bucket = QuotaBucket.from_remaining(id="5h", label="5h", scope="account",
        remaining_percent=75, window_seconds=18000, resets_at=19000)
    provider = _Provider("codex", "fixture", QuotaSnapshot(
        provider="codex", source="fixture", observed_at=1000, status="ok", buckets=(bucket,)))
    now = [1000.0]
    service = QuotaService([provider], clock=lambda: now[0])
    service.read_all()
    provider.result = RuntimeError("fixture failure")
    now[0] = 1002.0
    stale = service.read_all()["providers"][0]
    assert stale["status"] == "stale"
    assert stale["last_observed_at"] == 1000
    assert stale["checked_at"] == 1002
    now[0] = 1601.0
    expired = service.read_all()["providers"][0]
    assert expired["last_observed_at"] == expired["observed_at"] == 1000
    assert expired["checked_at"] == 1601
    assert expired["buckets"] == []


def test_hidden_display_pools_remain_available_in_api():
    providers = []
    for name, bucket_id, label in (
        ("codex", "codex_bengalfox-300m", "GPT-5.3-Codex-Spark · 5h"),
        ("antigravity", "3p-weekly", "Claude and GPT models · 7d"),
    ):
        bucket = QuotaBucket.from_remaining(id=bucket_id, label=label, scope="account",
            remaining_percent=100, window_seconds=18000, resets_at=19000)
        providers.append(_Provider(name, "fixture", QuotaSnapshot(
            provider=name, source="fixture", observed_at=1000, status="ok", buckets=(bucket,))))
    payload = QuotaService(providers, clock=lambda: 1000).read_all()
    assert [p["buckets"][0]["id"] for p in payload["providers"]] == [
        "codex_bengalfox-300m", "3p-weekly"]


def test_usage_script_filters_unused_pools_and_explains_claude_waiting():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the UI runtime regression")
    source = Path(__file__).resolve().parents[1] / "dashboard" / "quota_server.py"
    assignments = {target.id: ast.literal_eval(stmt.value)
        for stmt in ast.parse(source.read_text(encoding="utf-8")).body
        if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant)
        for target in stmt.targets if isinstance(target, ast.Name)}
    script = assignments["_USAGE_SCRIPT"].split(">", 1)[1].rsplit("</script>", 1)[0]
    harness = r"""
const vm=require('node:vm'),assert=require('node:assert/strict');
const script=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
const now=1800003600;
class Clock extends Date{static now(){return now*1000}}
async function run(payload,search=''){
  let requests=0;
  const roots=Object.fromEntries(['usage-providers','usage-additional','usage-additional-shell',
    'usage-additional-track','usage-additional-viewport','usage-additional-prev','usage-additional-next']
    .map(id=>[id,{innerHTML:'',hidden:false,dataset:{},scrollLeft:0,scrollWidth:300,clientWidth:300,
      addEventListener(){},setAttribute(){}}]));
  const before=JSON.stringify(payload);
  const context={Date:Clock,document:{getElementById:id=>roots[id]??null},location:{search},
    window:{},URLSearchParams,setInterval(){},
    fetch:async()=>{requests++;return {ok:true,json:async()=>payload}}};
  vm.runInNewContext(script,context);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(JSON.stringify(payload),before,'presentation must not mutate API data');
  return {requests,main:roots['usage-providers'].innerHTML,
    extra:roots['usage-additional-track'].innerHTML,hidden:roots['usage-additional'].hidden};
}
(async()=>{
  const buckets=[
    {id:'codex-10080m',label:'7d',remaining_percent:56,resets_at:1800500000},
    {id:'codex_bengalfox-300m',label:'GPT-5.3-Codex-Spark · 5h',remaining_percent:100},
    {id:'spark-10080m',label:'Other label',remaining_percent:100},
    {id:'opaque-300m',label:'GPT-5.3-Codex-Spark · 5h',remaining_percent:100}];
  const payload={providers:[
    {provider:'claude',status:'unavailable',reason:'observation_expired',observed_at:now,
      last_observed_at:now-3600,checked_at:now,buckets:[]},
    {provider:'codex',status:'ok',buckets},
    {provider:'antigravity',status:'ok',buckets:[
      {id:'gemini-weekly',label:'Gemini Models · 7d',remaining_percent:10},
      {id:'3p-weekly',label:'Claude and GPT models · 7d',remaining_percent:100},
      {id:'opaque-claude',label:'Other',scope:'claude',remaining_percent:100},
      {id:'opaque-combined',label:'Other',scope:'claude-gpt',remaining_percent:100}]}]};
  const result=await run(payload);
  assert.equal(result.requests,1);
  assert.equal(result.extra,''); assert.equal(result.hidden,true);
  assert.match(result.main,/codex-10080m/); assert.match(result.main,/gemini-weekly/);
  assert.doesNotMatch(result.main,/3p-weekly|opaque-claude|opaque-combined|via Antigravity/);
  assert.match(result.main,/WAITING FOR UPDATE/);
  assert.match(result.main,/Last observed 1 hour ago/);
  assert.match(result.main,/last observed 08:00/);
  assert.doesNotMatch(result.main,/last update|observation_expired<\/span>/);
  assert.match(result.main,/Waiting for Claude Code activity/);
  const unknown=await run({providers:[{provider:'codex',status:'ok',buckets:[
    {id:'unknown-300m',label:'Unrecognised pool',remaining_percent:20}]}]});
  assert.equal(unknown.hidden,false); assert.match(unknown.extra,/unknown-300m/);
  assert.doesNotMatch(unknown.main,/data-bucket-id="unknown-300m"/);
  const absent=await run({providers:[{provider:'claude',status:'unavailable',reason:'not_observed',
    observed_at:now,last_observed_at:null,checked_at:now,buckets:[]}]});
  assert.doesNotMatch(absent.main,/last observed|class="usage-observed"/);
  const demo=await run({providers:[]},'?demo=1');
  assert.equal(demo.requests,0); assert.doesNotMatch(demo.main+demo.extra,/Spark|3p-weekly|via Antigravity/i);
  assert.match(demo.extra,/Demo extra pool/);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run([node, "-e", harness], input=json.dumps(script), text=True,
        capture_output=True, timeout=10, env={**os.environ, "TZ": "UTC"})
    assert result.returncode == 0, result.stderr
