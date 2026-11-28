"""NETWORK の線の数が comet と同時に増える: 新しい message で graph の cache を作り直す。"""

from __future__ import annotations

import json
import pathlib
import re
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

import dashboard.server as server

# server は dashboard/ を sys.path に足して `import graph_data` する。同じ module を直す。
if server.HERE not in sys.path:
    sys.path.insert(0, server.HERE)
import graph_data  # noqa: E402

INDEX = pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "index.html"


@pytest.fixture
def mail(tmp_path, monkeypatch):
    path = tmp_path / "mail.sqlite3"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE messages (id INTEGER PRIMARY KEY, body TEXT);
        CREATE TABLE message_recipients (message_id INTEGER NOT NULL,
          agent_id INTEGER NOT NULL, PRIMARY KEY(message_id, agent_id));
        INSERT INTO messages VALUES (1, 'a');
        INSERT INTO message_recipients VALUES (1, 1);
    """)
    con.commit()
    con.close()
    monkeypatch.setattr(graph_data, "DB_PATH", str(path))
    monkeypatch.setattr(server, "_GRAPH_CACHE", {"ts": 0, "data": None, "mail": None})
    builds = []

    def build():
        builds.append(time.time())
        return {"nodes": [], "edges": [], "spawn": [], "n": len(builds)}

    monkeypatch.setattr(graph_data, "build_graph", build)

    def write(sql, *args):
        c = sqlite3.connect(path)
        c.execute(sql, args)
        c.commit()
        c.close()

    return builds, write


def test_a_new_message_rebuilds_within_the_ttl(mail):
    builds, write = mail
    server._raw_graph()
    write("INSERT INTO messages VALUES (2, 'b')")
    write("INSERT INTO message_recipients VALUES (2, 1)")
    assert server._raw_graph()["n"] == 2


def test_without_new_mail_the_eight_second_cache_stays(mail):
    builds, _ = mail
    for _ in range(5):
        server._raw_graph()
    assert len(builds) == 1


def test_recipients_written_after_the_message_rebuild_again(mail):
    builds, write = mail
    write("INSERT INTO messages VALUES (2, 'b')")
    server._raw_graph()
    write("INSERT INTO message_recipients VALUES (2, 1)")
    server._raw_graph()
    assert len(builds) == 2


def test_a_message_arriving_during_the_build_rebuilds_on_the_next_request(mail, monkeypatch):
    builds, write = mail

    def build():
        builds.append(1)
        if len(builds) == 1:
            write("INSERT INTO messages VALUES (2, 'b')")
        return {"n": len(builds)}

    monkeypatch.setattr(graph_data, "build_graph", build)
    server._raw_graph()
    assert server._raw_graph()["n"] == 2
    assert server._raw_graph()["n"] == 2


def test_concurrent_requests_after_new_mail_build_once(mail, monkeypatch):
    builds, write = mail
    server._raw_graph()

    def slow():
        builds.append(1)
        time.sleep(0.2)
        return {"n": len(builds)}

    monkeypatch.setattr(graph_data, "build_graph", slow)
    write("INSERT INTO messages VALUES (2, 'b')")
    threads = [threading.Thread(target=server._raw_graph) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(builds) == 2


def test_an_unreadable_mail_db_keeps_the_time_cache(mail, monkeypatch, tmp_path):
    builds, _ = mail
    monkeypatch.setattr(graph_data, "DB_PATH", str(tmp_path / "missing.sqlite3"))
    server._raw_graph()
    server._raw_graph()
    assert len(builds) == 1
    assert graph_data.mail_mark() is None


def _function(html, name):
    m = re.search(r"(?:async )?function " + name + r"\(\)\{.*?\n\}", html, re.DOTALL)
    assert m, name
    return m.group(0)


def test_new_mail_refreshes_the_graph_once_and_coalesces():
    html = INDEX.read_text(encoding="utf-8")
    decl = re.search(r"let netMailRefresh=null,netMailRefreshAgain=false;", html)
    assert decl
    harness = r"""
let ticks=0,release=[];
async function netTick(){ticks++;await new Promise(r=>release.push(r));}
(async()=>{
  const a=netRefreshForMail();netRefreshForMail();netRefreshForMail();
  await new Promise(r=>setTimeout(r,0));
  const during=ticks;
  release.shift()();await new Promise(r=>setTimeout(r,0));
  release.shift()();await a;
  process.stdout.write(JSON.stringify({during,ticks}));
})();
"""
    out = subprocess.run(
        ["node", "-e", decl.group(0) + "\n" + _function(html, "netRefreshForMail") + harness],
        capture_output=True, text=True, timeout=10, check=True)
    assert json.loads(out.stdout) == {"during": 1, "ticks": 2}

    pulse = r"""
