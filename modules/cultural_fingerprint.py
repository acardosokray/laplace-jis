"""
modules/cultural_fingerprint.py — Objeto estático comparável entre vídeos.

Agrega todos os dados já salvos no banco em uma única linha por vídeo.
Este é o objeto que permite comparar vídeos de contexto cultural semelhante.

Campos calculados:
  speech_presence_pct  — % do vídeo com fala detectada (Whisper)
  music_presence_pct   — % com música (Demucs/ShazamIO via timeline_events)

ATENÇÃO — semântica de presença NÃO-EXCLUSIVA:
  speech_presence_pct e music_presence_pct medem, cada um, a fração do vídeo
  em que a respectiva fonte foi detectada. Whisper (voz) e Demucs (trilha
  instrumental) operam em modelos independentes sobre o mesmo áudio. Por isso,
  em vídeos com voz cantada ou narração sobre música, os dois podem coexistir
  no MESMO segmento temporal e a soma pode ultrapassar 100%. Isso não é bug:
  é a semântica natural da medida. Se precisar de frações mutuamente exclusivas
  (que somem 100%), é preciso uma camada adicional de classificação por janela
  dominante — não implementada.
  avg_pitch         — tom médio da voz (Hz)
  avg_volume_db     — volume médio (dB)
  question_ratio    — fração dos segmentos classificados como pergunta
  cta_ratio         — fração classificados como chamada_ação
  attention_ratio   — fração classificados como chamada_atenção
  top_5_words       — 5 palavras mais frequentes (excluindo stopwords)
  dominant_register — registro linguístico predominante (gíria/informal/formal/neutro)
  music_presence          — bool: o vídeo tem música identificada?
  comment_sentiment_avg   — média ponderada do sentimento dos comentários (-1 a +1)
  comment_sentiment_std   — desvio padrão do sentimento (dispersão de opinião)
  comment_positive_ratio  — fração de comentários positivos
  comment_negative_ratio  — fração de comentários negativos
  comment_top_words       — top 10 palavras dos comentários (excluindo stopwords)
  emotion_dominant              — label GoEmotions bruta dominante na fala
  emotion_distribution          — JSON das labels GoEmotions ativadas na fala (pct + n)
  audience_emotion_dominant     — label GoEmotions bruta dominante nos comentários
  audience_emotion_distribution — JSON das labels GoEmotions ativadas nos comentários
  top_emojis                    — top 5 emojis dos comentários no formato "😂×23 | 😢×8 | …"
"""

import sqlite3
from datetime import datetime, timezone
from typing import Optional


