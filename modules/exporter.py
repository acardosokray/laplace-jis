"""
modules/exporter.py — Exporta dados para Excel (videos_laplace.xlsx).

Organização em 3 grupos separados por abas-separadoras coloridas, refletindo
a teoria do LAPLACE (estímulo → resposta → síntese):

  resumo                         — dashboard geral (antes de todos os grupos)

  [SEPARADOR] ESTÍMULO DO VÍDEO  (azul escuro)
    videos                       — thumbnail, link áudio, metadados
    transcripts                  — segmentos temporais da fala (Whisper)
    speech_analysis              — classificação auditável: tipo + registro + sentimento
    audio_features               — pitch, volume, energia, speech rate por segmento
    timeline_events              — mapa temporal unificado de eventos
    emotion_curve                — emoção raw por segmento com t_*_pct
    music_events                 — músicas identificadas (ShazamIO) com timestamps
    lexical_profile              — vocabulário: frequência + categoria
    analise_segmentos            — AUDITABILIDADE: linha por segmento com fonte de cada modelo

  [SEPARADOR] RESPOSTA COMPORTAMENTAL  (verde escuro)
    metrics                      — histórico de views/likes/comentários
    comments                     — comentários brutos coletados da API
    comment_sentiment            — até 100 comentários com sentimento + emoção raw
    comment_sentiment_agg        — resumo agregado do sentimento dos comentários
    emoji_stats                  — todos os emojis registrados por vídeo
    analise_comentarios          — AUDITABILIDADE: linha por comentário com fonte de cada modelo

  [SEPARADOR] SÍNTESE  (roxo escuro)
    cultural_fingerprint         — objeto estático comparável entre vídeos
"""

import io
import os
import requests
from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

THUMB_W = 120
THUMB_H = 68
ROW_H   = 55

# ── Paleta de cores consistente ───────────────────────────────────────────────

_COLORS = {
    # Headers
    "header_blue":      "1565C0",
    "header_dark_blue": "0D47A1",
    "header_green":     "2E7D32",
    "header_purple":    "6A1B9A",
    "header_teal":      "00695C",
    "header_orange":    "E65100",
    "header_red":       "B71C1C",
    "header_indigo":    "283593",
    "header_cyan":      "00838F",
    "header_gold":      "F57F17",
    "header_pink":      "AD1457",
    "header_brown":     "5D4037",
    # Group separators
    "group_stimulus":    "0D47A1",  # azul escuro — estímulo do vídeo
    "group_response":    "1B5E20",  # verde escuro — resposta comportamental
    "group_synthesis":   "4A148C",  # roxo escuro — síntese
    # Row alternates
    "alt_blue":         "E3F2FD",
    "alt_green":        "E8F5E9",
    "alt_purple":       "F3E5F5",
    "alt_teal":         "E0F2F1",
    "alt_orange":       "FFF3E0",
    "alt_red":          "FFEBEE",
    # Timeline lane colors
    "lane_speech":      "BBDEFB",
    "lane_music":       "C8E6C9",
    "lane_utterance":   "FFE0B2",
    "lane_sentiment":   "F8BBD0",
    "lane_register":    "D1C4E9",
    "lane_volume":      "B2DFDB",
    # Event type colors
    "speech":           "2196F3",
    "music":            "4CAF50",
    "sfx":              "FF9800",
    "silence":          "9E9E9E",
    "cut":              "F44336",
}

_THIN_BORDER = Border(
    left=Side(style="thin", color="BDBDBD"),
    right=Side(style="thin", color="BDBDBD"),
    top=Side(style="thin", color="BDBDBD"),
    bottom=Side(style="thin", color="BDBDBD"),
)


def _sheet_title(ws, title: str, subtitle: str, n_cols: int, fill_hex: str = "1A237E") -> int:
    """
    Escreve título e subtítulo descritivo no topo da aba.
    Retorna a próxima linha disponível (onde o header de colunas deve ir).
    """
    # Linha 1: título da aba
    cell = ws.cell(row=1, column=1, value=title)
    cell.font = Font(bold=True, size=13, color="FFFFFF", name="Calibri")
    cell.fill = PatternFill("solid", fgColor=fill_hex)
    cell.alignment = Alignment(vertical="center")
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=n_cols)
    for c in range(2, n_cols + 1):
        ws.cell(row=1, column=c).fill = PatternFill("solid", fgColor=fill_hex)
    ws.row_dimensions[1].height = 28

    # Linha 2: descrição / fonte dos dados
    cell2 = ws.cell(row=2, column=1, value=subtitle)
    cell2.font = Font(size=9, color="666666", italic=True, name="Calibri")
    cell2.alignment = Alignment(vertical="center", wrap_text=True)
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=n_cols)
    ws.row_dimensions[2].height = 22

    return 3  # próxima linha para header de colunas


def _header(ws, headers: list[str], fill_hex: str = "1565C0", start_row: int = 1) -> None:
    """Escreve linha de cabeçalho com estilo melhorado."""
    fill = PatternFill("solid", fgColor=fill_hex)
    font = Font(bold=True, color="FFFFFF", size=10, name="Calibri")
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=start_row, column=col, value=h)
        cell.fill = fill
        cell.font = font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = _THIN_BORDER
    ws.row_dimensions[start_row].height = 30
    ws.freeze_panes = ws.cell(row=start_row + 1, column=1).coordinate


def _video_separator(ws, row: int, n_cols: int, title: str, vid_id: str) -> int:
    """
    Insere uma barra separadora entre vídeos com título e ID.
    Retorna a próxima linha disponível.
    """
    cell = ws.cell(row=row, column=1, value=f"  {title}  [{vid_id}]")
    cell.font = Font(bold=True, size=10, color="1A237E", name="Calibri")
    cell.fill = PatternFill("solid", fgColor="E8EAF6")
    cell.alignment = Alignment(vertical="center")
    for c in range(2, n_cols + 1):
        ws.cell(row=row, column=c).fill = PatternFill("solid", fgColor="E8EAF6")
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=n_cols)
    ws.row_dimensions[row].height = 26
    return row + 1


def _set_col_widths(ws, widths: list[float]) -> None:
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _styled_cell(ws, row, col, value, fill_hex=None, font_color="000000",
                 bold=False, align_h="left", wrap=False, number_format=None):
    """Helper para criar célula estilizada."""
    cell = ws.cell(row=row, column=col, value=value)
    if fill_hex:
        cell.fill = PatternFill("solid", fgColor=fill_hex)
    cell.font = Font(color=font_color, bold=bold, size=10, name="Calibri")
    cell.alignment = Alignment(horizontal=align_h, vertical="center", wrap_text=wrap)
    cell.border = _THIN_BORDER
    if number_format:
        cell.number_format = number_format
    return cell


try:
    from config import THUMBS_PATH as _THUMB_CACHE_DIR
except ImportError:
    _THUMB_CACHE_DIR = os.path.join("media", "thumbs")


