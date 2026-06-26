import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


class ValidUuidTests(unittest.TestCase):
    def test_accepts_uuid_rejects_garbage(self):
        from web.server import _valid_uuid

        self.assertTrue(_valid_uuid("123e4567-e89b-12d3-a456-426614174000"))
        self.assertFalse(_valid_uuid("../../etc/passwd"))
        self.assertFalse(_valid_uuid(""))
        self.assertFalse(_valid_uuid(None))
