from __future__ import annotations

import os
import unittest

os.environ.setdefault("ENV", "test")
os.environ.setdefault("LOG_LEVEL", "INFO")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://user:pass@localhost:5432/db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from shared.db import REQUIRED_TABLES


class DBRequiredTablesTests(unittest.TestCase):
    def test_required_tables_include_telemetry_entities(self):
        self.assertIn("conversation_turns", REQUIRED_TABLES)
        self.assertIn("audit_logs", REQUIRED_TABLES)


if __name__ == "__main__":
    unittest.main()
