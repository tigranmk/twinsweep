import os
import unittest

from twinsweep import hashing
from twinsweep.cache import HashCache
from twinsweep.finder import find_duplicates
from twinsweep.scanner import ScanStats, scan

from helpers import TempTree


def paths(groups):
    return sorted(sorted(os.path.basename(f.path) for f in g.files) for g in groups)


class ScannerTests(TempTree):
    def test_skips_hidden_and_empty_by_default(self):
        self.write("a.txt", "x")
        self.write(".hidden", "x")
        self.write(".git/config", "x")
        self.write("empty", b"")
        names = {os.path.basename(e.path) for e in scan([self.tmp])}
        self.assertEqual(names, {"a.txt"})

    def test_hidden_flag_and_exclude(self):
        self.write(".hidden", "x")
        self.write("keep.txt", "x")
        self.write("skip.tmp", "x")
        self.write("cache/inner.txt", "x")
        names = {os.path.basename(e.path)
                 for e in scan([self.tmp], include_hidden=True, exclude=["*.tmp", "cache"])}
        self.assertEqual(names, {".hidden", "keep.txt"})

    def test_size_filters(self):
        self.write("small", b"a" * 10)
        self.write("big", b"a" * 1000)
        names = {os.path.basename(e.path) for e in scan([self.tmp], min_size=100)}
        self.assertEqual(names, {"big"})
        names = {os.path.basename(e.path) for e in scan([self.tmp], max_size=100)}
        self.assertEqual(names, {"small"})

    def test_hardlinks_and_overlapping_roots_collapse(self):
        a = self.write("sub/a.txt", "same")
        os.link(a, self.p("sub/a-link.txt"))
        stats = ScanStats()
        entries = scan([self.tmp, self.p("sub")], stats=stats)
        self.assertEqual(len(entries), 1)
        self.assertGreaterEqual(stats.hardlinks_skipped, 1)

    def test_symlinks_ignored_by_default(self):
        a = self.write("a.txt", "data")
        os.symlink(a, self.p("link.txt"))
        self.assertEqual(len(scan([self.tmp])), 1)

    def test_managed_folders_skipped_by_default(self):
        self.write("src/app.js", "code")
        self.write("node_modules/lib/index.js", "code")
        self.write("pkg/node_modules/lib/index.js", "code")
        self.write("__pycache__/m.pyc", "code")
        self.write("env/pyvenv.cfg", "home = /usr")       # virtualenv, any name
        self.write("env/lib/site.py", "code")
        self.write("build/output.txt", "code")            # ambiguous name: kept
        stats = ScanStats()
        names = {os.path.relpath(e.path, self.tmp) for e in scan([self.tmp], stats=stats)}
        self.assertEqual(names, {os.path.join("src", "app.js"), os.path.join("build", "output.txt")})
        self.assertEqual(stats.managed_skipped["node_modules"], 2)
        self.assertEqual(stats.managed_skipped["__pycache__"], 1)
        self.assertEqual(stats.managed_skipped["virtualenv"], 1)

    def test_include_managed_scans_everything(self):
        self.write("src/app.js", "code")
        self.write("node_modules/lib/index.js", "code")
        self.assertEqual(len(scan([self.tmp], include_managed=True)), 2)

    def test_managed_folder_given_as_root_is_scanned(self):
        self.write("node_modules/lib/index.js", "code")
        self.assertEqual(len(scan([self.p("node_modules")])), 1)

    def test_single_file_root(self):
        a = self.write("a.txt", "data")
        self.assertEqual([e.path for e in scan([a])], [a])


class FinderTests(TempTree):
    def test_finds_identical_content_only(self):
        self.write("a.txt", "hello world")
        self.write("b/copy.txt", "hello world")
        self.write("c.txt", "hello there")  # same size, different content
        self.write("d.txt", "unique")
        groups = find_duplicates(scan([self.tmp]))
        self.assertEqual(paths(groups), [["a.txt", "copy.txt"]])
        self.assertEqual(groups[0].wasted_bytes, len("hello world"))

    def test_large_files_differing_only_in_middle(self):
        # Head and tail are equal, so only the full-hash stage can tell them apart.
        n = hashing.PARTIAL_CHUNK * 3
        base = bytearray(b"z" * n)
        self.write("x.bin", bytes(base))
        self.write("y.bin", bytes(base))
        base[n // 2] = ord("q")
        self.write("z.bin", bytes(base))
        groups = find_duplicates(scan([self.tmp]))
        self.assertEqual(paths(groups), [["x.bin", "y.bin"]])

    def test_multiple_groups_sorted_by_wasted_space(self):
        self.write("s1", "ab")
        self.write("s2", "ab")
        self.write("l1", "a" * 500)
        self.write("l2", "a" * 500)
        self.write("l3", "a" * 500)
        groups = find_duplicates(scan([self.tmp]))
        self.assertEqual([len(g.files) for g in groups], [3, 2])

    def test_paranoid_mode_agrees(self):
        self.write("a", "content")
        self.write("b", "content")
        self.assertEqual(paths(find_duplicates(scan([self.tmp]), paranoid=True)), [["a", "b"]])

    def test_cache_hits_on_second_run_and_misses_after_change(self):
        a = self.write("a", "content" * 100)
        self.write("b", "content" * 100)
        db = self.p("cache.sqlite3")
        with HashCache(db) as c:
            find_duplicates(scan([self.tmp], exclude=["cache.sqlite3*"]), cache=c)
        with HashCache(db) as c:
            groups = find_duplicates(scan([self.tmp], exclude=["cache.sqlite3*"]), cache=c)
            self.assertEqual(c.misses, 0)
            self.assertEqual(len(groups), 1)
        # Modify one file: its cache entry must not be trusted.
        with open(a, "wb") as f:
            f.write(b"CONTENT" * 100)
        with HashCache(db) as c:
            groups = find_duplicates(scan([self.tmp], exclude=["cache.sqlite3*"]), cache=c)
            self.assertEqual(groups, [])
            self.assertGreater(c.misses, 0)


if __name__ == "__main__":
    unittest.main()
