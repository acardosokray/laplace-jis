"""
modules/transcript.py — Transcrição de áudio via faster-whisper (local, offline).

Usa o modelo large-v3 com detecção automática de CUDA (RTX 4050).
Retorna texto completo + segmentos com timestamps para a curva emocional Γ_i do Laplace.

NOTA: O modelo Whisper é mantido como singleton (_model_cache) para evitar segfault
no destructor do ctranslate2 no Windows. A VRAM só é liberada explicitamente via
unload_model() — chamado pelo pipeline após a transcrição.
"""

import os
import sys
from config import WHISPER_MODEL


_cuda_paths_added = False


def _add_cuda_dll_paths() -> None:
    """
    No Windows, injeta os diretórios das DLLs CUDA (instaladas via pip) em:
      1. os.add_dll_directory — afeta imports de extensões Python
      2. os.environ["PATH"]  — afeta LoadLibraryA interno do ctranslate2
    Deve ser chamado ANTES de qualquer import de ctranslate2/faster-whisper.

    IDEMPOTÊNCIA: usa flag global `_cuda_paths_added` para garantir que o PATH
    só é prepended uma vez por processo. Sem isso, em batch.py (várias chamadas
    sequenciais entre módulos), o PATH crescia sem dedup e estourava o limite
    do Windows de 32767 chars (CreateProcess fail) após ~33 vídeos.
    """
    global _cuda_paths_added
    if _cuda_paths_added or sys.platform != "win32":
        return

    candidates: set[str] = set()
    venv_root = os.path.dirname(os.path.dirname(sys.executable))
    candidates.add(os.path.join(venv_root, "Lib", "site-packages"))

    try:
        import site
        for sp in site.getsitepackages():
            candidates.add(sp)
    except Exception:
        pass

    dll_dirs: list[str] = []
    for sp in candidates:
        # Pacotes nvidia-*-cu12 instalados separadamente.
        nvidia_dir = os.path.join(sp, "nvidia")
        if os.path.isdir(nvidia_dir):
            for package in os.listdir(nvidia_dir):
                bin_dir = os.path.join(nvidia_dir, package, "bin")
                if os.path.isdir(bin_dir):
                    dll_dirs.append(bin_dir)

        # As mesmas DLLs vêm embutidas na roda do torch com CUDA. Instalar
        # `torch --index-url .../cu121` não traz os pacotes nvidia-* avulsos,
        # e sem este caminho o ctranslate2 não acha cublas64_12.dll.
        torch_lib = os.path.join(sp, "torch", "lib")
        if os.path.isdir(torch_lib):
            dll_dirs.append(torch_lib)

    for d in dll_dirs:
        try:
            os.add_dll_directory(d)
        except Exception:
            pass

    if dll_dirs:
        os.environ["PATH"] = ";".join(dll_dirs) + ";" + os.environ.get("PATH", "")

    _cuda_paths_added = True


# Garante que as DLLs CUDA estejam disponíveis antes de qualquer import
_add_cuda_dll_paths()

# ── Singleton do modelo Whisper ──────────────────────────────────────────────
# O destructor do ctranslate2 causa segfault no Windows ao liberar CUDA.
# Estratégia: manter modelo vivo para SEMPRE — nunca deixar o GC coletar.
# A VRAM só é liberada quando o processo termina (o OS cuida disso).
# Para liberar VRAM para o classificador de sentimento, usamos unload_model() que move para
# uma lista "cemitério" (evita GC) e limpa o cache CUDA via torch.
_model_cache = None
_model_graveyard = []  # impede GC de destruir modelos antigos


def _detect_device() -> tuple[str, str]:
    """
    Detecta se CUDA está disponível para Whisper.
    Retorna (device, compute_type).

    VRAM: large-v3 usa ~3 GB. Demucs já deve ter liberado antes desta chamada.
    """
    try:
        import ctranslate2
        n = ctranslate2.get_cuda_device_count()
        if n > 0:
            return "cuda", "int8_float16"
    except Exception:
        pass
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda", "int8_float16"
    except ImportError:
        pass
    return "cpu", "int8"


def unload_model() -> None:
    """
    Libera VRAM do Whisper de forma segura (Windows ctranslate2 segfault workaround).

    Move o modelo para _model_graveyard (impede GC de chamar o destructor)
    e limpa o cache CUDA via torch. A VRAM principal é liberada pelo
    torch.cuda.empty_cache(), mas o objeto C++ permanece vivo (sem destructor).
    """
    global _model_cache
    if _model_cache is None:
        return
    _model_graveyard.append(_model_cache)
    _model_cache = None
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        print("   Whisper: VRAM liberada (modelo preservado).", flush=True)
    except Exception:
        print("   Whisper: cache CUDA limpo.", flush=True)


def get_transcript(video_id: str, audio_path: str) -> dict:
    """
    Transcreve o áudio do vídeo usando faster-whisper.

    Args:
        video_id:   ID do vídeo (para referência).
        audio_path: Caminho do arquivo MP3.

    Returns:
        {
          text:      str  — texto completo concatenado,
          language:  str  — idioma detectado pelo Whisper,
          segments:  list — [{start: float, end: float, text: str}, ...],
          is_auto:   bool — sempre True (gerado automaticamente),
          available: bool,
          error:     str | None,
          device:    str  — "cuda" ou "cpu",
          model:     str  — nome do modelo usado,
        }
    """
    global _model_cache

    base = {
        "text": None, "language": None, "segments": [],
        "is_auto": True, "available": False,
        "error": None, "device": None, "model": WHISPER_MODEL,
    }

    if not audio_path or not os.path.exists(audio_path):
        base["error"] = f"Arquivo de áudio não encontrado: {audio_path}"
        return base

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        base["error"] = "faster-whisper não instalado. Execute: pip install faster-whisper"
        return base

    device, compute_type = _detect_device()
    base["device"] = device

    try:
        # Reutiliza modelo se já carregado (singleton)
        if _model_cache is None:
            print(f"   Whisper: carregando {WHISPER_MODEL} em {device} ({compute_type})...", flush=True)
            _model_cache = WhisperModel(WHISPER_MODEL, device=device, compute_type=compute_type)
            print("   Whisper: modelo carregado.", flush=True)
        else:
            print(f"   Whisper: reutilizando modelo em cache ({device}).", flush=True)

        segments_iter, info = _model_cache.transcribe(
            audio_path,
            language="pt",
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )

        segments = []
        parts = []
        for seg in segments_iter:
            text = seg.text.strip()
            if not text:
                continue
            segments.append({
                "start": round(seg.start, 2),
                "end":   round(seg.end, 2),
                "text":  text,
            })
            parts.append(text)

        return {
            "text":      " ".join(parts),
            "language":  info.language,
            "segments":  segments,
            "is_auto":   True,
            "available": bool(segments),
            "error":     None if segments else "Nenhum segmento extraído pelo Whisper",
            "device":    device,
            "model":     WHISPER_MODEL,
        }
    except Exception as e:
        base["error"] = f"Erro na transcrição: {e}"
        return base


def transcript_summary(transcript_data: dict) -> str:
    """Linha de status resumida para o terminal."""
    if not transcript_data.get("available"):
        return f"⚠️  Transcrição indisponível: {transcript_data.get('error')}"
    device = transcript_data.get("device", "?")
    model  = transcript_data.get("model",  "?")
    lang   = transcript_data.get("language", "?")
    n_segs = len(transcript_data.get("segments", []))
    words  = len((transcript_data.get("text") or "").split())
    return (
        f"✅ Whisper {model} [{device}] — "
        f"idioma: {lang} — {words:,} palavras — {n_segs} segmentos"
    )
