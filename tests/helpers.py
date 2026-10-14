"""Shared fixtures for the test suite."""

import os
import shutil
import tempfile
import unittest


class TempTree(unittest.TestCase):
    """Gives each test an isolated temporary directory and a `write` helper."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="twinsweep-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rel, data, mtime=None):
        path = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode())
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def p(self, rel):
        return os.path.join(self.tmp, rel)
