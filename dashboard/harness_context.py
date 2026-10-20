"""Read native context status from an already captured pane, without I/O.

Only the active footer is observed. Missing/hidden/truncated status is unknown;
model names and cumulative billed tokens never supply a context estimate.
"""
import re

_ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)')
_COUNT = r'\d+(?:\.\d+)?[kKmM]?'
_PAIR = rf'(?P<used>{_COUNT})\s*/\s*(?P<window>{_COUNT})'
_DSH = re.compile(
    rf'\bctx\s+(?:(?P<pct>\d+(?:\.\d+)?)%\s+\({_PAIR}\)'
    rf'|(?P<used2>{_COUNT})/(?P<window2>{_COUNT})\s+\((?P<pct2>\d+(?:\.\d+)?)%\))')
_HERMES = re.compile(rf'☤\s+.+?\s+[·│]\s+(?P<estimated>~?){_PAIR}(?:\s+pinned)?\s+[·│]\s+(?:\[[█░]*\]\s+)?~?(?P<pct>\d+)%')


def parse_context(text, program):
    """Return the existing ctx_used (percent) / ctx_window (display size).

    dsh-tui 0.16.1: ``ctx 19% (24k/128k)`` or its noncompact inverse.
    Hermes pinned source: ``☤ model ~24k/128K [bar] ~19% ...``.
    Hermes estimates stay estimates; this function does not infer activity,
    running state, model identity or exact token counts from rounded labels.
    """
    unknown = {'ctx_used': None, 'ctx_window': None}
    if program not in ('dsh', 'hermes') or not isinstance(text, str):
        return unknown
    lines = _ANSI.sub('', text).splitlines()[-12:]
    # The newest footer wins; old captures above the current one are ignored.
    for line in reversed(lines):
        match = (_DSH.search(line) if program == 'dsh' else _HERMES.search(line))
        if not match:
            continue
        groups = match.groupdict()
        window = groups.get('window') or groups.get('window2')
        percent = float(groups.get('pct') or groups.get('pct2'))
        if float(window.rstrip('kKmM')) <= 0 or percent > 999:
            return unknown
        return {'ctx_used': int(percent + .5), 'ctx_window': window.upper()}
    return unknown
