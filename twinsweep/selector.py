"""Decide which file in each duplicate group to keep.

Choosing the wrong "original" is the most common way people lose data with
duplicate finders, so the rules here are explicit and deterministic: the same
input always produces the same keeper, and ties are broken by path.
"""

from __future__ import annotations

import os
from typing import Callable, Dict, List, Sequence, Tuple

from .finder import DuplicateGroup
from .scanner import FileEntry

KEEP_RULES: Dict[str, Callable[[FileEntry], object]] = {
    # Sort key: the smallest value wins.
    "oldest": lambda e: e.mtime_ns,
    "newest": lambda e: -e.mtime_ns,
    "shortest-path": lambda e: (len(e.path), e.path.count(os.sep)),
    "longest-path": lambda e: (-len(e.path), -e.path.count(os.sep)),
    # Order of the roots given on the command line (like jdupes -O).
    "first": lambda e: e.root_index,
}


def _is_under(path: str, directory: str) -> bool:
    directory = os.path.abspath(os.path.expanduser(directory))
    try:
        return os.path.commonpath([path, directory]) == directory
    except ValueError:  # different drives on Windows
        return False


def choose_keeper(
    group: DuplicateGroup,
    rule: str = "oldest",
    prefer: Sequence[str] = (),
) -> Tuple[FileEntry, List[FileEntry]]:
    """Return (keeper, removable) for a group.

    `prefer` lists directories whose files win over everything else, in order;
    `rule` decides among files that are equally preferred.
    """
    if rule not in KEEP_RULES:
        raise ValueError(f"unknown keep rule {rule!r}; choose from {', '.join(KEEP_RULES)}")
    rule_key = KEEP_RULES[rule]

    def preference(e: FileEntry) -> int:
        for i, d in enumerate(prefer):
            if _is_under(e.path, d):
                return i
        return len(prefer)

    ordered = sorted(group.files, key=lambda e: (preference(e), rule_key(e), e.path))
    return ordered[0], ordered[1:]