def _fetch_thumb(url: str) -> io.BytesIO | None:
    # Cache em disco por hash da URL: cada thumbnail é baixada/redimensionada
    # uma única vez; exports seguintes leem do disco (rede era o gargalo).
    cache_path = None
    try:
        import hashlib
        os.makedirs(_THUMB_CACHE_DIR, exist_ok=True)
        cache_path = os.path.join(
            _THUMB_CACHE_DIR, hashlib.sha1(url.encode()).hexdigest() + ".png")
        if os.path.exists(cache_path):
            with open(cache_path, "rb") as f:
                return io.BytesIO(f.read())
    except Exception:
        cache_path = None
    try:
        from PIL import Image as PILImage
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        img = PILImage.open(io.BytesIO(r.content))
        img = img.resize((THUMB_W, THUMB_H), PILImage.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        if cache_path:
            try:
                with open(cache_path, "wb") as f:
                    f.write(buf.getvalue())
            except Exception:
                pass
        buf.seek(0)
        return buf
    except ImportError:
        # Pillow não instalado — retorna imagem original redimensionada via openpyxl
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            buf = io.BytesIO(r.content)
            buf.seek(0)
            return buf
        except Exception:
            return None
    except Exception:
        return None


# ── ABA: resumo (dashboard) ──────────────────────────────────────────────────

def _resumo_sheet(ws, conn) -> None:
    """Dashboard resumido por vídeo — métricas + fingerprint lado a lado."""
    headers = [
        "#", "Thumbnail", "Video ID", "Título", "Canal", "Duração",
        "Views", "Likes", "Comentários",
        "Presença Fala %", "Presença Música %",
        "Perguntas %", "CTAs %", "Atenção %",
        "Pitch (Hz)", "Volume (dB)", "Registro", "Sentimento",
        "Emoção Fala", "Emoção Audiência",
        "Top 5 Palavras", "Músicas Detectadas",
    ]
    hdr_row = _sheet_title(ws, "RESUMO — Dashboard por Vídeo",
        "Fonte: YouTube API (métricas) + Whisper (fala) + DistilBERT (sentimento/registro) + pysentimiento/bert-pt-emotion (emoção raw) + librosa (pitch/volume) + ShazamIO (músicas). "
        "Presença Fala/Música são métricas não-exclusivas (Whisper e Demucs operam independentes; voz + música no mesmo segmento fazem a soma passar de 100%). "
        "Ordem: mais recente primeiro.",
        len(headers), _COLORS["header_indigo"])
    _header(ws, headers, fill_hex=_COLORS["header_indigo"], start_row=hdr_row)
    _set_col_widths(ws, [5, 18, 14, 38, 22, 10, 12, 10, 12, 9, 9, 11, 9, 10, 10, 11, 13, 13, 15, 16, 36, 38])

    rows = conn.execute("""
        SELECT
            v.video_id, v.title, v.channel_name, v.duration_seconds,
            v.thumbnail_url,
            COALESCE(m.views, 0), COALESCE(m.likes, 0), COALESCE(m.comments, 0),
            cf.speech_presence_pct, cf.music_presence_pct,
            ROUND(COALESCE(cf.question_ratio, 0) * 100, 1),
            ROUND(COALESCE(cf.cta_ratio, 0) * 100, 1),
            ROUND(COALESCE(cf.attention_ratio, 0) * 100, 1),
            cf.avg_pitch, cf.avg_volume_db,
            cf.dominant_register, cf.top_5_words,
            cf.emotion_dominant, cf.audience_emotion_dominant
        FROM videos v
        LEFT JOIN (
            SELECT video_id, views, likes, comments
            FROM metrics WHERE id IN (SELECT MAX(id) FROM metrics GROUP BY video_id)
        ) m ON v.video_id = m.video_id
        LEFT JOIN cultural_fingerprint cf ON v.video_id = cf.video_id
        ORDER BY v.created_at DESC
    """).fetchall()

    data_start = hdr_row + 1
    for idx, row in enumerate(rows):
        row_i = data_start + idx
        vid_id = row[0]
        thumb_url = row[4]
        fill = _COLORS["alt_blue"] if idx % 2 == 0 else None
        ws.row_dimensions[row_i].height = ROW_H

        # # (número de ordem)
        _styled_cell(ws, row_i, 1, idx + 1, fill_hex=fill, align_h="center",
                     font_color="999999")

        # Thumbnail
        if thumb_url:
            buf = _fetch_thumb(thumb_url)
            if buf:
                img = XLImage(buf)
                img.width = THUMB_W
                img.height = THUMB_H
                ws.add_image(img, f"B{row_i}")

        # Video ID, Título, Canal
        _styled_cell(ws, row_i, 3, vid_id, fill_hex=fill, align_h="left")
        _styled_cell(ws, row_i, 4, row[1], fill_hex=fill, wrap=True)
        _styled_cell(ws, row_i, 5, row[2], fill_hex=fill)

        # Duração formatada
        dur = row[3] or 0
        mins, secs = divmod(dur, 60)
        _styled_cell(ws, row_i, 6, f"{mins}:{secs:02d}", fill_hex=fill, align_h="center")

        # Views, Likes, Comentários
        _styled_cell(ws, row_i, 7, row[5], fill_hex=fill, align_h="center", number_format="#,##0")
        _styled_cell(ws, row_i, 8, row[6], fill_hex=fill, align_h="center", number_format="#,##0")
        _styled_cell(ws, row_i, 9, row[7], fill_hex=fill, align_h="center", number_format="#,##0")

        # Presença Fala %, Presença Música %
        _styled_cell(ws, row_i, 10, row[8], fill_hex=fill, align_h="center",
                     number_format="0.0")
        _styled_cell(ws, row_i, 11, row[9], fill_hex=fill, align_h="center",
                     number_format="0.0")

        # Perguntas %, CTAs %, Atenção %
        _styled_cell(ws, row_i, 12, row[10], fill_hex=fill, align_h="center")
        _styled_cell(ws, row_i, 13, row[11], fill_hex=fill, align_h="center")
        _styled_cell(ws, row_i, 14, row[12], fill_hex=fill, align_h="center")

        # Pitch, Volume
        _styled_cell(ws, row_i, 15, row[13], fill_hex=fill, align_h="center")
        _styled_cell(ws, row_i, 16, row[14], fill_hex=fill, align_h="center")

        # Registro (colorido)
        reg = row[15] or "—"
        reg_colors = {"gíria": "FF6F00", "informal": "FFA000", "formal": "78909C"}
        reg_fg = {"gíria": "FFFFFF", "informal": "000000", "formal": "FFFFFF"}
        _styled_cell(ws, row_i, 17, reg.upper(), fill_hex=reg_colors.get(reg),
                     font_color=reg_fg.get(reg, "333333"), align_h="center", bold=True)

        # Sentimento dominante (buscar do banco)
        sent_row = conn.execute("""
            SELECT sentiment, COUNT(*) as n FROM speech_analysis
            WHERE video_id = ? GROUP BY sentiment ORDER BY n DESC LIMIT 1
        """, (vid_id,)).fetchone()
        sent = sent_row[0] if sent_row else "—"
        sent_colors = {"positivo": "4CAF50", "negativo": "F44336", "neutro": "E0E0E0", "misto": "FF9800"}
        sent_fg = {"positivo": "FFFFFF", "negativo": "FFFFFF", "neutro": "333333", "misto": "FFFFFF"}
        _styled_cell(ws, row_i, 18, sent.upper(),
                     fill_hex=sent_colors.get(sent), font_color=sent_fg.get(sent, "333333"),
                     align_h="center", bold=True)

        # Emoção dominante da fala (pysentimiento) — col 19
        emo_speech = row[17] or "—"
        _styled_cell(ws, row_i, 19, emo_speech, fill_hex=fill, align_h="center")

        # Emoção dominante da audiência (pysentimiento) — col 20
        emo_audience = row[18] or "—"
        _styled_cell(ws, row_i, 20, emo_audience, fill_hex=fill, align_h="center")

        # Top 5 palavras — col 21
        _styled_cell(ws, row_i, 21, row[16] or "—", fill_hex=fill, wrap=True)

        # Músicas detectadas — col 22
        music_rows = conn.execute(
            "SELECT track_name, artist FROM music_events WHERE video_id = ?",
            (vid_id,),
        ).fetchall()
        if music_rows:
            music_str = " | ".join(f"{r[0]} — {r[1]}" for r in music_rows if r[0])
            _styled_cell(ws, row_i, 22, music_str, fill_hex="C8E6C9",
                         font_color="1B5E20", wrap=True)
        else:
            _styled_cell(ws, row_i, 22, "—", fill_hex=fill, align_h="center")


def _videos_sheet(ws, conn) -> None:
    headers = [
        "#", "Thumbnail", "Video ID", "Título", "Canal", "Formato",
        "Duração", "Views", "Likes", "Comentários",
        "Publicado", "Processado em", "Idioma", "Áudio", "Tags", "Hashtags",
    ]
    hdr_row = _sheet_title(ws, "VÍDEOS — Metadados completos",
        "Fonte: YouTube Data API v3 (metadados, métricas) + yt-dlp (áudio). Cada linha = 1 vídeo, ordem: mais recente primeiro.",
        len(headers), _COLORS["header_blue"])
    _header(ws, headers, fill_hex=_COLORS["header_blue"], start_row=hdr_row)
    _set_col_widths(ws, [5, 18, 14, 42, 26, 12, 10, 12, 10, 12, 14, 18, 8, 12, 30, 30])

    rows = conn.execute("""
        SELECT
            v.video_id, v.title, v.channel_name, v.format,
            v.duration_seconds, v.thumbnail_url, v.publish_date,
            v.language, v.tags, v.hashtags,
            COALESCE(m.views,    0) AS views,
            COALESCE(m.likes,    0) AS likes,
            COALESCE(m.comments, 0) AS comments,
            a.file_path,
            v.created_at
        FROM videos v
        LEFT JOIN (
            SELECT video_id, views, likes, comments
            FROM metrics
            WHERE id IN (SELECT MAX(id) FROM metrics GROUP BY video_id)
        ) m ON v.video_id = m.video_id
        LEFT JOIN audio_files a ON v.video_id = a.video_id
        ORDER BY v.created_at DESC
    """).fetchall()

    data_start = hdr_row + 1
    for idx, row in enumerate(rows):
        row_i = data_start + idx
        ws.row_dimensions[row_i].height = ROW_H
        alt_fill = _COLORS["alt_blue"] if idx % 2 == 0 else None

        (vid_id, title, channel, fmt, dur, thumb_url,
         pub_date, lang, tags, hashtags,
         views, likes, comments, audio_path, created_at) = row

        # # (número de ordem)
        _styled_cell(ws, row_i, 1, idx + 1, fill_hex=alt_fill, align_h="center",
                     font_color="999999")

        if thumb_url:
            buf = _fetch_thumb(thumb_url)
            if buf:
                img = XLImage(buf)
                img.width  = THUMB_W
                img.height = THUMB_H
                ws.add_image(img, f"B{row_i}")

        # Duração formatada
        dur_val = dur or 0
        mins, secs = divmod(dur_val, 60)
        dur_str = f"{mins}:{secs:02d}"

        # Formatar data de processamento (created_at)
        proc_date = (created_at or "")[:16].replace("T", " ")

        values = [
            None, None, vid_id, title, channel, fmt,
            dur_str, f"{views:,}", f"{likes:,}", f"{comments:,}",
            pub_date, proc_date, lang, None, tags, hashtags,
        ]
        for col_i, val in enumerate(values, 1):
            if val is not None:
                _styled_cell(ws, row_i, col_i, val, fill_hex=alt_fill,
                             wrap=(col_i in (4, 15, 16)),
                             align_h="center" if col_i in (1, 6, 7, 8, 9, 10, 12, 13) else "left")

        if audio_path and os.path.exists(audio_path):
            abs_path = os.path.abspath(audio_path).replace("\\", "/")
            c = ws.cell(row=row_i, column=14, value="▶ Ouvir")
            c.hyperlink = f"file:///{abs_path}"
            c.font = Font(color="0563C1", underline="single", name="Calibri")
            c.alignment = Alignment(horizontal="center", vertical="center")


# ── ABA: transcripts ─────────────────────────────────────────────────────────

def _transcripts_sheet(ws, conn) -> None:
    headers = ["Video ID", "Título", "Seg #", "Início (s)", "Fim (s)", "Dur (s)", "Texto"]
    n_cols = len(headers)
    hdr_row = _sheet_title(ws, "TRANSCRIÇÕES — Segmentos de fala",
        "Fonte: Whisper large-v3 (transcrição local com VAD). Cada linha = 1 segmento de fala detectado. Ordem: vídeo mais recente primeiro, segmentos por tempo.",
        n_cols, _COLORS["header_dark_blue"])
    _header(ws, headers, fill_hex=_COLORS["header_dark_blue"], start_row=hdr_row)
    _set_col_widths(ws, [14, 36, 6, 10, 10, 8, 80])

    rows = conn.execute("""
        SELECT ts.video_id, v.title, ts.seg_index,
               ts.start_sec, ts.end_sec,
               ROUND(ts.end_sec - ts.start_sec, 2) AS duration, ts.text
        FROM transcript_segments ts
        JOIN videos v ON ts.video_id = v.video_id
        ORDER BY v.created_at DESC, ts.seg_index
    """).fetchall()

    current_row = hdr_row + 1
    last_vid = None
    for row in rows:
        vid_id, title = row[0], row[1]
        if vid_id != last_vid:
            if last_vid is not None:
                current_row = _video_separator(ws, current_row, n_cols, title, vid_id)
            else:
                current_row = _video_separator(ws, current_row, n_cols, title, vid_id)
            last_vid = vid_id
        alt = _COLORS["alt_blue"] if current_row % 2 == 0 else None
        for col_i, val in enumerate(row, 1):
            _styled_cell(ws, current_row, col_i, val, fill_hex=alt,
                         wrap=(col_i == 7),
                         align_h="center" if col_i in (3, 4, 5, 6) else "left")
        current_row += 1


# ── ABA: metrics ─────────────────────────────────────────────────────────────

def _metrics_sheet(ws, conn) -> None:
    headers = ["Video ID", "Título", "Coletado em", "Views", "Likes", "Comentários"]
    n_cols = len(headers)
    hdr_row = _sheet_title(ws, "MÉTRICAS — Histórico de engajamento",
        "Fonte: YouTube Data API v3. Cada linha = 1 snapshot de métricas no momento da coleta. Permite acompanhar evolução temporal.",
        n_cols, _COLORS["header_green"])
    _header(ws, headers, fill_hex=_COLORS["header_green"], start_row=hdr_row)
    _set_col_widths(ws, [14, 42, 22, 14, 12, 14])

    rows = conn.execute("""
        SELECT m.video_id, v.title, m.collected_at, m.views, m.likes, m.comments
        FROM metrics m
        JOIN videos v ON m.video_id = v.video_id
        ORDER BY v.created_at DESC, m.collected_at ASC
    """).fetchall()

    current_row = hdr_row + 1
    last_vid = None
    for row in rows:
        vid_id, title = row[0], row[1]
        if vid_id != last_vid:
            current_row = _video_separator(ws, current_row, n_cols, title, vid_id)
            last_vid = vid_id
        alt = _COLORS["alt_green"] if current_row % 2 == 0 else None
        for col_i, val in enumerate(row, 1):
            _styled_cell(ws, current_row, col_i, val, fill_hex=alt,
                         align_h="center" if col_i >= 3 else "left",
                         number_format="#,##0" if col_i >= 4 else None)
        current_row += 1


# ── ABA: timeline_events ─────────────────────────────────────────────────────

def _timeline_events_sheet(ws, conn) -> None:
    headers = ["Video ID", "Título", "Início (s)", "Fim (s)", "Dur (s)", "Tipo", "Valor", "Confiança", "Fonte"]
    n_cols = len(headers)
    hdr_row = _sheet_title(ws, "TIMELINE EVENTS — Mapa temporal unificado",
        "Fonte: Whisper (fala) + ShazamIO (música). Cada linha = 1 evento temporal (fala, música, sfx, silêncio, corte). Cores indicam tipo de evento.",
        n_cols, _COLORS["header_cyan"])
    _header(ws, headers, fill_hex=_COLORS["header_cyan"], start_row=hdr_row)
    _set_col_widths(ws, [14, 32, 10, 10, 8, 14, 60, 10, 14])

    rows = conn.execute("""
        SELECT te.video_id, v.title,
               te.t_start, te.t_end,
               ROUND(te.t_end - te.t_start, 2), te.event_type, te.value,
               te.confidence, te.source
        FROM timeline_events te
        JOIN videos v ON te.video_id = v.video_id
        ORDER BY v.created_at DESC, te.t_start
    """).fetchall()

    type_colors = {
        "speech":  _COLORS["lane_speech"],
        "music":   _COLORS["lane_music"],
        "sfx":     _COLORS["alt_orange"],
        "silence": "F5F5F5",
        "cut":     _COLORS["alt_red"],
    }
    current_row = hdr_row + 1
    last_vid = None
    for row in rows:
        vid_id, title = row[0], row[1]
        if vid_id != last_vid:
            current_row = _video_separator(ws, current_row, n_cols, title, vid_id)
            last_vid = vid_id
        event_type = row[5] or ""
        fill = type_colors.get(event_type, None)
        for col_i, val in enumerate(row, 1):
            _styled_cell(ws, current_row, col_i, val, fill_hex=fill,
                         wrap=(col_i == 7),
                         align_h="center" if col_i in (3, 4, 5, 6, 8, 9) else "left")
        current_row += 1


# ── ABA: audio_features ──────────────────────────────────────────────────────

def _audio_features_sheet(ws, conn) -> None:
    headers = [
        "Video ID", "Título", "Início (s)", "Fim (s)",
        "Volume (dB)", "Pitch (Hz)", "Energia", "Speech Rate (p/s)", "Registro Vocal",
    ]
    n_cols = len(headers)
    hdr_row = _sheet_title(ws, "AUDIO FEATURES — Características acústicas",
        "Fonte: librosa (pitch, volume, energia, speech rate) + regras heurísticas (registro vocal). Cada linha = 1 segmento de fala analisado acusticamente.",
        n_cols, _COLORS["header_purple"])
    _header(ws, headers, fill_hex=_COLORS["header_purple"], start_row=hdr_row)
    _set_col_widths(ws, [14, 32, 10, 10, 12, 12, 10, 16, 16])

    rows = conn.execute("""
        SELECT af.video_id, v.title,
               af.t_start, af.t_end,
               af.volume_db, af.pitch_hz, af.energy, af.speech_rate, af.voice_register
        FROM audio_features af
        JOIN videos v ON af.video_id = v.video_id
        ORDER BY v.created_at DESC, af.t_start
    """).fetchall()

    voice_reg_colors = {
        "grito":     _COLORS["alt_red"],
        "agitado":   _COLORS["alt_orange"],
        "acelerado": "FFF9C4",
        "sussurro":  _COLORS["alt_blue"],
        "normal":    None,
    }

    current_row = hdr_row + 1
    last_vid = None
    for row in rows:
        vid_id, title = row[0], row[1]
        if vid_id != last_vid:
            current_row = _video_separator(ws, current_row, n_cols, title, vid_id)
            last_vid = vid_id
        vreg = row[8] or "normal"
        fill = voice_reg_colors.get(vreg, None)
        if fill is None and current_row % 2 == 0:
            fill = _COLORS["alt_purple"]
        for col_i, val in enumerate(row, 1):
            _styled_cell(ws, current_row, col_i, val, fill_hex=fill,
                         align_h="center" if col_i >= 3 else "left")
        current_row += 1


# ── ABA: speech_analysis ─────────────────────────────────────────────────────

def _speech_analysis_sheet(ws, conn) -> None:
    headers = [
        "Video ID", "Título", "Início (s)", "Fim (s)",
        "Texto", "Utterance", "Registro", "Sentimento",
        "Evidência", "Método",
    ]
    n_cols = len(headers)
    hdr_row = _sheet_title(ws, "SPEECH ANALYSIS — Classificação auditável",
        "Fonte: regras heurísticas (utterance type, registro) + DistilBERT (sentimento contextual). Cada classificação inclui evidência auditável.",
        n_cols, _COLORS["header_teal"])
    _header(ws, headers, fill_hex=_COLORS["header_teal"], start_row=hdr_row)
    _set_col_widths(ws, [14, 28, 10, 10, 60, 18, 12, 12, 55, 16])

    rows = conn.execute("""
        SELECT sa.video_id, v.title,
               sa.t_start, sa.t_end, sa.text,
               sa.utterance_type, sa.language_register, sa.sentiment,
               sa.classification_evidence, sa.classification_method
        FROM speech_analysis sa
        JOIN videos v ON sa.video_id = v.video_id
        ORDER BY v.created_at DESC, sa.t_start
    """).fetchall()

    type_colors = {
        "pergunta":        _COLORS["lane_speech"],
        "chamada_ação":    _COLORS["lane_music"],
        "chamada_atenção": _COLORS["lane_utterance"],
        "exclamação":      _COLORS["alt_red"],
        "afirmação":       None,
    }
    current_row = hdr_row + 1
    last_vid = None
    for row in rows:
        vid_id, title = row[0], row[1]
        if vid_id != last_vid:
            current_row = _video_separator(ws, current_row, n_cols, title, vid_id)
            last_vid = vid_id
        utype = row[5] or ""
        fill = type_colors.get(utype, None)
        if fill is None and current_row % 2 == 0:
            fill = _COLORS["alt_teal"]
        for col_i, val in enumerate(row, 1):
            _styled_cell(ws, current_row, col_i, val, fill_hex=fill,
                         wrap=(col_i in (5, 9)),
                         align_h="center" if col_i in (3, 4, 6, 7, 8, 10) else "left")
        current_row += 1


# ── ABA: lexical_profile ─────────────────────────────────────────────────────

def _lexical_profile_sheet(ws, conn) -> None:
    headers = ["Video ID", "Título", "Palavra", "Frequência", "Categoria", "1ª Ocorrência (s)"]
    n_cols = len(headers)
    hdr_row = _sheet_title(ws, "LEXICAL PROFILE — Vocabulário do criador",
        "Fonte: transcrição Whisper + classificação rule-based. Palavras relevantes (excluindo stopwords), ordenadas por frequência. Categorias: CTA, gíria, common.",
        n_cols, _COLORS["header_orange"])
    _header(ws, headers, fill_hex=_COLORS["header_orange"], start_row=hdr_row)
    _set_col_widths(ws, [14, 32, 20, 12, 14, 16])

    rows = conn.execute("""
        SELECT lp.video_id, v.title,
               lp.word, lp.frequency, lp.category, lp.first_occurrence_t
        FROM lexical_profile lp
        JOIN videos v ON lp.video_id = v.video_id
        WHERE lp.category != 'stopword'
        ORDER BY v.created_at DESC, lp.frequency DESC
    """).fetchall()

    cat_colors = {
        "cta":    _COLORS["lane_music"],
        "gíria":  _COLORS["lane_utterance"],
        "common": None,
    }
    current_row = hdr_row + 1
    last_vid = None
    for row in rows:
        vid_id, title = row[0], row[1]
        if vid_id != last_vid:
            current_row = _video_separator(ws, current_row, n_cols, title, vid_id)
            last_vid = vid_id
        cat = row[4] or "common"
        fill = cat_colors.get(cat, None)
        if fill is None and current_row % 2 == 0:
            fill = _COLORS["alt_orange"]
        for col_i, val in enumerate(row, 1):
            _styled_cell(ws, current_row, col_i, val, fill_hex=fill,
                         align_h="center" if col_i in (4, 5, 6) else "left")
        current_row += 1


# ── ABA: cultural_fingerprint ─────────────────────────────────────────────────

def _format_emotion_top5(dist_json: str | None) -> str:
    """Transforma JSON de distribuição em string 'neutral 68% | curiosity 14% | ...' (top 5)."""
    if not dist_json:
        return "—"
    try:
        import json
        d = json.loads(dist_json)
    except Exception:
        return "—"
    # JSON já vem ordenado por pct desc; pegamos os 5 primeiros com n > 0
    top = [(label, v.get("pct", 0), v.get("n", 0))
           for label, v in d.items() if v.get("n", 0) > 0][:5]
    if not top:
        return "—"
    return " | ".join(f"{label} {pct:.0f}%" for label, pct, _ in top)


def _cultural_fingerprint_sheet(ws, conn) -> None:
    headers = [
        "Video ID", "Título", "Duração",
        "Presença Fala %", "Presença Música %",
        "Perguntas %", "CTAs %", "Atenção %",
        "Pitch médio (Hz)", "Pitch mediana (Hz)", "Volume (dB)",
        "Sent. Fala avg", "Registro", "Sentimento Fala", "Vel. Fala",
        "Top 5 Palavras", "Músicas",
        "Sent. Coment.", "Coment. +%", "Coment. -%", "Top Palavras Coment.",
        "Emoção Fala", "Distribuição Fala (top 5)",
        "Emoção Audiência", "Distribuição Audiência (top 5)",
        "Top 5 Emojis",
    ]
    n_cols = len(headers)
    hdr_row = _sheet_title(ws, "CULTURAL FINGERPRINT — Objeto comparável",
        "Fonte: agregação SQL de todas as análises (fala, áudio, léxico, música, comentários, emoções, emojis). "
        "Cada linha = 1 vídeo. Presença Fala/Música são NÃO-EXCLUSIVAS: Whisper (voz) e Demucs (trilha) medem fontes independentes — voz sobre música coexiste e a soma pode passar de 100%. "
        "Pitch mediana e Sent. Fala avg são os valores que aparecem como overlays no gráfico visualization.py — batem 1:1 com as células desta aba. "
        "Sent. Coment. = média aritmética dos scores ±conf do BERT (-1 a +1). "
        "Coment. +%/-% = fração sobre total_analyzed * 100. Permite comparação direta entre vídeos.",
        n_cols, _COLORS["header_red"])
    _header(ws, headers, fill_hex=_COLORS["header_red"], start_row=hdr_row)
    _set_col_widths(ws, [14, 36, 9, 9, 9, 11, 9, 10, 12, 12, 11, 12, 13, 13, 11,
                         38, 38, 13, 11, 11, 38, 15, 45, 16, 45, 32])

    rows = conn.execute("""
        SELECT v.video_id, v.title, v.duration_seconds,
               cf.speech_presence_pct, cf.music_presence_pct,
               ROUND(COALESCE(cf.question_ratio, 0) * 100, 1),
               ROUND(COALESCE(cf.cta_ratio, 0) * 100, 1),
               ROUND(COALESCE(cf.attention_ratio, 0) * 100, 1),
               cf.avg_pitch, cf.median_pitch_hz, cf.avg_volume_db,
               cf.speech_sent_avg,
               cf.dominant_register, cf.top_5_words,
               cf.music_presence,
               cf.comment_sentiment_avg,
               ROUND(COALESCE(cf.comment_positive_ratio, 0) * 100, 1),
               ROUND(COALESCE(cf.comment_negative_ratio, 0) * 100, 1),
               cf.comment_top_words,
               cf.emotion_dominant, cf.emotion_distribution,
               cf.audience_emotion_dominant, cf.audience_emotion_distribution,
               cf.top_emojis
        FROM videos v
        LEFT JOIN cultural_fingerprint cf ON v.video_id = cf.video_id
        ORDER BY v.created_at DESC
    """).fetchall()

    reg_colors = {"gíria": "FF6F00", "informal": "FFA000", "formal": "78909C"}
    reg_fg = {"gíria": "FFFFFF", "informal": "000000", "formal": "FFFFFF"}
    sent_colors = {"positivo": "4CAF50", "negativo": "F44336", "neutro": "E0E0E0"}
    sent_fg = {"positivo": "FFFFFF", "negativo": "FFFFFF", "neutro": "333333"}

    data_start = hdr_row + 1
    for idx, row in enumerate(rows):
        (vid_id, title, duration_s,
         speech_pres, music_pres,
         q_pct, c_pct, a_pct,
         avg_pitch, median_pitch, avg_vol,
         speech_sent_avg,
         dom_register, top_words,
         music_presence_bool,
         cs_avg, cs_pos_pct, cs_neg_pct, cs_top_words,
         emo_dom, emo_dist,
         aud_emo_dom, aud_emo_dist,
         top_emojis) = row

        row_i = data_start + idx
        alt = _COLORS["alt_red"] if idx % 2 == 0 else None

        # col 1-2: Video ID, Título
        _styled_cell(ws, row_i, 1, vid_id, fill_hex=alt)
        _styled_cell(ws, row_i, 2, title, fill_hex=alt, wrap=True)

        # col 3: Duração formatada
        mins, secs = divmod(duration_s or 0, 60)
        _styled_cell(ws, row_i, 3, f"{mins}:{secs:02d}", fill_hex=alt, align_h="center")

        # col 4-5: Presença Fala %, Presença Música %
        _styled_cell(ws, row_i, 4, speech_pres, fill_hex=alt, align_h="center", number_format="0.0")
        _styled_cell(ws, row_i, 5, music_pres, fill_hex=alt, align_h="center", number_format="0.0")

        # col 6-8: Perguntas %, CTAs %, Atenção %
        _styled_cell(ws, row_i, 6, q_pct, fill_hex=alt, align_h="center")
        _styled_cell(ws, row_i, 7, c_pct, fill_hex=alt, align_h="center")
        _styled_cell(ws, row_i, 8, a_pct, fill_hex=alt, align_h="center")

        # col 9-10: Pitch médio, Pitch mediana (novo — valor usado na linha 'mediana' do gráfico)
        _styled_cell(ws, row_i, 9, avg_pitch, fill_hex=alt, align_h="center")
        _styled_cell(ws, row_i, 10, median_pitch, fill_hex=alt, align_h="center")

        # col 11: Volume
        _styled_cell(ws, row_i, 11, avg_vol, fill_hex=alt, align_h="center")

        # col 12: Sent. Fala avg (novo — valor usado na linha 'avg' do Painel 2 do gráfico)
        if speech_sent_avg is not None:
            label = "positivo" if speech_sent_avg > 0.1 else ("negativo" if speech_sent_avg < -0.1 else "neutro")
            _styled_cell(ws, row_i, 12, f"{speech_sent_avg:+.4f}",
                         fill_hex=sent_colors.get(label, alt),
                         font_color=sent_fg.get(label, "333333"),
                         align_h="center", bold=True,
                         number_format="+0.0000;-0.0000;0.0000")
        else:
            _styled_cell(ws, row_i, 12, "—", fill_hex=alt, align_h="center")

        # col 13: Registro (colorido)
        reg = dom_register or "—"
        _styled_cell(ws, row_i, 13, reg.upper(),
                     fill_hex=reg_colors.get(reg, alt),
                     font_color=reg_fg.get(reg, "333333"),
                     align_h="center", bold=True)

        # col 14: Sentimento dominante (fala) — calculado com query auxiliar
        sent_row = conn.execute("""
            SELECT sentiment, COUNT(*) as n FROM speech_analysis
            WHERE video_id = ? GROUP BY sentiment ORDER BY n DESC LIMIT 1
        """, (vid_id,)).fetchone()
        sent = sent_row[0] if sent_row else "—"
        _styled_cell(ws, row_i, 14, sent.upper(),
                     fill_hex=sent_colors.get(sent, alt),
                     font_color=sent_fg.get(sent, "333333"),
                     align_h="center", bold=True)

        # col 15: Vel. fala média
        sr_row = conn.execute(
            "SELECT AVG(speech_rate) FROM audio_features WHERE video_id = ?",
            (vid_id,),
        ).fetchone()
        sr = f"{sr_row[0]:.1f} p/s" if sr_row and sr_row[0] else "—"
        _styled_cell(ws, row_i, 15, sr, fill_hex=alt, align_h="center")

        # col 16: Top 5 palavras
        _styled_cell(ws, row_i, 16, top_words or "—", fill_hex=alt, wrap=True)

        # col 17: Músicas detectadas
        music_rows = conn.execute(
            "SELECT track_name, artist FROM music_events WHERE video_id = ?",
            (vid_id,),
        ).fetchall()
        if music_rows:
            music_str = " | ".join(f"{r[0]} — {r[1]}" for r in music_rows if r[0])
            _styled_cell(ws, row_i, 17, music_str,
                         fill_hex="C8E6C9", font_color="1B5E20", wrap=True)
        else:
            _styled_cell(ws, row_i, 17, "—", fill_hex=alt, align_h="center")

        # col 18: Sentimento comentários (média -1 a +1)
        if cs_avg is not None:
            cs_label = "positivo" if cs_avg > 0.1 else ("negativo" if cs_avg < -0.1 else "neutro")
            _styled_cell(ws, row_i, 18, f"{cs_avg:+.2f}",
                         fill_hex=sent_colors.get(cs_label, alt),
                         font_color=sent_fg.get(cs_label, "333333"),
                         align_h="center", bold=True)
        else:
            _styled_cell(ws, row_i, 18, "—", fill_hex=alt, align_h="center")

        # col 19-20: Coment. +%, Coment. -%
        _styled_cell(ws, row_i, 19, cs_pos_pct, fill_hex=alt, align_h="center")
        _styled_cell(ws, row_i, 20, cs_neg_pct, fill_hex=alt, align_h="center")

        # col 21: Top palavras comentários
        _styled_cell(ws, row_i, 21, cs_top_words or "—", fill_hex=alt, wrap=True)

        # col 22-25: Emoções
        _styled_cell(ws, row_i, 22, emo_dom or "—", fill_hex=alt, align_h="center")
        _styled_cell(ws, row_i, 23, _format_emotion_top5(emo_dist),
                     fill_hex=alt, wrap=True)
        _styled_cell(ws, row_i, 24, aud_emo_dom or "—", fill_hex=alt, align_h="center")
        _styled_cell(ws, row_i, 25, _format_emotion_top5(aud_emo_dist),
                     fill_hex=alt, wrap=True)

        # col 26: Top 5 emojis — fonte específica pra renderizar
        emoji_cell = _styled_cell(ws, row_i, 26, top_emojis or "—",
                                  fill_hex=alt, align_h="center", wrap=True)
        emoji_cell.font = Font(size=12, name="Segoe UI Emoji")


# ── ABA: comment_sentiment ────────────────────────────────────────────────────

def _comment_sentiment_sheet(ws, conn) -> None:
    """
    Até 100 comentários por vídeo com sentimento + emoção raw (GoEmotions).
    Ordem: vídeo mais recente primeiro, dentro de cada vídeo por likes (desc).
    """
    headers = [
        "Video ID", "Título", "Texto do Comentário", "Likes",
        "Sentimento", "Score", "Emoção (raw)", "Emo Score",
    ]
    n_cols = len(headers)
    hdr_row = _sheet_title(ws, "COMMENT SENTIMENT — Sentimento + Emoção por comentário",
        "Fonte: YouTube Data API v3 + DistilBERT (sentimento multilingual) + pysentimiento/bert-pt-emotion (28 labels GoEmotions brutas). Até 100 comentários por vídeo, ordenados por likes desc.",
        n_cols, _COLORS["header_gold"])
    _header(ws, headers, fill_hex=_COLORS["header_gold"], start_row=hdr_row)
    _set_col_widths(ws, [14, 32, 60, 10, 14, 10, 16, 10])

    rows = conn.execute("""
        SELECT cs.video_id, v.title, cs.text, cs.likes,
               cs.sentiment, cs.sentiment_score,
               cs.emotion_raw, cs.emotion_score
        FROM comment_sentiments cs
        JOIN videos v ON cs.video_id = v.video_id
        ORDER BY v.created_at DESC, cs.video_id, cs.likes DESC
    """).fetchall()

    sent_colors = {
        "positive": ("4CAF50", "FFFFFF"),
        "negative": ("F44336", "FFFFFF"),
        "neutral":  ("E0E0E0", "333333"),
        "positivo": ("4CAF50", "FFFFFF"),
        "negativo": ("F44336", "FFFFFF"),
        "neutro":   ("E0E0E0", "333333"),
    }

    current_row = hdr_row + 1
    last_vid = None
    for row in rows:
        vid_id, title, text, likes, sentiment, score, emotion_raw, emo_score = row
        if vid_id != last_vid:
            current_row = _video_separator(ws, current_row, n_cols, title, vid_id)
            last_vid = vid_id

        alt = _COLORS["alt_orange"] if current_row % 2 == 0 else None
        s_bg, s_fg = sent_colors.get((sentiment or "").lower(), (alt, "333333"))

        _styled_cell(ws, current_row, 1, vid_id, fill_hex=alt)
        _styled_cell(ws, current_row, 2, title, fill_hex=alt, wrap=True)
        _styled_cell(ws, current_row, 3, text or "", fill_hex=alt, wrap=True)
        _styled_cell(ws, current_row, 4, likes or 0, fill_hex=alt,
                     align_h="center", number_format="#,##0")
        _styled_cell(ws, current_row, 5, (sentiment or "—").upper(),
                     fill_hex=s_bg, font_color=s_fg, align_h="center", bold=True)
        _styled_cell(ws, current_row, 6,
                     f"{score:+.2f}" if score is not None else "—",
                     fill_hex=alt, align_h="center",
                     number_format="+0.00;-0.00;0.00")
        _styled_cell(ws, current_row, 7, emotion_raw or "—",
                     fill_hex=alt, align_h="center")
        _styled_cell(ws, current_row, 8,
                     f"{emo_score:.2f}" if emo_score is not None else "—",
                     fill_hex=alt, align_h="center", number_format="0.00")
        current_row += 1


# ── ABA: comment_sentiment_agg ────────────────────────────────────────────────

def _comment_sentiment_agg_sheet(ws, conn) -> None:
    """
    Resumo agregado por vídeo do sentimento dos comentários.
    Uma linha por vídeo.
    """
    headers = [
        "Video ID", "Título", "Total Analisados",
        "Positivos %", "Negativos %", "Neutros %",
        "Sentimento Médio", "Desvio Padrão", "Top Palavras",
    ]
    n_cols = len(headers)
    hdr_row = _sheet_title(ws, "COMMENT SENTIMENT AGG — Resumo por vídeo",
        "Fonte: agregação SQL sobre comment_sentiments. "
        "sentiment_avg = média aritmética dos scores assinados (cada score = ±confiança do BERT, intervalo [-1, +1]; neutros = 0). "
        "sentiment_std = desvio padrão amostral (alto = polarização; baixo = monotonia). "
        "Ratios são #label/total_analyzed e sempre somam 1.0. top_words exclui stopwords.",
        n_cols, _COLORS["header_pink"])
    _header(ws, headers, fill_hex=_COLORS["header_pink"], start_row=hdr_row)
    _set_col_widths(ws, [14, 38, 14, 12, 12, 12, 16, 14, 50])

    rows = conn.execute("""
        SELECT csa.video_id, v.title,
               csa.total_analyzed,
               ROUND(COALESCE(csa.positive_ratio, 0) * 100, 1),
               ROUND(COALESCE(csa.negative_ratio, 0) * 100, 1),
               ROUND(COALESCE(csa.neutral_ratio, 0) * 100, 1),
               csa.sentiment_avg, csa.sentiment_std, csa.top_words
        FROM comment_sentiment_agg csa
        JOIN videos v ON csa.video_id = v.video_id
        ORDER BY v.created_at DESC
    """).fetchall()

    sent_colors = {"positivo": ("4CAF50", "FFFFFF"),
                   "negativo": ("F44336", "FFFFFF"),
                   "neutro":   ("E0E0E0", "333333")}

    data_start = hdr_row + 1
    for idx, row in enumerate(rows):
        row_i = data_start + idx
        alt = _COLORS["alt_red"] if idx % 2 == 0 else None
        vid_id, title, total, pos, neg, neu, avg, std, top = row

        _styled_cell(ws, row_i, 1, vid_id, fill_hex=alt)
        _styled_cell(ws, row_i, 2, title, fill_hex=alt, wrap=True)
        _styled_cell(ws, row_i, 3, total or 0, fill_hex=alt,
                     align_h="center", number_format="#,##0")
        _styled_cell(ws, row_i, 4, pos, fill_hex=alt, align_h="center",
                     number_format="0.0")
        _styled_cell(ws, row_i, 5, neg, fill_hex=alt, align_h="center",
                     number_format="0.0")
        _styled_cell(ws, row_i, 6, neu, fill_hex=alt, align_h="center",
                     number_format="0.0")

        if avg is not None:
            label = "positivo" if avg > 0.1 else ("negativo" if avg < -0.1 else "neutro")
            s_bg, s_fg = sent_colors[label]
            _styled_cell(ws, row_i, 7, f"{avg:+.2f}",
                         fill_hex=s_bg, font_color=s_fg,
                         align_h="center", bold=True,
                         number_format="+0.00;-0.00;0.00")
        else:
            _styled_cell(ws, row_i, 7, "—", fill_hex=alt, align_h="center")

        _styled_cell(ws, row_i, 8,
                     f"{std:.2f}" if std is not None else "—",
                     fill_hex=alt, align_h="center",
                     number_format="0.00")
        _styled_cell(ws, row_i, 9, top or "—", fill_hex=alt, wrap=True)


# ── ABA: emoji_stats ──────────────────────────────────────────────────────────

def _emoji_stats_sheet(ws, conn) -> None:
    """
    Ranking de emojis nos comentários, por vídeo.
    Agrupa por vídeo e ordena por contagem (desc).
    """
    headers = ["Video ID", "Título", "Emoji", "Contagem"]
    n_cols = len(headers)
    hdr_row = _sheet_title(ws, "EMOJI STATS — Emojis mais usados nos comentários",
        "Fonte: YouTube Data API v3 (comentários) + extração de emojis. Cada linha = 1 emoji em 1 vídeo, ordenado por contagem desc dentro de cada vídeo.",
        n_cols, _COLORS["header_brown"])
    _header(ws, headers, fill_hex=_COLORS["header_brown"], start_row=hdr_row)
    _set_col_widths(ws, [14, 42, 10, 14])

    rows = conn.execute("""
        SELECT es.video_id, v.title, es.emoji, es.count
        FROM emoji_stats es
        JOIN videos v ON es.video_id = v.video_id
        ORDER BY v.created_at DESC, es.video_id, es.count DESC
    """).fetchall()

    current_row = hdr_row + 1
    last_vid = None
    for row in rows:
        vid_id, title, emoji, count = row
        if vid_id != last_vid:
            current_row = _video_separator(ws, current_row, n_cols, title, vid_id)
            last_vid = vid_id
        alt = "EFEBE9" if current_row % 2 == 0 else None
        _styled_cell(ws, current_row, 1, vid_id, fill_hex=alt)
        _styled_cell(ws, current_row, 2, title, fill_hex=alt, wrap=True)
        emoji_cell = _styled_cell(ws, current_row, 3, emoji or "",
                                  fill_hex=alt, align_h="center")
        emoji_cell.font = Font(size=14, name="Segoe UI Emoji")
        _styled_cell(ws, current_row, 4, count or 0, fill_hex=alt,
                     align_h="center", number_format="#,##0")
        current_row += 1


# ── ABA: emotion_curve ────────────────────────────────────────────────────────

def _emotion_curve_sheet(ws, conn) -> None:
    """
    Curva emocional por segmento de fala, com tempo absoluto (s) e normalizado (%).
    Permite comparar a trajetória emocional entre vídeos de durações diferentes.
    Inclui a label categórica GoEmotions (pysentimiento) por segmento.
    """
    headers = [
        "Video ID", "Título", "Seg #",
        "t_start (s)", "t_end (s)", "t_start %", "t_end %",
        "Emoção (raw)", "Score",
        "Texto do Segmento",
    ]
    n_cols = len(headers)
    hdr_row = _sheet_title(ws, "EMOTION CURVE — Trajetória emocional da fala",
        "Fonte: speech_analysis × pysentimiento/bert-pt-emotion (28 labels GoEmotions brutas, score 0-1). t_*_pct normalizam o tempo pela duração total do vídeo. Uma linha por segmento de fala.",
        n_cols, _COLORS["header_purple"])
    _header(ws, headers, fill_hex=_COLORS["header_purple"], start_row=hdr_row)
    _set_col_widths(ws, [14, 32, 6, 10, 10, 10, 10, 16, 10, 60])

    rows = conn.execute("""
        SELECT ec.video_id, v.title,
               ec.segment_id, ec.t_start, ec.t_end,
               ec.t_start_pct, ec.t_end_pct,
               ec.emotion_raw, ec.emotion_score,
               ec.segment_text
        FROM emotion_curve ec
        JOIN videos v ON ec.video_id = v.video_id
        ORDER BY v.created_at DESC, ec.video_id, ec.segment_id
    """).fetchall()

    current_row = hdr_row + 1
    last_vid = None
    for row in rows:
        (vid_id, title, seg_id, t_s, t_e, pct_s, pct_e,
         emo_raw, emo_score, text) = row
        if vid_id != last_vid:
            current_row = _video_separator(ws, current_row, n_cols, title, vid_id)
            last_vid = vid_id

        alt = _COLORS["alt_purple"] if current_row % 2 == 0 else None

        _styled_cell(ws, current_row, 1, vid_id, fill_hex=alt)
        _styled_cell(ws, current_row, 2, title, fill_hex=alt, wrap=True)
        _styled_cell(ws, current_row, 3, seg_id, fill_hex=alt, align_h="center")
        _styled_cell(ws, current_row, 4, f"{t_s:.1f}" if t_s is not None else "—",
                     fill_hex=alt, align_h="center")
        _styled_cell(ws, current_row, 5, f"{t_e:.1f}" if t_e is not None else "—",
                     fill_hex=alt, align_h="center")
        _styled_cell(ws, current_row, 6,
                     f"{pct_s:.1f}%" if pct_s is not None else "—",
                     fill_hex=alt, align_h="center")
        _styled_cell(ws, current_row, 7,
                     f"{pct_e:.1f}%" if pct_e is not None else "—",
                     fill_hex=alt, align_h="center")
        _styled_cell(ws, current_row, 8, emo_raw or "—",
                     fill_hex=alt, align_h="center")
        _styled_cell(ws, current_row, 9,
                     f"{emo_score:.2f}" if emo_score is not None else "—",
                     fill_hex=alt, align_h="center", number_format="0.00")
        _styled_cell(ws, current_row, 10, text or "", fill_hex=alt, wrap=True)
        current_row += 1


def _music_events_sheet(ws, conn) -> None:
    """
    Músicas identificadas no music_fx do vídeo via ShazamIO.
    Uma linha por evento (um trecho da música pode ser detectado várias vezes).
    """
    headers = [
        "Video ID", "Título", "Início (s)", "Fim (s)", "Dur (s)",
        "Faixa", "Artista", "Confiança", "Fonte",
    ]
    n_cols = len(headers)
    hdr_row = _sheet_title(ws, "MUSIC EVENTS — Músicas identificadas",
        "Fonte: Demucs (separa music_fx) + ShazamIO (identifica). Uma linha por detecção; vídeos sem música ficam ausentes desta aba.",
        n_cols, _COLORS["header_green"])
    _header(ws, headers, fill_hex=_COLORS["header_green"], start_row=hdr_row)
    _set_col_widths(ws, [14, 32, 10, 10, 8, 32, 24, 11, 14])

    rows = conn.execute("""
        SELECT me.video_id, v.title,
               me.t_start, me.t_end,
               ROUND(me.t_end - me.t_start, 2),
               me.track_name, me.artist, me.confidence, me.source
        FROM music_events me
        JOIN videos v ON me.video_id = v.video_id
        ORDER BY v.created_at DESC, me.video_id, me.t_start
    """).fetchall()

    current_row = hdr_row + 1
    last_vid = None
    for row in rows:
        vid_id, title = row[0], row[1]
        if vid_id != last_vid:
            current_row = _video_separator(ws, current_row, n_cols, title, vid_id)
            last_vid = vid_id
        alt = _COLORS["alt_green"] if current_row % 2 == 0 else None
        for col_i, val in enumerate(row, 1):
            _styled_cell(ws, current_row, col_i, val, fill_hex=alt,
                         wrap=(col_i in (6, 7)),
                         align_h="center" if col_i in (3, 4, 5, 8) else "left")
        current_row += 1


# ── ABA: comments (dado bruto da audiência) ──────────────────────────────────

def _comments_sheet(ws, conn) -> None:
    """Comentários brutos coletados via YouTube Data API v3."""
    headers = [
        "Video ID", "Título", "Autor", "Texto", "Likes",
        "Respostas", "Publicado em", "Coletado em",
    ]
    n_cols = len(headers)
    hdr_row = _sheet_title(ws, "COMMENTS — Comentários brutos",
        "Fonte: YouTube Data API v3 (commentThreads). Dado bruto da audiência, sem filtro ou classificação. Ordem: vídeo mais recente primeiro, dentro de cada vídeo por likes desc.",
        n_cols, _COLORS["header_green"])
    _header(ws, headers, fill_hex=_COLORS["header_green"], start_row=hdr_row)
    _set_col_widths(ws, [14, 32, 22, 70, 10, 10, 18, 18])

    rows = conn.execute("""
        SELECT c.video_id, v.title,
               c.author_pseudo, c.text, c.likes, c.reply_count,
               c.published_at, c.collected_at
        FROM comments c
        JOIN videos v ON c.video_id = v.video_id
        ORDER BY v.created_at DESC, c.video_id, c.likes DESC
    """).fetchall()

    current_row = hdr_row + 1
    last_vid = None
    for row in rows:
        vid_id, title = row[0], row[1]
        if vid_id != last_vid:
            current_row = _video_separator(ws, current_row, n_cols, title, vid_id)
            last_vid = vid_id
        alt = _COLORS["alt_green"] if current_row % 2 == 0 else None
        _styled_cell(ws, current_row, 1, vid_id, fill_hex=alt)
        _styled_cell(ws, current_row, 2, title, fill_hex=alt, wrap=True)
        _styled_cell(ws, current_row, 3, row[2] or "", fill_hex=alt)
        _styled_cell(ws, current_row, 4, row[3] or "", fill_hex=alt, wrap=True)
        _styled_cell(ws, current_row, 5, row[4] or 0, fill_hex=alt,
                     align_h="center", number_format="#,##0")
        _styled_cell(ws, current_row, 6, row[5] or 0, fill_hex=alt,
                     align_h="center", number_format="#,##0")
        _styled_cell(ws, current_row, 7, (row[6] or "")[:16].replace("T", " "),
                     fill_hex=alt, align_h="center")
        _styled_cell(ws, current_row, 8, (row[7] or "")[:16].replace("T", " "),
                     fill_hex=alt, align_h="center")
        current_row += 1


# ── Meta-header de fonte (agrupa colunas por origem) ─────────────────────────

def _source_meta_header(
    ws, row: int, groups: list[tuple[str, int, str]], n_cols: int,
) -> None:
    """
    Escreve uma linha-rótulo acima do header de colunas indicando a fonte de
    cada grupo de colunas. `groups` é uma lista de (label, n_cols_do_grupo, cor_hex).

    Exemplo: [("DADO BRUTO", 4, "455A64"), ("DistilBERT", 3, "1565C0"),
              ("pysentimiento", 2, "6A1B9A")]
    """
    col = 1
    for label, span, color in groups:
        end_col = col + span - 1
        cell = ws.cell(row=row, column=col, value=label)
        cell.fill = PatternFill("solid", fgColor=color)
        cell.font = Font(bold=True, color="FFFFFF", size=9, name="Calibri")
        cell.alignment = Alignment(horizontal="center", vertical="center")
        # pintar todas as células do grupo antes do merge
        for c in range(col, end_col + 1):
            ws.cell(row=row, column=c).fill = PatternFill("solid", fgColor=color)
        if span > 1:
            ws.merge_cells(start_row=row, start_column=col,
                           end_row=row, end_column=end_col)
        col = end_col + 1
    ws.row_dimensions[row].height = 20


# ── Natureza do dado: brutos / derivados / inferidos ─────────────────────────

_NATURE_COLORS = {
    # (label_curto, cor_fundo, cor_texto)
    "bruto":    ("BRUTO",    "1565C0", "FFFFFF"),  # azul — vem direto da API / sensor
    "derivado": ("DERIVADO", "F9A825", "000000"),  # amarelo — processamento determinístico
    "inferido": ("INFERIDO", "C62828", "FFFFFF"),  # vermelho — classificação com confidence
}


def _nature_meta_header(ws, row: int, natures_per_col: list[str], n_cols: int) -> None:
    """
    Escreve meta-linha de natureza do dado acima do header de colunas.
    `natures_per_col` tem length == n_cols; cada entrada é "bruto"|"derivado"|"inferido".

    Colunas contíguas da mesma natureza ficam merged num bloco colorido.
    """
    # Agrupa sequências contíguas de mesma natureza
    groups = []
    i = 0
    while i < len(natures_per_col):
        j = i
        while j < len(natures_per_col) and natures_per_col[j] == natures_per_col[i]:
            j += 1
        groups.append((natures_per_col[i], j - i))
        i = j

    col = 1
    for nature, span in groups:
        label, bg, fg = _NATURE_COLORS[nature]
        end_col = col + span - 1
        cell = ws.cell(row=row, column=col, value=label)
        cell.fill = PatternFill("solid", fgColor=bg)
        cell.font = Font(bold=True, color=fg, size=9, name="Calibri")
        cell.alignment = Alignment(horizontal="center", vertical="center")
        for c in range(col, end_col + 1):
            ws.cell(row=row, column=c).fill = PatternFill("solid", fgColor=bg)
        if span > 1:
            ws.merge_cells(start_row=row, start_column=col,
                           end_row=row, end_column=end_col)
        col = end_col + 1
    ws.row_dimensions[row].height = 18


def _legenda_sheet(ws, conn=None) -> None:
    """
    Aba-legenda que documenta a natureza de cada campo do banco.

    Três níveis:
      BRUTO    — dado vem direto da API / sensor / fonte primária (sem modelo)
      DERIVADO — processamento determinístico (Whisper/Demucs/librosa) sem juízo
      INFERIDO — classificação com confidence (DistilBERT/pysentimiento/rule-based)
    """
    title_row = 1
    cell = ws.cell(row=title_row, column=1, value="LEGENDA — Natureza dos dados no banco")
    cell.font = Font(bold=True, size=13, color="FFFFFF", name="Calibri")
    cell.fill = PatternFill("solid", fgColor=_COLORS["group_synthesis"])
    cell.alignment = Alignment(vertical="center")
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=5)
    for c in range(2, 6):
        ws.cell(row=1, column=c).fill = PatternFill("solid", fgColor=_COLORS["group_synthesis"])
    ws.row_dimensions[1].height = 30

    # Subtítulo
    subtitle = (
        "Referência metodológica: cada campo do cultural_fingerprint e tabelas auditáveis "
        "é classificado por sua natureza. BRUTOS vêm direto da fonte; DERIVADOS passam por "
        "processamento determinístico; INFERIDOS trazem juízo de modelo + score de confiança."
    )
    cell2 = ws.cell(row=2, column=1, value=subtitle)
    cell2.font = Font(size=9, color="666666", italic=True, name="Calibri")
    cell2.alignment = Alignment(vertical="center", wrap_text=True)
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=5)
    ws.row_dimensions[2].height = 36

    # Paleta visual
    ws.cell(row=3, column=1, value="PALETA VISUAL").font = Font(bold=True, size=10)
    row = 4
    for nature, (label, bg, fg) in _NATURE_COLORS.items():
        legend_cell = ws.cell(row=row, column=1, value=label)
        legend_cell.fill = PatternFill("solid", fgColor=bg)
        legend_cell.font = Font(bold=True, color=fg, size=10, name="Calibri")
        legend_cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.cell(row=row, column=2, value={
            "bruto":    "Dado direto da fonte primária (API, medição física, arquivo).",
            "derivado": "Resultado determinístico de processamento (ASR, separação de fontes, features acústicas).",
            "inferido": "Classificação de modelo com score de confiança — carrega incerteza herdada.",
        }[nature]).alignment = Alignment(wrap_text=True, vertical="center")
        ws.row_dimensions[row].height = 22
        row += 1

    # Tabela detalhada
    row += 1
    ws.cell(row=row, column=1, value="MAPEAMENTO COMPLETO CAMPO → NATUREZA → FONTE").font = Font(bold=True, size=10)
    row += 1
    hdr = ["Campo", "Natureza", "Fonte / Processador", "Escala / Formato", "Onde aparece"]
    for i, h in enumerate(hdr, 1):
        c = ws.cell(row=row, column=i, value=h)
        c.font = Font(bold=True, color="FFFFFF", size=10, name="Calibri")
        c.fill = PatternFill("solid", fgColor="37474F")
        c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[row].height = 22
    row += 1

    # ── Conteúdo da tabela: (campo, natureza, fonte, escala, aba) ────────────
    ENTRIES = [
        # BRUTOS
        ("channel_name, title, description, publish_date, duration_seconds",
         "bruto", "YouTube Data API v3", "texto / ISO8601", "videos, resumo"),
        ("tags, hashtags, category_id, language, country",
         "bruto", "YouTube Data API v3", "texto", "videos"),
        ("views, likes, comments (contagens)",
         "bruto", "YouTube Metrics API", "inteiro ≥ 0", "metrics, resumo"),
        ("comments.text, author_pseudo, likes, published_at",
         "bruto", "YouTube Comments API", "texto / ISO8601", "comments"),
        ("thumbnail_url + arquivo .mp3",
         "bruto", "yt-dlp", "URL / binário", "videos"),
        # DERIVADOS
        ("transcripts.text, transcript_segments.text",
         "derivado", "Whisper large-v3 (ASR)", "texto PT-BR", "transcripts"),
        ("vocals.wav, music_fx.wav, stems individuais",
         "derivado", "Demucs htdemucs_ft", "arquivo WAV", "—"),
        ("volume_db, pitch_hz, speech_rate",
         "derivado", "librosa (análise acústica)", "dB / Hz / palavras·s⁻¹", "audio_features"),
        ("speech_presence_pct, music_presence_pct",
         "derivado", "timeline de Whisper+Demucs", "% não-exclusiva", "cultural_fingerprint"),
        ("music_events.song_title, artist, t_start, t_end",
         "derivado", "ShazamIO (match de áudio)", "texto / segundos", "music_events"),
        ("emoji_stats: emoji + contagem",
         "derivado", "contagem literal por regex unicode", "inteiro ≥ 0", "emoji_stats"),
        # INFERIDOS
        ("speech_analysis.utterance_type (pergunta/CTA/chamada_atenção/afirmação)",
         "inferido", "regras regex PT-BR", "categórico", "speech_analysis"),
        ("speech_analysis.language_register (gíria/informal/formal)",
         "inferido", "vocabulário rule-based + heurística", "categórico", "speech_analysis"),
        ("speech_analysis.sentiment + sentiment_score",
         "inferido", "DistilBERT (multilingual sentiments-student)", "label + [-1,+1]", "speech_analysis, analise_segmentos"),
        ("speech_analysis.emotion_raw + emotion_score",
         "inferido", "pysentimiento/bert-pt-emotion (GoEmotions)", "1 de 28 labels + [0,1]", "analise_segmentos"),
        ("emotion_curve.emotion_raw + emotion_score + t_*_pct",
         "inferido", "pysentimiento aplicado por segmento", "label + [0,1]", "emotion_curve"),
        ("comment_sentiments.sentiment + sentiment_score",
         "inferido", "DistilBERT aplicado por comentário", "label + [-1,+1]", "comment_sentiment, analise_comentarios"),
        ("comment_sentiments.emotion_raw + emotion_score",
         "inferido", "pysentimiento aplicado por comentário", "label + [0,1]", "analise_comentarios"),
        ("comment_sentiment_agg.sentiment_avg / _std / _ratio / emotion_distribution",
         "inferido", "agregação sobre classificações individuais", "estatísticas ∈ [-1,+1] / [0,1]", "comment_sentiment_agg"),
        ("cultural_fingerprint.dominant_register, emotion_dominant, audience_emotion_dominant",
         "inferido", "moda ou rank-1 das inferências", "categórico (herda incerteza)", "cultural_fingerprint"),
        ("cultural_fingerprint.top_5_words, comment_top_words, top_emojis",
         "inferido", "ranking sobre texto tokenizado + stopwords PT", "ordenado por frequência", "cultural_fingerprint"),
        ("cultural_fingerprint.question_ratio, cta_ratio, attention_ratio",
         "inferido", "fração de utterance_types sobre total de segmentos", "[0, 1]", "cultural_fingerprint"),

    ]

    ws.column_dimensions["A"].width = 62
    ws.column_dimensions["B"].width = 12
    ws.column_dimensions["C"].width = 45
    ws.column_dimensions["D"].width = 32
    ws.column_dimensions["E"].width = 38

    for field, nature, source, scale, where in ENTRIES:
        label, bg, fg = _NATURE_COLORS[nature]
        _styled_cell(ws, row, 1, field, wrap=True)
        nc = ws.cell(row=row, column=2, value=label)
        nc.fill = PatternFill("solid", fgColor=bg)
        nc.font = Font(bold=True, color=fg, size=9, name="Calibri")
        nc.alignment = Alignment(horizontal="center", vertical="center")
        nc.border = _THIN_BORDER
        _styled_cell(ws, row, 3, source, wrap=True)
        _styled_cell(ws, row, 4, scale, wrap=True, align_h="center")
        _styled_cell(ws, row, 5, where, wrap=True)
        ws.row_dimensions[row].height = 26
        row += 1

    # Nota de rodapé
    row += 1
    note = ws.cell(row=row, column=1, value=(
        "NOTA — Quando uma métrica agregada depende de campos inferidos (ex: emotion_distribution, "
        "sentiment_avg), ela é classificada como INFERIDO e HERDA a incerteza dos modelos subjacentes. "
        "A cadeia de fonte fica rastreável via classification_evidence nas tabelas speech_analysis e comment_sentiments."
    ))
    note.font = Font(size=9, italic=True, color="666666", name="Calibri")
    note.alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=5)
    ws.row_dimensions[row].height = 50


