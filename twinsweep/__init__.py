"""twinsweep - find and safely clean up duplicate files.

The public API is small and stable enough to use from other Python code:

    from twinsweep import scan, find_duplicates

    entries = scan(["~/Pictures"])
    for group in find_duplicates(entries):
        print(group.size, [f.path for f in group.files])
"""

from .finder import DuplicateGroup, find_duplicates
from .scanner import FileEntry, scan

__all__ = ["DuplicateGroup", "FileEntry", "find_duplicates", "scan", "__version__"]
__version__ = "1.0.1"
