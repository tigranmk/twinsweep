"""Walk directory trees and collect candidate files.

Hard links are collapsed: several paths pointing to the same inode are one
physical file and take no extra space, so only the first path seen is kept.
This also means overlapping roots (e.g. `~/a` and `~/a/b`) never cause a file
to be reported as a duplicate of itself.

Directories owned by package managers and build tools (node_modules, Python
virtualenvs, .git, ...) are skipped by default. Their contents are often
genuinely duplicated - npm nests identical copies of a package under each
dependent - but removing any of them breaks the project, so reporting them
only buries the duplicates people can actually act on.
"""

from __future__ import annotations

import fnmatch
import os
import stat
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator, List, Optional, Sequence, Set, Tuple


# Directory names that are reliably tool-managed. Deliberately conservative:
# ambiguous names such as "build", "dist", "target" or "vendor" are often real
# user folders, so they are NOT listed. Hidden entries only matter with --hidden.
MANAGED_DIR_NAMES = frozenset({
    # JavaScript
    "node_modules", "bower_components", "jspm_packages", ".pnpm-store",
    ".yarn", ".next", ".nuxt", ".parcel-cache", ".turbo", ".svelte-kit",
    # Python
    "__pycache__", "site-packages", ".venv", ".tox", ".nox",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
    # Apple / mobile / JVM / infra
    "Pods", "DerivedData", ".gradle", ".terraform",
    # Version control internals
    ".git", ".hg", ".svn",
})

# A directory containing this file is a Python virtualenv, whatever its name.
_VENV_MARKER = "pyvenv.cfg"


def managed_reason(path: str, name: str) -> Optional[str]:
    """Return a short label if `path` is a tool-managed directory, else None."""
    if name in MANAGED_DIR_NAMES:
        return name
    if os.path.isfile(os.path.join(path, _VENV_MARKER)):
        return "virtualenv"
    return None


@dataclass(frozen=True)
class FileEntry:
    path: str
    size: int
    mtime_ns: int
    dev: int
    ino: int
    root: str = ""
    # Position of `root` on the command line; used by the "first" keep rule.
    root_index: int = 0


@dataclass
class ScanStats:
    files_seen: int = 0
    hardlinks_skipped: int = 0
    # Label -> count of tool-managed directories that were not descended into.
    managed_skipped: Counter = field(default_factory=Counter)
    errors: List[Tuple[str, str]] = field(default_factory=list)


def _is_hidden(name: str) -> bool:
    return name.startswith(".")


def _matches_any(path: str, name: str, patterns: Sequence[str]) -> bool:
    # Patterns are tried against both the basename and the full path, so
    # `--exclude '*.tmp'` and `--exclude '*/cache/*'` both behave intuitively.
    for pat in patterns:
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(path, pat):
            return True
    return False


def scan(
    roots: Iterable[str],
    *,
    min_size: int = 1,
    max_size: Optional[int] = None,
    exclude: Sequence[str] = (),
    include_hidden: bool = False,
    follow_symlinks: bool = False,
    include_managed: bool = False,
    stats: Optional[ScanStats] = None,
    on_file: Optional[Callable[[int], None]] = None,
) -> List[FileEntry]:
    """Return regular files under `roots` that pass the filters.

    `roots` may contain files as well as directories. Unreadable entries are
    recorded in `stats.errors` instead of aborting the scan. Tool-managed
    directories are skipped unless `include_managed` is set; a root itself is
    always scanned, since naming it explicitly is a clear request.
    """
    stats = stats if stats is not None else ScanStats()
    seen_inodes: Set[Tuple[int, int]] = set()
    results: List[FileEntry] = []

    for index, raw_root in enumerate(roots):
        root = os.path.abspath(os.path.expanduser(raw_root))
        for path, st in _walk(root, exclude, include_hidden, follow_symlinks, include_managed, stats):
            stats.files_seen += 1
            if on_file:
                on_file(stats.files_seen)
            if st.st_size < min_size or (max_size is not None and st.st_size > max_size):
                continue
            key = (st.st_dev, st.st_ino)
            if key in seen_inodes:
                stats.hardlinks_skipped += 1
                continue
            seen_inodes.add(key)
            results.append(
                FileEntry(
                    path=path,
                    size=st.st_size,
                    mtime_ns=st.st_mtime_ns,
                    dev=st.st_dev,
                    ino=st.st_ino,
                    root=root,
                    root_index=index,
                )
            )
    return results


def _walk(
    root: str,
    exclude: Sequence[str],
    include_hidden: bool,
    follow_symlinks: bool,
    include_managed: bool,
    stats: ScanStats,
) -> Iterator[Tuple[str, os.stat_result]]:
    """Yield (path, stat) for every regular file under `root`.

    Uses an explicit stack with os.scandir rather than os.walk so we can reuse
    the cached DirEntry stat results and record per-entry errors.
    """
    try:
        st = os.stat(root, follow_symlinks=follow_symlinks)
    except OSError as exc:
        stats.errors.append((root, exc.strerror or str(exc)))
        return
    if stat.S_ISREG(st.st_mode):
        yield root, st
        return
    if not stat.S_ISDIR(st.st_mode):
        return

    stack = [root]
    visited_dirs: Set[Tuple[int, int]] = {(st.st_dev, st.st_ino)}
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError as exc:
            stats.errors.append((current, exc.strerror or str(exc)))
            continue

        subdirs = []
        for entry in entries:
            if not include_hidden and _is_hidden(entry.name):
                continue
            if exclude and _matches_any(entry.path, entry.name, exclude):
                continue
            try:
                if entry.is_symlink() and not follow_symlinks:
                    continue
                est = entry.stat(follow_symlinks=follow_symlinks)
            except OSError as exc:
                stats.errors.append((entry.path, exc.strerror or str(exc)))
                continue
            if stat.S_ISDIR(est.st_mode):
                if not include_managed:
                    reason = managed_reason(entry.path, entry.name)
                    if reason:
                        stats.managed_skipped[reason] += 1
                        continue
                # Guard against symlink loops when following links.
                dkey = (est.st_dev, est.st_ino)
                if dkey not in visited_dirs:
                    visited_dirs.add(dkey)
                    subdirs.append(entry.path)
            elif stat.S_ISREG(est.st_mode):
                yield entry.path, est
        # Reverse so the stack pops directories in alphabetical order,
        # giving a deterministic, human-friendly traversal.
        stack.extend(reversed(subdirs))
