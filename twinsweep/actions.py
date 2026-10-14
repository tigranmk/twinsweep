"""Apply a cleanup action to duplicates.

Every action re-verifies, immediately before touching anything, that both the
keeper and the duplicate still exist and are unchanged since the scan (same
size and mtime). This protects against files being edited or moved while a
long scan was running. Replacements (hardlink/symlink) go through a temporary
name plus an atomic os.replace(), so a crash never leaves the path missing.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from typing import Callable, List, Optional

from .scanner import FileEntry

ACTIONS = ("report", "delete", "trash", "move", "hardlink", "symlink")


@dataclass
class ActionResult:
    action: str
    path: str
    keeper: str
    ok: bool
    dry_run: bool
    detail: str = ""


class ActionError(Exception):
    pass


def _unchanged(e: FileEntry) -> bool:
    try:
        st = os.stat(e.path, follow_symlinks=False)
    except OSError:
        return False
    return st.st_size == e.size and st.st_mtime_ns == e.mtime_ns


def _unique_path(path: str) -> str:
    if not os.path.lexists(path):
        return path
    stem, ext = os.path.splitext(path)
    n = 1
    while os.path.lexists(f"{stem} ({n}){ext}"):
        n += 1
    return f"{stem} ({n}){ext}"


def _tmp_sibling(path: str) -> str:
    return _unique_path(path + ".twinsweep-tmp")


# --- individual actions ------------------------------------------------------

def _delete(dupe: FileEntry, keeper: FileEntry, **_) -> str:
    os.remove(dupe.path)
    return "deleted"


def _trash(dupe: FileEntry, keeper: FileEntry, **_) -> str:
    try:
        from send2trash import send2trash  # optional, BSD-3-Clause
    except ImportError:
        send2trash = None
    if send2trash is not None:
        send2trash(dupe.path)
        return "moved to trash"
    if sys.platform == "darwin":
        trash = os.path.expanduser("~/.Trash")
        dest = _unique_path(os.path.join(trash, os.path.basename(dupe.path)))
        shutil.move(dupe.path, dest)
        return f"moved to {dest}"
    raise ActionError("trash needs the optional 'send2trash' package on this OS "
                      "(pip install send2trash), or use --action move")


def _move(dupe: FileEntry, keeper: FileEntry, *, move_to: Optional[str] = None, **_) -> str:
    if not move_to:
        raise ActionError("--move-to is required for the move action")
    # Mirror the file's location relative to its scan root (and the root's
    # name) so moved files are easy to inspect and restore.
    rel = os.path.relpath(dupe.path, dupe.root) if dupe.root and dupe.path != dupe.root else os.path.basename(dupe.path)
    root_name = os.path.basename(dupe.root.rstrip(os.sep)) if dupe.root and dupe.path != dupe.root else ""
    dest = _unique_path(os.path.join(os.path.abspath(os.path.expanduser(move_to)), root_name, rel))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.move(dupe.path, dest)
    return f"moved to {dest}"


def _hardlink(dupe: FileEntry, keeper: FileEntry, **_) -> str:
    if dupe.dev != keeper.dev:
        raise ActionError("cannot hardlink across filesystems")
    tmp = _tmp_sibling(dupe.path)
    os.link(keeper.path, tmp)
    try:
        os.replace(tmp, dupe.path)
    except OSError:
        os.remove(tmp)
        raise
    return f"hardlinked to {keeper.path}"


def _symlink(dupe: FileEntry, keeper: FileEntry, **_) -> str:
    tmp = _tmp_sibling(dupe.path)
    os.symlink(os.path.abspath(keeper.path), tmp)
    try:
        os.replace(tmp, dupe.path)
    except OSError:
        os.remove(tmp)
        raise
    return f"symlinked to {keeper.path}"


_IMPL: dict = {
    "delete": _delete,
    "trash": _trash,
    "move": _move,
    "hardlink": _hardlink,
    "symlink": _symlink,
}


def apply_action(
    action: str,
    dupe: FileEntry,
    keeper: FileEntry,
    *,
    dry_run: bool = False,
    move_to: Optional[str] = None,
) -> ActionResult:
    """Apply `action` to one duplicate, never raising for per-file problems."""
    if action not in _IMPL:
        raise ValueError(f"unknown action {action!r}")
    result = ActionResult(action=action, path=dupe.path, keeper=keeper.path, ok=False, dry_run=dry_run)

    if not _unchanged(keeper):
        result.detail = "skipped: keeper changed or disappeared since scan"
        return result
    if not _unchanged(dupe):
        result.detail = "skipped: file changed or disappeared since scan"
        return result
    if dry_run:
        result.ok = True
        result.detail = f"would {action}"
        return result
    try:
        result.detail = _IMPL[action](dupe, keeper, move_to=move_to)
        result.ok = True
    except (OSError, ActionError) as exc:
        result.detail = f"failed: {getattr(exc, 'strerror', None) or exc}"
    return result


def apply_all(
    action: str,
    plan: List[tuple],
    *,
    dry_run: bool = False,
    move_to: Optional[str] = None,
    on_result: Optional[Callable[[ActionResult], None]] = None,
) -> List[ActionResult]:
    """`plan` is a list of (keeper, [dupes]) tuples, as produced by selector."""
    results = []
    for keeper, dupes in plan:
        for d in dupes:
            r = apply_action(action, d, keeper, dry_run=dry_run, move_to=move_to)
            results.append(r)
            if on_result:
                on_result(r)
    return results
