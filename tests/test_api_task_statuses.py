from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("ENV", "test")
os.environ.setdefault("LOG_LEVEL", "INFO")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://user:pass@localhost:5432/db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from api import routes


class ApiTaskStatusesTests(unittest.TestCase):
    def test_build_task_status_ready_success(self):
        fake = SimpleNamespace(state="SUCCESS", ready=lambda: True, successful=lambda: True, result={"ok": True})
        with patch("api.routes.get_result", return_value=fake):
            out = routes._build_task_status("abc")

        self.assertEqual(out.state, "SUCCESS")
        self.assertTrue(out.ready)
        self.assertTrue(out.successful)
        self.assertEqual(out.result, {"ok": True})

    def test_build_task_status_not_ready(self):
        fake = SimpleNamespace(state="PENDING", ready=lambda: False, successful=lambda: False, result=None)
        with patch("api.routes.get_result", return_value=fake):
            out = routes._build_task_status("abc")

        self.assertEqual(out.state, "PENDING")
        self.assertFalse(out.ready)
        self.assertIsNone(out.successful)


if __name__ == "__main__":
    unittest.main()
