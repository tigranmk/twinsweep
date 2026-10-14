"""Staged duplicate detection.

The pipeline follows the approach popularised by fdupes, jdupes and rmlint:
each stage is more expensive than the previous one and only runs on files
that are still candidates.

  1. Group by exact size            (free - already known from stat)
  2. Group by partial hash          (reads <= 128 KiB per file)
  3. Group by full hash             (reads every byte)
  4. Optional byte-for-byte check   (--paranoid)

Any group that shrinks to a single file is dropped immediately.
"""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import hashing
from .cache import HashCache
from .scanner import FileEntry, ScanStats

ProgressFn = Callable[[str, int, int], None]


@dataclass
class DuplicateGroup:
    size: int
    digest: str
    files: List[FileEntry]

    @property
    def wasted_bytes(self) -> int:
        """Space that would be reclaimed by keeping only one copy."""
        return self.size * (len(self.files) - 1)


def _group(entries: Iterable[FileEntry], key: Callable[[FileEntry], object]) -> List[List[FileEntry]]:
    buckets: Dict[object, List[FileEntry]] = defaultdict(list)
    for e in entries:
        buckets[key(e)].append(e)
    return [b for b in buckets.values() if len(b) > 1]


def _hash_all(
    entries: Sequence[FileEntry],
    kind: str,
    fn: Callable[[FileEntry], str],
    cache: Optional[HashCache],
    workers: int,
    stats: ScanStats,
    progress: Optional[ProgressFn],
) -> Dict[FileEntry, str]:
    """Hash entries in parallel, consulting the cache. Unreadable files are skipped."""
    out: Dict[FileEntry, str] = {}
    todo: List[FileEntry] = []
    for e in entries:
        cached = cache.get(e.path, e.size, e.mtime_ns, kind) if cache else None
        if cached is not None:
            out[e] = cached
        else:
            todo.append(e)

    def work(e: FileEntry) -> Tuple[FileEntry, Optional[str], Optional[str]]:
        try:
            return e, fn(e), None
        except OSError as exc:
            return e, None, exc.strerror or str(exc)

    done = len(out)
    total = len(entries)
    if progress:
        progress(kind, done, total)
    # Hashing releases the GIL inside hashlib and during file reads, so
    # threads give a real speed-up, especially on SSDs and network drives.
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for e, digest, err in pool.map(work, todo):
            done += 1
            if progress:
                progress(kind, done, total)
            if digest is None:
                stats.errors.append((e.path, err or "unreadable"))
                continue
            out[e] = digest
            if cache:
                cache.put(e.path, e.size, e.mtime_ns, kind, digest)
    return out


def _split_identical(files: List[FileEntry]) -> List[List[FileEntry]]:
    """Partition a hash group into byte-identical clusters (paranoid mode)."""
    clusters: List[List[FileEntry]] = []
    for f in files:
        for c in clusters:
            if hashing.files_identical(c[0].path, f.path):
                c.append(f)
                break
        else:
            clusters.append([f])
    return [c for c in clusters if len(c) > 1]


def find_duplicates(
    entries: Iterable[FileEntry],
    *,
    cache: Optional[HashCache] = None,
    workers: int = 4,
    paranoid: bool = False,
    stats: Optional[ScanStats] = None,
    progress: Optional[ProgressFn] = None,
) -> List[DuplicateGroup]:
    """Return groups of identical files, largest wasted space first."""
    stats = stats if stats is not None else ScanStats()
    chunk = hashing.PARTIAL_CHUNK

    # Stage 1: size.
    size_groups = _group(entries, lambda e: e.size)

    # Stage 2: partial hash.
    candidates = [e for g in size_groups for e in g]
    partial = _hash_all(
        candidates,
        f"partial:{chunk}",
        lambda e: hashing.partial_hash(e.path, e.size, chunk),
        cache, workers, stats, progress,
    )
    partial_groups = _group((e for e in candidates if e in partial), lambda e: (e.size, partial[e]))

    # Stage 3: full hash, skipped for small files the partial hash already covered.
    needs_full = [e for g in partial_groups for e in g if not hashing.covers_whole_file(e.size, chunk)]
    full = _hash_all(
        needs_full, "full", lambda e: hashing.full_hash(e.path),
        cache, workers, stats, progress,
    )

    def final_digest(e: FileEntry) -> Optional[str]:
        if hashing.covers_whole_file(e.size, chunk):
            return partial.get(e)
        return full.get(e)

    groups: List[DuplicateGroup] = []
    for g in partial_groups:
        hashed = [e for e in g if final_digest(e) is not None]
        for bucket in _group(hashed, lambda e: final_digest(e)):
            clusters = _split_identical(bucket) if paranoid else [bucket]
            for c in clusters:
                c.sort(key=lambda e: e.path)
                groups.append(DuplicateGroup(size=c[0].size, digest=final_digest(c[0]) or "", files=c))

    groups.sort(key=lambda g: (-g.wasted_bytes, g.files[0].path))
    return groups
