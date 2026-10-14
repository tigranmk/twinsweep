"""Output formatting: human-readable text, JSON and CSV."""

from __future__ import annotations

import csv
import json
import os
from typing import IO, List, Tuple

from .finder import DuplicateGroup
from .scanner import FileEntry

Plan = List[Tuple[DuplicateGroup, FileEntry, List[FileEntry]]]


def human_size(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TiB"  # unreachable, keeps type checkers happy


def display_path(path: str) -> str:
    """Abbreviate the home directory to ~ for readable text reports."""
    home = os.path.expanduser("~")
    if home and home != "~" and (path == home or path.startswith(home + os.sep)):
        return "~" + path[len(home):]
    return path


def summary(plan: Plan) -> dict:
    return {
        "groups": len(plan),
        "duplicate_files": sum(len(d) for _, _, d in plan),
        "reclaimable_bytes": sum(g.wasted_bytes for g, _, _ in plan),
    }


def write_text(plan: Plan, out: IO[str], *, limit: int = 0) -> None:
    shown = plan[:limit] if limit else plan
    for i, (group, keeper, dupes) in enumerate(shown, 1):
        out.write(
            f"\n[{i}] {len(group.files)} copies x {human_size(group.size)}"
            f"  (reclaim {human_size(group.wasted_bytes)})\n"
        )
        out.write(f"   keep  {display_path(keeper.path)}\n")
        for d in dupes:
            out.write(f"   dupe  {display_path(d.path)}\n")
    if limit and len(plan) > limit:
        out.write(f"\n... and {len(plan) - limit} more groups (use --limit 0 to show all)\n")
    s = summary(plan)
    out.write(
        f"\nFound {s['groups']} duplicate groups, {s['duplicate_files']} redundant files, "
        f"{human_size(s['reclaimable_bytes'])} reclaimable.\n"
    )


def write_json(plan: Plan, out: IO[str]) -> None:
    doc = {
        "summary": summary(plan),
        "groups": [
            {
                "size": g.size,
                "digest": g.digest,
                "keep": keeper.path,
                "duplicates": [d.path for d in dupes],
            }
            for g, keeper, dupes in plan
        ],
    }
    json.dump(doc, out, indent=2)
    out.write("\n")


def write_csv(plan: Plan, out: IO[str]) -> None:
    w = csv.writer(out)
    w.writerow(["group", "role", "size", "digest", "path"])
    for i, (g, keeper, dupes) in enumerate(plan, 1):
        w.writerow([i, "keep", g.size, g.digest, keeper.path])
        for d in dupes:
            w.writerow([i, "duplicate", g.size, g.digest, d.path])