# ── ABA: analise_comentarios (auditabilidade completa) ───────────────────────

_SENTIMENT_NUMERIC = {
    "positivo": 1, "positive": 1,
    "negativo": -1, "negative": -1,
    "neutro": 0, "neutral": 0,
}


def _analise_comentarios_sheet(ws, conn) -> None:
    """
    Camada de auditabilidade completa: uma linha por comentário, com a label
    e score de cada modelo separadamente (DistilBERT + pysentimiento).

    Permite rastrear como cada comentário foi classificado, por qual modelo.
    """
    headers = [
        "Video ID", "Título",                     # dado bruto: 2
        "Texto do Comentário", "Likes",           # dado bruto: 2
        "sentiment", "sentiment_score", "sentiment_numeric",  # DistilBERT: 3
        "emotion_raw", "emotion_score",           # pysentimiento: 2
    ]
    n_cols = len(headers)
    hdr_row = _sheet_title(ws, "ANÁLISE DE COMENTÁRIOS — Auditabilidade completa",
        "Uma linha por comentário com a classificação de cada modelo separadamente. "
        "Permite verificar como cada comentário foi classificado, por qual modelo e com qual confiança. "
        "Ver aba 'legenda' para o mapeamento completo natureza → fonte.",
        n_cols, _COLORS["header_indigo"])

    # Meta-linha 1: natureza do dado (bruto/derivado/inferido)
    _nature_meta_header(ws, hdr_row, [
        "bruto", "bruto", "bruto", "bruto",
        "inferido", "inferido", "inferido",
        "inferido", "inferido",
    ], n_cols)

    # Meta-linha 2: fonte / modelo que produziu
    _source_meta_header(ws, hdr_row + 1, [
        ("DADO BRUTO (YouTube API)", 4, "455A64"),
        ("DistilBERT (sentimento multilingual)", 3, "1565C0"),
        ("pysentimiento/bert-pt-emotion (28 labels GoEmotions)", 2, "6A1B9A"),
    ], n_cols)

    _header(ws, headers, fill_hex=_COLORS["header_indigo"], start_row=hdr_row + 2)
    _set_col_widths(ws, [14, 32, 60, 10, 14, 14, 14, 18, 11])

    rows = conn.execute("""
        SELECT cs.video_id, v.title, cs.text, cs.likes,
               cs.sentiment, cs.sentiment_score,
               cs.emotion_raw, cs.emotion_score
        FROM comment_sentiments cs
        JOIN videos v ON cs.video_id = v.video_id
        ORDER BY v.created_at DESC, cs.video_id, cs.likes DESC
    """).fetchall()

    sent_colors = {
        "positivo": ("4CAF50", "FFFFFF"), "positive": ("4CAF50", "FFFFFF"),
        "negativo": ("F44336", "FFFFFF"), "negative": ("F44336", "FFFFFF"),
        "neutro":   ("E0E0E0", "333333"), "neutral":  ("E0E0E0", "333333"),
    }

    current_row = hdr_row + 3  # nature@hdr_row, source@hdr_row+1, header@hdr_row+2
    last_vid = None
    for row in rows:
        vid_id, title, text, likes, sentiment, score, emotion_raw, emo_score = row
        if vid_id != last_vid:
            current_row = _video_separator(ws, current_row, n_cols, title, vid_id)
            last_vid = vid_id

        alt = _COLORS["alt_blue"] if current_row % 2 == 0 else None
        s_bg, s_fg = sent_colors.get((sentiment or "").lower(), (alt, "333333"))
        num = _SENTIMENT_NUMERIC.get((sentiment or "").lower(), None)

        _styled_cell(ws, current_row, 1, vid_id, fill_hex=alt)
        _styled_cell(ws, current_row, 2, title, fill_hex=alt, wrap=True)
        _styled_cell(ws, current_row, 3, text or "", fill_hex=alt, wrap=True)
        _styled_cell(ws, current_row, 4, likes or 0, fill_hex=alt,
                     align_h="center", number_format="#,##0")
        _styled_cell(ws, current_row, 5, (sentiment or "—").upper(),
                     fill_hex=s_bg, font_color=s_fg, align_h="center", bold=True)
        _styled_cell(ws, current_row, 6,
                     f"{score:+.4f}" if score is not None else "—",
                     fill_hex=alt, align_h="center",
                     number_format="+0.0000;-0.0000;0.0000")
        _styled_cell(ws, current_row, 7,
                     num if num is not None else "—",
                     fill_hex=alt, align_h="center")
        _styled_cell(ws, current_row, 8, emotion_raw or "—",
                     fill_hex=alt, align_h="center")
        _styled_cell(ws, current_row, 9,
                     f"{emo_score:.4f}" if emo_score is not None else "—",
                     fill_hex=alt, align_h="center", number_format="0.0000")
        current_row += 1


