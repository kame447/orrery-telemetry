"""Guard the macOS CI interpreter boundary used by fresh import/process tests."""
from pathlib import Path


def test_macos_ci_uses_venv_for_import_and_real_process_e2e():
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml").read_text()
    macos_job = workflow.split("\n  tests:\n", 1)[1]
    assert 'runs-on: macos-latest' in macos_job
    assert 'python3 -m venv "$RUNNER_TEMP/orrery-ci-venv"' in macos_job
    assert '"$RUNNER_TEMP/orrery-ci-venv/bin/python3" -m pip install ' in macos_job
    assert 'echo "$RUNNER_TEMP/orrery-ci-venv/bin" >> "$GITHUB_PATH"' in macos_job
    assert 'sys.prefix != sys.base_prefix' in macos_job
    assert 'is_relative_to(site)' in macos_job
    assert 'PYTHONPATH=.:packages/agentstack_mail/src python3 -m pytest' in macos_job
