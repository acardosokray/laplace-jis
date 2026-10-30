-- Laplace — esquema do banco SQLite: 16 tabelas relacionais.
-- Cada tabela é escrita por uma das 7 etapas do pipeline, indicada no
-- comentário acima dela.


-- ── Etapa 1 — Coleta de metadados e comentários ─────────────────────────────

CREATE TABLE IF NOT EXISTS videos (
    video_id         TEXT PRIMARY KEY,
    platform         TEXT NOT NULL,
    channel_id       TEXT,
    channel_name     TEXT,
    channel_pseudo   TEXT,
    title            TEXT,
    description      TEXT,
    publish_date     TEXT,
    duration_seconds INTEGER,
    thumbnail_url    TEXT,
    tags             TEXT,
    hashtags         TEXT,
    language         TEXT,
    country          TEXT,
    format           TEXT,
    category_id      TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS metrics (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id     TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    views        INTEGER,
    likes        INTEGER,
    comments     INTEGER,
    FOREIGN KEY (video_id) REFERENCES videos(video_id)
);
CREATE INDEX IF NOT EXISTS idx_metrics_video_id ON metrics(video_id);

CREATE TABLE IF NOT EXISTS comments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id     TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    comment_id   TEXT,
    author_pseudo TEXT,
    text         TEXT,
    likes        INTEGER DEFAULT 0,
    reply_count  INTEGER DEFAULT 0,
    published_at TEXT,
    FOREIGN KEY (video_id) REFERENCES videos(video_id)
);
CREATE INDEX IF NOT EXISTS idx_comments_video_id ON comments(video_id);

CREATE TABLE IF NOT EXISTS emoji_stats (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id     TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    emoji        TEXT,
    count        INTEGER,
    FOREIGN KEY (video_id) REFERENCES videos(video_id)
);


-- ── Etapa 2 — Download de áudio e separação de fontes ───────────────────────

CREATE TABLE IF NOT EXISTS audio_files (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id      TEXT NOT NULL UNIQUE,
    downloaded_at TEXT NOT NULL,
    file_path     TEXT NOT NULL,
    format        TEXT DEFAULT 'mp3',
    file_size_mb  REAL,
    FOREIGN KEY (video_id) REFERENCES videos(video_id)
);
CREATE INDEX IF NOT EXISTS idx_audio_video_id ON audio_files(video_id);


-- ── Etapa 3 — Transcrição automática ────────────────────────────────────────

CREATE TABLE IF NOT EXISTS transcripts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id       TEXT NOT NULL UNIQUE,
    collected_at   TEXT NOT NULL,
    language       TEXT,
    is_auto        INTEGER DEFAULT 0,
    text           TEXT,
    whisper_model  TEXT,
    whisper_device TEXT,
    FOREIGN KEY (video_id) REFERENCES videos(video_id)
);

CREATE TABLE IF NOT EXISTS transcript_segments (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id  TEXT NOT NULL,
    seg_index INTEGER NOT NULL,
    start_sec REAL NOT NULL,
    end_sec   REAL NOT NULL,
    text      TEXT,
    FOREIGN KEY (video_id) REFERENCES videos(video_id)
);
CREATE INDEX IF NOT EXISTS idx_segments_video_id ON transcript_segments(video_id);

-- Eixo temporal comum: fala (etapa 3) e música (etapa 7) gravam aqui, e é
-- desta tabela que saem as durações agregadas do fingerprint.
CREATE TABLE IF NOT EXISTS timeline_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id   TEXT NOT NULL,
    t_start    REAL NOT NULL,
    t_end      REAL NOT NULL,
    event_type TEXT NOT NULL,
    value      TEXT,
    confidence REAL,
    source     TEXT,
    FOREIGN KEY (video_id) REFERENCES videos(video_id)
);
CREATE INDEX IF NOT EXISTS idx_timeline_video_id ON timeline_events(video_id);


-- ── Etapa 4 — Análise linguística, semântica e emocional por segmento ───────

CREATE TABLE IF NOT EXISTS speech_analysis (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id                TEXT NOT NULL,
    t_start                 REAL NOT NULL,
    t_end                   REAL NOT NULL,
    text                    TEXT,
    utterance_type          TEXT,
    classification_evidence TEXT,
    classification_method   TEXT,
    language_register       TEXT,
    sentiment               TEXT,
    sentiment_score         REAL,
    sentiment_conf          REAL,
    emotion_raw             TEXT,
    emotion_score           REAL,
    FOREIGN KEY (video_id) REFERENCES videos(video_id)
);
CREATE INDEX IF NOT EXISTS idx_speechanal_video_id ON speech_analysis(video_id);

