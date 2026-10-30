"""
analise/comum.py — Carga dos CSVs publicados em `dados/`.

Os quatro scripts desta pasta reproduzem as tabelas do artigo a partir dos
CSVs versionados, sem banco e sem rede. Cada vídeo é identificado pelo
`video_id` e cada criador pelo pseudônimo C1…C10, como em `dados/videos.csv`.

As nove métricas da Tabela 2 vêm de quatro arquivos: três estão prontas nos
CSVs, três são médias por vídeo e três são razões calculadas aqui. A origem
de cada uma está anotada em `METRICAS`.
"""

import csv
import os
import statistics

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DADOS = os.path.join(RAIZ, "dados")

#: Nome da métrica na Tabela 2 → grupo com o valor maior no artigo.
#: A direção é conferida na saída: uma inversão indicaria erro de carga.
METRICAS = {
    "Views por vídeo":            "A",
    "Likes por vídeo":            "A",
    "Comentários por vídeo":      "A",
    "Engajamento %":              "A",
    "% segmentos não-neutros":    "A",
    "Velocidade de fala":         "A",
    "Pitch mediano (Hz)":         "B",
    "Volume médio (dB)":          "A",
    "Sentimento dos comentários": "B",
}


def ler_csv(nome: str) -> list[dict]:
    with open(os.path.join(DADOS, f"{nome}.csv"), encoding="utf-8") as f:
        return list(csv.DictReader(f))


def numero(valor):
    """Float, ou None quando a célula está vazia — nunca zero por omissão.

    Tratar ausência como zero deslocaria médias e postos; o Mann-Whitney
    descarta o vídeo na métrica em que falta o valor, e só nela.
    """
    if valor is None or valor == "":
        return None
    try:
        return float(valor)
    except ValueError:
        return None


def carregar() -> dict:
    """{video_id: {grupo, criador, <as nove métricas>}} para os 100 vídeos."""
    grupo = {r["video_id"]: r["grupo"] for r in ler_csv("corpus_estudo")}
    criador = {r["video_id"]: r["canal"] for r in ler_csv("videos")}
    metricas = {r["video_id"]: r for r in ler_csv("metricas")}
    fingerprint = {r["video_id"]: r for r in ler_csv("cultural_fingerprint")}

    # Velocidade de fala: média dos segmentos do vídeo. O fingerprint não
    # guarda essa média, só a prosódia segmento a segmento.
    fala: dict[str, list] = {}
    for r in ler_csv("prosodia"):
        v = numero(r["speech_rate"])
        if v is not None:
            fala.setdefault(r["video_id"], []).append(v)

    # Segmentos não-neutros: a Tabela 2 usa a proporção, não a contagem,
    # porque os vídeos têm número de segmentos muito diferente.
    emo_total: dict[str, int] = {}
    emo_nao_neutro: dict[str, int] = {}
    for r in ler_csv("curva_emocional"):
        vid = r["video_id"]
        emo_total[vid] = emo_total.get(vid, 0) + 1
        if (r["emotion_raw"] or "").strip().lower() != "neutral":
            emo_nao_neutro[vid] = emo_nao_neutro.get(vid, 0) + 1

    dados = {}
    for vid in grupo:
        m = metricas.get(vid, {})
        f = fingerprint.get(vid, {})
        views = numero(m.get("views"))
        likes = numero(m.get("likes"))
        coment = numero(m.get("comments"))

        engajamento = None
        if views and likes is not None and coment is not None:
            engajamento = (likes + coment) / views * 100.0

        nao_neutros = None
        if emo_total.get(vid):
            nao_neutros = emo_nao_neutro.get(vid, 0) / emo_total[vid] * 100.0

        dados[vid] = {
            "grupo":   grupo[vid],
            "criador": criador.get(vid),
            "Views por vídeo":            views,
            "Likes por vídeo":            likes,
            "Comentários por vídeo":      coment,
            "Engajamento %":              engajamento,
            "% segmentos não-neutros":    nao_neutros,
            "Velocidade de fala":         statistics.mean(fala[vid]) if fala.get(vid) else None,
            "Pitch mediano (Hz)":         numero(f.get("median_pitch_hz")),
            "Volume médio (dB)":          numero(f.get("avg_volume_db")),
            "Sentimento dos comentários": numero(f.get("comment_sentiment_avg")),
        }
    return dados


def por_grupo(dados: dict, metrica: str, vids=None) -> tuple[list, list]:
    """Valores da métrica nos grupos A e B, omitindo os ausentes."""
    vids = list(dados) if vids is None else vids
    a = [dados[v][metrica] for v in vids
         if dados[v]["grupo"] == "A" and dados[v][metrica] is not None]
    b = [dados[v][metrica] for v in vids
         if dados[v]["grupo"] == "B" and dados[v][metrica] is not None]
    return a, b
