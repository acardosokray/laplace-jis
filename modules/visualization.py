"""
modules/visualization.py — Curva emocional tríplice (Matplotlib PNG).

Gera a visualização do Laplace: três painéis verticais empilhados
compartilhando eixo X normalizado [0%, 100%] da duração do vídeo.

  Painel 1 — Tom de Voz (pitch em Hz)
            curva suavizada de pitch_hz (f0 via librosa.pyin), valores reais
            em Hz no eixo Y. Mediana desenhada como linha tracejada.
            Picos e vales anotados com o valor exato em Hz.

  Painel 2 — Valência Sentimental (DistilBERT)
            sentiment_score por segmento de speech_analysis, faixa [-1, +1]
            verde acima do zero, vermelho abaixo, cinza em zero
            preenchimento bidirecional + pontos por segmento
            extremos e segmentos com |score|>=0.85 anotados com valor exato
            linha tracejada horizontal = média do sentimento da fala do vídeo

  Painel 3 — Curva Emocional (pysentimiento)
            trapezóide por segmento de emotion_curve, altura = emotion_score,
            cor fixa por label (paleta GoEmotions de 28 cores)
            labels SEMPRE visíveis: horizontais em blocos largos, verticais
            em médios, callouts alternados acima do painel em estreitos

Pré-requisito: pip install matplotlib

Uso programático:
    from modules.visualization import generate_triple_view
    generate_triple_view("25y3jRRs5Qc", output_dir="media/visualizations")
"""

import os
import re
import sqlite3
from typing import Optional

import numpy as np


# ── Paleta determinística GoEmotions (28 labels) ─────────────────────────────

GOEMOTIONS_PALETTE = {
    # Alegria (amarelo-dourado)
    "joy":            "#FFC107",
    "amusement":      "#FFD54F",
    "excitement":     "#FFEB3B",
    "optimism":       "#FFEE58",
    "pride":          "#F9A825",
    # Raiva (vermelho)
    "anger":          "#C62828",
    "annoyance":      "#E57373",
    "disapproval":    "#EF5350",
    # Surpresa (laranja)
    "surprise":       "#FF9800",
    "realization":    "#FFB74D",
    "confusion":      "#FFA726",
    # Medo (roxo)
    "fear":           "#6A1B9A",
    "nervousness":    "#7E57C2",
    "embarrassment":  "#9575CD",
    # Tristeza (azul)
    "sadness":        "#1565C0",
    "grief":          "#0D47A1",
    "disappointment": "#1976D2",
    "remorse":        "#1E88E5",
    # Confiança (verde)
    "admiration":     "#2E7D32",
    "gratitude":      "#388E3C",
    "caring":         "#43A047",
    "love":           "#66BB6A",
    "approval":       "#81C784",
    "relief":         "#A5D6A7",
    # Antecipação (ciano)
    "curiosity":      "#00ACC1",
    "desire":         "#00838F",
    # Desgosto / Neutro
    "disgust":        "#4E342E",
    "neutral":        "#9E9E9E",
}

# ── Cores fixas dos painéis ──────────────────────────────────────────────────

_COLOR_ENERGY_LINE   = "#1F3864"   # azul escuro
_COLOR_ENERGY_FILL   = "#1F3864"   # alpha aplicado no fill
_COLOR_VALENCE_POS   = "#2E7D32"   # verde (polo positivo)
_COLOR_VALENCE_NEG   = "#C62828"   # vermelho (polo negativo)
_COLOR_VALENCE_ZERO  = "#9E9E9E"   # cinza neutro no meio
_COLOR_AXIS_REF      = "#BDBDBD"   # linha de zero / médias
_COLOR_PANEL_BG      = "#FAFAFA"


# ── Carregamento de dados do banco ───────────────────────────────────────────

def load_video_data(conn: sqlite3.Connection, video_id: str) -> dict:
    """
    Lê do banco tudo que a visualização precisa. Retorna:
      {
        "video_id":         str,
        "title":            str,
        "channel_name":     str,
        "duration_seconds": int | None,
        "audio_features":   list[dict]  (t_start, t_end, volume_db, pitch_hz, speech_rate),
        "speech_segments":  list[dict]  (t_start, t_end, sentiment_score, sentiment),
        "emotion_segments": list[dict]  (t_start_pct, t_end_pct, emotion_raw, emotion_score),
        "sentiment_avg":    float | None,
      }
    Listas ausentes ficam vazias (permite gráfico parcial).
    """
    meta = conn.execute("""
        SELECT title, channel_name, duration_seconds
        FROM videos WHERE video_id = ?
    """, (video_id,)).fetchone()
    if meta is None:
        raise ValueError(f"Vídeo {video_id!r} não encontrado no banco.")

    audio = [dict(zip(("t_start", "t_end", "volume_db", "pitch_hz",
                       "speech_rate", "voice_register"), r))
             for r in conn.execute("""
        SELECT t_start, t_end, volume_db, pitch_hz, speech_rate, voice_register
        FROM audio_features WHERE video_id = ?
        ORDER BY t_start
    """, (video_id,)).fetchall()]

    speech = [dict(zip(("t_start", "t_end", "sentiment_score", "sentiment",
                        "text", "utterance_type"), r))
              for r in conn.execute("""
        SELECT t_start, t_end, sentiment_score, sentiment, text, utterance_type
        FROM speech_analysis WHERE video_id = ?
        ORDER BY t_start
    """, (video_id,)).fetchall()]

    emotion = [dict(zip(("t_start_pct", "t_end_pct", "emotion_raw", "emotion_score",
                         "segment_text"), r))
               for r in conn.execute("""
        SELECT t_start_pct, t_end_pct, emotion_raw, emotion_score, segment_text
        FROM emotion_curve WHERE video_id = ?
        ORDER BY t_start_pct
    """, (video_id,)).fetchall()]

    # Lê do cultural_fingerprint (valores persistidos — bate 1:1 com Excel).
    # Assim a linha "avg" do Painel 2 e a "mediana" do Painel 1 referenciam
    # exatamente as mesmas células que aparecem na aba cultural_fingerprint.
    fp_row = conn.execute("""
        SELECT speech_sent_avg, median_pitch_hz
        FROM cultural_fingerprint WHERE video_id = ?
    """, (video_id,)).fetchone()
    speech_sent_avg = fp_row[0] if fp_row else None
    median_pitch_hz = fp_row[1] if fp_row else None

    avg_row = conn.execute("""
        SELECT sentiment_avg FROM comment_sentiment_agg WHERE video_id = ?
    """, (video_id,)).fetchone()

    return {
        "video_id":         video_id,
        "title":            meta[0] or "",
        "channel_name":     meta[1] or "",
        "duration_seconds": meta[2],
        "audio_features":   audio,
        "speech_segments":  speech,
        "emotion_segments": emotion,
        "speech_sent_avg":  speech_sent_avg,
        "median_pitch_hz":  median_pitch_hz,
        "comment_sent_avg": avg_row[0] if avg_row else None,
    }