# ── ABA: analise_segmentos (auditabilidade completa da fala) ─────────────────

def _analise_segmentos_sheet(ws, conn) -> None:
    """
    Uma linha por segmento de fala com todas as classificações de cada modelo
    separadamente (rule-based + DistilBERT + pysentimiento) + evidência.
    """
    headers = [
        "Video ID", "Título",                               # dado bruto: 2
        "t_start", "t_end", "t_start_pct", "t_end_pct",     # temporais: 4
        "Texto do Segmento",                                # dado bruto: 1
        "utterance_type", "register",                       # rule-based: 2
        "sentiment", "sentiment_score",                     # DistilBERT: 2
        "emotion_raw", "emotion_score",                     # pysentimiento: 2
        "classification_evidence",                          # auditabilidade: 1
    ]
    n_cols = len(headers)
    hdr_row = _sheet_title(ws, "ANÁLISE DE SEGMENTOS — Auditabilidade completa",
        "Uma linha por segmento de fala com a classificação de cada modelo separadamente. "
        "Cada célula pode ser rastreada até a regra/modelo que a produziu (classification_evidence). "
        "Ver aba 'legenda' para o mapeamento completo natureza → fonte.",
        n_cols, _COLORS["header_indigo"])

    # Meta-linha 1: natureza do dado (14 cols)
    # Video ID, Título = bruto (YouTube API)
    # t_start, t_end, t_start_pct, t_end_pct, texto = derivado (Whisper ASR objetivo)
    # utterance_type, register, sentiment, sentiment_score, emotion_raw, emotion_score, evidence = inferido
    _nature_meta_header(ws, hdr_row, [
        "bruto", "bruto",
        "derivado", "derivado", "derivado", "derivado", "derivado",
        "inferido", "inferido", "inferido", "inferido", "inferido", "inferido", "inferido",
    ], n_cols)

    # Meta-linha 2: fonte
    _source_meta_header(ws, hdr_row + 1, [
        ("DADO BRUTO (YouTube API)", 2, "455A64"),
        ("Whisper ASR (texto + timing)", 5, "37474F"),
        ("Regras heurísticas", 2, "2E7D32"),
        ("DistilBERT", 2, "1565C0"),
        ("pysentimiento", 2, "6A1B9A"),
        ("Auditoria", 1, "B71C1C"),
    ], n_cols)

    _header(ws, headers, fill_hex=_COLORS["header_indigo"], start_row=hdr_row + 2)
    _set_col_widths(ws, [14, 28, 8, 8, 10, 10, 55, 16, 11, 12, 14, 16, 11, 45])

    rows = conn.execute("""
        SELECT sa.video_id, v.title, v.duration_seconds,
               sa.t_start, sa.t_end, sa.text,
               sa.utterance_type, sa.language_register,
               sa.sentiment, sa.sentiment_score, sa.classification_evidence,
               sa.emotion_raw, sa.emotion_score
        FROM speech_analysis sa
        JOIN videos v ON sa.video_id = v.video_id
        ORDER BY v.created_at DESC, sa.video_id, sa.t_start
    """).fetchall()

    sent_colors = {
        "positivo": ("4CAF50", "FFFFFF"), "positive": ("4CAF50", "FFFFFF"),
        "negativo": ("F44336", "FFFFFF"), "negative": ("F44336", "FFFFFF"),
        "neutro":   ("E0E0E0", "333333"), "neutral":  ("E0E0E0", "333333"),
    }

    current_row = hdr_row + 3  # nature@hdr_row, source@hdr_row+1, header@hdr_row+2
    last_vid = None
    for row in rows:
        (vid_id, title, dur_s,
         t_start, t_end, text,
         utype, register, sentiment, sent_score, evidence,
         emotion_raw, emo_score) = row

        if vid_id != last_vid:
            current_row = _video_separator(ws, current_row, n_cols, title, vid_id)
            last_vid = vid_id

        alt = _COLORS["alt_blue"] if current_row % 2 == 0 else None
        s_bg, s_fg = sent_colors.get((sentiment or "").lower(), (alt, "333333"))

        pct_s = round(t_start / dur_s * 100, 2) if dur_s else None
        pct_e = round(t_end   / dur_s * 100, 2) if dur_s else None

        _styled_cell(ws, current_row, 1, vid_id, fill_hex=alt)
        _styled_cell(ws, current_row, 2, title, fill_hex=alt, wrap=True)
        _styled_cell(ws, current_row, 3, f"{t_start:.2f}" if t_start is not None else "—",
                     fill_hex=alt, align_h="center")
        _styled_cell(ws, current_row, 4, f"{t_end:.2f}" if t_end is not None else "—",
                     fill_hex=alt, align_h="center")
        _styled_cell(ws, current_row, 5,
                     f"{pct_s:.2f}%" if pct_s is not None else "—",
                     fill_hex=alt, align_h="center")
        _styled_cell(ws, current_row, 6,
                     f"{pct_e:.2f}%" if pct_e is not None else "—",
                     fill_hex=alt, align_h="center")
        _styled_cell(ws, current_row, 7, text or "", fill_hex=alt, wrap=True)
        _styled_cell(ws, current_row, 8, utype or "—",
                     fill_hex=alt, align_h="center")
        _styled_cell(ws, current_row, 9, register or "—",
                     fill_hex=alt, align_h="center")
        _styled_cell(ws, current_row, 10, (sentiment or "—").upper(),
                     fill_hex=s_bg, font_color=s_fg, align_h="center", bold=True)
        _styled_cell(ws, current_row, 11,
                     f"{sent_score:+.4f}" if sent_score is not None else "—",
                     fill_hex=alt, align_h="center",
                     number_format="+0.0000;-0.0000;0.0000")
        _styled_cell(ws, current_row, 12, emotion_raw or "—",
                     fill_hex=alt, align_h="center")
        _styled_cell(ws, current_row, 13,
                     f"{emo_score:.4f}" if emo_score is not None else "—",
                     fill_hex=alt, align_h="center", number_format="0.0000")
        _styled_cell(ws, current_row, 14, evidence or "—",
                     fill_hex=alt, wrap=True)
        current_row += 1


