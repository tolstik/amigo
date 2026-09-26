"""Synthetic security checks for the private model staging reader."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location(
    "read_body_model", Path(__file__).with_name("read-body-model.py")
)
assert spec is not None and spec.loader is not None
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


class PrivateModelReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "staged.html"
        self.path.write_bytes(b"<html>synthetic fixture</html>")
        self.path.chmod(0o600)

    def read(self, path=None, owner_uid=None):
        return reader.read_private_html(
            str(path or self.path), os.getuid() if owner_uid is None else owner_uid
        )

    def test_valid_regular_file(self):
        self.assertEqual(self.read(), b"<html>synthetic fixture</html>")

    def test_rejects_symlink_and_directory(self):
        link = Path(self.temp.name) / "link.html"
        link.symlink_to(self.path)
        with self.assertRaises(OSError):
            self.read(link)
        with self.assertRaises(ValueError):
            self.read(Path(self.temp.name))

    def test_rejects_wrong_owner_permissions_and_size(self):
        with self.assertRaises(ValueError):
            self.read(owner_uid=os.getuid() + 1)
        self.path.chmod(0o640)
        with self.assertRaises(ValueError):
            self.read()
        self.path.chmod(0o600)
        self.path.write_bytes(b"")
        with self.assertRaises(ValueError):
            self.read()
        self.path.write_bytes(b"x" * (reader.MAX_HTML_BYTES + 1))
        with self.assertRaises(ValueError):
            self.read()


if __name__ == "__main__":
    unittest.main()
