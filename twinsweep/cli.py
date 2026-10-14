"""Command-line interface."""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from typing import List, Optional, Sequence

from . import __version__
from .actions import ACTIONS, apply_all
from .cache import HashCache, default_cache_path
from .finder import find_duplicates
from .html_report import write_html
from .report import human_size, summary, write_csv, write_json, write_text
from .scanner import ScanStats, scan
from .selector import KEEP_RULES, choose_keeper

_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?)i?b?\s*$", re.IGNORECASE)
_UNITS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}


def parse_size(text: str) -> int:
    """Parse '500', '10k', '1.5M', '2GiB' into bytes (binary units)."""
    m = _SIZE_RE.match(text)
    if not m:
        raise argparse.ArgumentTypeError(f"invalid size: {text!r} (examples: 100, 10k, 1.5M, 2G)")
    return int(float(m.group(1)) * _UNITS[m.group(2).lower()])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="twinsweep",
        description="Find duplicate files by content and clean them up safely. "
                    "Without --action, only a report is produced and nothing is changed.",
        epilog="Examples:\n"
               "  twinsweep ~/Pictures\n"
               "  twinsweep ~/Downloads ~/Documents --min-size 1M --format json\n"
               "  twinsweep ~/Pictures --format html -o report.html\n"
               "  twinsweep ~/Photos ~/Backup --prefer ~/Photos --action trash --dry-run\n"
               "  twinsweep ~/Music --action hardlink --yes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("paths", nargs="+", help="files or directories to scan")

    f = p.add_argument_group("filtering")
    f.add_argument("--min-size", type=parse_size, default=1, metavar="SIZE",
                   help="ignore files smaller than SIZE (default: 1, i.e. skip empty files)")
    f.add_argument("--max-size", type=parse_size, default=None, metavar="SIZE",
                   help="ignore files larger than SIZE")
    f.add_argument("-x", "--exclude", action="append", default=[], metavar="GLOB",
                   help="skip files/dirs matching GLOB (name or full path); repeatable")
    f.add_argument("--hidden", action="store_true", help="include hidden files and directories")
    f.add_argument("--follow-symlinks", action="store_true", help="follow symbolic links")
    f.add_argument("--include-managed", action="store_true",
                   help="also scan folders managed by tools (node_modules, virtualenvs, "
                        "__pycache__, .git, Pods, ...), which are skipped by default")

    k = p.add_argument_group("choosing which copy to keep")
    k.add_argument("--keep", choices=list(KEEP_RULES), default="oldest",
                   help="rule for picking the copy to keep (default: oldest)")
    k.add_argument("--prefer", action="append", default=[], metavar="DIR",
                   help="always keep copies inside DIR when possible; repeatable, in priority order")

    a = p.add_argument_group("actions")
    a.add_argument("--action", choices=ACTIONS, default="report",
                   help="what to do with duplicates (default: report)")
    a.add_argument("--move-to", metavar="DIR", help="destination for --action move")
    a.add_argument("-n", "--dry-run", action="store_true", help="show what would happen, change nothing")
    a.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")

    o = p.add_argument_group("output")
    o.add_argument("--format", choices=("text", "json", "csv", "html"), default="text",
                   help="report format (default: text); html is best with -o report.html")
    o.add_argument("-o", "--output", metavar="FILE", help="write the report to FILE instead of stdout")
    o.add_argument("--limit", type=int, default=0, metavar="N",
                   help="text format: show only the N biggest groups (default: all)")
    o.add_argument("-q", "--quiet", action="store_true", help="no progress output")

    perf = p.add_argument_group("performance and accuracy")
    perf.add_argument("--paranoid", action="store_true",
                      help="confirm matches byte-by-byte after hashing")
    perf.add_argument("-j", "--workers", type=int, default=min(8, (os.cpu_count() or 2)),
                      help="parallel hashing threads (default: %(default)s)")
    perf.add_argument("--no-cache", action="store_true", help="do not read or write the hash cache")
    perf.add_argument("--cache-path", default=None, metavar="FILE",
                      help=f"hash cache location (default: {default_cache_path()})")

    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


class Progress:
    """Single-line progress indicator on stderr, only when it is a terminal."""

    def __init__(self, enabled: bool):
        self.enabled = enabled and sys.stderr.isatty()
        self._last = 0.0

    def __call__(self, label: str, done: int, total: Optional[int] = None) -> None:
        if not self.enabled:
            return
        now = time.monotonic()
        if total is not None and done < total and now - self._last < 0.1:
            return
        self._last = now
        text = f"{label}: {done}/{total}" if total is not None else f"{label}: {done}"
        sys.stderr.write(f"\r\033[K{text}")
        sys.stderr.flush()

    def clear(self) -> None:
        if self.enabled:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()