# ── Aba separadora de grupo ──────────────────────────────────────────────────

def _separator_sheet(ws, title: str, subtitle: str, fill_hex: str) -> None:
    """
    Cria uma aba visual separadora de grupo — sem dados, apenas título grande
    centralizado e cor de guia para marcar o início de um grupo de abas.
    """
    # Cor da guia (tab) — visível na barra inferior do Excel
    try:
        ws.sheet_properties.tabColor = fill_hex
    except Exception:
        pass

    # Pintar várias colunas de largura para a cor se estender horizontalmente
    n_cols = 10
    for c in range(1, n_cols + 1):
        ws.column_dimensions[get_column_letter(c)].width = 18

    # Título grande ocupando uma faixa de linhas
    for r in range(1, 8):
        for c in range(1, n_cols + 1):
            cell = ws.cell(row=r, column=c)
            cell.fill = PatternFill("solid", fgColor=fill_hex)
        ws.row_dimensions[r].height = 30

    title_cell = ws.cell(row=4, column=1, value=title)
    title_cell.font = Font(bold=True, size=26, color="FFFFFF", name="Calibri")
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.merge_cells(start_row=4, start_column=1, end_row=4, end_column=n_cols)

    sub_cell = ws.cell(row=6, column=1, value=subtitle)
    sub_cell.font = Font(size=11, color="FFFFFF", italic=True, name="Calibri")
    sub_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.merge_cells(start_row=6, start_column=1, end_row=6, end_column=n_cols)

    # Esconde grid lines pra visual mais limpo
    ws.sheet_view.showGridLines = False


