from __future__ import annotations

import pathlib


ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "scripts" / "install-gemini-provider.sh"
SETUP = ROOT / "bin" / "agentstack-gemini-setup"


def test_optional_provider_installs_the_mcp_runner_required_by_setup():
    installer = INSTALLER.read_text(encoding="utf-8")
    setup = SETUP.read_text(encoding="utf-8")

    assert 'MCP_COMMAND="${AGENTSTACK_GEMINI_MCP_RUNNER:-$AGENTSTACK_HOME/bin/agentstack-gemini-mcp}"' in setup
    assert '"bin/agentstack-gemini-mcp"' in installer
