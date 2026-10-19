"""Preparation only: actual dashboard callers, with no terminal or ttyd child."""
import ast
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import pytest

ROOT = Path(__file__).resolve().parents[1]


def fixture_consumer(admitted):
    return SimpleNamespace(admitted_argv=admitted,home=ROOT,
        transport=SimpleNamespace(identity=('socket',1),path=Path('/fixture/socket'),server_pid=77))


def caller(name, namespace):
    tree = ast.parse((ROOT / 'dashboard/server.py').read_text())
    wanted = {name, '_legacy_terminal_argv'}
    nodes = [node for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name in wanted]
    exec(compile(ast.Module(body=nodes, type_ignores=[]),
                 str(ROOT / 'dashboard/server.py'), 'exec'), namespace)
    return namespace[name]


@pytest.mark.parametrize('command', [
    ['tmux', 'attach-session', '-t', 'Beta'],
    ['tmux', 'new-session', '-A', '-s', 'Beta', '/bin/bash'],
])
def test_terminal_caller_defers_to_common_effect_time_admission(command):
    admissions, commands = [], []
    consumer = fixture_consumer(lambda args: admissions.append(args))
    namespace = dict(Path=Path, sys=sys, HOOKS_DIR=str(ROOT / 'hooks'),
                     _terminal_adapter=lambda: 'ghostty',
                     _legacy_tmux_consumer=lambda: consumer,
                     subprocess=SimpleNamespace(run=lambda args, **kw:
                         commands.append(args) or
                         SimpleNamespace(returncode=0, stdout='', stderr='')))
    result = caller('_open_terminal_tmux', namespace)(command, 'Beta')
    assert result['ok'] is True
    assert admissions == [command[1:]]
    assert commands[0][-len(command[1:])-2:] == [
        sys.executable, str(ROOT / 'bin/lib/runtime_tmux_consumer.py'),
        *command[1:]]


def test_ttyd_rejects_target_replaced_after_has_session_before_popen():
    calls, admissions = [], []
    def refuse(args):
        admissions.append(args)
        raise RuntimeError('LEGACY_TARGET_IS_GLOBAL')
    namespace = dict(Path=Path, sys=sys, HOOKS_DIR=str(ROOT / 'hooks'),
        _valid=lambda _: True, _has_session=lambda _: True,
        _legacy_tmux_consumer=lambda: fixture_consumer(refuse),
        os=SimpleNamespace(path=SimpleNamespace(exists=lambda _: True)),
        TTYD_BIN='/fixture/ttyd', _TTYD_LOCK=threading.Lock(), _TTYD={},
        _ttyd_alive=lambda _: False, _free_port=lambda: 12345,
        time=SimpleNamespace(time=lambda: 0, sleep=lambda _: None),
        subprocess=SimpleNamespace(DEVNULL=-3,
            Popen=lambda args, **kw: calls.append(args) or
                SimpleNamespace(poll=lambda: None)))
    result = caller('ttyd_ensure', namespace)('Beta')
    assert result['ok'] is False
    assert admissions == [['attach', '-t', '=Beta']]
    assert calls == []


def test_ttyd_child_uses_deferred_common_admission_on_each_browser_attach():
    calls, admissions = [], []
    namespace = dict(Path=Path, sys=sys, HOOKS_DIR=str(ROOT / 'hooks'),
        _valid=lambda _: True, _has_session=lambda _: True,
        _legacy_tmux_consumer=lambda: fixture_consumer(lambda args: admissions.append(args)),
        os=SimpleNamespace(path=SimpleNamespace(exists=lambda _: True)),
        TTYD_BIN='/fixture/ttyd', _TTYD_LOCK=threading.Lock(), _TTYD={},
        _ttyd_alive=lambda _: False, _free_port=lambda: 12345,
        time=SimpleNamespace(time=lambda: 0, sleep=lambda _: None),
        subprocess=SimpleNamespace(DEVNULL=-3,
            Popen=lambda args, **kw: calls.append(args) or
                SimpleNamespace(poll=lambda: None)))
    assert caller('ttyd_ensure', namespace)('Beta')['ok'] is True
    assert admissions == [['attach', '-t', '=Beta']]
    assert calls[0][-5:] == [sys.executable,
        str(ROOT / 'bin/lib/runtime_tmux_consumer.py'), 'attach', '-t', '=Beta']


