"""
modules/database.py — Persistência em SQLite.

As 16 tabelas são definidas em schema.sql, na raiz do projeto.
"""

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import DB_PATH

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


def create_tables(conn: sqlite3.Connection) -> None:
    """Cria as 16 tabelas e seus índices a partir de schema.sql."""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


def get_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.row_factory = sqlite3.Row
    return conn


def video_exists(conn: sqlite3.Connection, video_id: str) -> bool:
    """Este vídeo já foi processado — pode ser pulado com segurança?

    A checagem é por `speech_analysis`, não por `videos`: uma linha em
    `videos` sem análise linguística é um registro incompleto, e seria
    pulado para sempre se a mera presença do vídeo bastasse.
    """
    row = conn.execute(
        "SELECT 1 FROM speech_analysis WHERE video_id = ? LIMIT 1",
        (video_id,)).fetchone()
    return row is not None


def insert_video(conn: sqlite3.Connection, data: dict) -> None:
    """Insere o vídeo se ainda não existir."""
    conn.execute("""
        INSERT OR IGNORE INTO videos
            (video_id, platform, channel_id, channel_name, channel_pseudo, title, description,
             publish_date, duration_seconds, thumbnail_url,
             tags, hashtags, language, country, format, category_id)
        VALUES
            (:video_id, :platform, :channel_id, :channel_name, :channel_pseudo, :title, :description,
             :publish_date, :duration_seconds, :thumbnail_url,
             :tags, :hashtags, :language, :country, :format, :category_id)
    """, data)
    conn.commit()


def insert_metrics(conn: sqlite3.Connection, data: dict) -> None:
    collected_at = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT INTO metrics (video_id, collected_at, views, likes, comments)
        VALUES (:video_id, :collected_at, :views, :likes, :comments)
    """, {**data, "collected_at": collected_at})
    conn.commit()


def insert_comments(conn: sqlite3.Connection, video_id: str, comments: list[dict]) -> None:
    """Substitui todos os comentários do vídeo pela coleta mais recente."""
    collected_at = datetime.now(timezone.utc).isoformat()
    conn.execute("DELETE FROM comments WHERE video_id = ?", (video_id,))
    conn.executemany("""
        INSERT INTO comments
            (video_id, collected_at, comment_id, author_pseudo, text, likes, reply_count, published_at)
        VALUES
            (:video_id, :collected_at, :comment_id, :author_pseudo, :text, :likes, :reply_count, :published_at)
    """, [{**c, "video_id": video_id, "collected_at": collected_at} for c in comments])
    conn.commit()


def insert_transcript(conn: sqlite3.Connection, video_id: str, transcript: dict) -> None:
    """Insere ou substitui a transcrição completa (texto + metadados Whisper)."""
    if not transcript.get("available"):
        return
    collected_at = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO transcripts
            (video_id, collected_at, language, is_auto, text, whisper_model, whisper_device)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        video_id,
        collected_at,
        transcript.get("language"),
        1,
        transcript.get("text"),
        transcript.get("model"),
        transcript.get("device"),
    ))
    conn.commit()


def insert_transcript_segments(conn: sqlite3.Connection, video_id: str, segments: list[dict]) -> None:
    """
    Insere os segmentos temporais da transcrição.
    Cada segmento = {start, end, text} — base da curva emocional Γ_i do Laplace.
    """
    if not segments:
        return
    conn.execute("DELETE FROM transcript_segments WHERE video_id = ?", (video_id,))
    conn.executemany("""
        INSERT INTO transcript_segments (video_id, seg_index, start_sec, end_sec, text)
        VALUES (?, ?, ?, ?, ?)
    """, [
        (video_id, i, seg["start"], seg["end"], seg["text"])
        for i, seg in enumerate(segments)
    ])
    conn.commit()


def insert_emoji_stats(conn: sqlite3.Connection, video_id: str, top_emojis: list) -> None:
    if not top_emojis:
        return
    collected_at = datetime.now(timezone.utc).isoformat()
    conn.execute("DELETE FROM emoji_stats WHERE video_id = ?", (video_id,))
    conn.executemany("""
        INSERT INTO emoji_stats (video_id, collected_at, emoji, count)
        VALUES (?, ?, ?, ?)
    """, [(video_id, collected_at, emoji, count) for emoji, count in top_emojis])
    conn.commit()


def insert_audio_file(conn: sqlite3.Connection, video_id: str, audio_info: dict) -> None:
    """Registra o arquivo de áudio baixado."""
    if not audio_info.get("success"):
        return
    downloaded_at = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO audio_files
            (video_id, downloaded_at, file_path, format, file_size_mb)
        VALUES (?, ?, ?, 'mp3', ?)
    """, (
        video_id,
        downloaded_at,
        audio_info.get("path"),
        audio_info.get("file_size_mb"),
    ))
    conn.commit()


