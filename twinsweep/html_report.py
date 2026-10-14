"""Self-contained HTML report.

The page has no external assets (CSS and JS are inline), so it works offline,
can be emailed or archived, and opens straight from disk. All file paths are
HTML-escaped; file names are attacker-controllable on shared drives.
"""

from __future__ import annotations

import datetime as _dt
import html
import os
import pathlib
from typing import IO, Optional, Sequence

from . import __version__
from .report import Plan, display_path, human_size, summary
from .scanner import FileEntry

_CSS = """
:root {
  --bg: #f7f7f8; --card: #ffffff; --text: #1d1d1f; --muted: #6b6b73;
  --border: #e3e3e8; --keep: #1a7f37; --keep-bg: #e6f4ea;
  --dupe: #b35900; --dupe-bg: #fff1e0; --accent: #3b5bdb;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #141416; --card: #1e1e21; --text: #ececf1; --muted: #9a9aa3;
    --border: #2e2e33; --keep: #5fd27a; --keep-bg: #183222;
    --dupe: #ffb35c; --dupe-bg: #3a2a14; --accent: #8ea6ff;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
main { max-width: 1100px; margin: 0 auto; padding: 32px 16px 64px; }
h1 { font-size: 26px; margin: 0 0 4px; }
.meta { color: var(--muted); font-size: 13px; margin-bottom: 24px; word-break: break-all; }
.note { background: var(--card); border: 1px solid var(--border); border-left: 3px solid var(--accent);
  border-radius: 8px; padding: 10px 14px; margin: -8px 0 20px; font-size: 13.5px; color: var(--muted); }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px; margin-bottom: 24px; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }
.card .n { font-size: 24px; font-weight: 650; font-variant-numeric: tabular-nums; }
.card .l { color: var(--muted); font-size: 13px; }
.toolbar { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }
.toolbar input { flex: 1 1 260px; padding: 9px 12px; border-radius: 8px; font: inherit;
  border: 1px solid var(--border); background: var(--card); color: var(--text); }
.toolbar button { padding: 9px 14px; border-radius: 8px; border: 1px solid var(--border);
  background: var(--card); color: var(--text); font: inherit; cursor: pointer; }
details { background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  margin-bottom: 10px; overflow: hidden; }
summary { padding: 12px 16px; cursor: pointer; display: flex; gap: 12px; align-items: baseline;
  flex-wrap: wrap; list-style: none; }
summary::-webkit-details-marker { display: none; }
summary::before { content: "\\25B8"; color: var(--muted); transition: transform .15s; }
details[open] summary::before { transform: rotate(90deg); }
.gid { color: var(--muted); font-variant-numeric: tabular-nums; }
.gname { font-weight: 600; word-break: break-all; flex: 1 1 200px; }
.gstat { color: var(--muted); font-size: 13px; white-space: nowrap; }
.gstat b { color: var(--text); }
.bar { height: 3px; background: var(--accent); opacity: .55; }
table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
td { padding: 7px 16px; border-top: 1px solid var(--border); vertical-align: top; }
td.path { word-break: break-all; }
td.path a { color: inherit; text-decoration: none; }
td.path a:hover { text-decoration: underline; }
td.date { color: var(--muted); white-space: nowrap; width: 1%; font-variant-numeric: tabular-nums; }
.tag { display: inline-block; font-size: 11px; font-weight: 650; letter-spacing: .03em;
  padding: 2px 7px; border-radius: 99px; text-transform: uppercase; }
.tag.keep { color: var(--keep); background: var(--keep-bg); }
.tag.dupe { color: var(--dupe); background: var(--dupe-bg); }
td.role { width: 1%; }
.empty { text-align: center; color: var(--muted); padding: 48px 0; }
footer { color: var(--muted); font-size: 12px; margin-top: 32px; text-align: center; }
@media (max-width: 600px) { td.date { display: none; } }
"""

