"""
modules/comment_sentiment.py — Análise de sentimento dos comentários do YouTube.

Classifica a **reação da audiência** ao conteúdo: dado complementar ao sentimento
expresso na fala do criador (em speech_analysis), que captura a resposta
simbólica da rede social ao fingerprint cultural do vídeo.

FLUXO
─────
  YouTube API (comentários brutos)
    → normalizer (ordena por likes desc)
    → analyze_comments_from_list (top N)
    → BERTAnalyzer.analyze_comments (DistilBERT multilingual)
    → dict com {individual: [...], agg: {...}}
    → database.insert_comment_sentiments / insert_comment_sentiment_agg
    → cultural_fingerprint (agrega as 5 métricas chave com prefixo comment_)

TABELAS
───────
  comment_sentiments    — 1 linha por comentário analisado
                          (video_id, comment_id, text, likes, sentiment,
                           sentiment_score, classification_evidence)
  comment_sentiment_agg — 1 linha por vídeo, colunas SEM prefixo comment_
                          (context da tabela já é comentários):
                          total_analyzed, positive_ratio, negative_ratio,
                          neutral_ratio, sentiment_avg, sentiment_std, top_words
  cultural_fingerprint  — as 5 métricas chave são replicadas com prefixo
                          comment_ (comment_sentiment_avg etc) para
                          comparabilidade direta entre vídeos.

ESCALA DO sentiment_score (por comentário)
──────────────────────────────────────────
O DistilBERT classifica em positive/negative/neutral com uma confiança (0.0-1.0).
Convertemos para um score assinado:
  label = positive, conf >= 0.5  →  sentiment_score = +conf   (intervalo [+0.5, +1.0])
  label = negative, conf >= 0.5  →  sentiment_score = -conf   (intervalo [-1.0, -0.5])
  label = neutral                →  sentiment_score =  0.0
  conf < 0.5 (qualquer label)    →  rebaixa para neutro, score = 0.0
    (threshold conservador: descarta classificações fracas para não inflar sinal)

Resultado: comentários positivos ficam em [+0.5, +1.0], negativos em [-1.0, -0.5],
neutros exatos em 0.0 — nunca há scores em (-0.5, 0) ou (0, +0.5).

AGREGAÇÕES (comment_sentiment_agg, por vídeo)
─────────────────────────────────────────────
  sentiment_avg = mean(sentiment_score)     média aritmética simples dos N scores
                                            Como cada score já é sinal×confiança,
                                            comentários confiantes pesam mais
                                            implicitamente (não é weighted mean clássica).
  sentiment_std = stdev(sentiment_score)    desvio padrão amostral (n-1).
                                            Alto = polarização; baixo = monotonia.
  positive_ratio = #positivos / total_analyzed   (antes de multiplicar por 100)
  negative_ratio, neutral_ratio — idem
  As três ratios SEMPRE somam 1.0 (total_analyzed = pos + neg + neu).

RESTRIÇÕES
──────────
  - Reutiliza BERTAnalyzer já carregado na etapa da transcrição
    (zero VRAM extra; mesmo pipeline de sentimento multilingual).
  - Analisa apenas os top N por likes desc (default 100) — foca no que
    a audiência validou, ignora long tail irrelevante.
  - Idempotente: DELETE + INSERT em tudo que envolve video_id.
  - Degradação graciosa: se o classificador falhar no load, o chamador deve
    pular a etapa sem quebrar o pipeline.
  - classification_evidence obrigatório em cada linha — sem caixas-pretas.

AUDITABILIDADE
──────────────
  Cada comentário classificado carrega classification_evidence (ex:
  "bert:positivo(0.87)"), permitindo rastrear qual padrão ativou a
  decisão e reclassificar retroativamente se o modelo mudar.

EXEMPLO
───────
  from modules.bert_analyzer import BERTAnalyzer
  from modules.comment_sentiment import analyze_comments_from_list

  bert = BERTAnalyzer()
  bert.load()
  result = analyze_comments_from_list(bert, video_id, comments, limit=100)
  bert.unload()

  # result["individual"] = lista de dicts (1 por comentário)
  # result["agg"]        = dict com métricas agregadas
"""

import re
import statistics
from collections import Counter

from modules.bert_analyzer import BERTAnalyzer
from modules.speech_analysis import _STOPWORDS_PT


def analyze_comments_from_list(
    bert: BERTAnalyzer,
    video_id: str,
    comments: list[dict],
    limit: int = 100,
) -> dict:
    """
    Analisa sentimento dos comentários já em memória.

    Args:
        bert:     BERTAnalyzer já carregado (com _sentiment_pipeline ativo)
        video_id: ID do vídeo
        comments: lista de dicts [{comment_id, text, likes, ...}]
        limit:    máximo de comentários a analisar

    Returns:
        {
          "video_id":       str,
          "individual":     [{comment_id, text, likes, sentiment, sentiment_score, evidence}, ...],
          "agg": {
              "total_analyzed":       int,
              "positive_ratio":       float,
              "negative_ratio":       float,
              "neutral_ratio":        float,
              "sentiment_avg":        float,
              "sentiment_std":        float,
              "top_words":            str,   # CSV das top 10 palavras
          }
        }
    """
    # Top N por likes
    top = sorted(comments, key=lambda c: c.get("likes", 0), reverse=True)[:limit]

    if not top:
        return {"video_id": video_id, "individual": [], "agg": _empty_agg()}

    # Análise individual via DistilBERT
    analyzed = bert.analyze_comments(top)

    if not analyzed:
        return {"video_id": video_id, "individual": [], "agg": _empty_agg()}

    # Agregados
    total = len(analyzed)
    pos_count = sum(1 for a in analyzed if a["sentiment"] == "positivo")
    neg_count = sum(1 for a in analyzed if a["sentiment"] == "negativo")

    scores = [a["sentiment_score"] for a in analyzed]
    avg = statistics.mean(scores) if scores else 0.0
    std = statistics.stdev(scores) if len(scores) >= 2 else 0.0

    # Top 10 palavras (excluindo stopwords)
    all_text = " ".join(a["text"] for a in analyzed)
    words = re.findall(r"\b[a-záàãâéêíóôõúüç]{3,}\b", all_text.lower())
    freq = Counter(w for w in words if w not in _STOPWORDS_PT)
    top_words = ", ".join(w for w, _ in freq.most_common(10))

    return {
        "video_id": video_id,
        "individual": analyzed,
        "agg": {
            "total_analyzed":  total,
            "positive_ratio":  round(pos_count / total, 3),
            "negative_ratio":  round(neg_count / total, 3),
            "neutral_ratio":   round((total - pos_count - neg_count) / total, 3),
            "sentiment_avg":   round(avg, 4),
            "sentiment_std":   round(std, 4),
            "top_words":       top_words or None,
        },
    }


def _empty_agg() -> dict:
    return {
        "total_analyzed":  0,
        "positive_ratio":  None,
        "negative_ratio":  None,
        "neutral_ratio":   None,
        "sentiment_avg":   None,
        "sentiment_std":   None,
        "top_words":       None,
    }
