from __future__ import annotations

import json
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


class FakeUsage:
    def model_dump(self, mode: str = "python"):
        return {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19}


class WorkerSerializationTests(unittest.TestCase):
    def test_answer_question_returns_json_serializable_result(self):
        hit = SimpleNamespace(
            document_id=uuid4(),
            chunk_id=uuid4(),
            title="Doc",
            url="https://example.com/law",
            path="Розділ I",
            heading="Стаття 1",
            unit_type="article",
            unit_id="1",
            text="Текст норми для перевірки.",
            score=0.98,
        )

        @contextmanager
        def fake_session():
            yield object()

        with patch("worker.tasks.get_session", fake_session), patch(
            "worker.tasks.retrieve", return_value=[hit]
        ), patch(
            "worker.tasks.answer_with_citations",
            return_value={"text": "Відповідь [1]", "usage": FakeUsage()},
        ):
            result = tasks.answer_question(
                user_external_id=1,
                chat_id=str(uuid4()),
                question="Що каже стаття 1?",
                max_citations=3,
                temperature=0.2,
            )

        self.assertIsInstance(result["citations"][0]["document_id"], str)
        self.assertIsInstance(result["citations"][0]["chunk_id"], str)
        self.assertEqual(result["usage"]["total_tokens"], 19)

        payload = json.dumps(result, ensure_ascii=False)
        self.assertIn("Відповідь", payload)


if __name__ == "__main__":
    unittest.main()
