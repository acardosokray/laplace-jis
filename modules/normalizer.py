"""
modules/normalizer.py — Padroniza os dados brutos retornados pela API.

Converte tipos, trata valores ausentes, extrai hashtags e classifica formato.
"""

import re
from collections import Counter
from typing import Optional

from modules.anonimizacao import pseudonimo


def normalize_video_data(raw: dict) -> dict:
    """
    Converte o dicionário bruto da API para o formato padronizado do sistema.
    """
    duration_seconds = _iso8601_to_seconds(raw.get("duration"))
    description = _str(raw.get("description")) or ""

    return {
        "video_id":         _str(raw.get("video_id")),
        "platform":         "youtube",
        "channel_id":       _str(raw.get("channel_id")),
        "channel_name":     _str(raw.get("channel_name")),
        "channel_pseudo":   pseudonimo("canal", _str(raw.get("channel_id"))),
        "title":            _str(raw.get("title")),
        "description":      description,
        "publish_date":     _normalize_date(raw.get("publish_date")),
        "published_at":     _normalize_datetime(raw.get("publish_date")),
        "duration_seconds": duration_seconds,
        "views":            _int(raw.get("views")),
        "likes":            _int(raw.get("likes")),
        "comments":         _int(raw.get("comments")),
        "thumbnail_url":    _str(raw.get("thumbnail_url")),
        "tags":             _tags(raw.get("tags", [])),
        "hashtags":         _extract_hashtags(description),
        "language":         _str(raw.get("language")),
        "country":          _str(raw.get("country")),
        "format":           _detect_format(duration_seconds),
        "category_id":      _str(raw.get("category_id")),
    }


def normalize_comments(raw_comments: list) -> dict:
    """
    Processa lista de comentários e retorna estrutura enriquecida com top emojis.
    """
    emoji_counter: Counter = Counter()
    for c in raw_comments:
        for emoji in _extract_emojis(c.get("text", "")):
            emoji_counter[emoji] += 1

    return {
        "comments_list":   raw_comments,
        "top_emojis":      emoji_counter.most_common(),
        "total_collected": len(raw_comments),
    }


# ─── helpers ──────────────────────────────────────────────────────────────────

def _str(value) -> Optional[str]:
    return str(value).strip() if value is not None else None


def _int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_date(value: Optional[str]) -> Optional[str]:
    """Só a data (YYYY-MM-DD). Mantido para compatibilidade — quem precisa
    da hora usa `published_at`."""
    if not value:
        return None
    return value[:10] if len(value) >= 10 else value


def _normalize_datetime(value: Optional[str]) -> Optional[str]:
    """Instante completo de publicação, como a API devolve (ISO 8601 UTC:
    `2026-03-14T18:23:45Z`). É o que permite analisar horário de postagem."""
    if not value:
        return None
    return value.strip() or None


def _iso8601_to_seconds(duration: Optional[str]) -> Optional[int]:
    """Converte duração ISO 8601 para segundos inteiros."""
    if not duration:
        return None
    pattern = r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?"
    match = re.fullmatch(pattern, duration)
    if not match:
        return None
    days    = int(match.group(1) or 0)
    hours   = int(match.group(2) or 0)
    minutes = int(match.group(3) or 0)
    seconds = int(match.group(4) or 0)
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _tags(tags) -> str:
    if not tags:
        return ""
    return ", ".join(str(t).strip() for t in tags if t)


def _extract_hashtags(text: str) -> str:
    if not text:
        return ""
    found = re.findall(r"#\w+", text)
    return ", ".join(found) if found else ""


def _detect_format(duration_seconds: Optional[int]) -> str:
    """
    Classifica o formato:
      <= 60s  → shorts
      <= 600s → short_video
      > 600s  → long_video
    """
    if duration_seconds is None:
        return "unknown"
    if duration_seconds <= 60:
        return "shorts"
    if duration_seconds <= 600:
        return "short_video"
    return "long_video"


def _extract_emojis(text: str) -> list:
    """Extrai emojis individuais de um texto usando ranges Unicode."""
    if not text:
        return []
    emojis = []
    for ch in text:
        cp = ord(ch)
        # Variation selectors — ignorar
        if 0xFE00 <= cp <= 0xFE0F:
            continue
        if (
            0x1F300 <= cp <= 0x1FAFF or   # Misc symbols, emoticons, transport
            0x2600  <= cp <= 0x27BF or    # Misc symbols
            0x1F000 <= cp <= 0x1F02F or   # Mahjong / domino
            0x1F0A0 <= cp <= 0x1F0FF or   # Playing cards
            0x1F100 <= cp <= 0x1F1FF or   # Enclosed alphanumeric
            0x1F900 <= cp <= 0x1F9FF or   # Supplemental symbols
            0x231A  <= cp <= 0x231B  or   # Watch, hourglass
            0x23E9  <= cp <= 0x23F3  or   # Various clock symbols
            0x25AA  <= cp <= 0x25FE  or   # Geometric shapes
            0x2614  <= cp <= 0x2615  or   # Umbrella, hot beverage
            0x2648  <= cp <= 0x2653  or   # Zodiac
            0x267F  == cp             or   # Wheelchair
            0x2693  == cp             or   # Anchor
            0x26A1  == cp             or   # Lightning
            0x2764  == cp                  # Heart
        ):
            emojis.append(ch)
    return emojis
