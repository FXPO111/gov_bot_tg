from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

os.environ.setdefault("ENV", "test")
os.environ.setdefault("LOG_LEVEL", "INFO")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://user:pass@localhost:5432/db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from worker import tasks


class WorkerIngestBatchTests(unittest.TestCase):
    def _fake_session(self):
        @contextmanager
        def fake_session():
            yield object()

        return fake_session

    def test_ingest_batch_sources_returns_summary(self):
        r = SimpleNamespace(source_id=uuid4(), document_id=uuid4(), chunks_upserted=10, changed=True)

        with patch("worker.tasks.get_session", self._fake_session()), patch("worker.tasks.ingest_url", return_value=r):
            out = tasks.ingest_batch_sources(["https://a.example", "not-url", "https://b.example"], title=None, meta={})

        self.assertEqual(out["total"], 2)
        self.assertEqual(out["succeeded"], 2)
        self.assertEqual(out["failed"], 0)
        self.assertEqual(len(out["results"]), 2)


if __name__ == "__main__":
    unittest.main()
