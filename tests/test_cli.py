import contextlib
import csv
import io
import json
import os
import unittest

from twinsweep.cli import main, parse_size

from helpers import TempTree


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class ParseSizeTests(unittest.TestCase):
    def test_units(self):
        self.assertEqual(parse_size("100"), 100)
        self.assertEqual(parse_size("10k"), 10 * 1024)
        self.assertEqual(parse_size("1.5M"), int(1.5 * 1024**2))
        self.assertEqual(parse_size("2GiB"), 2 * 1024**3)
        self.assertEqual(parse_size("3 MB"), 3 * 1024**2)


class CliTests(TempTree):
    def setUp(self):
        super().setUp()
        self.write("a.txt", "duplicate content", mtime=1_000_000)
        self.write("b/a-copy.txt", "duplicate content", mtime=2_000_000)
        self.write("unique.txt", "nothing like it")

    def test_text_report(self):
        code, out, _ = run(self.tmp, "--no-cache", "-q")
        self.assertEqual(code, 0)
        self.assertIn("keep  " + self.p("a.txt"), out)
        self.assertIn("dupe  " + self.p("b/a-copy.txt"), out)
        self.assertIn("1 duplicate groups", out)

    def test_json_report(self):
        code, out, _ = run(self.tmp, "--no-cache", "-q", "--format", "json")
        doc = json.loads(out)
        self.assertEqual(doc["summary"]["groups"], 1)
        self.assertEqual(doc["groups"][0]["keep"], self.p("a.txt"))

    def test_csv_report_to_file(self):
        report = self.p("report.csv")
        run(self.tmp, "--no-cache", "-q", "--format", "csv", "-o", report, "-x", "report.csv")
        with open(report, newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual([r["role"] for r in rows], ["keep", "duplicate"])

    def test_html_report_to_file(self):
        report = self.p("report.html")
        code, _, _ = run(self.tmp, "--no-cache", "-q", "--format", "html", "-o", report, "-x", "report.html")
        self.assertEqual(code, 0)
        with open(report, encoding="utf-8") as f:
            page = f.read()
        self.assertTrue(page.startswith("<!doctype html>"))
        self.assertIn("a-copy.txt", page)
        self.assertIn('class="tag keep"', page)
        self.assertIn('class="tag dupe"', page)

    def test_html_escapes_file_names(self):
        evil = "<script>alert(1)</script>.txt"
        self.write(evil, "duplicate content")
        code, out, _ = run(self.tmp, "--no-cache", "-q", "--format", "html")
        self.assertEqual(code, 0)
        self.assertNotIn(evil, out)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;.txt", out)

    def test_html_report_without_duplicates(self):
        code, out, _ = run(self.p("unique.txt"), "--no-cache", "-q", "--format", "html")
        self.assertEqual(code, 0)
        self.assertIn("No duplicates found.", out)

    def test_reports_skipped_managed_folders(self):
        self.write("node_modules/x/a.txt", "duplicate content")
        code, out, err = run(self.tmp, "--no-cache")
        self.assertIn("Skipped 1 tool-managed folder (node_modules x1)", err)
        self.assertNotIn("node_modules", out)

        code, out, _ = run(self.tmp, "--no-cache", "-q", "--include-managed")
        self.assertIn("node_modules", out)

        code, out, _ = run(self.tmp, "--no-cache", "-q", "--format", "html")
        self.assertIn('class="note"', out)

    def test_destructive_action_needs_confirmation(self):
        # stdin is not a TTY under test, so the prompt answers "no".
        code, _, err = run(self.tmp, "--no-cache", "-q", "--action", "delete")
        self.assertEqual(code, 1)
        self.assertIn("Aborted", err)
        self.assertTrue(os.path.exists(self.p("b/a-copy.txt")))

    def test_dry_run_then_real_delete(self):
        code, _, err = run(self.tmp, "--no-cache", "-q", "--action", "delete", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("would delete 1 files", err)
        self.assertTrue(os.path.exists(self.p("b/a-copy.txt")))

        code, _, _ = run(self.tmp, "--no-cache", "-q", "--action", "delete", "--yes")
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(self.p("b/a-copy.txt")))
        self.assertTrue(os.path.exists(self.p("a.txt")))

    def test_move_requires_destination(self):
        code, _, err = run(self.tmp, "--no-cache", "--action", "move")
        self.assertEqual(code, 2)
        self.assertIn("--move-to", err)

    def test_missing_path(self):
        code, _, err = run(self.p("nope"), "--no-cache")
        self.assertEqual(code, 2)

    def test_no_duplicates(self):
        code, out, _ = run(self.p("unique.txt"), "--no-cache", "-q")
        self.assertEqual(code, 0)
        self.assertIn("Found 0 duplicate groups", out)


if __name__ == "__main__":
    unittest.main()
