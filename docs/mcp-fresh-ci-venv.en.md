# Isolated Python for fresh MCP CI

The fresh MCP import-attestation and real subprocess SDK tests on macOS CI need to find the same installed dependencies in both the parent test interpreter and the credential/HOME-isolated child.

A global Python pip install can select a per-user site-packages directory. The test interpreter may import packages there, while the attestation child running with `-I -S` cannot see them at the explicitly supplied `sysconfig.get_path("purelib")`. A real fresh MCP subprocess with a private HOME may likewise fail to resolve dependencies and never start listening.

The fork's macOS job now builds `$RUNNER_TEMP/orrery-ci-venv` for each selected Python version (3.11, 3.12, 3.14), installs test dependencies in that interpreter and places its bin directory first on PATH for later steps. It checks that the actual dependency imports originate inside the selected venv. A failed check stops CI before running the real-process tests. No production interpreter, Mail, MCP or Tunnel is changed.

This is CI test isolation, not runtime dependency pinning, signing, or proof of service acceptance. Full CI completion, independent review and production cutover require separate evidence. Rollback is limited to reverting this workflow change.
