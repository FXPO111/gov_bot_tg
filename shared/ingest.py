from __future__ import annotations

import io
import mimetypes
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Tuple
from urllib.parse import urldefrag, urlparse

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.orm import Session
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

try:
    import trafilatura
except Exception:  # pragma: no cover
    trafilatura = None

from . import llm
from .models import Chunk, Document, Source
from .settings import get_settings
from .utils import estimate_tokens, normalize_text, sha256_hex, chunk_text

settings = get_settings()


@dataclass
class IngestResult:
    source_id: Any
    document_id: Any
    chunks_upserted: int
    changed: bool


@dataclass
class Segment:
    text: str
    path: Optional[str] = None
    heading: Optional[str] = None
    unit_type: Optional[str] = None
    unit_id: Optional[str] = None
    part: int = 0


_RADA_HOSTS = {"zakon.rada.gov.ua", "zakon2.rada.gov.ua", "zakon3.rada.gov.ua"}
_KMU_SUFFIX = "kmu.gov.ua"

_RE_DATE_DDMMYYYY = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
_RE_RADA_ED = re.compile(r"/ed(\d{8})(?:/|$)")
_RE_RADA_DOCKEY = re.compile(r"/(?:go|laws/show)(?:/[a-z]{2})?/([0-9A-Za-zА-Яа-яІЇЄҐієїґ\-_–]+)")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _strip_url(url: str) -> str:
    url = normalize_text(url)
    url, _frag = urldefrag(url)
    return url.strip()


def _guess_pdf(url: str, content_type: str) -> bool:
    if "pdf" in (content_type or "").lower():
        return True
    mt, _ = mimetypes.guess_type(url)
    return (mt or "").lower() == "application/pdf" or url.lower().endswith(".pdf")


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((requests.RequestException,)),
)
def fetch_url(url: str, timeout_s: int = 45) -> Tuple[bytes, str, str]:
    headers = {
        "User-Agent": "yourbot/1.0 (+legal-assistant; ingestion)",
        "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
        "Accept-Language": "uk,ru;q=0.8,en;q=0.6",
        "Connection": "close",
    }
    r = requests.get(url, headers=headers, timeout=timeout_s, allow_redirects=True)
    r.raise_for_status()
    ctype = r.headers.get("content-type", "")
    final_url = str(getattr(r, "url", url))
    return r.content, ctype, final_url


