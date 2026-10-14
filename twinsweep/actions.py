"""Apply a cleanup action to duplicates.

Every action re-verifies, immediately before touching anything, that both the
keeper and the duplicate still exist and are unchanged since the scan (same
size and mtime). This protects against files being edited or moved while a
long scan was running. Replacements (hardlink/symlink) go through a temporary
name plus an atomic os.replace(), so a crash never leaves the path missing.

On macOS, trashing goes through Finder (via osascript) so files land in the
right Trash for their volume and Finder's "Put Back" works. Files are sent in
batches because each osascript call costs ~0.1-0.3 s.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

from .scanner import FileEntry

ACTIONS = ("report", "delete", "trash", "move", "hardlink", "symlink")

# Paths per Finder call: keeps the argument list far below ARG_MAX while
# amortising the osascript start-up cost.
FINDER_BATCH = 200
# Set to 1 to skip Finder (e.g. over SSH, or when automation is not allowed).
NO_FINDER_ENV = "TWINSWEEP_NO_FINDER"


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


# Paths arrive as argv items, never interpolated into the script text, so no
# file name can alter the AppleScript. Coercion to alias happens outside the
# Finder block, where POSIX file coercion is reliable.
_FINDER_SCRIPT = """
on run argv
    set targets to {}
    repeat with p in argv
        set end of targets to (POSIX file (contents of p)) as alias
    end repeat
    tell application "Finder" to delete targets
end run
"""


def finder_available() -> bool:
    return (sys.platform == "darwin" and not os.environ.get(NO_FINDER_ENV)
            and shutil.which("osascript") is not None)


class FinderError(Exception):
    """Finder could not trash the batch. `fatal` means retrying is pointless
    (no permission, osascript missing or hung), as opposed to a bad file."""

    def __init__(self, message: str, fatal: bool):
        super().__init__(message)
        self.fatal = fatal


def finder_trash(paths: Sequence[str]) -> None:
    """Ask Finder to move `paths` to the Trash; raises FinderError on failure.

    The first call may trigger macOS's "allow Terminal to control Finder"
    prompt, hence the generous timeout. Callers must check which paths are
    actually gone: Finder can fail part-way through a batch.
    """
    try:
        proc = subprocess.run(
            ["osascript", "-e", _FINDER_SCRIPT, "--", *paths],
            capture_output=True, text=True, timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FinderError(str(exc), fatal=True) from exc
    if proc.returncode != 0:
        msg = proc.stderr.strip() or f"osascript exited with {proc.returncode}"
        if "-1743" in msg:  # errAEEventNotPermitted
            raise FinderError("macOS blocked control of Finder; allow it in System Settings > "
                              "Privacy & Security > Automation", fatal=True)
        raise FinderError(msg, fatal=False)


def _trash(dupe: FileEntry, keeper: FileEntry, **_) -> str:
    """Per-file trash, used off macOS or when Finder is unavailable."""
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


def _precheck(action: str, dupe: FileEntry, keeper: FileEntry, dry_run: bool) -> ActionResult:
    """Return a result; a non-empty `detail` means it is final (skipped or dry run)."""
    if action not in _IMPL:
        raise ValueError(f"unknown action {action!r}")
    result = ActionResult(action=action, path=dupe.path, keeper=keeper.path, ok=False, dry_run=dry_run)
    if not _unchanged(keeper):
        result.detail = "skipped: keeper changed or disappeared since scan"
    elif not _unchanged(dupe):
        result.detail = "skipped: file changed or disappeared since scan"
    elif dry_run:
        result.ok = True
        result.detail = f"would {action}"
    return result


def apply_action(
    action: str,
    dupe: FileEntry,
    keeper: FileEntry,
    *,
    dry_run: bool = False,
    move_to: Optional[str] = None,
) -> ActionResult:
    """Apply `action` to one duplicate, never raising for per-file problems."""
    result = _precheck(action, dupe, keeper, dry_run)
    if result.detail:
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
    pairs = [(k, d) for k, dupes in plan for d in dupes]
    if action == "trash" and not dry_run and finder_available():
        return _trash_via_finder(pairs, on_result)
    results = []
    for keeper, d in pairs:
        r = apply_action(action, d, keeper, dry_run=dry_run, move_to=move_to)
        results.append(r)
        if on_result:
            on_result(r)
    return results


def _trash_via_finder(pairs, on_result) -> List[ActionResult]:
    results: List[ActionResult] = []

    def emit(r: ActionResult) -> None:
        results.append(r)
        if on_result:
            on_result(r)

    # Verify everything up front, then trash in batches.
    pending: List[ActionResult] = []
    for keeper, d in pairs:
        r = _precheck("trash", d, keeper, dry_run=False)
        if r.detail:
            emit(r)
        else:
            pending.append(r)

    by_path: Dict[str, tuple] = {d.path: (k, d) for k, d in pairs}
    finder_disabled: Optional[str] = None  # set after a fatal Finder error

    def try_finder(paths: List[str]) -> None:
        nonlocal finder_disabled
        if finder_disabled or not paths:
            return
        try:
            finder_trash(paths)
        except FinderError as exc:
            if exc.fatal:
                finder_disabled = str(exc)
            elif len(paths) > 1:
                # Finder stops at the first bad file; retry the rest one by
                # one so a single locked file doesn't cost everyone Put Back.
                for p in paths:
                    if os.path.lexists(p):
                        try_finder([p])

    for i in range(0, len(pending), FINDER_BATCH):
        batch = pending[i:i + FINDER_BATCH]
        try_finder([r.path for r in batch])
        for r in batch:
            if not os.path.lexists(r.path):
                r.ok, r.detail = True, "moved to Trash (Finder: Put Back available)"
            else:
                keeper, d = by_path[r.path]
                r = apply_action("trash", d, keeper)
                if r.ok and finder_disabled:
                    r.detail += f" (Finder unavailable: {finder_disabled})"
            emit(r)
    return results
