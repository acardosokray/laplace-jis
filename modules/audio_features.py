"""
modules/audio_features.py — Extrai features de áudio por segmento via librosa.

Features por segmento de tempo (alinhado com os segmentos do Whisper):
  volume_db:      RMS em dB — quão alto o locutor está falando
  pitch_hz:       frequência fundamental mediana (f0) — tom de voz
  energy:         energia RMS normalizada 0-1 em relação ao áudio inteiro
  speech_rate:    palavras por segundo (calculado via texto + timestamps do Whisper)
  voice_register: inferido das features acima (normal | agitado | grito | sussurro | acelerado)

Pré-requisito: pip install librosa soundfile
Se librosa não estiver instalado, retorna lista vazia com aviso — não quebra o pipeline.
"""

import os
from typing import Optional


# ── Parâmetros da extração de pitch ──────────────────────────────────
# Taxa de amostragem original do arquivo, f0 limitada ao intervalo C2–C7.
# Gravados junto com cada medição na coluna `pitch_method`.
PITCH_SR = None            # None preserva a taxa original do áudio
PITCH_FMIN_NOTA = "C2"     # ~65 Hz
PITCH_FMAX_NOTA = "C7"     # ~2093 Hz
PITCH_METHOD = "pyin_srorig_fmax2093"



def extract_features(audio_path: str, segments: list[dict]) -> list[dict]:
    """
    Para cada segmento [{start, end, text}, ...], extrai features do áudio.

    Args:
        audio_path: caminho do arquivo de áudio (MP3/WAV).
        segments:   segmentos do Whisper com start, end, text.

    Returns:
        Lista de dicts com features por segmento. Vazia se librosa não disponível.
    """
    if not audio_path or not os.path.exists(audio_path):
        return []

    if not segments:
        return []

    try:
        import librosa
        import numpy as np
    except ImportError:
        print("   ⚠️  librosa não instalado — audio_features pulado.")
        print("        Para instalar: pip install librosa soundfile")
        return []

    try:
        y, sr = librosa.load(audio_path, sr=PITCH_SR, mono=True)
    except Exception as e:
        print(f"   ⚠️  Erro ao carregar áudio para análise de features: {e}")
        return []

    # RMS global para normalização de energia
    global_rms = float(np.sqrt(np.mean(y ** 2))) or 1e-9

    results = []
    for seg in segments:
        t_start = float(seg.get("start", 0))
        t_end   = float(seg.get("end",   0))
        text    = seg.get("text", "") or ""
        dur     = max(t_end - t_start, 0.01)

        s_start = int(t_start * sr)
        s_end   = int(t_end   * sr)
        chunk   = y[s_start:s_end]

        if len(chunk) < sr * 0.1:   # segmento < 100ms — pula
            continue

        # ── Volume RMS em dB ───────────────────────────────────────────────
        rms = float(np.sqrt(np.mean(chunk ** 2)))
        volume_db = float(librosa.amplitude_to_db(
            np.array([max(rms, 1e-9)])
        )[0])

        # ── Energia normalizada (0-1) ──────────────────────────────────────
        energy = min(rms / global_rms, 1.0)

        # ── Pitch (f0) via pyin ────────────────────────────────────────────
        pitch_hz: Optional[float] = None
        try:
            f0, voiced_flag, _ = librosa.pyin(
                chunk,
                sr=sr,
                fmin=librosa.note_to_hz(PITCH_FMIN_NOTA),
                fmax=librosa.note_to_hz(PITCH_FMAX_NOTA),
            )
            if f0 is not None:
                voiced_f0 = f0[voiced_flag & ~np.isnan(f0)]
                if len(voiced_f0) > 0:
                    pitch_hz = float(np.median(voiced_f0))
        except Exception:
            pass   # pyin pode falhar em chunks muito curtos

        # ── Speech rate: palavras por segundo ─────────────────────────────
        word_count  = len(text.split()) if text.strip() else 0
        speech_rate = round(word_count / dur, 2)

        # ── Voice register inferido ────────────────────────────────────────
        voice_register = _infer_register(pitch_hz, volume_db, speech_rate)

        results.append({
            "t_start":        round(t_start, 2),
            "t_end":          round(t_end,   2),
            "volume_db":      round(volume_db, 1),
            "pitch_hz":       round(pitch_hz, 1) if pitch_hz is not None else None,
            "energy":         round(energy, 4),
            "speech_rate":    speech_rate,
            "voice_register": voice_register,
        })

    for f in results:
        f["pitch_method"] = PITCH_METHOD
    return results


def _infer_register(
    pitch_hz: Optional[float],
    volume_db: float,
    speech_rate: float,
) -> str:
    """
    Infere registro vocal a partir das features numéricas.

    Regras (auditáveis — cada threshold é explícito):
      grito:     volume_db > -10 dB
      agitado:   pitch_hz > 280 Hz  (voz feminina/infantil agitada ou masculina muito aguda)
      sussurro:  volume_db < -35 dB
      acelerado: speech_rate > 4.5 palavras/segundo
      normal:    nenhum dos acima

    Evidence está implícita nas features armazenadas — não precisa de campo extra.
    """
    if volume_db > -10:
        return "grito"
    if volume_db < -35:
        return "sussurro"
    if pitch_hz is not None and pitch_hz > 280:
        return "agitado"
    if speech_rate > 4.5:
        return "acelerado"
    return "normal"