CREATE TABLE IF NOT EXISTS lexical_profile (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id           TEXT NOT NULL,
    word               TEXT NOT NULL,
    frequency          INTEGER NOT NULL,
    category           TEXT,
    first_occurrence_t REAL,
    FOREIGN KEY (video_id) REFERENCES videos(video_id)
);
CREATE INDEX IF NOT EXISTS idx_lexical_video_id ON lexical_profile(video_id);

-- Fonte primária das análises de sequência narrativa.
CREATE TABLE IF NOT EXISTS emotion_curve (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id      TEXT NOT NULL,
    segment_id    INTEGER NOT NULL,
    t_start       REAL NOT NULL,
    t_end         REAL NOT NULL,
    t_start_pct   REAL,
    t_end_pct     REAL,
    emotion_raw   TEXT,
    emotion_score REAL,
    segment_text  TEXT,
    FOREIGN KEY (video_id) REFERENCES videos(video_id)
);
CREATE INDEX IF NOT EXISTS idx_emocurve_video_id ON emotion_curve(video_id);


-- ── Etapa 5 — Análise prosódica ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS audio_features (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id       TEXT NOT NULL,
    t_start        REAL NOT NULL,
    t_end          REAL NOT NULL,
    volume_db      REAL,
    pitch_hz       REAL,
    energy         REAL,
    speech_rate    REAL,
    voice_register TEXT,
    FOREIGN KEY (video_id) REFERENCES videos(video_id)
);
CREATE INDEX IF NOT EXISTS idx_audiofeats_video_id ON audio_features(video_id);


-- ── Etapa 6 — Sentimento dos comentários da audiência ───────────────────────

CREATE TABLE IF NOT EXISTS comment_sentiments (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id                TEXT NOT NULL,
    comment_id              TEXT,
    text                    TEXT,
    likes                   INTEGER DEFAULT 0,
    sentiment               TEXT,
    sentiment_score         REAL,
    classification_evidence TEXT,
    emotion_raw             TEXT,
    emotion_score           REAL,
    FOREIGN KEY (video_id) REFERENCES videos(video_id)
);
CREATE INDEX IF NOT EXISTS idx_commsent_video_id ON comment_sentiments(video_id);

CREATE TABLE IF NOT EXISTS comment_sentiment_agg (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id             TEXT NOT NULL UNIQUE,
    calculated_at        TEXT NOT NULL,
    total_analyzed       INTEGER,
    positive_ratio       REAL,
    negative_ratio       REAL,
    neutral_ratio        REAL,
    sentiment_avg        REAL,
    sentiment_std        REAL,
    top_words            TEXT,
    emotion_distribution TEXT,
    FOREIGN KEY (video_id) REFERENCES videos(video_id)
);


-- ── Etapa 7 — Identificação musical e fingerprint cultural ──────────────────

CREATE TABLE IF NOT EXISTS music_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id   TEXT NOT NULL,
    t_start    REAL NOT NULL,
    t_end      REAL NOT NULL,
    track_name TEXT,
    artist     TEXT,
    confidence REAL,
    source     TEXT DEFAULT 'shazamio',
    FOREIGN KEY (video_id) REFERENCES videos(video_id)
);
CREATE INDEX IF NOT EXISTS idx_music_video_id ON music_events(video_id);

-- Uma linha por vídeo: o vetor comparável entre vídeos.
CREATE TABLE IF NOT EXISTS cultural_fingerprint (
    id                            INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id                      TEXT NOT NULL UNIQUE,
    calculated_at                 TEXT NOT NULL,
    speech_presence_pct           REAL,
    music_presence_pct            REAL,
    avg_pitch                     REAL,
    median_pitch_hz               REAL,
    avg_volume_db                 REAL,
    speech_sent_avg               REAL,
    question_ratio                REAL,
    cta_ratio                     REAL,
    attention_ratio               REAL,
    top_5_words                   TEXT,
    dominant_register             TEXT,
    music_presence                INTEGER DEFAULT 0,
    comment_sentiment_avg         REAL,
    comment_sentiment_std         REAL,
    comment_positive_ratio        REAL,
    comment_negative_ratio        REAL,
    comment_top_words             TEXT,
    emotion_dominant              TEXT,
    emotion_distribution          TEXT,
    audience_emotion_dominant     TEXT,
    audience_emotion_distribution TEXT,
    top_emojis                    TEXT,
    FOREIGN KEY (video_id) REFERENCES videos(video_id)
);
