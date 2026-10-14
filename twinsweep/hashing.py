"""File hashing primitives.

BLAKE2b is used because it is in the standard library, is cryptographically
strong (so accidental collisions are not a practical concern), and is faster
than SHA-256 in CPython on 64-bit machines.
"""

from __future__ import annotations

import hashlib

# Bytes read from each end of a file during the cheap "partial" stage.
# Many files that share a size (e.g. same-format images, or archives padded to
# block boundaries) differ in either their header or their trailer, so
# sampling both ends removes most false candidates before a full read.
PARTIAL_CHUNK = 64 * 1024
READ_BLOCK = 1024 * 1024


def _hasher() -> "hashlib._Hash":
    return hashlib.blake2b(digest_size=20)


def partial_hash(path: str, size: int, chunk: int = PARTIAL_CHUNK) -> str:
    """Hash the first and last `chunk` bytes of a file.

    Files no larger than 2*chunk are read completely, so for them the partial
    hash *is* the full hash (see `covers_whole_file`).
    """
    h = _hasher()
    with open(path, "rb") as f:
        if size <= 2 * chunk:
            h.update(f.read())
        else:
            h.update(f.read(chunk))
            f.seek(-chunk, 2)
            h.update(f.read(chunk))
    return h.hexdigest()


def covers_whole_file(size: int, chunk: int = PARTIAL_CHUNK) -> bool:
    return size <= 2 * chunk


def full_hash(path: str) -> str:
    h = _hasher()
    with open(path, "rb") as f:
        while True:
            block = f.read(READ_BLOCK)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def files_identical(a: str, b: str) -> bool:
    """Byte-by-byte comparison, used by --paranoid."""
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            ba = fa.read(READ_BLOCK)
            bb = fb.read(READ_BLOCK)
            if ba != bb:
                return False
            if not ba:
                return True
