"""Check the narrow Reader header without shrinking its tap targets."""
import base64
import os
from pathlib import Path

import pytest

from mobile_reader_browser import phone  # noqa: F401


@pytest.mark.parametrize("phone", [(320, 720), (360, 780)], indirect=True)
def test_reader_header_keeps_name_space_and_full_touch_targets(phone):
    client, js, wait, state = phone
    wait("document.querySelector('.reader-bar .reader-icon') !== null")
    result = js("""(() => {
      const bar=document.querySelector('.reader-bar'), who=bar.querySelector('.reader-person');
      const title=who.querySelector('h2'), bounds=bar.getBoundingClientRect();
      const buttons=[...bar.querySelectorAll(':scope > .reader-icon')].filter(el=>el.getClientRects().length);
      const rects=buttons.map(el=>el.getBoundingClientRect());
      return {
        name:title.textContent, width:title.getBoundingClientRect().width,
        overhead:bounds.width-who.getBoundingClientRect().width-rects.reduce((n,r)=>n+r.width,0),
        innerOverhead:who.getBoundingClientRect().width-title.getBoundingClientRect().width,
        controls:rects.length,
        targets:rects.every(r=>r.width>=44&&r.height>=44&&r.left>=bounds.left&&r.right<=bounds.right+1),
        separated:rects.every((r,i)=>i===0||rects[i-1].right<=r.left+1),
      };
    })()""")
    assert result["name"] == "SampleAgent"
    assert result["controls"] >= 3 and result["targets"] and result["separated"], result
    assert result["width"] >= 36, result
    assert result["overhead"] <= 24 and result["innerOverhead"] <= 40, result
    assert not any(op in {"prepare", "commit", "send"} for op, _ in state["calls"]), state["calls"]
    folder = os.environ.get("MOBILE_HEADER_CAPTURE_DIR")
    if folder:
        target = Path(folder)
        target.mkdir(parents=True, exist_ok=True)
        width = int(js("innerWidth"))
        for theme in ("dark", "light"):
            if theme == "light":
                js((Path(__file__).resolve().parents[1] / "dashboard/theme_core.js").read_text())
                js("document.documentElement.dataset.colorTheme='light';Object.entries(AgentStackColorThemeCore.deriveWarmPaperLightTheme().cssVariables).forEach(([k,v])=>document.documentElement.style.setProperty(k,v))")
            shot = client.call("Page.captureScreenshot", format="png", captureBeyondViewport=False)
            (target / f"{width}-{theme}.png").write_bytes(base64.b64decode(shot["data"]))