# ── EXPORT PRINCIPAL ──────────────────────────────────────────────────────────

# Registro de abas disponíveis, agrupadas na ordem canônica do workbook.
# Cada builder tem assinatura (ws, conn).
_SHEET_GROUPS: list = [
    ("▓ ESTÍMULO ▓", "ESTÍMULO DO VÍDEO",
     "Tudo que o criador emite: fala, áudio, música, linguagem.",
     "group_stimulus", [
        ("videos", "_videos_sheet"),
        ("timeline_visual", "_timeline_visual_sheet"),
        ("transcripts", "_transcripts_sheet"),
        ("speech_analysis", "_speech_analysis_sheet"),
        ("audio_features", "_audio_features_sheet"),
        ("timeline_events", "_timeline_events_sheet"),
        ("emotion_curve", "_emotion_curve_sheet"),
        ("music_events", "_music_events_sheet"),
        ("lexical_profile", "_lexical_profile_sheet"),
        ("analise_segmentos", "_analise_segmentos_sheet"),
    ]),
    ("▓ RESPOSTA ▓", "RESPOSTA COMPORTAMENTAL",
     "Como a audiência reage: métricas, comentários, sentimento, emojis.",
     "group_response", [
        ("metrics", "_metrics_sheet"),
        ("comments", "_comments_sheet"),
        ("comment_sentiment", "_comment_sentiment_sheet"),
        ("comment_sentiment_agg", "_comment_sentiment_agg_sheet"),
        ("emoji_stats", "_emoji_stats_sheet"),
        ("analise_comentarios", "_analise_comentarios_sheet"),
    ]),
    ("▓ SÍNTESE ▓", "SÍNTESE",
     "Objeto agregado comparável entre vídeos: o fingerprint cultural + referência metodológica.",
     "group_synthesis", [
        ("cultural_fingerprint", "_cultural_fingerprint_sheet"),
        ("legenda", "_legenda_sheet"),
    ]),
]


