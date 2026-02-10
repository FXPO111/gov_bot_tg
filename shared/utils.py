from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from typing import List

_ws_re = re.compile(r"[ \t\u00A0]+")
_nl_re = re.compile(r"\n{3,}")


def normalize_text(s: str) -> str:
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = _ws_re.sub(" ", s)
    s = _nl_re.sub("\n\n", s)
    return s.strip()


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def estimate_tokens(text: str) -> int:
    # грубая оценка
    return max(1, len(text) // 4)


def chunk_text(text: str, chunk_size: int, overlap: int) -> List[str]:
    text = normalize_text(text)
    if chunk_size <= 0:
        return [text]
    if overlap >= chunk_size:
        overlap = chunk_size // 4

    chunks: List[str] = []
    i = 0
    n = len(text)
    while i < n:
        j = min(n, i + chunk_size)
        chunk = text[i:j].strip()
        if chunk:
            chunks.append(chunk)
        if j == n:
            break
        i = max(0, j - overlap)
    return chunks


def compact_quote(text: str, max_len: int = 320) -> str:
    t = normalize_text(text)
    if len(t) <= max_len:
        return t
    return t[: max_len - 1].rstrip() + "…"


@dataclass
class RateLimiter:
    per_minute: int
    _tokens: float = 0.0
    _last: float = 0.0

    def __post_init__(self) -> None:
        self._tokens = float(self.per_minute)
        self._last = time.time()

    def allow(self, cost: float = 1.0) -> bool:
        now = time.time()
        elapsed = now - self._last
        self._last = now
        refill = elapsed * (self.per_minute / 60.0)
        self._tokens = min(float(self.per_minute), self._tokens + refill)
        if self._tokens >= cost:
            self._tokens -= cost
            return True
        return False