def insert_timeline_events_from_transcript(
    conn: sqlite3.Connection, video_id: str, segments: list[dict]
) -> None:
    """Popula timeline_events com os segmentos de fala do Whisper."""
    if not segments:
        return
    conn.execute(
        "DELETE FROM timeline_events WHERE video_id = ? AND source = 'whisper'", (video_id,)
    )
    conn.executemany("""
        INSERT INTO timeline_events (video_id, t_start, t_end, event_type, value, confidence, source)
        VALUES (?, ?, ?, 'speech', ?, 1.0, 'whisper')
    """, [(video_id, seg["start"], seg["end"], seg["text"]) for seg in segments])
    conn.commit()


def insert_speech_analysis(
    conn: sqlite3.Connection, video_id: str, segments: list[dict]
) -> None:
    """Substitui análise linguística do vídeo.

    Segmentos podem conter opcionalmente emotion_raw/emotion_score quando o
    pipeline de emoções já tiver rodado; caso contrário ficam NULL e podem ser
    preenchidos depois por update_speech_emotions.
    """
    if not segments:
        return
    conn.execute("DELETE FROM speech_analysis WHERE video_id = ?", (video_id,))
    conn.executemany("""
        INSERT INTO speech_analysis
            (video_id, t_start, t_end, text, utterance_type,
             classification_evidence, classification_method, language_register, sentiment,
             sentiment_score, sentiment_conf, emotion_score, emotion_raw)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [(
        video_id,
        s["t_start"], s["t_end"], s["text"],
        s["utterance_type"], s["classification_evidence"],
        s["classification_method"], s["language_register"], s["sentiment"],
        s.get("sentiment_score"), s.get("sentiment_conf"),
        s.get("emotion_score"), s.get("emotion_raw"),
    ) for s in segments])
    conn.commit()


def insert_lexical_profile(
    conn: sqlite3.Connection, video_id: str, words: list[dict]
) -> None:
    """Substitui perfil lexical do vídeo."""
    if not words:
        return
    conn.execute("DELETE FROM lexical_profile WHERE video_id = ?", (video_id,))
    conn.executemany("""
        INSERT INTO lexical_profile (video_id, word, frequency, category, first_occurrence_t)
        VALUES (?, ?, ?, ?, ?)
    """, [(video_id, w["word"], w["frequency"], w["category"], w["first_occurrence_t"])
          for w in words])
    conn.commit()


def insert_audio_features(
    conn: sqlite3.Connection, video_id: str, features: list[dict]
) -> None:
    """Substitui features de áudio do vídeo."""
    if not features:
        return
    conn.execute("DELETE FROM audio_features WHERE video_id = ?", (video_id,))
    conn.executemany("""
        INSERT INTO audio_features
            (video_id, t_start, t_end, volume_db, pitch_hz, energy,
             speech_rate, voice_register)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, [(
        video_id,
        f["t_start"], f["t_end"], f["volume_db"], f["pitch_hz"],
        f["energy"], f["speech_rate"], f["voice_register"],
    ) for f in features])
    conn.commit()