# Filters groups client-side; no data leaves the page.
_JS = """
const q = document.getElementById('q');
const groups = [...document.querySelectorAll('details[data-text]')];
const shown = document.getElementById('shown');
q.addEventListener('input', () => {
  const needle = q.value.trim().toLowerCase();
  let n = 0;
  for (const g of groups) {
    const hit = !needle || g.dataset.text.includes(needle);
    g.hidden = !hit; if (hit) n++;
  }
  shown.textContent = n;
});
document.getElementById('expand').onclick = () => groups.forEach(g => g.open = true);
document.getElementById('collapse').onclick = () => groups.forEach(g => g.open = false);
"""


def _fmt_time(mtime_ns: int) -> str:
    return _dt.datetime.fromtimestamp(mtime_ns / 1e9).strftime("%Y-%m-%d %H:%M")


def _file_row(e: FileEntry, role: str, base: Optional[str] = None) -> str:
    label = "keep" if role == "keep" else "dupe"
    try:
        href = pathlib.Path(e.path).as_uri()
    except ValueError:
        href = ""
    # With a single scanned folder, paths relative to it are far easier to read;
    # the full path stays available in the link and tooltip.
    rel = os.path.relpath(e.path, base) if base and e.path.startswith(base + os.sep) else None
    shown = html.escape(rel or display_path(e.path))
    title = html.escape(e.path)
    link = f'<a href="{html.escape(href)}" title="{title}">{shown}</a>' if href else shown
    return (f'<tr><td class="role"><span class="tag {label}">{label}</span></td>'
            f'<td class="path">{link}</td><td class="date">{_fmt_time(e.mtime_ns)}</td></tr>')


def write_html(plan: Plan, out: IO[str], *, roots: Sequence[str] = (),
               notes: Sequence[str] = ()) -> None:
    s = summary(plan)
    biggest = max((g.wasted_bytes for g, _, _ in plan), default=0) or 1
    generated = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    scanned = ", ".join(display_path(r) for r in roots) or "-"
    base = roots[0] if len(roots) == 1 and os.path.isdir(roots[0]) else None

    parts = [
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">",
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Duplicate Files Report</title>",
        f"<style>{_CSS}</style></head><body><main>",
        "<h1>Duplicate files</h1>",
        f'<div class="meta">Scanned {html.escape(scanned)} &middot; generated {generated}</div>',
        *(f'<div class="note">{html.escape(n)}</div>' for n in notes),
        '<div class="cards">',
        f'<div class="card"><div class="n">{human_size(s["reclaimable_bytes"])}</div>'
        '<div class="l">reclaimable space</div></div>',
        f'<div class="card"><div class="n">{s["duplicate_files"]:,}</div>'
        '<div class="l">redundant files</div></div>',
        f'<div class="card"><div class="n">{s["groups"]:,}</div>'
        '<div class="l">duplicate groups</div></div>',
        "</div>",
    ]

    if not plan:
        parts.append('<div class="empty">No duplicates found.</div>')
    else:
        parts.append(
            '<div class="toolbar"><input id="q" type="search" placeholder="Filter by path or name...">'
            '<button id="expand">Expand all</button><button id="collapse">Collapse all</button></div>'
            f'<div class="meta">Showing <span id="shown">{len(plan)}</span> of {len(plan)} groups, '
            "largest savings first.</div>"
        )
        for i, (g, keeper, dupes) in enumerate(plan, 1):
            files = [keeper, *dupes]
            search = html.escape(" ".join(f.path for f in files).lower())
            name = html.escape(pathlib.PurePath(keeper.path).name)
            width = max(1.0, 100.0 * g.wasted_bytes / biggest)
            # Open the first few groups so the page is useful at a glance.
            opened = " open" if i <= 10 else ""
            parts.append(
                f'<details data-text="{search}"{opened}>'
                f'<summary><span class="gid">#{i}</span><span class="gname">{name}</span>'
                f'<span class="gstat"><b>{len(files)}</b> copies &times; {human_size(g.size)}'
                f' &middot; reclaim <b>{human_size(g.wasted_bytes)}</b></span></summary>'
                f'<div class="bar" style="width:{width:.1f}%"></div><table>'
                + _file_row(keeper, "keep", base)
                + "".join(_file_row(d, "dupe", base) for d in dupes)
                + "</table></details>"
            )
        parts.append(f"<script>{_JS}</script>")

    parts.append(f"<footer>Generated by twinsweep {__version__}</footer></main></body></html>\n")
    out.write("\n".join(parts))
