from __future__ import annotations

from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
adapter = root / "hooks/spawn_gemini_preregistered.sh"
text = adapter.read_text(encoding="utf-8")
register_block = '''REGISTER_LIB="${AGENTSTACK_REGISTER_LIB:-$AGENTSTACK_HOME_DIR/bin/lib/agentstack-register.sh}"
[[ -f "$REGISTER_LIB" ]] || { echo "$PROG: shared registration helper is missing" >&2; exit 1; }
# shellcheck disable=SC1090
. "$REGISTER_LIB"
'''
if text.count(register_block) != 1:
    raise SystemExit(f"register block count={text.count(register_block)}")
text = text.replace(register_block, "", 1)
anchor = 'RESOURCES="$(validate_resources)" || exit 2\n'
if text.count(anchor) != 1:
    raise SystemExit(f"resource validation anchor count={text.count(anchor)}")
text = text.replace(anchor, anchor + register_block, 1)
adapter.write_text(text, encoding="utf-8")

path = root / "tests/test_gemini_new_agent.py"
test = path.read_text(encoding="utf-8")
anchor = '''    home = tmp_path / "home"
    _executable(home / "bin" / "agentstack-gemini-child-mail", _FAKE_MAIL_HELPER)
'''
replacement = '''    home = tmp_path / "home"
    register_lib = home / "bin" / "lib" / "agentstack-register.sh"
    register_lib.parent.mkdir(parents=True, exist_ok=True)
    register_lib.write_bytes((ROOT / "bin" / "lib" / "agentstack-register.sh").read_bytes())
    with register_lib.open("a", encoding="utf-8") as handle:
        handle.write("\\nags_verify_registration_owner_with_mail() { return 0; }\\n")
    scientists = home / "bin" / "lib" / "agentstack-scientists.sh"
    scientists.write_bytes((ROOT / "bin" / "lib" / "agentstack-scientists.sh").read_bytes())
    hooks_home = home / "hooks"
    hooks_home.mkdir(parents=True, exist_ok=True)
    for hook_name in (
        "project-context.sh",
        "cleanup-child-agent.sh",
        "resolve-agent-name.sh",
        "session-identity-policy.sh",
    ):
        target = hooks_home / hook_name
        target.write_bytes((ROOT / "hooks" / hook_name).read_bytes())
        target.chmod(0o755)
    _executable(home / "bin" / "agentstack-gemini-child-mail", _FAKE_MAIL_HELPER)
'''
if test.count(anchor) != 1:
    raise SystemExit(f"adapter home anchor count={test.count(anchor)}")
test = test.replace(anchor, replacement, 1)

anchor = '''    token = runtime / "one-shot.token"
    token.write_text("child-token", encoding="utf-8")
'''
replacement = '''    token = runtime / "one-shot.token"
    token.write_text("child-token", encoding="utf-8")
    token.chmod(0o600)
    owner = {
        "schema": 1,
        "agent_name": "Parent",
        "name_key": "parent",
        "project_key": "/project",
        "repository_key": str(repo.resolve()),
        "non_git_root": None,
        "created_by": "top-level",
        "token_sha256": __import__("hashlib").sha256(b"parent-token").hexdigest(),
        "updated_at": "2026-09-15T00:00:00Z",
    }
    owner_file = runtime / "agent_owner_Parent.json"
    owner_file.write_text(json.dumps(owner) + "\\n", encoding="utf-8")
    owner_file.chmod(0o600)
    parent_token = runtime / "agent_token_Parent"
    parent_token.write_text("parent-token", encoding="utf-8")
    parent_token.chmod(0o600)
'''
if test.count(anchor) != 1:
    raise SystemExit(f"adapter token anchor count={test.count(anchor)}")
test = test.replace(anchor, replacement, 1)

anchor = '''        "AGENTSTACK_HOME": str(home),
        "AGENTSTACK_LABEL_PREFIX": "org.agentstack.test.gemini-adapter",
        "AGENTSTACK_HOOKS_DIR": str(tmp_path / "hooks"),
'''
replacement = '''        "AGENTSTACK_HOME": str(home),
        "AGENTSTACK_REGISTER_LIB": str(register_lib),
        "AGENTSTACK_LABEL_PREFIX": "org.agentstack.test.gemini-adapter",
        "AGENTSTACK_HOOKS_DIR": str(hooks_home),
'''
if test.count(anchor) != 1:
    raise SystemExit(f"adapter env anchor count={test.count(anchor)}")
test = test.replace(anchor, replacement, 1)

# The adapter's old private mail helper no longer owns release/retire. Phase 5b
# tests cover those remote mutations on the shared cleanup helper directly; the
# adapter integration asserts only the adapter-owned reserve call and local
# cleanup state. Limit this expectation migration to the real adapter section.
marker = "# Real adapter boundary and termination"
pos = test.index(marker)
prefix, tail = test[:pos], test[pos:]
tail = tail.replace('assert _mail_calls(run) == ["reserve", "retire"]',
                    'assert _mail_calls(run) == ["reserve"]')
tail = tail.replace('assert _mail_calls(run) == ["retire"]',
                    'assert _mail_calls(run) == []')
test = prefix + tail
path.write_text(test, encoding="utf-8")
