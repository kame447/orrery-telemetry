"""Native selections reuse the standard copy/approval decision, never broaden it."""
import json
import pytest
from hooks import child_tools as tools


def settings(tmp_path):
    path=tmp_path/'user.json'
    path.write_text(json.dumps({'mcpServers':{'reader':{'command':'/fixture/reader','args':[]},
        'windows':{'command':'/fixture/uvx','args':['windows-mcp@0.8.6']}}}))
    return str(path)


def test_default_inherits_and_plain_mcp_copies_same_definition(tmp_path):
    config=settings(tmp_path)
    plan=tools.native_plan(tools.normalize(),cwd=str(tmp_path),claude_json=config)
    assert plan=={'servers':{},'inherit':True,'approved':{},'enabled_tools':{}}
    spec=tools.normalize('mail-only',{'mcp':['reader']})
    plan=tools.native_plan(spec,cwd=str(tmp_path),claude_json=config)
    assert plan['inherit'] is False and plan['servers']['reader']['command']=='/fixture/reader'
    assert plan['approved']=={}


@pytest.mark.parametrize('spec',[
    {'screen':{'access':'read','server':'windows'}},
    {'mcp':['windows'],'approve_all':['windows']},
    {'screen':{'access':'operate','server':None}},
    {'browser':{'device':'specific-device'}},
])
def test_unrepresentable_native_approval_is_rejected(tmp_path,spec):
    with pytest.raises(tools.ToolsError):
        tools.native_plan(tools.normalize(tools=spec),cwd=str(tmp_path),claude_json=settings(tmp_path))


def test_missing_selected_server_is_not_silently_dropped(tmp_path):
    with pytest.raises(tools.ToolsError):
        tools.native_plan(tools.normalize(tools={'mcp':['absent']}),cwd=str(tmp_path),claude_json=settings(tmp_path))