# ── Índice de energia composto ───────────────────────────────────────────────

def compute_energy_index(audio_features: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """
    Compõe um índice de 'energia vocal' em [0, 1] por segmento, com pesos iguais.

    Usa volume_db + pitch_hz + speech_rate.

    Normalização por vídeo (min-max de cada componente), não global — cada vídeo
    gera sua própria curva de dinâmica relativa. Isso é o que queremos para
    comparação visual.

    Retorna (t_mid_seconds, energy_index) com mesma length que audio_features.
    """
    if not audio_features:
        return np.array([]), np.array([])

    t_mid = np.array([(a["t_start"] + a["t_end"]) / 2.0 for a in audio_features])
    vol   = np.array([a["volume_db"]   if a["volume_db"]   is not None else np.nan for a in audio_features])
    pit   = np.array([a["pitch_hz"]    if a["pitch_hz"]    is not None else np.nan for a in audio_features])
    rate  = np.array([a["speech_rate"] if a["speech_rate"] is not None else np.nan for a in audio_features])

    def _norm(v: np.ndarray) -> np.ndarray:
        finite = v[np.isfinite(v)]
        if finite.size < 2:
            return np.zeros_like(v)
        lo, hi = np.nanmin(v), np.nanmax(v)
        if hi - lo < 1e-9:
            return np.zeros_like(v)
        n = (v - lo) / (hi - lo)
        n[~np.isfinite(n)] = 0.0
        return np.clip(n, 0.0, 1.0)

    energy = (_norm(vol) + _norm(pit) + _norm(rate)) / 3.0
    return t_mid, energy


# ── Painéis ──────────────────────────────────────────────────────────────────

def _smooth_curve(x: np.ndarray, y: np.ndarray, n_out: int = 200) -> tuple[np.ndarray, np.ndarray]:
    """Interpolação PCHIP (monotônica entre pontos) se N>=2; linear se <2.

    PCHIP (Piecewise Cubic Hermite Interpolating Polynomial) é suave MAS
    garante monotonia entre pontos adjacentes — não cria overshoots nem
    valores "fantasma" que não existem nos dados. Isso é crítico para
    auditabilidade: se dois pontos são 0.0 e 0.0 e o próximo é -0.6, a
    curva só desce — nunca sobe para algo positivo no meio.

    CubicSpline (anterior) criava overshoots inventados — removido.
    """
    if len(x) < 2:
        return x, y
    x_out = np.linspace(x.min(), x.max(), n_out)
    if len(x) >= 2:
        try:
            from scipy.interpolate import PchipInterpolator
            pchip = PchipInterpolator(x, y, extrapolate=False)
            y_out = pchip(x_out)
            # PCHIP pode devolver NaN fora do domínio; preenche com bordas
            y_out = np.where(np.isnan(y_out), np.interp(x_out, x, y), y_out)
            return x_out, y_out
        except Exception:
            pass
    y_out = np.interp(x_out, x, y)
    return x_out, y_out


def build_panel_energy(ax, data: dict) -> None:
    """Painel 1 — tom de voz em Hz (pitch) com valores nos picos.

    Mostra o pitch_hz médio por segmento (a frequência fundamental, f0) como
    curva suavizada — é o "tom de voz" literal medido por librosa (pyin).
    Anotações textuais nos picos máximos e na mediana para dar referência numérica.
    """
    af = data["audio_features"]
    duration = data["duration_seconds"] or (af[-1]["t_end"] if af else 1.0)

    ax.set_facecolor(_COLOR_PANEL_BG)
    ax.set_ylabel("Tom de voz\n(pitch em Hz, f0 via librosa)",
                  fontsize=9, color="#333")
    ax.tick_params(axis="y", labelsize=8)
    ax.grid(axis="y", linestyle=":", linewidth=0.5, color="#CCC", alpha=0.8)

    if not af:
        ax.set_ylim(0, 500)
        ax.text(50, 250, "— sem audio_features —",
                ha="center", va="center", color="#999", fontsize=11, style="italic",
                transform=ax.transData)
        return

    t_mid = np.array([(a["t_start"] + a["t_end"]) / 2.0 for a in af])
    pit   = np.array([a["pitch_hz"] if a["pitch_hz"] is not None else np.nan for a in af])
    x_pct = (t_mid / duration) * 100.0
    registers = [a.get("voice_register") or "normal" for a in af]

    # Descarta NaNs para interpolação
    finite_mask = np.isfinite(pit)
    if finite_mask.sum() < 2:
        ax.set_ylim(0, 500)
        ax.text(50, 250, "— pitch indisponível —",
                ha="center", va="center", color="#999", fontsize=11, style="italic")
        return

    x_f = x_pct[finite_mask]
    p_f = pit[finite_mask]
    reg_f = [registers[i] for i, m in enumerate(finite_mask) if m]

    # Faixa Y com padding suave para caber anotações
    # A margem de baixo precisa caber as duas linhas da anotação dos vales,
    # que são penduradas sob o ponto; com 20 Hz o rótulo do vale mais grave
    # era cortado pela borda do painel.
    y_lo = max(0, float(np.min(p_f)) - 55)
    y_hi = float(np.max(p_f)) + 40
    ax.set_ylim(y_lo, y_hi)

    # ── Bandas translúcidas de fundo para regiões de grito/sussurro ────────
    # Cada segmento com voice_register == "grito" pinta uma banda vermelha clara
    # e cada "sussurro" pinta uma banda azul clara, ajudando a destacar regiões
    # emocionalmente extremas sem poluir as anotações numéricas.
    _REG_BANDS = {
        "grito":    ("#FF5252", 0.12, "GRITO"),
        "sussurro": ("#42A5F5", 0.14, "SUSSURRO"),
    }
    labelled_bands: set[str] = set()
    af_finite = [a for a, m in zip(af, finite_mask) if m]
    for i, a in enumerate(af_finite):
        reg = a.get("voice_register")
        if reg not in _REG_BANDS:
            continue
        band_color, band_alpha, band_label = _REG_BANDS[reg]
        x0 = (a["t_start"] / duration) * 100.0
        x1 = (a["t_end"]   / duration) * 100.0
        ax.axvspan(x0, x1, color=band_color, alpha=band_alpha, zorder=1, linewidth=0)
        # Pequena tag acima da banda (só 1 vez por tipo para não repetir)
        if reg not in labelled_bands:
            ax.text((x0 + x1) / 2, y_hi - 6, band_label,
                    fontsize=6.5, color=band_color, ha="center", va="top",
                    fontweight="bold", alpha=0.85, zorder=5)
            labelled_bands.add(reg)

    x_smooth, y_smooth = _smooth_curve(x_f, p_f, n_out=300)

    ax.fill_between(x_smooth, y_lo, y_smooth, color=_COLOR_ENERGY_FILL, alpha=0.14, zorder=2)
    ax.plot(x_smooth, y_smooth, color=_COLOR_ENERGY_LINE, linewidth=2.0, zorder=3)
    ax.plot(x_f, p_f, "o", color=_COLOR_ENERGY_LINE, markersize=3.5,
            zorder=4, alpha=0.75, markeredgecolor="white", markeredgewidth=0.8)

    # Linha horizontal de mediana — lê do banco (cultural_fingerprint.median_pitch_hz)
    # pra garantir 1:1 com a aba cultural_fingerprint do Excel.
    median_pitch = data.get("median_pitch_hz")
    if median_pitch is None:
        median_pitch = float(np.median(p_f))   # fallback defensivo
    median_pitch = float(median_pitch)
    ax.axhline(median_pitch, color="#555", linestyle="--", linewidth=0.9,
               alpha=0.7, zorder=2)
    ax.text(101, median_pitch, f"mediana {median_pitch:.0f} Hz",
            fontsize=7, color="#555", va="center", ha="left",
            transform=ax.transData)

    # Anota PICOS E VALES LOCAIS (não os top-N absolutos).
    # Pico local = ponto maior que seus dois vizinhos; vale = menor que ambos.
    # Usamos scipy.signal.find_peaks com distância mínima para evitar picos
    # ruidosos muito próximos — resultado são os "cumes" e "fundos" visíveis
    # da curva, que é o que o olho procura.
    n = len(p_f)
    if n >= 3:
        peak_indices: list[int] = []
        valley_indices: list[int] = []
        try:
            from scipy.signal import find_peaks
            # Distância mínima entre picos: ~15% do total de segmentos
            min_dist = max(1, int(n * 0.15))
            peaks, _ = find_peaks(p_f, distance=min_dist)
            valleys, _ = find_peaks(-p_f, distance=min_dist)
            peak_indices = list(peaks)
            valley_indices = list(valleys)
        except Exception:
            # Fallback: detecção manual de máximos/mínimos locais
            for i in range(1, n - 1):
                if p_f[i] > p_f[i - 1] and p_f[i] > p_f[i + 1]:
                    peak_indices.append(i)
                elif p_f[i] < p_f[i - 1] and p_f[i] < p_f[i + 1]:
                    valley_indices.append(i)

        # Limita número de anotações (em vídeos com muita oscilação fica poluído):
        # pega os 4 picos mais altos e 3 vales mais baixos.
        if len(peak_indices) > 4:
            peak_indices = sorted(peak_indices, key=lambda i: -p_f[i])[:4]
        if len(valley_indices) > 3:
            valley_indices = sorted(valley_indices, key=lambda i: p_f[i])[:3]

        # Caso especial — primeiro e último ponto não são "picos locais" por
        # definição (não têm vizinho de um dos lados), mas frequentemente são
        # pontos notáveis da curva. Adiciona se forem extremos absolutos OU se
        # divergirem significativamente do resto (> 1 desvio-padrão da mediana).
        median_p = float(np.median(p_f))
        std_p = float(np.std(p_f))
        for edge_idx in (0, n - 1):
            val = float(p_f[edge_idx])
            is_global_max = val >= float(np.max(p_f))
            is_global_min = val <= float(np.min(p_f))
            is_outlier = abs(val - median_p) > std_p * 1.0
            if is_global_max or is_global_min or is_outlier:
                # Classifica como pico ou vale pelo lado onde está
                if val >= median_p:
                    if edge_idx not in peak_indices:
                        peak_indices.append(edge_idx)
                else:
                    if edge_idx not in valley_indices:
                        valley_indices.append(edge_idx)

        # Cor da classificação para destacar em cada anotação
        _REG_COLOR = {
            "grito":     "#C62828",
            "sussurro":  "#1565C0",
            "agitado":   "#6A1B9A",
            "acelerado": "#F57F17",
            "normal":    "#1F3864",
        }

        annotated_positions: list[float] = []
        for idx in peak_indices:
            xp, yp = float(x_f[idx]), float(p_f[idx])
            if any(abs(xp - o) < 6 for o in annotated_positions):
                continue
            annotated_positions.append(xp)
            reg = reg_f[idx]
            col = _REG_COLOR.get(reg, "#1F3864")
            ax.annotate(f"{reg}\n{yp:.0f} Hz",
                        xy=(xp, yp), xytext=(xp, yp + 22),
                        fontsize=7.0, color=col, ha="center", va="bottom",
                        fontweight="bold", zorder=5,
                        linespacing=1.0,
                        arrowprops=dict(arrowstyle="-", color=col,
                                        lw=0.6, alpha=0.5))
        for idx in valley_indices:
            xp, yp = float(x_f[idx]), float(p_f[idx])
            if any(abs(xp - o) < 6 for o in annotated_positions):
                continue
            annotated_positions.append(xp)
            reg = reg_f[idx]
            col = _REG_COLOR.get(reg, "#1F3864")
            # va="top" pendura o bloco sob o ponto. Sem isso o texto crescia
            # para cima a partir de yp-28 e atravessava a própria curva.
            ax.annotate(f"{yp:.0f} Hz\n{reg}",
                        xy=(xp, yp), xytext=(xp, yp - 10),
                        fontsize=7.0, color=col, ha="center", va="top",
                        fontweight="bold", zorder=5,
                        linespacing=1.0,
                        arrowprops=dict(arrowstyle="-", color=col,
                                        lw=0.6, alpha=0.5))


def _indices_dos_picos(y, x, dist_min: float = 3.5,
                       limiar: float = 0.05) -> list[int]:
    """Índices dos extremos locais, separados por `dist_min` no eixo x.

    O que se lê numa curva são os picos, não cada ponto. Entre dois picos
    próximos demais para os rótulos caberem lado a lado, fica o de maior
    magnitude — o critério é a distância em % da duração, então vídeos curtos
    recebem mais rótulos e vídeos longos, menos, sem precisar de ajuste.
    """
    picos = []
    for i in range(len(y)):
        v = float(y[i])
        if abs(v) <= limiar:
            continue
        ant = float(y[i - 1]) if i > 0 else 0.0
        prox = float(y[i + 1]) if i < len(y) - 1 else 0.0
        if (v > 0 and v >= ant and v >= prox) or (v < 0 and v <= ant and v <= prox):
            picos.append(i)

    picos.sort(key=lambda i: -abs(float(y[i])))
    aceitos: list[int] = []
    for i in picos:
        if all(abs(float(x[i]) - float(x[j])) >= dist_min for j in aceitos):
            aceitos.append(i)
    return sorted(aceitos)


def build_panel_valence(ax, data: dict) -> None:
    """Painel 2 — valência sentimental [-1, +1] com preenchimento bidirecional."""
    segs = [s for s in data["speech_segments"]
            if s.get("sentiment_score") is not None]
    duration = data["duration_seconds"] or (
        max((s["t_end"] for s in data["speech_segments"]), default=1.0)
    )

    ax.set_facecolor(_COLOR_PANEL_BG)
    ax.set_ylim(-1.20, 1.20)   # +0.2 de margem p/ anotações acima/abaixo dos extremos
    ax.set_ylabel("Valência sentimental\n(DistilBERT, [-1, +1])",
                  fontsize=9, color="#333")
    ax.tick_params(axis="y", labelsize=8)
    ax.axhline(0, color=_COLOR_AXIS_REF, linewidth=1.0, zorder=2)
    ax.grid(axis="y", linestyle=":", linewidth=0.5, color="#CCC", alpha=0.8)
    ax.set_yticks([-1.0, -0.5, 0.0, 0.5, 1.0])

    if not segs:
        ax.text(50, 0.0, "— sem segmentos de fala classificados —",
                ha="center", va="center", color="#999", fontsize=11, style="italic",
                transform=ax.transData)
        return

    t_mid = np.array([(s["t_start"] + s["t_end"]) / 2.0 for s in segs])
    scores = np.array([s["sentiment_score"] for s in segs])
    x_pct = (t_mid / duration) * 100.0

    x_smooth, y_smooth = _smooth_curve(x_pct, scores, n_out=300)

    # Preenchimento bidirecional (verde acima do zero, vermelho abaixo)
    ax.fill_between(x_smooth, 0, y_smooth,
                    where=(y_smooth >= 0), color=_COLOR_VALENCE_POS,
                    alpha=0.35, interpolate=True, zorder=2)
    ax.fill_between(x_smooth, 0, y_smooth,
                    where=(y_smooth <  0), color=_COLOR_VALENCE_NEG,
                    alpha=0.35, interpolate=True, zorder=2)

    # Linha da curva colorida por sinal (segmento a segmento da série suavizada)
    for i in range(len(x_smooth) - 1):
        y_i, y_j = y_smooth[i], y_smooth[i + 1]
        mid = (y_i + y_j) / 2.0
        col = _COLOR_VALENCE_POS if mid > 0.05 else (
              _COLOR_VALENCE_NEG if mid < -0.05 else _COLOR_VALENCE_ZERO)
        ax.plot(x_smooth[i:i+2], y_smooth[i:i+2],
                color=col, linewidth=1.8, zorder=3)

    # Pontos originais (cada segmento)
    for xp, yp in zip(x_pct, scores):
        col = _COLOR_VALENCE_POS if yp > 0.05 else (
              _COLOR_VALENCE_NEG if yp < -0.05 else _COLOR_VALENCE_ZERO)
        ax.plot(xp, yp, "o", color=col, markersize=4, zorder=4,
                markeredgecolor="white", markeredgewidth=0.8)

    # Rótulo só nos picos. Rotular todo segmento não-nulo era ilegível: num
    # vídeo de 91 segmentos os números se encavalavam e viravam borrão
    # ("+0.60+0.56+0.50"). Os valores de todos os segmentos continuam exatos
    # na aba de atos de fala do Excel e em dados/atos_de_fala.csv.
    for idx in _indices_dos_picos(scores, x_pct):
        xp, yp = float(x_pct[idx]), float(scores[idx])
        acima = yp > 0
        ax.annotate(
            f"{yp:+.2f}", xy=(xp, yp),
            xytext=(xp, yp + (0.12 if acima else -0.12)),
            fontsize=7.0, color=_COLOR_VALENCE_POS if acima else _COLOR_VALENCE_NEG,
            ha="center", va="bottom" if acima else "top",
            fontweight="bold", zorder=5)

    # Linha tracejada da média de sentimento da fala deste vídeo
    avg = data.get("speech_sent_avg")
    if avg is not None:
        ax.axhline(avg, color="#555", linestyle="--", linewidth=0.9,
                   alpha=0.7, zorder=2)
        ax.text(101, avg, f"avg {avg:+.2f}", fontsize=7, color="#555",
                va="center", ha="left", transform=ax.transData)


def build_panel_emotion(ax, data: dict) -> None:
    """Painel 3 — curva emocional colorida por rótulo vencedor.

    Cada segmento é um trapezóide (rampa sobe → platô → rampa desce) cuja:
      altura   = emotion_score    (confiança da predição, [0, 1])
      cor      = cor da emoção    (paleta GoEmotions fixa de 28 labels)

    Labels SEMPRE visíveis — horizontal, vertical ou em callout conforme a
    largura do segmento. Evita o problema de blocos pequenos ficarem sem
    identificação textual.
    """
    segs = data["emotion_segments"]

    ax.set_facecolor(_COLOR_PANEL_BG)
    ax.set_ylim(0.0, 1.80)   # 1.0 para o score + espaço para os callouts em 3 alturas
    ax.set_yticks([0.0, 0.25, 0.5, 0.75, 1.0])
    ax.set_ylabel("Emoção\n(pysentimiento,\naltura = score de\nconfiança, [0, 1])",
                  fontsize=9, color="#333")
    ax.tick_params(axis="y", labelsize=8)
    ax.grid(axis="y", linestyle=":", linewidth=0.5, color="#CCC", alpha=0.6)

    if not segs:
        ax.text(50, 0.5, "— sem emotion_curve —",
                ha="center", va="center", color="#999", fontsize=11, style="italic",
                transform=ax.transData)
        return

    # ATENÇÃO: emotion_curve.t_*_pct já é 0-100 no banco. Clip defensivo.
    prepared = []
    for s in segs:
        start = float(s["t_start_pct"]) if s["t_start_pct"] is not None else 0.0
        end   = float(s["t_end_pct"])   if s["t_end_pct"]   is not None else start
        start = max(0.0, min(start, 100.0))
        end   = max(0.0, min(end,   100.0))
        if end <= start:
            continue
        label = s["emotion_raw"] or "neutral"
        color = GOEMOTIONS_PALETTE.get(label, "#BDBDBD")
        score = max(0.0, min(float(s["emotion_score"] or 0.0), 1.0))
        prepared.append({"start": start, "end": end, "width": end - start,
                         "label": label, "color": color, "score": score})

    if not prepared:
        return

    # Desenha cada segmento como trapezóide para formar uma curva contínua colorida
    ramp_default = 0.4  # largura (em % do vídeo) das rampas entre segmentos
    for s in prepared:
        start, end, score = s["start"], s["end"], s["score"]
        # Rampa limitada a 20% da largura do segmento para não distorcer
        r = min(ramp_default, s["width"] * 0.2)
        xs = [start, start + r, max(end - r, start + r), end]
        ys = [0.0,   score,     score,                    0.0]
        # Fecha o polígono voltando ao início
        ax.fill(xs + [end, start], ys + [0.0, 0.0],
                color=s["color"], alpha=0.88, linewidth=0, zorder=2)
        # Contorno superior mais escuro para definir o topo
        darker = _darken(s["color"], 0.35)
        ax.plot([start + r, max(end - r, start + r)], [score, score],
                color=darker, linewidth=1.8, zorder=3)
        ax.plot([start, start + r], [0, score], color=darker,
                linewidth=0.9, alpha=0.6, zorder=3)
        ax.plot([max(end - r, start + r), end], [score, 0], color=darker,
                linewidth=0.9, alpha=0.6, zorder=3)

    ax.axhline(0.0, color="#999", linewidth=0.6, zorder=1)
    ax.axhline(1.0, color="#DDD", linewidth=0.5, linestyle=":", zorder=1)

    _render_emotion_labels(ax, prepared)


def _render_emotion_labels(ax, segs: list[dict]) -> None:
    """Labels + valor numérico SEMPRE visíveis, na cor da emoção, SEMPRE verticais.

    Padronização visual: todos os labels usam rotação 90° (vertical), independente
    da largura do trapezóide. Isso elimina a inconsistência visual anterior onde
    blocos largos ficavam horizontais e estreitos verticais.

    Estratégia por largura:
      >= 2.0%   → label vertical acima do topo + score vertical dentro do bloco
      < 2.0%    → só label vertical em callout acima (sem score, por falta de
                  espaço — score continua exato no Excel)

    Fontsize adaptativo por densidade (n de segmentos no vídeo).
    Valores mostrados com 2 casas decimais, batendo com o Excel.
    """
    # Fontsize adaptativo: quanto mais segmentos, menor o texto
    n = len(segs)
    if n >= 80:
        fs_label, fs_score = 5.0, 4.0
        fs_callout = 4.0
    elif n >= 40:
        fs_label, fs_score = 5.5, 4.5
        fs_callout = 4.5
    elif n >= 22:
        fs_label, fs_score = 6.0, 5.0
        fs_callout = 5.0
    elif n >= 14:
        fs_label, fs_score = 6.5, 5.5
        fs_callout = 5.5
    else:
        fs_label, fs_score = 7.0, 6.0
        fs_callout = 6.0

    # Callouts em três alturas, cada uma com distância mínima própria. Com
    # duas alturas alternadas, um vídeo de 91 segmentos punha rótulo a cada
    # 1,1% da largura e eles se encavalavam dois a dois; o que não couber em
    # nenhuma das alturas fica sem rótulo na figura — e exato no Excel.
    niveis = (1.08, 1.26, 1.44)
    ocupado: list[list[float]] = [[] for _ in niveis]
    dist_min = 1.6

    for s in segs:
        start, end, width = s["start"], s["end"], s["width"]
        label, color, score = s["label"], s["color"], s["score"]
        cx = (start + end) / 2.0
        text_color = _darken(color, 0.25)
        score_str = f"{score:.2f}"

        if width >= 2.0:
            # LABEL: vertical acima do topo do trapezóide
            ax.text(cx, score + 0.03, label, ha="center", va="bottom",
                    fontsize=fs_label, fontweight="bold",
                    color=text_color, rotation=90, zorder=6)
            # SCORE: vertical dentro do trapezóide, apenas se altura suficiente
            if score > 0.25:
                r, g, b = _hex_to_rgb(color)
                luminance = 0.299 * r + 0.587 * g + 0.114 * b
                score_fg = "#FFFFFF" if luminance < 140 else "#111111"
                ax.text(cx, score / 2, score_str, ha="center", va="center",
                        fontsize=fs_score, fontweight="bold",
                        color=score_fg, rotation=90, zorder=6)
        else:
            # Blocos muito estreitos: só callout vertical acima, sem score
            vaga = next(
                (k for k in range(len(niveis))
                 if all(abs(cx - o) >= dist_min for o in ocupado[k])),
                None,
            )
            if vaga is None:
                continue
            ocupado[vaga].append(cx)
            y_call = niveis[vaga]
            ax.annotate(label, xy=(cx, score), xytext=(cx, y_call),
                        ha="center", va="bottom",
                        fontsize=fs_callout, fontweight="bold",
                        color=text_color, rotation=90, zorder=6,
                        arrowprops=dict(arrowstyle="-",
                                        color=_darken(color, 0.4),
                                        lw=0.5, alpha=0.6))


def _darken(hex_str: str, factor: float) -> str:
    """Escurece uma cor HEX multiplicando RGB por (1 - factor)."""
    r, g, b = _hex_to_rgb(hex_str)
    mul = max(0.0, 1.0 - factor)
    return "#{:02X}{:02X}{:02X}".format(int(r * mul), int(g * mul), int(b * mul))


def _hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    h = hex_str.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def assemble_triple_view(data: dict):
    """Monta a curva emocional tríplice. Retorna Figure.

    Painel 1 — tom de voz (pitch via librosa)
    Painel 2 — valência sentimental (DistilBERT)
    Painel 3 — emoção categórica (pysentimiento)
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(
        nrows=3, ncols=1, sharex=True, figsize=(12, 9),
        gridspec_kw={"height_ratios": [1.0, 1.0, 1.25], "hspace": 0.22},
    )
    build_panel_energy(axes[0], data)
    build_panel_valence(axes[1], data)
    build_panel_emotion(axes[2], data)

    # Eixo X compartilhado (0 a 100%)
    for ax in axes:
        ax.set_xlim(0, 100)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        ax.spines["left"].set_color("#999")
        ax.spines["bottom"].set_color("#999")
    axes[-1].set_xlabel("Duração do vídeo (% normalizado)", fontsize=10, color="#333")
    axes[-1].set_xticks([0, 20, 40, 60, 80, 100])
    axes[-1].tick_params(axis="x", labelsize=9)

    # Título
    title = _sanitize_for_title(data.get("title") or "")
    channel = _sanitize_for_title(data.get("channel_name") or "")
    dur = data.get("duration_seconds")
    dur_str = f"{int(dur // 60)}m{int(dur % 60):02d}s" if dur else "—"
    fig.suptitle(
        f"Curva emocional tríplice — {data['video_id']}\n"
        f"{title[:80]}  ·  {channel}  ·  {dur_str}",
        fontsize=11, y=0.995, color="#222",
    )
    fig.subplots_adjust(top=0.94, left=0.10, right=0.89, bottom=0.06)
    return fig


def _sanitize_for_title(s: str) -> str:
    # Remove emojis e caracteres exóticos que matplotlib tem dificuldade em renderizar
    return re.sub(r"[^\w\s\-\.\,\!\?\:\;\(\)áàãâéêíóôõúüçÁÀÃÂÉÊÍÓÔÕÚÜÇ\%\#]", "", s).strip()


# ── Export e API pública ─────────────────────────────────────────────────────

def _slugify_filename(title: str, video_id: str, max_len: int = 80) -> str:
    """Converte o título do vídeo em nome de arquivo seguro.

    Regras:
      - Remove emojis e caracteres não-ASCII/acentos (ficariam estranhos em FS)
      - Remove caracteres inválidos do Windows: < > : " / \\ | ? *
      - Substitui espaços e pontuação por hífens (-)
      - Colapsa hífens múltiplos em um só
      - Trunca no max_len para ficar manejável
      - Anexa `__{video_id}` no final para garantir unicidade (evita colisões
        entre 2 vídeos com mesmo título, e mantém rastreabilidade ao banco)
      - Se o título ficar vazio após limpeza (só emojis/unicode), usa só o video_id

    Exemplo: ("24 HORAS Gastando em EURO 🤑", "ZDGbRST94tc")
             → "24-HORAS-Gastando-em-EURO__ZDGbRST94tc"
    """
    import unicodedata
    # NFD separa acentos, NFKC compõe formas compatíveis
    norm = unicodedata.normalize("NFKD", title or "")
    # Tira acentos e caracteres combinantes
    ascii_str = norm.encode("ascii", "ignore").decode("ascii")
    # Troca caracteres inválidos por hífens
    slug = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "-", ascii_str)
    # Colapsa tudo que não é letra/dígito/hífen em hífen
    slug = re.sub(r"[^A-Za-z0-9\-]+", "-", slug)
    # Colapsa múltiplos hífens e remove hífens do começo/fim
    slug = re.sub(r"-+", "-", slug).strip("-")
    # Trunca
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")
    # Se vazio, usa só video_id
    if not slug:
        return video_id
    return f"{slug}__{video_id}"


def export_figure(fig, output_path: str, fmt: str = "png", dpi: int = 140) -> str:
    """Salva a figura em PNG (ou outro formato). Retorna o path absoluto.

    NÃO usa bbox_inches='tight' — preserva o canvas definido em subplots_adjust.
    Sem essa proteção, qualquer artista fora dos xlim (ex: texto da média do
    painel de valência em x=101) expande o bbox e pode gerar imagens de
    dimensões absurdas se um bug introduzir coordenadas fora de escala.
    """
    base, _ = os.path.splitext(output_path)
    final = f"{base}.{fmt}"
    fig.savefig(final, dpi=dpi, facecolor="white")
    return os.path.abspath(final)


def generate_triple_view(
    video_id: str,
    conn: Optional[sqlite3.Connection] = None,
    output_dir: str = "visualizations",
    fmt: str = "png",
    sequence_index: Optional[int] = None,
    total_count: Optional[int] = None,
) -> str:
    """
    API pública de alto nível: carrega → monta → salva. Retorna o path do PNG.
    Fecha matplotlib ao final para liberar memória em rodadas em lote.

    DPI adaptativo por densidade: vídeos com muitos segmentos no emotion_curve
    exportam em resolução maior para que os labels verticais do Painel 3
    fiquem legíveis com zoom. Evita truncar informação em vídeos de 3 min, que
    chegam a 111 segmentos.

    Se `sequence_index` é fornecido, o arquivo ganha prefixo zero-padded
    baseado em `total_count` (ex: "003_Titulo__videoid.png") para ordenar
    no filesystem na mesma ordem do banco.
    """
    close_conn = False
    if conn is None:
        from modules.database import get_connection
        conn = get_connection()
        close_conn = True

    try:
        data = load_video_data(conn, video_id)
        fig = assemble_triple_view(data)
        os.makedirs(output_dir, exist_ok=True)

        # DPI adaptativo pela densidade do emotion_curve
        n_emo = len(data["emotion_segments"])
        if n_emo >= 80:
            dpi = 220    # vídeos muito densos — até 111 segmentos
        elif n_emo >= 40:
            dpi = 180
        else:
            dpi = 140    # padrão

        # Nome do arquivo derivado do título do vídeo (com video_id de sufixo
        # para garantir unicidade e rastreabilidade ao banco)
        base_name = _slugify_filename(data.get("title") or "", video_id)
        if sequence_index is not None:
            width = max(3, len(str(total_count or 1)))
            base_name = f"{sequence_index:0{width}d}_{base_name}"
        path = export_figure(fig, os.path.join(output_dir, base_name),
                             fmt=fmt, dpi=dpi)

        import matplotlib.pyplot as plt
        plt.close(fig)
        return path
    finally:
        if close_conn:
            conn.close()


# ── Figura 2: linha do tempo tabular ─────────────────────────────────────────

def assemble_timeline_view(data: dict):
    """Monta a linha do tempo tabular. Retorna Figure.

    Uma linha por segmento de fala, com todas as camadas lado a lado. É a
    leitura vertical do vídeo: a curva tríplice mostra a forma ao longo do
    tempo, esta mostra o conteúdo que produziu cada ponto.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.patheffects as pe
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    segs = data["speech_segments"]
    emocoes = {round(float(e["t_start_pct"] or 0), 2): e
               for e in data["emotion_segments"]}
    audio = {round(float(a["t_start"] or 0), 2): a for a in data["audio_features"]}
    dur = float(data.get("duration_seconds") or 0) or 1.0

    n = len(segs)
    altura = max(3.0, 0.32 * n + 1.6)
    fig, ax = plt.subplots(figsize=(14, altura))
    ax.set_facecolor(_COLOR_PANEL_BG)

    COLUNAS = [
        ("Tempo", 0.000, 0.060),
        ("Fala transcrita", 0.065, 0.480),
        ("Sentimento", 0.550, 0.110),
        ("Emoção", 0.665, 0.125),
        ("Pitch", 0.795, 0.070),
        ("Volume", 0.870, 0.070),
        ("Registro", 0.945, 0.055),
    ]

    for titulo, x, _larg in COLUNAS:
        ax.text(x, n + 0.55, titulo, fontsize=8.5, fontweight="bold",
                color="#333", va="bottom", ha="left")
    ax.plot([0, 1], [n + 0.45, n + 0.45], color="#999", linewidth=0.8)

    for i, seg in enumerate(segs):
        y = n - i - 1
        t0 = float(seg.get("t_start") or 0)
        if i % 2 == 0:
            ax.add_patch(Rectangle((0, y - 0.02), 1, 0.96,
                                   facecolor="#FFFFFF", edgecolor="none",
                                   alpha=0.55, zorder=0))

        ax.text(0.000, y + 0.25, f"{t0:.0f}s", fontsize=7, color="#666",
                va="center", ha="left")

        texto = (seg.get("text") or "").strip()
        if len(texto) > 78:
            texto = texto[:75] + "…"
        ax.text(0.065, y + 0.25, texto, fontsize=7.5, color="#222",
                va="center", ha="left")

        sc = seg.get("sentiment_score")
        if sc is not None:
            neutro = abs(float(sc)) <= 0.05
            cor = (_COLOR_VALENCE_POS if sc > 0.05 else
                   _COLOR_VALENCE_NEG if sc < -0.05 else _COLOR_VALENCE_ZERO)

            # A barra vai até 0.610 e o número começa em 0.618. Antes a barra
            # ia até 0.645 e o número era ancorado à direita em 0.650: os
            # valores altos caíam por baixo da própria barra e apareciam
            # cortados — "+0.93" era desenhado, mas só se via "93".
            largura = 0.060 * min(abs(float(sc)), 1.0)
            ax.add_patch(Rectangle((0.550, y + 0.08), largura, 0.34,
                                   facecolor=cor, edgecolor="none", zorder=3))

            # Neutro é zero por construção: a valência só tem sinal nos polos
            # positivo e negativo. Com "+0.00" e barra de largura zero, a
            # linha parecia não ter sido classificada; a palavra deixa claro
            # que houve classificação, e qual foi.
            if neutro:
                ax.add_patch(Rectangle((0.550, y + 0.20), 0.006, 0.10,
                                       facecolor=cor, edgecolor="none", zorder=3))
            ax.text(0.618, y + 0.25, "neutro" if neutro else f"{sc:+.2f}",
                    fontsize=6.5, color=cor, va="center", ha="left",
                    fontweight="bold")

        emo = emocoes.get(round(100.0 * t0 / dur, 2))
        if emo and emo.get("emotion_raw"):
            rotulo = emo["emotion_raw"]
            cor = GOEMOTIONS_PALETTE.get(rotulo, "#9E9E9E")
            score = float(emo.get("emotion_score") or 0)
            ax.add_patch(Rectangle((0.665, y + 0.08), 0.110 * score, 0.34,
                                   facecolor=cor, edgecolor="none",
                                   alpha=0.85, zorder=3))
            # O texto fica sobre a barra, cuja cor varia nas 28 emoções: em
            # cima de sadness, fear ou disgust, que são escuras, o cinza fixo
            # anterior sumia. A cor vem da luminância da barra e o contorno
            # sustenta a leitura onde o texto passa da barra para o fundo.
            r, g, b = _hex_to_rgb(cor)
            claro = (0.299 * r + 0.587 * g + 0.114 * b) >= 140
            ax.text(0.668, y + 0.25, rotulo[:13], fontsize=6.5,
                    color="#111111" if claro else "#FFFFFF",
                    va="center", ha="left", zorder=4,
                    path_effects=[pe.withStroke(
                        linewidth=1.4,
                        foreground="#FFFFFF" if claro else _darken(cor, 0.55))])

        af = audio.get(round(t0, 2))
        if af:
            if af.get("pitch_hz"):
                ax.text(0.795, y + 0.25, f"{af['pitch_hz']:.0f} Hz", fontsize=7,
                        color="#1F3864", va="center", ha="left")
            if af.get("volume_db") is not None:
                ax.text(0.870, y + 0.25, f"{af['volume_db']:.0f} dB", fontsize=7,
                        color="#555", va="center", ha="left")
            if af.get("voice_register"):
                ax.text(0.945, y + 0.25, af["voice_register"][:9], fontsize=6.5,
                        color="#6A1B9A", va="center", ha="left")

    ax.set_xlim(0, 1)
    ax.set_ylim(-0.4, n + 1.2)
    ax.set_xticks([])
    ax.set_yticks([])
    for lado in ("top", "right", "left", "bottom"):
        ax.spines[lado].set_visible(False)

    titulo = _sanitize_for_title(data.get("title") or "")
    canal = _sanitize_for_title(data.get("channel_name") or "")
    d = data.get("duration_seconds")
    d_str = f"{int(d // 60)}m{int(d % 60):02d}s" if d else "—"
    fig.suptitle(
        f"Linha do tempo — {data['video_id']}\n"
        f"{titulo[:80]}  ·  {canal}  ·  {d_str}  ·  {n} segmentos",
        fontsize=11, y=0.997, color="#222",
    )
    fig.subplots_adjust(top=0.955, left=0.015, right=0.985, bottom=0.015)
    return fig


def generate_timeline_view(
    video_id: str,
    conn: Optional[sqlite3.Connection] = None,
    output_dir: str = "media/visualizations",
    fmt: str = "png",
) -> str:
    """Carrega, monta e salva a linha do tempo. Retorna o caminho do arquivo."""
    import matplotlib.pyplot as plt

    propria = conn is None
    if propria:
        from modules.database import get_connection
        conn = get_connection()
    try:
        dados = load_video_data(conn, video_id)
        fig = assemble_timeline_view(dados)
        nome = _slugify_filename(dados.get("title") or "", video_id)
        caminho = os.path.join(output_dir, f"{nome}_timeline")
        final = export_figure(fig, caminho, fmt=fmt, dpi=130)
        plt.close(fig)
        return final
    finally:
        if propria:
            conn.close()