def _detect_kind(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host in _RADA_HOSTS:
        return "zakon_rada"
    if host.endswith(_KMU_SUFFIX):
        return "kmu"
    return "url"


def _rada_parse_dockey(url: str) -> Optional[str]:
    m = _RE_RADA_DOCKEY.search(url)
    if not m:
        return None
    return m.group(1)


def _rada_parse_lang(url: str) -> Optional[str]:
    # zakon.rada.gov.ua/laws/show/en/2939-17/...
    path = urlparse(url).path
    m = re.search(r"/laws/show/([a-z]{2})/", path)
    if m:
        return m.group(1)
    return None


def _rada_parse_edition(url: str) -> Optional[str]:
    m = _RE_RADA_ED.search(urlparse(url).path)
    return m.group(1) if m else None


def _rada_urls(dockey: str, lang: Optional[str], edition: Optional[str]) -> Tuple[str, str, str]:
    # source_url (permanent), show_url, print_url
    source_url = f"https://zakon.rada.gov.ua/go/{dockey}"
    parts = ["https://zakon.rada.gov.ua", "laws", "show"]
    if lang and lang != "uk":
        parts.append(lang)
    parts.append(dockey)
    if edition:
        parts.append(f"ed{edition}")
    show_url = "/".join(parts)
    print_url = show_url + "/print"
    return source_url, show_url, print_url


def extract_text_from_pdf(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    parts: list[str] = []
    for page in reader.pages:
        try:
            t = page.extract_text() or ""
        except Exception:
            t = ""
        if t.strip():
            parts.append(t)
    return normalize_text("\n\n".join(parts))


def _bs_text(html: bytes) -> str:
    s = html.decode("utf-8", errors="ignore")
    soup = BeautifulSoup(s, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n")
    return normalize_text(text)


def extract_text_from_html_generic(html: bytes) -> str:
    s = html.decode("utf-8", errors="ignore")

    if trafilatura is not None:
        extracted = trafilatura.extract(s, include_comments=False, include_tables=True)
        if extracted:
            return normalize_text(extracted)

    return _bs_text(html)


def extract_text_from_html_rada_print(html: bytes) -> str:
    # print-страницы zakon.rada: берём чистый текст и выкидываем шапку "Друкувати/Help/Font"
    txt = _bs_text(html)
    lines = [ln.strip() for ln in txt.splitlines()]
    lines = [ln for ln in lines if ln]

    drop_prefix = []
    for i, ln in enumerate(lines):
        if ln.lower().startswith("друкувати") or ln.lower().startswith("print"):
            drop_prefix.append(i)
            continue
        if ln.lower().startswith("допомога") or ln.lower().startswith("help"):
            drop_prefix.append(i)
            continue
        if ln.lower().startswith("шрифт") or "ctrl" in ln.lower():
            drop_prefix.append(i)
            continue
        if ln.lower().startswith("image:"):
            drop_prefix.append(i)
            continue
        # реальная шапка документа обычно начинается с "ЗАКОН УКРАЇНИ" / "ПОСТАНОВА" / "УКАЗ" и т.п.
        if re.match(r"^(ЗАКОН|ПОСТАНОВА|УКАЗ|НАКАЗ|РОЗПОРЯДЖЕННЯ|КОДЕКС|КОНСТИТУЦІЯ)\b", ln):
            lines = lines[i:]
            break

    return normalize_text("\n".join(lines))


def _html_title(html: bytes) -> Optional[str]:
    s = html.decode("utf-8", errors="ignore")
    soup = BeautifulSoup(s, "html.parser")
    h1 = soup.find("h1")
    if h1:
        t = h1.get_text(" ", strip=True)
        if t:
            return normalize_text(t)
    title = soup.find("title")
    if title and title.text:
        t = normalize_text(title.text)
        if t:
            return t
    # fallback: первая строка # ...
    txt = _bs_text(html)
    for ln in txt.splitlines():
        ln = ln.strip()
        if ln.startswith("# "):
            return normalize_text(ln[2:])
    return None


def _uk_date_to_iso(s: str) -> Optional[str]:
    m = _RE_DATE_DDMMYYYY.search(s)
    if not m:
        return None
    dd, mm, yyyy = m.group(1), m.group(2), m.group(3)
    return f"{yyyy}-{mm}-{dd}"


def _rada_extract_meta(show_html: bytes) -> dict[str, Any]:
    txt = _bs_text(show_html)
    title = _html_title(show_html) or None

    meta: dict[str, Any] = {}
    if title:
        meta["title"] = title

    # ищем строку типа: "Закон України від 13.01.2011 № 2939-VI"
    # или: "Постанова Кабінету Міністрів України від ... № ..."
    doc_type = None
    doc_number = None
    adopted = None
    authority = None

    for ln in txt.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        m = re.search(r"\b(Закон України|КОДЕКС УКРАЇНИ|Указ Президента України|ПОСТАНОВА.*?України|Наказ.*?України|Розпорядження.*?України)\b", ln, re.IGNORECASE)
        if m and ("від" in ln and "№" in ln):
            doc_type = normalize_text(m.group(1))
            dm = _RE_DATE_DDMMYYYY.search(ln)
            if dm:
                adopted = _uk_date_to_iso(dm.group(0))
            nm = re.search(r"№\s*([0-9A-Za-zА-Яа-яІЇЄҐієїґ\-/–]+)", ln)
            if nm:
                doc_number = normalize_text(nm.group(1))
            break

    if doc_type:
        meta["doc_type"] = doc_type
        # орган по типу акта (грубо, но стабильно)
        dt_low = doc_type.lower()
        if "закон" in dt_low or "кодекс" in dt_low:
            authority = "Верховна Рада України"
        elif "указ президента" in dt_low:
            authority = "Президент України"
        elif "кабінету міністрів" in dt_low or dt_low.startswith("постанова"):
            authority = "Кабінет Міністрів України"
        meta["authority"] = authority

    if doc_number:
        meta["doc_number"] = doc_number
    if adopted:
        meta["adopted_date"] = adopted

    # статус + текущая редакция: "поточна редакція — Редакція від 08.08.2025, підстава - 4321-IX"
    status = None
    revision_date = None
    revision_basis = None
    for ln in txt.splitlines():
        ln = ln.strip()
        if "Документ" in ln and ("чинний" in ln.lower() or "не чинний" in ln.lower() or "втратив" in ln.lower()):
            status = ln
        if "Редакція від" in ln:
            dm = _RE_DATE_DDMMYYYY.search(ln)
            if dm:
                revision_date = _uk_date_to_iso(dm.group(0))
            bm = re.search(r"підстава\s*[-–]\s*([0-9A-Za-zА-Яа-яІЇЄҐієїґ\-/–]+)", ln)
            if bm:
                revision_basis = normalize_text(bm.group(1))

    if status:
        meta["status_line"] = status
    if revision_date:
        meta["revision_date"] = revision_date
    if revision_basis:
        meta["revision_basis"] = revision_basis

    return meta


def _kmu_find_pdf_link(html: bytes, base_url: str) -> Optional[str]:
    s = html.decode("utf-8", errors="ignore")
    soup = BeautifulSoup(s, "html.parser")
    best = None
    best_score = 0

    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        txt = (a.get_text(" ", strip=True) or "").lower()

        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            u = urlparse(base_url)
            href = f"{u.scheme}://{u.netloc}{href}"

        if not href.lower().startswith("http"):
            continue
        if ".pdf" not in href.lower():
            continue

        score = 1
        if "постан" in txt or "наказ" in txt or "розпоряд" in txt:
            score += 2
        if "завантаж" in txt or "download" in txt:
            score += 2
        if "додат" in txt:
            score += 1

        if score > best_score:
            best_score = score
            best = href

    return best


def _kmu_extract_meta(html: bytes) -> dict[str, Any]:
    title = _html_title(html)
    txt = _bs_text(html)

    meta: dict[str, Any] = {}
    if title:
        meta["title"] = title
    meta["authority"] = "Кабінет Міністрів України"

    # попытка выдернуть дату и № из текста
    adopted = None
    doc_number = None
    for ln in txt.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if "№" in ln and _RE_DATE_DDMMYYYY.search(ln):
            adopted = _uk_date_to_iso(_RE_DATE_DDMMYYYY.search(ln).group(0))
            nm = re.search(r"№\s*([0-9A-Za-zА-Яа-яІЇЄҐієїґ\-/–]+)", ln)
            if nm:
                doc_number = normalize_text(nm.group(1))
            break

    if adopted:
        meta["adopted_date"] = adopted
    if doc_number:
        meta["doc_number"] = doc_number

    return meta


def _is_upper_title(line: str) -> bool:
    # "ЗАГАЛЬНІ ПОЛОЖЕННЯ" и подобные
    if len(line) < 5:
        return False
    # допускаем пробелы, тире и скобки
    letters = [ch for ch in line if ch.isalpha()]
    if not letters:
        return False
    upper_ratio = sum(1 for ch in letters if ch == ch.upper()) / max(1, len(letters))
    return upper_ratio > 0.9


_RE_SECTION = re.compile(r"^(Розділ|Раздел)\s+([IVXLC\d]+)\b", re.IGNORECASE)
_RE_CHAPTER = re.compile(r"^(Глава|ГЛАВА|Глава)\s+([IVXLC\d]+)\b", re.IGNORECASE)
_RE_ARTICLE = re.compile(r"^(Стаття|Статья)\s+([0-9]+(?:[-–][0-9]+)?(?:[¹²³⁴⁵⁶⁷⁸⁹])?)\b(?:\.\s*(.*))?$", re.IGNORECASE)
_RE_POINT_WORD = re.compile(r"^(Пункт)\s+([0-9]+(?:[-–][0-9]+)?)\b", re.IGNORECASE)
_RE_POINT_NUM = re.compile(r"^([0-9]{1,3})\.\s+\S", re.IGNORECASE)


def segment_legal_text(text: str, *, prefer_mode: Optional[str] = None) -> list[Segment]:
    """
    Режем по структуре:
      - article mode: Стаття N...
      - point mode: Пункт N / "N. ..." (только если нет статей)
      - fallback: обычный chunk_text
    """
    text = normalize_text(text)
    if not text:
        return []

    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln != ""]

    if prefer_mode:
        mode = prefer_mode
    else:
        # эвристика: если "Стаття" встречается несколько раз — это закон/кодекс
        art_hits = sum(1 for ln in lines if _RE_ARTICLE.match(ln))
        if art_hits >= 2:
            mode = "article"
        else:
            # если пунктов много — постановление/порядок
            p_hits = sum(1 for ln in lines if _RE_POINT_WORD.match(ln) or _RE_POINT_NUM.match(ln))
            mode = "point" if p_hits >= 3 else "plain"

    segments: list[Segment] = []

    section_id: Optional[str] = None
    section_title: Optional[str] = None
    chapter_id: Optional[str] = None
    chapter_title: Optional[str] = None

    have_seen_unit = False
    preface_lines: list[str] = []
    pending_between: list[str] = []

    cur_unit_type: Optional[str] = None
    cur_unit_id: Optional[str] = None
    cur_heading: Optional[str] = None
    cur_lines: Optional[list[str]] = None

    prev_was_section = False
    prev_was_chapter = False

    def cur_path() -> Optional[str]:
        parts = []
        if section_id:
            if section_title:
                parts.append(f"Розділ {section_id} ({section_title})")
            else:
                parts.append(f"Розділ {section_id}")
        if chapter_id:
            if chapter_title:
                parts.append(f"Глава {chapter_id} ({chapter_title})")
            else:
                parts.append(f"Глава {chapter_id}")
        if cur_unit_type == "article" and cur_unit_id:
            parts.append(f"Стаття {cur_unit_id}")
        if cur_unit_type == "point" and cur_unit_id:
            parts.append(f"Пункт {cur_unit_id}")
        if not parts:
            return None
        return " / ".join(parts)

    def flush_current():
        nonlocal cur_lines, cur_unit_type, cur_unit_id, cur_heading
        if cur_lines:
            segments.append(
                Segment(
                    text=normalize_text("\n".join(cur_lines)),
                    path=cur_path(),
                    heading=cur_heading,
                    unit_type=cur_unit_type,
                    unit_id=cur_unit_id,
                    part=0,
                )
            )
        cur_lines = None
        cur_unit_type = None
        cur_unit_id = None
        cur_heading = None

    for ln in lines:
        msec = _RE_SECTION.match(ln)
        if msec:
            # смена раздела заканчивает текущую единицу
            if cur_lines is not None:
                flush_current()
            section_id = normalize_text(msec.group(2))
            section_title = None
            prev_was_section = True
            prev_was_chapter = False

            if not have_seen_unit:
                preface_lines.append(ln)
            else:
                pending_between.append(ln)
            continue

        mch = _RE_CHAPTER.match(ln)
        if mch:
            if cur_lines is not None:
                flush_current()
            chapter_id = normalize_text(mch.group(2))
            chapter_title = None
            prev_was_section = False
            prev_was_chapter = True

            if not have_seen_unit:
                preface_lines.append(ln)
            else:
                pending_between.append(ln)
            continue

        # заголовки к разделу/главе (часто отдельной строкой верхним регистром)
        if prev_was_section and _is_upper_title(ln):
            section_title = ln
            prev_was_section = False
            if not have_seen_unit:
                preface_lines.append(ln)
            else:
                pending_between.append(ln)
            continue

        if prev_was_chapter and _is_upper_title(ln):
            chapter_title = ln
            prev_was_chapter = False
            if not have_seen_unit:
                preface_lines.append(ln)
            else:
                pending_between.append(ln)
            continue

        prev_was_section = False
        prev_was_chapter = False

        if mode == "article":
            mart = _RE_ARTICLE.match(ln)
            if mart:
                # первая статья: сливаем преамбулу
                if not have_seen_unit:
                    have_seen_unit = True
                    if preface_lines:
                        segments.append(Segment(text=normalize_text("\n".join(preface_lines)), unit_type="preamble", unit_id="0"))
                        preface_lines = []

                # новая статья — закрываем предыдущую
                if cur_lines is not None:
                    flush_current()

                cur_unit_type = "article"
                cur_unit_id = normalize_text(mart.group(2))
                title_part = (mart.group(3) or "").strip()
                cur_heading = normalize_text(f"Стаття {cur_unit_id}" + (f". {title_part}" if title_part else ""))

                cur_lines = []
                if pending_between:
                    cur_lines.extend(pending_between)
                    pending_between = []
                cur_lines.append(ln)
                continue

        if mode in ("point", "plain"):
            # точки начинаем только если не встретили статей
            mpw = _RE_POINT_WORD.match(ln)
            mpn = _RE_POINT_NUM.match(ln)
            if mpw or (mpn and mode == "point"):
                if not have_seen_unit:
                    have_seen_unit = True
                    if preface_lines:
                        segments.append(Segment(text=normalize_text("\n".join(preface_lines)), unit_type="preamble", unit_id="0"))
                        preface_lines = []

                if cur_lines is not None:
                    flush_current()

                cur_unit_type = "point"
                cur_unit_id = normalize_text((mpw.group(2) if mpw else mpn.group(1)))
                cur_heading = normalize_text(f"Пункт {cur_unit_id}")
                cur_lines = []
                if pending_between:
                    cur_lines.extend(pending_between)
                    pending_between = []
                cur_lines.append(ln)
                continue

        # обычные строки
        if not have_seen_unit:
            preface_lines.append(ln)
        else:
            if cur_lines is None:
                # между единицами — добавим к следующей, либо в tail
                pending_between.append(ln)
            else:
                cur_lines.append(ln)

    if cur_lines is not None:
        flush_current()

    if preface_lines and not segments:
        # документ без явных единиц
        segments = [Segment(text=normalize_text("\n".join(preface_lines)), unit_type="chunk", unit_id="0")]
        preface_lines = []

    if pending_between:
        segments.append(Segment(text=normalize_text("\n".join(pending_between)), unit_type="tail", unit_id="0"))

    # гарантируем, что сегменты не огромные: если да — режем на части
    out: list[Segment] = []
    for seg in segments:
        if len(seg.text) <= settings.chunk_size_chars * 2:
            out.append(seg)
            continue
        parts = chunk_text(seg.text, settings.chunk_size_chars, settings.chunk_overlap_chars)
        for pi, ptxt in enumerate(parts):
            out.append(
                Segment(
                    text=ptxt,
                    path=seg.path,
                    heading=seg.heading,
                    unit_type=seg.unit_type,
                    unit_id=seg.unit_id,
                    part=pi,
                )
            )
    return out


def _upsert_source(session: Session, *, source_url: str, kind: str, title: Optional[str]) -> Source:
    src = session.execute(select(Source).where(Source.url == source_url)).scalar_one_or_none()
    if src is None:
        src = Source(url=source_url, kind=kind, title=title)
        session.add(src)
        session.flush()
        return src

    if kind and src.kind != kind:
        src.kind = kind
    if title and not src.title:
        src.title = title
    return src


def _store_document_and_chunks(
    session: Session,
    *,
    src: Source,
    doc_url: str,
    title: Optional[str],
    content_text: str,
    meta: dict[str, Any],
    segments: list[Segment],
) -> IngestResult:
    content_text = normalize_text(content_text)
    if not content_text or len(content_text) < 80:
        raise ValueError("Empty/too short text extracted.")

    content_hash = sha256_hex(content_text)

    last_doc = (
        session.execute(
            select(Document).where(Document.source_id == src.id).order_by(Document.fetched_at.desc()).limit(1)
        ).scalar_one_or_none()
    )

    if last_doc is not None and last_doc.content_hash == content_hash:
        last_doc.fetched_at = _utcnow()
        session.flush()
        return IngestResult(source_id=src.id, document_id=last_doc.id, chunks_upserted=0, changed=False)

    doc = Document(
        source_id=src.id,
        url=doc_url,
        title=title or src.title,
        content_text=content_text,
        content_hash=content_hash,
        fetched_at=_utcnow(),
        meta_json=meta,
    )
    session.add(doc)
    session.flush()

    if not segments:
        segments = [Segment(text=content_text, unit_type="chunk", unit_id="0")]

    chunk_texts = [s.text for s in segments]
    embeddings: list[list[float] | None]
    try:
        embeddings_raw = llm.embed_texts(chunk_texts, batch_size=32)
        embeddings = [list(v) for v in embeddings_raw]
    except Exception:
        embeddings = [None] * len(segments)

    upserted = 0
    for idx, seg in enumerate(segments):
        vec = embeddings[idx] if idx < len(embeddings) else None
        session.add(
            Chunk(
                document_id=doc.id,
                idx=idx,
                path=seg.path,
                heading=seg.heading,
                unit_type=seg.unit_type,
                unit_id=seg.unit_id,
                part=int(seg.part or 0),
                text=seg.text,
                token_count=estimate_tokens(seg.text),
                embedding=vec,
            )
        )
        upserted += 1

    session.flush()
    return IngestResult(source_id=src.id, document_id=doc.id, chunks_upserted=upserted, changed=True)


def ingest_url(session: Session, url: str, title: Optional[str] = None, meta: Optional[dict[str, Any]] = None) -> IngestResult:
    meta = dict(meta or {})
    url = _strip_url(url)

    raw, ctype, final_url = fetch_url(url, timeout_s=settings.http_timeout_s)
    kind = _detect_kind(final_url)

    # PDF напрямую
    if _guess_pdf(final_url, ctype):
        text = extract_text_from_pdf(raw)
        src = _upsert_source(session, source_url=final_url, kind=kind, title=title)
        segs = segment_legal_text(text)
        meta.setdefault("ingest", {})
        meta["ingest"].update({"final_url": final_url, "content_type": ctype, "kind": kind})
        return _store_document_and_chunks(
            session,
            src=src,
            doc_url=final_url,
            title=title,
            content_text=text,
            meta=meta,
            segments=segs,
        )

    if kind == "zakon_rada":
        dockey = _rada_parse_dockey(final_url)
        if not dockey:
            raise ValueError("Не смог распознать doc id для zakon.rada URL.")

        lang = _rada_parse_lang(final_url)
        edition = _rada_parse_edition(final_url)

        source_url, show_url, print_url = _rada_urls(dockey, lang, edition)

        # мета берём из show (без /print), текст — из /print
        show_html, show_ctype, show_final = fetch_url(show_url, timeout_s=settings.http_timeout_s)
        if "text/html" not in (show_ctype or ""):
            # бывает редирект на другое представление
            show_html = show_html

        print_html, print_ctype, print_final = fetch_url(print_url, timeout_s=settings.http_timeout_s)
        if "text/html" not in (print_ctype or ""):
            raise ValueError("Ожидал HTML на /print, но получил другой content-type.")

        extracted_meta = _rada_extract_meta(show_html)
        extracted_title = extracted_meta.get("title") or title

        text = extract_text_from_html_rada_print(print_html)
        segs = segment_legal_text(text)

        meta.setdefault("ingest", {})
        meta["ingest"].update(
            {
                "input_url": url,
                "final_url": final_url,
                "source_url": source_url,
                "show_url": show_final,
                "print_url": print_final,
                "kind": kind,
                "lang": lang or "uk",
                "edition": edition,
            }
        )
        meta.setdefault("extracted", {})
        meta["extracted"].update(extracted_meta)

        src = _upsert_source(session, source_url=source_url, kind=kind, title=extracted_title)
        return _store_document_and_chunks(
            session,
            src=src,
            doc_url=print_final,
            title=extracted_title,
            content_text=text,
            meta=meta,
            segments=segs,
        )

    if kind == "kmu":
        # может быть HTML + вложенный PDF
        extracted_meta = _kmu_extract_meta(raw)
        extracted_title = extracted_meta.get("title") or title

        pdf_link = _kmu_find_pdf_link(raw, final_url)
        if pdf_link:
            pdf_raw, pdf_ctype, pdf_final = fetch_url(pdf_link, timeout_s=settings.http_timeout_s)
            if _guess_pdf(pdf_final, pdf_ctype):
                text = extract_text_from_pdf(pdf_raw)
                segs = segment_legal_text(text)
                meta.setdefault("ingest", {})
                meta["ingest"].update({"input_url": url, "final_url": final_url, "pdf_url": pdf_final, "kind": kind})
                meta.setdefault("extracted", {})
                meta["extracted"].update(extracted_meta)

                src = _upsert_source(session, source_url=final_url, kind=kind, title=extracted_title)
                return _store_document_and_chunks(
                    session,
                    src=src,
                    doc_url=pdf_final,
                    title=extracted_title,
                    content_text=text,
                    meta=meta,
                    segments=segs,
                )

        # fallback: чистим HTML
        text = extract_text_from_html_generic(raw)
        segs = segment_legal_text(text)

        meta.setdefault("ingest", {})
        meta["ingest"].update({"input_url": url, "final_url": final_url, "kind": kind})
        meta.setdefault("extracted", {})
        meta["extracted"].update(extracted_meta)

        src = _upsert_source(session, source_url=final_url, kind=kind, title=extracted_title)
        return _store_document_and_chunks(
            session,
            src=src,
            doc_url=final_url,
            title=extracted_title,
            content_text=text,
            meta=meta,
            segments=segs,
        )

    # generic HTML
    text = extract_text_from_html_generic(raw)
    segs = segment_legal_text(text)

    meta.setdefault("ingest", {})
    meta["ingest"].update({"input_url": url, "final_url": final_url, "kind": kind, "content_type": ctype})

    src = _upsert_source(session, source_url=final_url, kind=kind, title=title)
    return _store_document_and_chunks(
        session,
        src=src,
        doc_url=final_url,
        title=title,
        content_text=text,
        meta=meta,
        segments=segs,
    )