let mailLastTs=1000,mailQueue=[],refreshes=0;
const mailSeenKeys=new Set(),mailSeenOrder=[];
const MAIL_SEEN_MAX=400,MAIL_QUEUE_MAX=40;
const document={hidden:false};const view='net',netSuspended=false;
function mailMessageKey(m){return String(m.id);}
function annotateBroadcastMessages(x){return x;}
function mailDrain(){}
function netRefreshForMail(){refreshes++;}
const replies=[[{id:1}],[{id:1}],[]];
async function fetch(){const messages=replies.shift();return {json:async()=>({ok:true,now:1,messages})};}
(async()=>{await mailPulseTick();await mailPulseTick();await mailPulseTick();
process.stdout.write(String(refreshes));})();
"""
    out = subprocess.run(["node", "-e", _function(html, "mailPulseTick") + pulse],
                         capture_output=True, text=True, timeout=10, check=True)
    assert out.stdout == "1"


def test_an_older_graph_reply_arriving_late_does_not_lower_the_count():
    html = INDEX.read_text(encoding="utf-8")
    tick = re.search(r"async function netTick\(\)\{\n  try\{\n(.*?)\n    const \{w,h\}=netDims\(\);", html, re.DOTALL)
    assert tick
    # netTick の頭（番号・fetch・json・古い応答を捨てる）だけを使い、残りは数を描く代わりに記録する。
    harness = r"""
let shown=null;const replies=[];
const gWin=1;
async function fetch(){let done;const p=new Promise(r=>done=r);replies.push(done);const j=await p;
  return {status:200,json:async()=>j};}