@pytest.mark.parametrize('tmux_set',[False,True])
@pytest.mark.parametrize('observation,retire',[('unknown',False),('other',False),('dead',True)])
def test_actual_retire_only_cleanup_keeps_unknown_identity(tmp_path,tmux_set,observation,retire):
    import os, subprocess
    source=(ROOT/'hooks/cleanup-child-agent.sh').read_text()
    start=source.index('identity_live_elsewhere() {')
    end=source.index('\n# Reservations of a retire-only exit',start)
    chunk=source[start:end]
    runtime=tmp_path/'runtime';runtime.mkdir()
    helper=tmp_path/'lease.py';helper.write_text('raise SystemExit(1)\n')
    if observation=='other':(runtime/'agent_name__9').write_text('Beta')
    reply='return 2' if observation=='unknown' else "printf '%s\\n' '%9'" if observation=='other' else 'return 0'
    script='\n'.join(['tmux() { '+reply+'; }',chunk,"printf 'retire-effect\\n'"])
    env={k:v for k,v in os.environ.items() if not k.startswith(('AGENTSTACK_','MCP_AGENT_')) and k not in ('TMUX','TMUX_PANE')}
    env.update(RUNTIME_DIR=str(runtime),HOOKS_DIR=str(tmp_path),AGENT_NAME='Beta',AGENTSTACK_CLEANUP_RETIRE_ONLY='1',AGENTSTACK_CHILD_RESUME_HELPER=str(helper),AGENTSTACK_PYTHON=sys.executable)
    if tmux_set:env['TMUX']='fixture,1,0'
    result=subprocess.run(['/bin/bash','-c',script],env=env,text=True,capture_output=True,timeout=5)
    assert result.returncode==0,result.stderr
    assert ('retire-effect' in result.stdout) is retire


def test_actual_detached_resume_uses_shared_admission_before_new_session():
    calls=[]
    def refusal(args,**kwargs):
        calls.append(args)
        raise RuntimeError('LEGACY_TARGET_IS_GLOBAL')
    namespace=dict(_resume_opens_terminal=lambda _:False,_tmux_run=refusal,
        subprocess=SimpleNamespace(run=lambda *args,**kw:SimpleNamespace(returncode=0,stdout='',stderr='')))
    result=caller('_launch_resume_tmux',namespace)(['tmux','new-session','-A','-s','Beta'],'Beta',False)
    assert result['ok'] is False
    assert calls==[['new-session','-d','-s','Beta']]


@pytest.mark.parametrize('failure',['timeout','missing-tool','permission'])
def test_actual_signal_measurement_failure_does_not_borrow_ambient_server(tmp_path,monkeypatch,failure):
    from test_mail_signal_health import test_session_measurement_failures_are_not_absence
    monkeypatch.setenv('TMUX_TMPDIR',str(tmp_path))
    monkeypatch.delenv('TMUX',raising=False)
    monkeypatch.delenv('TMUX_PANE',raising=False)
    test_session_measurement_failures_are_not_absence(monkeypatch,failure)


@pytest.mark.parametrize('changed,denied',[(False,False),(True,False),(False,True)])
def test_actual_ttyd_reuse_readmits_current_endpoint_before_url(changed,denied):
    consumer=fixture_consumer(lambda _:None)
    helper=str(ROOT/'bin/lib/runtime_tmux_consumer.py')
    existing=['env','AGENTSTACK_HOME='+str(ROOT),'TMUX=/fixture/socket,77,0',sys.executable,helper,'attach','-t','=Beta']
    record={'proc':SimpleNamespace(poll=lambda:None),'port':11111,'last':0,'attach_args':existing}
    killed=[];created=[]
    if changed:consumer.transport.server_pid=88
    if denied:
        def refuse(_):raise RuntimeError('LEGACY_TARGET_IS_GLOBAL')
        consumer.admitted_argv=refuse
    ns=dict(Path=Path,sys=sys,HOOKS_DIR=str(ROOT/'hooks'),_valid=lambda _:True,_has_session=lambda _:True,
        _legacy_tmux_consumer=lambda:consumer,os=SimpleNamespace(path=SimpleNamespace(exists=lambda _:True)),
        TTYD_BIN='/fixture/ttyd',_TTYD_LOCK=threading.Lock(),_TTYD={'Beta':record},
        _ttyd_alive=lambda _:True,_ttyd_kill=lambda rec:killed.append(rec),_free_port=lambda:22222,
        time=SimpleNamespace(time=lambda:0,sleep=lambda _:None),
        subprocess=SimpleNamespace(DEVNULL=-3,Popen=lambda args,**kw:created.append(args) or SimpleNamespace(poll=lambda:None)))
    result=caller('ttyd_ensure',ns)('Beta')
    if denied:
        assert result['ok'] is False and created==[]
    elif changed:
        assert result['port']==22222 and len(killed)==len(created)==1
    else:
        assert result['reused'] is True and result['port']==11111 and killed==created==[]