def available_sheets() -> list:
    """Nomes de todas as abas disponíveis, na ordem do workbook."""
    return ["resumo"] + [name for *_g, tabs in _SHEET_GROUPS for name, _ in tabs]


def export_to_excel(conn, output_path: str, sheets: "list | None" = None) -> None:
    """Gera o Excel a partir do banco, com seleção opcional de abas.

    Args:
        conn: conexão SQLite.
        output_path: caminho do .xlsx.
        sheets: lista de nomes de abas a incluir (ver available_sheets()).
            None = workbook completo. Grupos sem nenhuma aba selecionada não
            ganham separador.

    Ordem completa:
      resumo (dashboard geral)
      [SEPARADOR] ESTÍMULO DO VÍDEO
        videos, timeline_visual, transcripts, speech_analysis, audio_features,
        timeline_events, emotion_curve, music_events, lexical_profile,
        analise_segmentos
      [SEPARADOR] RESPOSTA COMPORTAMENTAL
        metrics, comments, comment_sentiment, comment_sentiment_agg,
        emoji_stats, analise_comentarios
      [SEPARADOR] SÍNTESE
        cultural_fingerprint, legenda
    """
    if sheets is not None:
        unknown = set(sheets) - set(available_sheets())
        if unknown:
            raise ValueError(f"Abas desconhecidas: {sorted(unknown)}. "
                             f"Disponíveis: {available_sheets()}")

    def _wants(name: str) -> bool:
        return sheets is None or name in sheets

    wb = Workbook()
    wb.remove(wb.active)

    # ── Dashboard geral (antes de todos os grupos) ─────────────────────
    if _wants("resumo"):
        _resumo_sheet(wb.create_sheet("resumo"), conn)

    builders = globals()
    for sep_title, group_title, group_desc, color_key, tabs in _SHEET_GROUPS:
        selected = [(name, fn) for name, fn in tabs if _wants(name)]
        if not selected:
            continue
        _separator_sheet(wb.create_sheet(sep_title), group_title,
                         group_desc, _COLORS[color_key])
        for name, fn in selected:
            builders[fn](wb.create_sheet(name), conn)

    if not wb.sheetnames:
        raise ValueError("Nenhuma aba selecionada — workbook vazio.")

    wb.save(output_path)