def calculate(
    conn: sqlite3.Connection,
    video_id: str,
    duration_seconds: Optional[int],
) -> dict:
    """
    Lê as tabelas já populadas e calcula o fingerprint.

    Args:
        conn:             conexão SQLite aberta
        video_id:         ID do vídeo
        duration_seconds: duração total do vídeo em segundos (para calcular %)

    Returns:
        Dict com todos os campos do cultural_fingerprint, pronto para INSERT.
    """
    dur = float(duration_seconds or 0)

    def pct(secs: float) -> Optional[float]:
        if dur <= 0:
            return None
        return round((secs / dur) * 100, 1)

    # ── Percentuais de timeline ────────────────────────────────────────────
    speech_secs  = _sum_event_duration(conn, video_id, "speech")
    music_secs   = _sum_event_duration(conn, video_id, "music")

    # ── Médias de pitch e volume (da tabela audio_features) ───────────────
    af_row = conn.execute("""
        SELECT AVG(pitch_hz), AVG(volume_db)
        FROM   audio_features
        WHERE  video_id = ?
    """, (video_id,)).fetchone()
    avg_pitch     = round(af_row[0], 1) if af_row and af_row[0] is not None else None
    avg_volume_db = round(af_row[1], 1) if af_row and af_row[1] is not None else None

    # ── Mediana do pitch (usada na linha tracejada do Painel 1 da viz) ─────
    pitch_rows = conn.execute("""
        SELECT pitch_hz FROM audio_features
        WHERE video_id = ? AND pitch_hz IS NOT NULL
        ORDER BY pitch_hz
    """, (video_id,)).fetchall()
    median_pitch_hz = None
    if pitch_rows:
        vals = [r[0] for r in pitch_rows]
        n = len(vals)
        if n % 2 == 1:
            median_pitch_hz = round(vals[n // 2], 1)
        else:
            median_pitch_hz = round((vals[n // 2 - 1] + vals[n // 2]) / 2.0, 1)

    # ── Média de sentiment_score da fala (usada na linha avg do Painel 2) ─
    # Distinta de comment_sentiment_avg (que é média dos comentários).
    sa_row = conn.execute("""
        SELECT AVG(sentiment_score)
        FROM   speech_analysis
        WHERE  video_id = ? AND sentiment_score IS NOT NULL
    """, (video_id,)).fetchone()
    speech_sent_avg = round(sa_row[0], 4) if sa_row and sa_row[0] is not None else None

    # ── Ratios de utterance (da tabela speech_analysis) ───────────────────
    total_segs = conn.execute(
        "SELECT COUNT(*) FROM speech_analysis WHERE video_id=?", (video_id,)
    ).fetchone()[0]

    def ratio(utype: str) -> float:
        if total_segs == 0:
            return 0.0
        n = conn.execute(
            "SELECT COUNT(*) FROM speech_analysis WHERE video_id=? AND utterance_type=?",
            (video_id, utype),
        ).fetchone()[0]
        return round(n / total_segs, 3)

    question_ratio  = ratio("pergunta")
    cta_ratio       = ratio("chamada_ação")
    attention_ratio = ratio("chamada_atenção")

    # ── Top 5 palavras (excluindo stopwords da tabela lexical_profile) ─────
    lex_rows = conn.execute("""
        SELECT word
        FROM   lexical_profile
        WHERE  video_id  = ?
          AND  category != 'stopword'
        ORDER  BY frequency DESC
        LIMIT  5
    """, (video_id,)).fetchall()
    top_5_words = ", ".join(r[0] for r in lex_rows) or None

    # ── Registro linguístico dominante ─────────────────────────────────────
    reg_row = conn.execute("""
        SELECT language_register, COUNT(*) AS n
        FROM   speech_analysis
        WHERE  video_id = ?
        GROUP  BY language_register
        ORDER  BY n DESC
        LIMIT  1
    """, (video_id,)).fetchone()
    dominant_register = reg_row[0] if reg_row else None

    # ── Presença de música ─────────────────────────────────────────────────
    music_event_count = conn.execute(
        "SELECT COUNT(*) FROM music_events WHERE video_id=?", (video_id,)
    ).fetchone()[0]
    music_presence = int(music_event_count > 0 or music_secs > 0)

    # ── Sentimento de comentários (da tabela comment_sentiment_agg) ────────
    cs_row = conn.execute("""
        SELECT sentiment_avg, sentiment_std, positive_ratio, negative_ratio, top_words
        FROM   comment_sentiment_agg
        WHERE  video_id = ?
    """, (video_id,)).fetchone()

    if cs_row:
        comment_sentiment_avg  = cs_row[0]
        comment_sentiment_std  = cs_row[1]
        comment_positive_ratio = cs_row[2]
        comment_negative_ratio = cs_row[3]
        comment_top_words      = cs_row[4]
    else:
        comment_sentiment_avg  = None
        comment_sentiment_std  = None
        comment_positive_ratio = None
        comment_negative_ratio = None
        comment_top_words      = None

    # ── Emoções da fala (labels GoEmotions brutas, agregadas) ─────────────
    from modules.emotion import build_emotion_distribution, dominant_emotion
    import json

    speech_emotion_rows = conn.execute(
        "SELECT emotion_raw FROM speech_analysis WHERE video_id = ?", (video_id,)
    ).fetchall()
    speech_raw_labels = [r[0] for r in speech_emotion_rows]

    if speech_raw_labels:
        emotion_dominant     = dominant_emotion(speech_raw_labels)
        emotion_distribution = json.dumps(
            build_emotion_distribution(speech_raw_labels), ensure_ascii=False,
        )
    else:
        emotion_dominant     = None
        emotion_distribution = None

    # ── Emoções da audiência (lê o JSON já agregado de comment_sentiment_agg) ─
    aud_row = conn.execute(
        "SELECT emotion_distribution FROM comment_sentiment_agg WHERE video_id = ?",
        (video_id,),
    ).fetchone()
    audience_emotion_distribution = aud_row[0] if aud_row else None

    # dominant a partir do JSON agregado (primeira chave = maior pct)
    audience_emotion_dominant = None
    if audience_emotion_distribution:
        try:
            parsed = json.loads(audience_emotion_distribution)
            for label, v in parsed.items():
                if v.get("n", 0) > 0:
                    audience_emotion_dominant = label
                    break
        except Exception:
            pass

    # ── Top 5 emojis (agregado de emoji_stats) ────────────────────────────
    emoji_rows = conn.execute("""
        SELECT emoji, count
        FROM   emoji_stats
        WHERE  video_id = ?
        ORDER  BY count DESC
        LIMIT  5
    """, (video_id,)).fetchall()
    if emoji_rows:
        top_emojis = " | ".join(f"{e[0]}×{e[1]}" for e in emoji_rows if e[0])
    else:
        top_emojis = None

    return {
        "video_id":                     video_id,
        "calculated_at":                datetime.now(timezone.utc).isoformat(),
        "speech_presence_pct":          pct(speech_secs),
        "music_presence_pct":           pct(music_secs),
        "avg_pitch":                    avg_pitch,
        "avg_volume_db":                avg_volume_db,
        "speech_sent_avg":              speech_sent_avg,
        "median_pitch_hz":              median_pitch_hz,
        "question_ratio":               question_ratio,
        "cta_ratio":                    cta_ratio,
        "attention_ratio":              attention_ratio,
        "top_5_words":                  top_5_words,
        "dominant_register":            dominant_register,
        "music_presence":               music_presence,
        "comment_sentiment_avg":        comment_sentiment_avg,
        "comment_sentiment_std":        comment_sentiment_std,
        "comment_positive_ratio":       comment_positive_ratio,
        "comment_negative_ratio":       comment_negative_ratio,
        "comment_top_words":            comment_top_words,
        "emotion_dominant":             emotion_dominant,
        "emotion_distribution":         emotion_distribution,
        "audience_emotion_dominant":    audience_emotion_dominant,
        "audience_emotion_distribution": audience_emotion_distribution,
        "top_emojis":                   top_emojis,
    }


def _sum_event_duration(
    conn: sqlite3.Connection, video_id: str, event_type: str
) -> float:
    row = conn.execute("""
        SELECT COALESCE(SUM(t_end - t_start), 0)
        FROM   timeline_events
        WHERE  video_id    = ?
          AND  event_type  = ?
    """, (video_id, event_type)).fetchone()
    return float(row[0]) if row else 0.0