def insert_music_events(
    conn: sqlite3.Connection, video_id: str, events: list[dict]
) -> None:
    """Substitui eventos musicais do vídeo (ShazamIO/ACRCloud)."""
    if not events:
        return
    conn.execute("DELETE FROM music_events WHERE video_id = ?", (video_id,))
    conn.executemany("""
        INSERT INTO music_events
            (video_id, t_start, t_end, track_name, artist, confidence, source)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, [(
        video_id,
        e["t_start"], e["t_end"],
        e.get("track_name"), e.get("artist"),
        e.get("confidence", 1.0), e.get("source", "shazamio"),
    ) for e in events])
    conn.commit()


def insert_timeline_events_music(
    conn: sqlite3.Connection, video_id: str, music_events: list[dict]
) -> None:
    """Insere eventos de música na timeline a partir dos resultados do ShazamIO."""
    if not music_events:
        return
    conn.execute(
        "DELETE FROM timeline_events WHERE video_id = ? AND source = 'shazamio'",
        (video_id,),
    )
    conn.executemany("""
        INSERT INTO timeline_events (video_id, t_start, t_end, event_type, value, confidence, source)
        VALUES (?, ?, ?, 'music', ?, ?, 'shazamio')
    """, [(
        video_id, e["t_start"], e["t_end"],
        f"{e.get('track_name', '?')} — {e.get('artist', '?')}",
        e.get("confidence", 1.0),
    ) for e in music_events])
    conn.commit()


def insert_comment_sentiments(
    conn: sqlite3.Connection, video_id: str, individual: list[dict]
) -> None:
    """Substitui sentimentos individuais dos comentários do vídeo.

    Cada comentário pode trazer opcionalmente emotion_raw/emotion_score.
    """
    if not individual:
        return
    conn.execute("DELETE FROM comment_sentiments WHERE video_id = ?", (video_id,))
    conn.executemany("""
        INSERT INTO comment_sentiments
            (video_id, comment_id, text, likes, sentiment, sentiment_score,
             classification_evidence, emotion_score, emotion_raw)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [(
        video_id,
        c["comment_id"], c["text"], c.get("likes", 0),
        c["sentiment"], c["sentiment_score"], c["classification_evidence"],
        c.get("emotion_score"), c.get("emotion_raw"),
    ) for c in individual])
    conn.commit()


def insert_comment_sentiment_agg(
    conn: sqlite3.Connection, video_id: str, agg: dict
) -> None:
    """Insere ou substitui os agregados de sentimento de comentários do vídeo."""
    from datetime import datetime, timezone
    calculated_at = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO comment_sentiment_agg
            (video_id, calculated_at, total_analyzed, positive_ratio, negative_ratio,
             neutral_ratio, sentiment_avg, sentiment_std, top_words, emotion_distribution)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        video_id, calculated_at,
        agg.get("total_analyzed"),
        agg.get("positive_ratio"),
        agg.get("negative_ratio"),
        agg.get("neutral_ratio"),
        agg.get("sentiment_avg"),
        agg.get("sentiment_std"),
        agg.get("top_words"),
        agg.get("emotion_distribution"),
    ))
    conn.commit()


def insert_cultural_fingerprint(conn: sqlite3.Connection, fp: dict) -> None:
    """Insere ou substitui o fingerprint cultural do vídeo."""
    conn.execute("""
        INSERT OR REPLACE INTO cultural_fingerprint
            (video_id, calculated_at, speech_presence_pct, music_presence_pct,
             avg_pitch, avg_volume_db, speech_sent_avg, median_pitch_hz,
             question_ratio, cta_ratio,
             attention_ratio, top_5_words, dominant_register, music_presence,
             comment_sentiment_avg, comment_sentiment_std,
             comment_positive_ratio, comment_negative_ratio, comment_top_words,
             emotion_dominant, emotion_distribution,
             audience_emotion_dominant, audience_emotion_distribution,
             top_emojis)
        VALUES
            (:video_id, :calculated_at, :speech_presence_pct, :music_presence_pct,
             :avg_pitch, :avg_volume_db, :speech_sent_avg, :median_pitch_hz,
             :question_ratio, :cta_ratio,
             :attention_ratio, :top_5_words, :dominant_register, :music_presence,
             :comment_sentiment_avg, :comment_sentiment_std,
             :comment_positive_ratio, :comment_negative_ratio, :comment_top_words,
             :emotion_dominant, :emotion_distribution,
             :audience_emotion_dominant, :audience_emotion_distribution,
             :top_emojis)
     """, fp)
    conn.commit()