# ── ABA: timeline_visual ──────────────────────────────────────────────────────

def _timeline_visual_sheet(ws, conn) -> None:
    """
    Linha do tempo tabular: uma linha por segmento, todas as dimensões juntas.

    É a leitura horizontal do vídeo — o que foi dito, como foi dito e o que a
    máquina inferiu disso, lado a lado. A curva tríplice mostra a forma; esta
    aba mostra o conteúdo que produziu a forma.
    """
    headers = [
        "#", "Início", "Fim", "Dur",
        "Texto", "Tipo", "Registro",
        "Sentimento", "Emoção", "Score",
        "Tom de Voz", "Volume (dB)", "Pitch (Hz)", "Vel. Fala",
        "Música no momento",
    ]
    n_cols = len(headers)
    _sheet_title(ws, "TIMELINE VISUAL — o vídeo lido segmento a segmento",
        "Uma linha por segmento de fala, com o texto transcrito e todas as "
        "camadas medidas sobre ele. Fonte: speech_analysis × audio_features × "
        "music_events, alinhados pelo tempo de início.",
        n_cols, _COLORS["header_indigo"])
    _set_col_widths(ws, [5, 9, 9, 7, 62, 15, 11, 12, 15, 8, 13, 12, 12, 10, 28])

    sent_cores = {"positivo": "C8E6C9", "negativo": "FFCDD2", "neutro": "EEEEEE"}
    reg_cores = {"gíria": "FFE0B2", "informal": "FFF9C4", "formal": "E1F5FE"}

    linha = ws.max_row + 2
    videos = conn.execute(
        "SELECT video_id, title, duration_seconds FROM videos "
        "ORDER BY created_at DESC").fetchall()

    for idx, (vid, titulo, duracao) in enumerate(videos, 1):
        linha = _video_separator(ws, linha, n_cols, titulo, vid)
        _header(ws, headers, fill_hex=_COLORS["header_indigo"], start_row=linha)
        linha += 1

        segmentos = conn.execute("""
            SELECT t_start, t_end, text, utterance_type, language_register,
                   sentiment, emotion_raw, emotion_score
            FROM speech_analysis WHERE video_id = ? ORDER BY t_start
        """, (vid,)).fetchall()

        # audio_features compartilha a fronteira de tempo com speech_analysis
        prosodia = {
            round(float(r[0]), 2): r[1:]
            for r in conn.execute("""
                SELECT t_start, voice_register, volume_db, pitch_hz, speech_rate
                FROM audio_features WHERE video_id = ?
            """, (vid,))
        }

        musicas = conn.execute("""
            SELECT t_start, t_end, track_name, artist FROM music_events
            WHERE video_id = ? ORDER BY t_start
        """, (vid,)).fetchall()

        for n, seg in enumerate(segmentos, 1):
            (t0, t1, texto, tipo, registro,
             sentimento, emocao, emo_score) = seg
            alt = "FAFAFA" if n % 2 == 0 else None
            pros = prosodia.get(round(float(t0 or 0), 2), (None,) * 4)

            tocando = next(
                (f"{m[2] or '?'} — {m[3] or '?'}"
                 for m in musicas
                 if (m[0] or 0) <= (t0 or 0) <= (m[1] or 0)), "")

            _styled_cell(ws, linha, 1, n, fill_hex=alt, align_h="center")
            _styled_cell(ws, linha, 2, f"{t0:.1f}s" if t0 is not None else "—",
                         fill_hex=alt, align_h="center")
            _styled_cell(ws, linha, 3, f"{t1:.1f}s" if t1 is not None else "—",
                         fill_hex=alt, align_h="center")
            _styled_cell(ws, linha, 4,
                         f"{(t1 - t0):.1f}s" if None not in (t0, t1) else "—",
                         fill_hex=alt, align_h="center")
            _styled_cell(ws, linha, 5, texto or "", fill_hex=alt, wrap=True)
            _styled_cell(ws, linha, 6, tipo or "—", fill_hex=alt, align_h="center")
            _styled_cell(ws, linha, 7, registro or "—",
                         fill_hex=reg_cores.get(registro, alt), align_h="center")
            _styled_cell(ws, linha, 8, sentimento or "—",
                         fill_hex=sent_cores.get(sentimento, alt), align_h="center")
            _styled_cell(ws, linha, 9, emocao or "—", fill_hex=alt, align_h="center")
            _styled_cell(ws, linha, 10,
                         f"{emo_score:.2f}" if emo_score is not None else "—",
                         fill_hex=alt, align_h="center", number_format="0.00")
            _styled_cell(ws, linha, 11, pros[0] or "—", fill_hex=alt, align_h="center")
            _styled_cell(ws, linha, 12,
                         f"{pros[1]:.1f}" if pros[1] is not None else "—",
                         fill_hex=alt, align_h="center", number_format="0.0")
            _styled_cell(ws, linha, 13,
                         f"{pros[2]:.0f}" if pros[2] is not None else "—",
                         fill_hex=alt, align_h="center", number_format="0")
            _styled_cell(ws, linha, 14,
                         f"{pros[3]:.2f}" if pros[3] is not None else "—",
                         fill_hex=alt, align_h="center", number_format="0.00")
            _styled_cell(ws, linha, 15, tocando, fill_hex=alt, wrap=True)
            linha += 1

        linha += 1
