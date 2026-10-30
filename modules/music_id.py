"""
modules/music_id.py — Identificação de músicas via ShazamIO.

Recebe o arquivo music_fx.wav (saída do Demucs, sem voz) e identifica
as músicas presentes, dividindo em janelas de ~12 segundos.

Para cada janela com match, retorna:
  - track_name, artist, genre, cover_art_url
  - t_start, t_end (alinhados com a timeline do vídeo)
  - confidence (1.0 para matches do Shazam)

Consolidação: janelas consecutivas com a mesma faixa são mescladas
em um único evento com t_start do primeiro e t_end do último.

Pré-requisito: pip install shazamio pydub
"""

import os
import asyncio
import tempfile
from typing import Optional


# Duração de cada janela de análise em segundos
WINDOW_SECONDS = 12
# Overlap entre janelas (para não perder transições)
OVERLAP_SECONDS = 2


def identify_from_file(audio_path: str, duration_seconds: Optional[float] = None) -> list[dict]:
    """
    Identifica músicas em todo o arquivo de áudio.

    Args:
        audio_path:       caminho do arquivo .wav (idealmente music_fx do Demucs)
        duration_seconds: duração total do vídeo (para limitar janelas)

    Returns:
        Lista de {
          t_start:       float
          t_end:         float
          track_name:    str | None
          artist:        str | None
          genre:         str | None
          cover_art_url: str | None
          shazam_url:    str | None
          confidence:    float
          source:        'shazamio'
        }
        Vazia se não identificar nada ou dependências faltarem.
    """
    if not audio_path or not os.path.exists(audio_path):
        return []

    try:
        from pydub import AudioSegment
    except ImportError:
        print("   ⚠️  pydub não instalado — music_id pulado.")
        print("        Para instalar: pip install pydub")
        return []

    try:
        from shazamio import Shazam
    except ImportError:
        print("   ⚠️  shazamio não instalado — music_id pulado.")
        print("        Para instalar: pip install shazamio")
        return []

    try:
        audio = AudioSegment.from_file(audio_path)
    except Exception as e:
        print(f"   ⚠️  Erro ao carregar áudio para music_id: {e}")
        return []

    total_ms = len(audio)
    if duration_seconds:
        total_ms = min(total_ms, int(duration_seconds * 1000))

    window_ms = WINDOW_SECONDS * 1000
    step_ms = (WINDOW_SECONDS - OVERLAP_SECONDS) * 1000

    # Gera janelas
    windows = []
    pos = 0
    while pos < total_ms:
        end = min(pos + window_ms, total_ms)
        if end - pos < 3000:  # janela < 3s — pula
            break
        windows.append((pos, end))
        pos += step_ms

    if not windows:
        return []

    # Roda identificação assíncrona
    raw_results = asyncio.run(_identify_windows(audio, windows))

    # Consolida janelas consecutivas com mesma faixa
    consolidated = _consolidate(raw_results)

    return consolidated


async def _identify_windows(audio, windows: list[tuple[int, int]]) -> list[dict]:
    """Identifica música em cada janela usando ShazamIO."""
    from shazamio import Shazam

    shazam = Shazam()
    results = []

    for i, (start_ms, end_ms) in enumerate(windows):
        chunk = audio[start_ms:end_ms]

        # Salva chunk como arquivo temporário
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
            chunk.export(tmp_path, format="wav")

        try:
            resp = await shazam.recognize(tmp_path)

            track = resp.get("track")
            if track:
                results.append({
                    "t_start": round(start_ms / 1000, 2),
                    "t_end": round(end_ms / 1000, 2),
                    "track_name": track.get("title"),
                    "artist": track.get("subtitle"),
                    "genre": _extract_genre(track),
                    "cover_art_url": _extract_cover(track),
                    "shazam_url": track.get("url"),
                    "confidence": 1.0,
                    "source": "shazamio",
                })
                t_s = round(start_ms / 1000, 1)
                t_e = round(end_ms / 1000, 1)
                print(f"      [{t_s}s-{t_e}s] {track.get('title')} — {track.get('subtitle')}")
            else:
                results.append({
                    "t_start": round(start_ms / 1000, 2),
                    "t_end": round(end_ms / 1000, 2),
                    "track_name": None,
                    "artist": None,
                    "genre": None,
                    "cover_art_url": None,
                    "shazam_url": None,
                    "confidence": 0.0,
                    "source": "shazamio",
                })
        except Exception as e:
            print(f"      ⚠️  Shazam erro na janela {i}: {e}")
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    return results


