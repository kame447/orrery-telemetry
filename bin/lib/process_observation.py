"""Shared lightweight process evidence; no global client/config initialization."""
import subprocess
import time
import signal


def request_provider_exit(*, send, interrupt, delegated_antigravity=False,
                          shell_exit=False, pause=time.sleep):
    """The existing dashboard exit policy, with proven effects injected.

    Callers own admission and liveness. Submission never proves termination.
    A delegated stream runner must survive to publish its provider's outcome.
    """
    if delegated_antigravity:
        interrupt(signal.SIGINT)
        return 'interrupt-sent'
    if shell_exit:
        send('exit', 'Enter')
        return 'shell-exit-sent'
    send('-l', '/exit')
    pause(0.3)
    send('C-m')
    return 'exit-sent'


class ClientError(RuntimeError):
    pass



def integer(value, minimum=0):
    return type(value) is int and value >= minimum



def remaining(deadline, limit=5):
    if deadline is None:
        return limit
    budget = deadline - time.monotonic()
    if budget <= 0:
        raise ClientError('OBSERVATION_INCOMPLETE')
    return min(limit, budget)



def process_start(pid, *, deadline=None):
    if not integer(pid,1):
        return None
    try:
        result = subprocess.run(['ps','-p',str(pid),'-o','lstart='], capture_output=True, text=True, timeout=remaining(deadline, 3))
        if result.returncode == 1 and not result.stderr:
            return 'absent'
        return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def belongs_to(provider, runner, uid, *, deadline=None):
    """An ephemeral same-UID parent chain, never a persisted second PID table."""
    try:
        result=subprocess.run(['ps','-axo','pid=,ppid=,uid='],capture_output=True,text=True,timeout=remaining(deadline))
        if result.returncode:return False
        rows={}
        for line in result.stdout.splitlines():
            parts=line.split()
            if len(parts)>=3 and all(x.isdigit() for x in parts[:3]):
                pid,parent,owner=map(int,parts[:3]);rows[pid]=(parent,owner)
        seen=set();current=provider
        while current!=runner:
            if current in seen or current not in rows or rows[current][1]!=uid:return False
            seen.add(current);current=rows[current][0]
        return runner in rows and rows[runner][1]==uid
    except (OSError,ValueError,subprocess.SubprocessError):return False
