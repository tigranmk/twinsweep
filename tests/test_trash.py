"""Trash action, including the batched macOS Finder path.

Finder is always replaced by a fake here: the suite must never touch the real
Trash of whoever runs it.
"""

import os
import shutil
import unittest
from unittest import mock

from twinsweep import actions
from twinsweep.actions import FinderError, apply_all
from twinsweep.finder import find_duplicates
from twinsweep.scanner import scan
from twinsweep.selector import choose_keeper

from helpers import TempTree


class FakeFinder:
    """Records calls; "trashes" by moving files into a private folder."""

    def __init__(self, trash_dir, refuse=(), fatal=None):
        self.trash_dir = trash_dir
        self.refuse = set(refuse)
        self.fatal = fatal
        self.calls = []

    def __call__(self, paths):
        self.calls.append(list(paths))
        if self.fatal:
            raise FinderError(self.fatal, fatal=True)
        for p in paths:
            if p in self.refuse:
                # Like Finder: stop at the first bad file, leaving the rest.
                raise FinderError(f"can't trash {p}", fatal=False)
            shutil.move(p, os.path.join(self.trash_dir, os.path.basename(p)))


class FinderTrashTests(TempTree):
    def setUp(self):
        super().setUp()
        self.write("keep/a.txt", "AAAA", mtime=1_000_000)
        self.write("keep/b.txt", "BBBB", mtime=1_000_000)
        for i in range(3):
            self.write(f"dupes/a{i}.txt", "AAAA", mtime=2_000_000)
            self.write(f"dupes/b{i}.txt", "BBBB", mtime=2_000_000)
        os.makedirs(self.p("_finder_trash"))
        os.makedirs(self.p("_fallback_trash"))
        groups = find_duplicates(scan([self.p("keep"), self.p("dupes")]))
        self.plan = [choose_keeper(g, "oldest") for g in groups]
        self.dupes = sorted(d.path for _, ds in self.plan for d in ds)

        # Fallback per-file trash also goes to a private folder.
        def fallback(dupe, keeper, **_):
            shutil.move(dupe.path, self.p("_fallback_trash"))
            return "moved to fallback trash"

        patches = [
            mock.patch.object(actions, "finder_available", lambda: True),
            mock.patch.dict(actions._IMPL, {"trash": fallback}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def run_with(self, finder, **kw):
        with mock.patch.object(actions, "finder_trash", finder):
            return apply_all("trash", self.plan, **kw)

    def test_batches_all_files_in_one_finder_call(self):
        finder = FakeFinder(self.p("_finder_trash"))
        results = self.run_with(finder)
        self.assertEqual(len(finder.calls), 1)
        self.assertEqual(sorted(finder.calls[0]), self.dupes)
        self.assertTrue(all(r.ok and "Finder" in r.detail for r in results))
        self.assertEqual(len(os.listdir(self.p("_finder_trash"))), 6)
        self.assertTrue(os.path.exists(self.p("keep/a.txt")))

    def test_respects_batch_size(self):
        finder = FakeFinder(self.p("_finder_trash"))
        with mock.patch.object(actions, "FINDER_BATCH", 4):
            self.run_with(finder)
        self.assertEqual([len(c) for c in finder.calls], [4, 2])

    def test_one_bad_file_does_not_cost_others_put_back(self):
        bad = self.dupes[0]
        finder = FakeFinder(self.p("_finder_trash"), refuse=[bad])
        results = {r.path: r for r in self.run_with(finder)}
        # Every other file still went through Finder, one by one.
        self.assertEqual(len(os.listdir(self.p("_finder_trash"))), 5)
        self.assertIn("Finder", results[self.dupes[1]].detail)
        # The refused file used the fallback.
        self.assertTrue(results[bad].ok)
        self.assertEqual(results[bad].detail, "moved to fallback trash")

    def test_fatal_error_falls_back_and_stops_calling_finder(self):
        finder = FakeFinder(self.p("_finder_trash"), fatal="macOS blocked control of Finder")
        with mock.patch.object(actions, "FINDER_BATCH", 2):
            results = self.run_with(finder)
        self.assertEqual(len(finder.calls), 1)  # not retried for later batches
        self.assertTrue(all(r.ok for r in results))
        self.assertTrue(all("Finder unavailable: macOS blocked" in r.detail for r in results))
        self.assertEqual(len(os.listdir(self.p("_fallback_trash"))), 6)

    def test_changed_files_are_never_sent_to_finder(self):
        with open(self.dupes[0], "w") as f:
            f.write("edited after scan")
        finder = FakeFinder(self.p("_finder_trash"))
        results = {r.path: r for r in self.run_with(finder)}
        self.assertNotIn(self.dupes[0], finder.calls[0])
        self.assertFalse(results[self.dupes[0]].ok)
        self.assertTrue(os.path.exists(self.dupes[0]))

    def test_dry_run_never_calls_finder(self):
        finder = FakeFinder(self.p("_finder_trash"))
        results = self.run_with(finder, dry_run=True)
        self.assertEqual(finder.calls, [])
        self.assertTrue(all(r.ok and r.detail == "would trash" for r in results))


class FinderAvailabilityTests(unittest.TestCase):
    def test_env_var_disables_finder(self):
        with mock.patch.object(actions.sys, "platform", "darwin"), \
                mock.patch.dict(os.environ, {actions.NO_FINDER_ENV: "1"}):
            self.assertFalse(actions.finder_available())

    def test_never_used_off_macos(self):
        with mock.patch.object(actions.sys, "platform", "linux"):
            self.assertFalse(actions.finder_available())

    def test_permission_error_is_fatal_with_helpful_message(self):
        proc = mock.Mock(returncode=1, stderr="execution error: Not authorized (-1743)")
        with mock.patch.object(actions.subprocess, "run", return_value=proc):
            with self.assertRaises(FinderError) as ctx:
                actions.finder_trash(["/x"])
        self.assertTrue(ctx.exception.fatal)
        self.assertIn("Automation", str(ctx.exception))

    def test_paths_are_passed_as_arguments_not_script_text(self):
        evil = '/tmp/a" & do shell script "rm -rf ~" & ".txt'
        proc = mock.Mock(returncode=0, stderr="")
        with mock.patch.object(actions.subprocess, "run", return_value=proc) as run:
            actions.finder_trash([evil])
        argv = run.call_args[0][0]
        self.assertEqual(argv[-1], evil)
        self.assertNotIn(evil, argv[2])  # the script itself


if __name__ == "__main__":
    unittest.main()