def _extract_genre(track: dict) -> Optional[str]:
    """Extrai gênero dos metadados do Shazam."""
    genres = track.get("genres", {})
    primary = genres.get("primary")
    if primary:
        return primary
    # Fallback: sections metadata
    for section in track.get("sections", []):
        if section.get("type") == "SONG":
            metadata = section.get("metadata", [])
            for m in metadata:
                if m.get("title", "").lower() == "genre":
                    return m.get("text")
    return None


def _extract_cover(track: dict) -> Optional[str]:
    """Extrai URL da capa do álbum."""
    images = track.get("images", {})
    return images.get("coverarthq") or images.get("coverart")


def _consolidate(raw: list[dict]) -> list[dict]:
    """
    Mescla janelas consecutivas que identificaram a mesma faixa.
    Resultado: 1 evento por trecho contínuo de música.
    """
    if not raw:
        return []

    # Filtra só as que tiveram match
    matched = [r for r in raw if r["track_name"] is not None]
    if not matched:
        return []

    # Ordena por tempo
    matched.sort(key=lambda r: r["t_start"])

    consolidated = []
    current = dict(matched[0])

    for r in matched[1:]:
        # Mesma faixa e janelas se sobrepõem ou são adjacentes (gap < 3s)?
        same_track = (
            r["track_name"] == current["track_name"]
            and r["artist"] == current["artist"]
        )
        adjacent = r["t_start"] <= current["t_end"] + 3.0

        if same_track and adjacent:
            # Extende o evento atual
            current["t_end"] = max(current["t_end"], r["t_end"])
        else:
            consolidated.append(current)
            current = dict(r)

    consolidated.append(current)
    return consolidated


def identify_segment(audio_path: str, t_start: float, t_end: float) -> dict:
    """
    Identifica música em um segmento específico do áudio.

    Returns:
        {
          success:       bool
          track_name:    str | None
          artist:        str | None
          genre:         str | None
          confidence:    float | None
          error:         str | None
          status:        'ok' | 'no_match' | 'error'
        }
    """
    if not audio_path or not os.path.exists(audio_path):
        return {
            "success": False, "track_name": None, "artist": None,
            "genre": None, "confidence": None,
            "error": "Arquivo não encontrado", "status": "error",
        }

    try:
        from pydub import AudioSegment
        from shazamio import Shazam
    except ImportError:
        return {
            "success": False, "track_name": None, "artist": None,
            "genre": None, "confidence": None,
            "error": "shazamio ou pydub não instalado", "status": "error",
        }

    try:
        audio = AudioSegment.from_file(audio_path)
        chunk = audio[int(t_start * 1000):int(t_end * 1000)]

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
            chunk.export(tmp_path, format="wav")

        async def _recognize():
            shazam = Shazam()
            return await shazam.recognize(tmp_path)

        resp = asyncio.run(_recognize())
        os.unlink(tmp_path)

        track = resp.get("track")
        if track:
            return {
                "success": True,
                "track_name": track.get("title"),
                "artist": track.get("subtitle"),
                "genre": _extract_genre(track),
                "confidence": 1.0,
                "error": None,
                "status": "ok",
            }
        return {
            "success": True, "track_name": None, "artist": None,
            "genre": None, "confidence": None,
            "error": None, "status": "no_match",
        }
    except Exception as e:
        return {
            "success": False, "track_name": None, "artist": None,
            "genre": None, "confidence": None,
            "error": str(e), "status": "error",
        }
