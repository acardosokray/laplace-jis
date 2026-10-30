"""
modules/extractor.py — Extrai o video_id de URLs do YouTube.

Formatos suportados:
  - https://www.youtube.com/watch?v=VIDEO_ID
  - https://youtu.be/VIDEO_ID
  - https://youtube.com/watch?v=VIDEO_ID&t=30s  (com parâmetros extras)
  - https://www.youtube.com/shorts/VIDEO_ID     (YouTube Shorts)
  - https://m.youtube.com/watch?v=VIDEO_ID      (versão mobile)
"""

import re
from urllib.parse import urlparse, parse_qs
from typing import Optional


def extract_video_id(url: str) -> Optional[str]:
    """
    Extrai o video_id de uma URL do YouTube.

    Args:
        url: URL do vídeo do YouTube.

    Returns:
        O video_id como string, ou None se não for possível extrair.
    """
    if not url or not isinstance(url, str):
        return None

    url = url.strip()

    try:
        parsed = urlparse(url)
    except Exception:
        return None

    hostname = parsed.hostname or ""

    # Normaliza hostname: remove "www." e "m."
    hostname = re.sub(r"^(www\.|m\.)", "", hostname)

    # Formato youtu.be/VIDEO_ID
    if hostname == "youtu.be":
        video_id = parsed.path.lstrip("/").split("/")[0].split("?")[0]
        return video_id if _is_valid_id(video_id) else None

    # Formatos youtube.com
    if hostname in ("youtube.com",):
        path = parsed.path

        # /watch?v=VIDEO_ID
        if path == "/watch":
            params = parse_qs(parsed.query)
            video_id = params.get("v", [None])[0]
            return video_id if video_id and _is_valid_id(video_id) else None

        # /shorts/VIDEO_ID
        shorts_match = re.match(r"^/shorts/([A-Za-z0-9_-]{11})", path)
        if shorts_match:
            return shorts_match.group(1)

        # /embed/VIDEO_ID
        embed_match = re.match(r"^/embed/([A-Za-z0-9_-]{11})", path)
        if embed_match:
            return embed_match.group(1)

    # Fallback: tenta regex genérico na URL completa
    pattern = r"(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})"
    match = re.search(pattern, url)
    if match:
        return match.group(1)

    return None


def _is_valid_id(video_id: str) -> bool:
    """Valida se o video_id tem o formato esperado pelo YouTube (11 chars)."""
    return bool(video_id and re.match(r"^[A-Za-z0-9_-]{11}$", video_id))