def _managed_summary(counts) -> str:
    """e.g. '34 tool-managed folders (node_modules x30, __pycache__ x4)'."""
    total = sum(counts.values())
    top = ", ".join(f"{name} x{n}" for name, n in counts.most_common(4))
    more = ", ..." if len(counts) > 4 else ""
    return f"{total} tool-managed folder{'s' if total != 1 else ''} ({top}{more})"


def _confirm(prompt: str) -> bool:
    if not sys.stdin.isatty():
        return False
    try:
        return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    err = sys.stderr

    if args.action == "move" and not args.move_to:
        err.write("twinsweep: --action move requires --move-to DIR\n")
        return 2
    for path in args.paths:
        if not os.path.lexists(os.path.expanduser(path)):
            err.write(f"twinsweep: no such file or directory: {path}\n")
            return 2

    progress = Progress(not args.quiet)
    stats = ScanStats()

    # 1. Scan.
    entries = scan(
        args.paths,
        min_size=args.min_size,
        max_size=args.max_size,
        exclude=args.exclude,
        include_hidden=args.hidden,
        follow_symlinks=args.follow_symlinks,
        include_managed=args.include_managed,
        stats=stats,
        on_file=lambda n: progress("scanning", n),
    )

    # 2. Hash and group.
    cache = None if args.no_cache else HashCache(args.cache_path or default_cache_path())
    labels = {"full": "hashing (full)"}
    try:
        groups = find_duplicates(
            entries,
            cache=cache,
            workers=args.workers,
            paranoid=args.paranoid,
            stats=stats,
            progress=lambda kind, d, t: progress(labels.get(kind, "hashing (quick)"), d, t),
        )
    finally:
        if cache:
            cache.close()
    progress.clear()

    # 3. Decide keepers.
    plan = []
    for g in groups:
        keeper, dupes = choose_keeper(g, args.keep, args.prefer)
        plan.append((g, keeper, dupes))

    notes = []
    if stats.managed_skipped:
        notes.append(f"Skipped {_managed_summary(stats.managed_skipped)}; "
                     "use --include-managed to scan them.")

    if not args.quiet:
        for n in notes:
            err.write(n + "\n")
        err.write(f"Scanned {stats.files_seen} files")
        if stats.hardlinks_skipped:
            err.write(f" ({stats.hardlinks_skipped} existing hard links ignored)")
        err.write(".\n")
        if stats.errors:
            err.write(f"Warning: {len(stats.errors)} paths could not be read, e.g. "
                      f"{stats.errors[0][0]}: {stats.errors[0][1]}\n")
    err.flush()

    # 4. Report.
    out = open(args.output, "w", encoding="utf-8", newline="") if args.output else sys.stdout
    try:
        if args.format == "json":
            write_json(plan, out)
        elif args.format == "csv":
            write_csv(plan, out)
        elif args.format == "html":
            write_html(plan, out, roots=[os.path.abspath(os.path.expanduser(p)) for p in args.paths],
                       notes=notes)
        else:
            write_text(plan, out, limit=args.limit)
    finally:
        if args.output:
            out.close()
        else:
            out.flush()

    if args.action == "report" or not plan:
        return 0

    # 5. Act.
    s = summary(plan)
    what = f"{args.action} {s['duplicate_files']} files ({human_size(s['reclaimable_bytes'])})"
    if not args.dry_run and not args.yes:
        note = " This is PERMANENT." if args.action == "delete" else ""
        if not _confirm(f"\nAbout to {what}.{note} Continue?"):
            err.write("Aborted; nothing was changed. (Use --yes to skip this prompt, "
                      "or --dry-run to preview.)\n")
            return 1

    # Action log goes to stdout for text output, stderr otherwise so it
    # cannot corrupt machine-readable reports.
    log = sys.stdout if args.format == "text" and not args.output else err

    def on_result(r) -> None:
        if not args.quiet or not r.ok:
            log.write(f"{'ok  ' if r.ok else 'FAIL'}  {r.path}: {r.detail}\n")

    results = apply_all(
        args.action, [(k, d) for _, k, d in plan],
        dry_run=args.dry_run, move_to=args.move_to, on_result=on_result,
    )
    log.flush()
    failed = [r for r in results if not r.ok]
    prefix = "Dry run: would " if args.dry_run else "Done: "
    err.write(f"{prefix}{args.action} {len(results) - len(failed)} files"
              f"{f', {len(failed)} skipped/failed' if failed else ''}.\n")
    return 1 if failed else 0
