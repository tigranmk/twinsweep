import os
import unittest

from twinsweep.actions import apply_action
from twinsweep.finder import find_duplicates
from twinsweep.scanner import scan
from twinsweep.selector import choose_keeper

from helpers import TempTree


class SelectorTests(TempTree):
    def setUp(self):
        super().setUp()
        self.write("deep/nested/old.txt", "same", mtime=1_000_000)
        self.write("new.txt", "same", mtime=2_000_000)
        self.group = find_duplicates(scan([self.tmp]))[0]

    def keeper(self, rule, prefer=()):
        return os.path.basename(choose_keeper(self.group, rule, prefer)[0].path)

    def test_rules(self):
        self.assertEqual(self.keeper("oldest"), "old.txt")
        self.assertEqual(self.keeper("newest"), "new.txt")
        self.assertEqual(self.keeper("shortest-path"), "new.txt")
        self.assertEqual(self.keeper("longest-path"), "old.txt")

    def test_prefer_overrides_rule(self):
        self.assertEqual(self.keeper("newest", prefer=[self.p("deep")]), "old.txt")

    def test_first_rule_uses_root_order(self):
        group = find_duplicates(scan([self.p("deep"), self.tmp]))[0]
        keep, _ = choose_keeper(group, "first")
        self.assertTrue(keep.path.endswith("old.txt"))

    def test_unknown_rule(self):
        with self.assertRaises(ValueError):
            choose_keeper(self.group, "random")


class ActionTests(TempTree):
    def setUp(self):
        super().setUp()
        self.write("keep.txt", "payload", mtime=1_000_000)
        self.write("sub/dupe.txt", "payload", mtime=2_000_000)
        self.keep, (self.dupe,) = choose_keeper(find_duplicates(scan([self.tmp]))[0], "oldest")

    def test_dry_run_changes_nothing(self):
        r = apply_action("delete", self.dupe, self.keep, dry_run=True)
        self.assertTrue(r.ok)
        self.assertTrue(os.path.exists(self.dupe.path))

    def test_delete(self):
        self.assertTrue(apply_action("delete", self.dupe, self.keep).ok)
        self.assertFalse(os.path.exists(self.dupe.path))
        self.assertTrue(os.path.exists(self.keep.path))

    def test_hardlink(self):
        self.assertTrue(apply_action("hardlink", self.dupe, self.keep).ok)
        self.assertTrue(os.path.samefile(self.dupe.path, self.keep.path))
        with open(self.dupe.path) as f:
            self.assertEqual(f.read(), "payload")

    def test_symlink(self):
        self.assertTrue(apply_action("symlink", self.dupe, self.keep).ok)
        self.assertTrue(os.path.islink(self.dupe.path))
        self.assertEqual(os.path.realpath(self.dupe.path), os.path.realpath(self.keep.path))

    def test_move_preserves_relative_layout(self):
        dest = self.p("_quarantine")
        r = apply_action("move", self.dupe, self.keep, move_to=dest)
        self.assertTrue(r.ok, r.detail)
        root_name = os.path.basename(self.tmp)
        self.assertTrue(os.path.exists(os.path.join(dest, root_name, "sub", "dupe.txt")))
        self.assertFalse(os.path.exists(self.dupe.path))

    def test_refuses_if_file_changed_since_scan(self):
        with open(self.dupe.path, "w") as f:
            f.write("edited after scan!")
        r = apply_action("delete", self.dupe, self.keep)
        self.assertFalse(r.ok)
        self.assertIn("changed", r.detail)
        self.assertTrue(os.path.exists(self.dupe.path))

    def test_refuses_if_keeper_missing(self):
        os.remove(self.keep.path)
        r = apply_action("delete", self.dupe, self.keep)
        self.assertFalse(r.ok)
        self.assertTrue(os.path.exists(self.dupe.path))


if __name__ == "__main__":
    unittest.main()
