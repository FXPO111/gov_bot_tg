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
    def _fake_session(self):
        @contextmanager
        def fake_session():
            yield object()

        return fake_session

    def test_answer_question_returns_json_serializable_result_and_filters_used_citations(self):
        hit1 = SimpleNamespace(
            document_id=uuid4(),
            chunk_id=uuid4(),
            title="Doc1",
            url="https://example.com/law1",
            path="Розділ I",
            heading="Стаття 1",
            unit_type="article",
            unit_id="1",
            text="Текст норми 1.",
            score=0.98,
        )
        hit2 = SimpleNamespace(
            document_id=uuid4(),
            chunk_id=uuid4(),
            title="Doc2",
            url="https://example.com/law2",
            path="Розділ II",
            heading="Стаття 2",
            unit_type="article",
            unit_id="2",
            text="Текст норми 2.",
            score=0.97,
        )

        with patch("worker.tasks.get_session", self._fake_session()), patch(
            "worker.tasks.retrieve", return_value=[hit1, hit2]
        ), patch("worker.tasks._history_for_chat", return_value=[]), patch(
            "worker.tasks.answer_with_citations",
            return_value={
                "answer_markdown": "Висновок: див. [2]",
                "citations_used": [2],
                "need_more_info": True,
                "questions": ["Уточніть дату договору"],
                "notes": ["Враховано практику"],
                "usage": FakeUsage(),
            },
        ):
            result = tasks.answer_question(
                user_external_id=1,
                chat_id=str(uuid4()),
                question="Що каже стаття 2?",
                max_citations=3,
                temperature=0.2,
                mode="consult",
            )

        self.assertEqual(len(result["citations"]), 1)
        self.assertEqual(result["citations"][0]["n"], 2)
        self.assertIsInstance(result["citations"][0]["document_id"], str)
        self.assertIsInstance(result["citations"][0]["chunk_id"], str)
        self.assertEqual(result["usage"]["total_tokens"], 19)
        self.assertTrue(result["need_more_info"])
        self.assertEqual(result["questions"], ["Уточніть дату договору"])
        self.assertEqual(result["notes"], ["Враховано практику"])

        payload = json.dumps(result, ensure_ascii=False)
        self.assertIn("Висновок", payload)

    def test_deduplicates_hits_before_numbering(self):
        common_doc = uuid4()
        common_chunk = uuid4()

        low = SimpleNamespace(
            document_id=common_doc,
            chunk_id=common_chunk,
            title="Doc low",
            url="https://example.com/law",
            path="Розділ I",
            heading="Стаття 10",
            unit_type="article",
            unit_id="10",
            text="Текст норми (нижчий score).",
            score=0.2,
        )
        high = SimpleNamespace(
            document_id=common_doc,
            chunk_id=common_chunk,
            title="Doc high",
            url="https://example.com/law",
            path="Розділ I",
            heading="Стаття 10",
            unit_type="article",
            unit_id="10",
            text="Текст норми (вищий score).",
            score=0.9,
        )

        with patch("worker.tasks.get_session", self._fake_session()), patch(
            "worker.tasks.retrieve", return_value=[low, high]
        ), patch("worker.tasks._history_for_chat", return_value=[]), patch(
            "worker.tasks.answer_with_citations",
            return_value={
                "answer_markdown": "Висновок [1]",
                "citations_used": [1],
                "need_more_info": False,
                "questions": [],
                "usage": {},
            },
        ):
            result = tasks.answer_question(
                user_external_id=1,
                chat_id=str(uuid4()),
                question="Тест дублювання",
                max_citations=6,
                temperature=0.2,
                mode="consult",
            )

        self.assertEqual(len(result["citations"]), 1)
        self.assertIn("вищий score", result["citations"][0]["quote"])

    def test_strips_need_more_info_service_line(self):
        hit = SimpleNamespace(
            document_id=uuid4(),
            chunk_id=uuid4(),
            title="Doc",
            url="https://example.com/law",
            path="Розділ I",
            heading="Стаття 1",
            unit_type="article",
            unit_id="1",
            text="Текст норми 1.",
            score=0.98,
        )

        with patch("worker.tasks.get_session", self._fake_session()), patch(
            "worker.tasks.retrieve", return_value=[hit]
        ), patch("worker.tasks._history_for_chat", return_value=[]), patch(
            "worker.tasks.answer_with_citations",
            return_value={
                "answer_markdown": "Висновок\nneed_more_info=true\nНорма [1]",
                "citations_used": [1],
                "need_more_info": False,
                "questions": [],
                "usage": {},
            },
        ):
            result = tasks.answer_question(
                user_external_id=1,
                chat_id=str(uuid4()),
                question="Тест маркера",
                max_citations=3,
                temperature=0.2,
                mode="consult",
            )

        self.assertNotIn("need_more_info=", result["answer"].lower())


if __name__ == "__main__":
    unittest.main()