async function netTick(){
  try{
""" + tick.group(1) + r"""
    shown=j.count;
  }catch(e){throw e;}
}
(async()=>{
  const older=netTick(),newer=netTick();
  await new Promise(r=>setTimeout(r,0));
  replies[1]({count:5});await newer;
  replies[0]({count:4});await older;
  const afterLate=shown;
  const third=netTick();await new Promise(r=>setTimeout(r,0));
  replies[2]({count:6});await third;
  process.stdout.write(afterLate+","+shown);
})();
"""
    out = subprocess.run(["node", "-e", harness],
                         capture_output=True, text=True, timeout=10, check=True)
    assert out.stdout == "5,6"  # 遅れた古い応答（4）で戻らず、次の新しい応答（6）は描く


def test_display_invalidation_discards_raw_graph_without_new_mail(mail):
    builds, _ = mail
    assert server._raw_graph()["n"] == 1
    server.invalidate_display_reuse()
    assert server._raw_graph()["n"] == 2
    assert server._raw_graph()["n"] == 2
    assert len(builds) == 2


def test_display_invalidation_does_not_republish_inflight_raw_graph(mail, monkeypatch):
    builds, _ = mail
    started, release = threading.Event(), threading.Event()
    answers = []

    def build():
        builds.append(1)
        n = len(builds)
        if n == 1:
            started.set()
            assert release.wait(5)
        return {"n": n}

    monkeypatch.setattr(graph_data, "build_graph", build)
    worker = threading.Thread(target=lambda: answers.append(server._raw_graph()))
    worker.start()
    try:
        assert started.wait(5)
        # Must not wait on the build lock; the builder is blocked on release.
        server.invalidate_display_reuse()
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert answers == [{"n": 1}]
    assert server._raw_graph()["n"] == 2
    assert server._raw_graph()["n"] == 2


def test_display_invalidation_reaches_both_http_graph_projections(mail, monkeypatch):
    from http.server import ThreadingHTTPServer
    from urllib.request import urlopen

    builds, _ = mail
    lineage = [{"source": "Parent", "target": "Child", "type": "spawn"}]

    def build():
        builds.append(1)
        return {"nodes": [{"name": "Parent"}, {"name": "Child"}],
                "edges": [], "spawn": list(lineage)}

    monkeypatch.setattr(graph_data, "build_graph", build)
    monkeypatch.setattr(server, "_GRAPH_REUSE", server._TimedReuse(5.0))
    http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    worker = threading.Thread(target=http.serve_forever, daemon=True)
    worker.start()
    try:
        def fetch(projection):
            with urlopen(f"http://127.0.0.1:{http.server_port}/api/graph?all=1&{projection}=1", timeout=10) as response:
                data = json.load(response)
            assert not data.get("degraded"), data
            return data["spawn"]

        assert fetch("spawn_only") == lineage
        assert fetch("topology_only") == lineage
        assert len(builds) == 1
        lineage.clear()
        server.invalidate_display_reuse()
        assert fetch("spawn_only") == []
        assert fetch("topology_only") == []
        assert len(builds) == 2
    finally:
        http.shutdown()
        http.server_close()
        worker.join(5)


@pytest.mark.parametrize("projection", ["spawn_only", "topology_only"])
def test_inflight_http_projection_is_not_reused_after_invalidation(mail, monkeypatch, projection):
    from http.server import ThreadingHTTPServer
    from urllib.request import urlopen

    builds, write = mail
    started, release = threading.Event(), threading.Event()
    edge = {"source": "Parent", "target": "Child", "type": "spawn"}
    answers = []

    def build():
        builds.append(1)
        first = len(builds) == 1
        if first:
            started.set()
            assert release.wait(10)
        return {"nodes": [{"name": "Parent"}, {"name": "Child"}],
                "edges": [], "spawn": [edge] if first else []}

    monkeypatch.setattr(graph_data, "build_graph", build)
    monkeypatch.setattr(server, "_GRAPH_REUSE", server._TimedReuse(5.0))
    http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    worker = threading.Thread(target=http.serve_forever, daemon=True)
    worker.start()

    def fetch():
        with urlopen(f"http://127.0.0.1:{http.server_port}/api/graph?all=1&{projection}=1", timeout=15) as response:
            data = json.load(response)
        assert not data.get("degraded"), data
        return data["spawn"]

    request = threading.Thread(target=lambda: answers.append(fetch()))
    request.start()
    try:
        assert started.wait(10)
        server.invalidate_display_reuse()
        release.set()
        request.join(10)
        assert not request.is_alive()
        assert answers == [[edge]]
        assert fetch() == []
        assert fetch() == []
        assert len(builds) == 2
        write("INSERT INTO messages VALUES (2, 'b')")
        # The raw cache must still respond to Mail changes after invalidation;
        # the outer HTTP reuse retains its existing independent TTL.
        assert server._raw_graph()["spawn"] == []
        assert len(builds) == 3
    finally:
        release.set()
        request.join(15)
        http.shutdown()
        http.server_close()
        worker.join(5)


def test_waiter_queued_before_invalidation_builds_current_graph(mail, monkeypatch):
    builds, _ = mail
    started, queued, release = threading.Event(), threading.Event(), threading.Event()
    results = {}
    lock = threading.Lock()

    class ObservedLock:
        def __enter__(self):
            if threading.current_thread().name == "graph-waiter":
                queued.set()
            lock.acquire()
            return self

        def __exit__(self, *args):
            lock.release()

    def build():
        builds.append(1)
        n = len(builds)
        if n == 1:
            started.set()
            assert release.wait(10)
        return {"n": n}

    monkeypatch.setattr(server, "_GRAPH_BUILD_LOCK", ObservedLock())
    monkeypatch.setattr(graph_data, "build_graph", build)
    first = threading.Thread(target=lambda: results.update(first=server._raw_graph()))
    waiter = threading.Thread(name="graph-waiter", target=lambda: results.update(waiter=server._raw_graph()))
    first.start()
    try:
        assert started.wait(10)
        waiter.start()
        assert queued.wait(10)
        server.invalidate_display_reuse()
    finally:
        release.set()
        first.join(10)
        if waiter.ident is not None:
            waiter.join(10)
    assert not first.is_alive() and not waiter.is_alive()
    assert results == {"first": {"n": 1}, "waiter": {"n": 2}}
    assert server._raw_graph() == {"n": 2}
    assert len(builds) == 2


def test_graph_invalidation_survives_parent_memo_cleanup_error(mail, monkeypatch):
    builds, _ = mail
    monkeypatch.setattr(server, "_GRAPH_REUSE", server._TimedReuse(5.0))
    assert server._raw_graph()["n"] == 1
    mark = server._GRAPH_REUSE.begin()
    key = ("test",)
    server._GRAPH_REUSE.put(key, {"n": 1}, mark)
    assert server._GRAPH_REUSE.get(key) == {"n": 1}

    def fail():
        raise RuntimeError("injected parent memo failure")

    monkeypatch.setattr(graph_data, "forget_parents", fail)
    with pytest.raises(RuntimeError, match="injected parent memo failure"):
        server.invalidate_display_reuse()
    assert server._GRAPH_REUSE.get(key) is None
    assert server._raw_graph()["n"] == 2
    assert len(builds) == 2