def update_speech_emotions(
    conn: sqlite3.Connection, video_id: str, segments: list[dict]
) -> None:
    """Atualiza emotion_raw/emotion_score dos segmentos.

    Cada dict precisa ter t_start, emotion_raw, emotion_score.
    """
    if not segments:
        return
    conn.executemany("""
        UPDATE speech_analysis
        SET    emotion_score = ?, emotion_raw = ?
        WHERE  video_id = ? AND t_start = ?
    """, [(
        s.get("emotion_score"), s.get("emotion_raw"),
        video_id, s["t_start"],
    ) for s in segments])
    conn.commit()


def update_comment_emotions(
    conn: sqlite3.Connection, video_id: str, items: list[dict]
) -> None:
    """Atualiza emotion_raw/emotion_score dos comentários.

    Cada dict precisa ter comment_id e os campos de emoção.
    """
    if not items:
        return
    conn.executemany("""
        UPDATE comment_sentiments
        SET    emotion_score = ?, emotion_raw = ?
        WHERE  video_id = ? AND comment_id = ?
    """, [(
        c.get("emotion_score"), c.get("emotion_raw"),
        video_id, c["comment_id"],
    ) for c in items])
    conn.commit()


def update_comment_sentiment_agg_emotion(
    conn: sqlite3.Connection, video_id: str, emotion_distribution_json: Optional[str],
) -> None:
    """Atualiza apenas o JSON de distribuição de emoções do agregado de comentários."""
    conn.execute("""
        UPDATE comment_sentiment_agg
        SET    emotion_distribution = ?
        WHERE  video_id = ?
    """, (emotion_distribution_json, video_id))
    conn.commit()


def insert_emotion_curve(
    conn: sqlite3.Connection, video_id: str, curve: list[dict]
) -> None:
    """Substitui a curva emocional do vídeo.

    Cada item deve ter segment_id, t_start, t_end, t_start_pct, t_end_pct,
    emotion_raw, emotion_score e segment_text.
    """
    if not curve:
        return
    conn.execute("DELETE FROM emotion_curve WHERE video_id = ?", (video_id,))
    conn.executemany("""
        INSERT INTO emotion_curve
            (video_id, segment_id, t_start, t_end, t_start_pct, t_end_pct,
             emotion_raw, emotion_score, segment_text)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [(
        video_id,
        c["segment_id"], c["t_start"], c["t_end"],
        c.get("t_start_pct"), c.get("t_end_pct"),
        c.get("emotion_raw"), c.get("emotion_score"), c.get("segment_text"),
    ) for c in curve])
    conn.commit()


def speech_emotions_analyzed(conn: sqlite3.Connection, video_id: str) -> bool:
    """True se todos os segmentos de fala do vídeo já têm emotion_raw preenchida."""
    row = conn.execute("""
        SELECT COUNT(*) AS total,
               COUNT(emotion_raw) AS with_emo
        FROM   speech_analysis
        WHERE  video_id = ?
    """, (video_id,)).fetchone()
    if not row or not row[0]:
        return False
    return row[0] == row[1]


def comment_emotions_analyzed(conn: sqlite3.Connection, video_id: str) -> bool:
    """True se todos os comentários analisados já têm emotion_raw preenchida."""
    row = conn.execute("""
        SELECT COUNT(*) AS total,
               COUNT(emotion_raw) AS with_emo
        FROM   comment_sentiments
        WHERE  video_id = ?
    """, (video_id,)).fetchone()
    if not row or not row[0]:
        return False
    return row[0] == row[1]


