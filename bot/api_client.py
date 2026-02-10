from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests

from shared.settings import get_settings

settings = get_settings()


@dataclass
class APIClient:
    base_url: str = settings.api_base_url
    timeout_s: int = settings.api_timeout_s

    def chat(self, question: str, user_external_id: Optional[int] = None, chat_id: Optional[str] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"question": question, "user_external_id": user_external_id}
        if chat_id:
            payload["chat_id"] = chat_id

        r = requests.post(f"{self.base_url}/chat", json=payload, timeout=self.timeout_s)
        r.raise_for_status()
        return r.json()
