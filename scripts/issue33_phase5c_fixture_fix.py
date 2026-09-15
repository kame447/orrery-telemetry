from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
path = root / "tests/test_spawn_child_readiness.py"
text = path.read_text(encoding="utf-8")
anchor = '    pathlib.Path(env["HOME"]).mkdir()\n\n    started = time.monotonic()\n'
replacement = '''    pathlib.Path(env["HOME"]).mkdir()\n\n    publish = subprocess.run(\n        [\n            "/bin/bash", "-c",\n            'set -euo pipefail; . "$1"; '\n            'ctx=$(agentstack_resolve_invocation_context "$2" /shared/project); '\n            'ags_store_registration_token "$3" child-owner-token "$ctx" preregister-child',\n            "fixture", str(ROOT / "bin/lib/agentstack-register.sh"),\n            str(workdir), "Fresh-Curie",\n        ],\n        env=env, capture_output=True, text=True, timeout=20,\n    )\n    assert publish.returncode == 0, publish.stderr\n\n    started = time.monotonic()\n'''
if text.count(anchor) != 1:
    raise SystemExit(f"readiness fixture anchor count={text.count(anchor)}")
path.write_text(text.replace(anchor, replacement, 1), encoding="utf-8")
