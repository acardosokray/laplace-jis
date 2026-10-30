"""
Gera os dados derivados publicáveis do estudo de caso, em CSV.

Uso:
    python exportar_dados.py <caminho_do_banco>

Os identificadores de criadores viram `C1`, `C2`, … via `modules.anonimizacao`.
Comentários entram apenas como agregados por vídeo — nunca o texto individual,
porque um comentário publicado é localizável na plataforma e reidentifica quem
o escreveu, por mais que o autor esteja pseudonimizado.

Saída: `dados/*.csv`, suficiente para reproduzir as Tabelas 2, 3 e 4 do artigo
sem executar o pipeline.

Nenhum criador é nomeado no código: o script lê do banco quais canais existem
e deriva deles os tokens a mascarar. Um vídeo de um criador novo entra sem
nenhuma alteração aqui.

O recorte a exportar vem de um arquivo de `video_id`, não de uma lista
embutida — `dados/corpus_estudo.csv` define os 100 vídeos do artigo e é dado
versionado, auditável como qualquer outro.
"""

import csv
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from modules.anonimizacao import pseudonimo

SAIDA = "dados"
SCHEMA = "schema.sql"


def colunas_do_schema(tabela: str) -> list:
    """Colunas que `schema.sql` declara para a tabela, na ordem de declaração.

    O fingerprint é exportado por esta lista, e não por `SELECT *`, porque o
    banco de origem pode ter colunas que o pipeline deste repositório não
    produz — camadas acrescentadas depois do artigo. Um `*` as publicaria, e o
    revisor receberia um CSV mais largo do que o schema que o código cria.

    Derivar do schema em vez de fixar a lista aqui mantém as duas pontas
    casadas sozinhas: mexer no `schema.sql` muda o CSV na mesma hora.
    """
    caminho = os.path.join(os.path.dirname(os.path.abspath(__file__)), SCHEMA)
    with open(caminho, encoding="utf-8") as f:
        sql = f.read()

    corpo = re.search(
        rf"CREATE TABLE (?:IF NOT EXISTS )?{tabela}\s*\((.*?)\n\);",
        sql, re.S,
    )
    if not corpo:
        raise SystemExit(f"tabela '{tabela}' não encontrada em {SCHEMA}")

    colunas = []
    for linha in corpo.group(1).split("\n"):
        linha = linha.split("--")[0].strip().rstrip(",")
        if not linha or linha.upper().startswith(
            ("FOREIGN", "PRIMARY", "UNIQUE", "CHECK")
        ):
            continue
        colunas.append(linha.split()[0].strip('"'))
    return colunas


CONSULTAS = {
    "videos": """
        SELECT v.video_id, v.channel_id, v.duration_seconds, v.publish_date,
               v.language, v.category_id
        FROM videos v WHERE v.video_id IN ({filtro}) ORDER BY v.video_id
    """,
    "metricas": """
        SELECT m.video_id, m.views, m.likes, m.comments
        FROM metrics m WHERE m.video_id IN ({filtro}) ORDER BY m.video_id
    """,
    "cultural_fingerprint": """
        SELECT {fingerprint} FROM cultural_fingerprint
        WHERE video_id IN ({filtro}) ORDER BY video_id
    """,
    "curva_emocional": """
        SELECT e.video_id, e.segment_id, e.t_start, e.t_end,
               e.t_start_pct, e.t_end_pct, e.emotion_raw, e.emotion_score
        FROM emotion_curve e WHERE e.video_id IN ({filtro})
        ORDER BY e.video_id, e.segment_id
    """,
    "prosodia": """
        SELECT a.video_id, a.t_start, a.t_end, a.volume_db, a.pitch_hz,
               a.energy, a.speech_rate, a.voice_register
        FROM audio_features a WHERE a.video_id IN ({filtro})
        ORDER BY a.video_id, a.t_start
    """,
    "atos_de_fala": """
        SELECT s.video_id, s.t_start, s.t_end, s.utterance_type,
               s.language_register, s.sentiment, s.sentiment_score,
               s.emotion_raw, s.emotion_score, s.classification_evidence
        FROM speech_analysis s WHERE s.video_id IN ({filtro})
        ORDER BY s.video_id, s.t_start
    """,
    "comentarios_agregados": """
        SELECT c.video_id, c.total_analyzed, c.positive_ratio, c.negative_ratio,
               c.neutral_ratio, c.sentiment_avg, c.sentiment_std, c.top_words
        FROM comment_sentiment_agg c WHERE c.video_id IN ({filtro})
        ORDER BY c.video_id
    """,
}

# Colunas que carregam identidade e são substituídas pelo pseudônimo.
PSEUDONIMIZAR = {"channel_id": "canal"}

# Colunas de palavras frequentes. A audiência chama o criador pelo nome
# ("<nome>, delícia, receita…"), então o nome reaparece aqui e desfaz o
# pseudônimo. Os tokens do nome do canal são trocados pelo próprio pseudônimo:
# a frequência continua legível, a identidade não.
COLUNAS_PALAVRAS = ("top_words", "comment_top_words", "top_5_words")

_IGNORAR_TOKEN = {"do", "da", "de", "e", "a", "o", "receitas", "reviews"}


def _tokens_do_canal(nome: str) -> set:
    """Palavras que identificam o canal, separando também nomes colados.

    Um nome colado como "ChefeFulano" rende {"chefe", "fulano"}: sem a
    separação, o token inteiro casaria por substring com "chefe" — palavra
    corrente num corpus de culinária — e mascararia vocabulário legítimo.
    """
    partes = re.findall(r"[A-ZÀ-Ý]?[a-zà-ÿ]+|\d+", nome or "")
    return {
        p.lower() for p in partes
        if len(p) > 2 and p.lower() not in _IGNORAR_TOKEN
    }


def _mascarar_palavras(valor: str, mapa_tokens: dict) -> str:
    """Troca pelo pseudônimo as palavras que identificam qualquer criador.

    O mapa cobre o corpus inteiro, não só o canal do vídeo: a audiência
    menciona outros criadores, e um nome citado identifica tanto quanto o
    próprio. A substituição preserva a informação de que houve menção.
    """
    if not valor or not mapa_tokens:
        return valor
    saida = []
    for palavra in str(valor).split(","):
        limpa = palavra.strip().lower()
        pseudo = mapa_tokens.get(limpa)
        saida.append(f" {pseudo}" if pseudo else palavra)
    return ",".join(saida)


def main() -> None:
    if len(sys.argv) < 2:
        print("Uso: python exportar_dados.py <caminho_do_banco> [data_limite]")
        print("     data_limite: exporta só o coletado antes dela (ex.: 2026-05-01)")
        raise SystemExit(1)

    conn = sqlite3.connect(sys.argv[1])
    lista = sys.argv[2] if len(sys.argv) > 2 else None

    if lista:
        with open(lista, encoding="utf-8") as f:
            ids = [l.split(",")[0].strip() for l in f.readlines()[1:] if l.strip()]
        filtro = ",".join("?" * len(ids))
        args = tuple(ids)
        print(f"recorte: {os.path.basename(lista)}")
    else:
        ids = [r[0] for r in conn.execute("SELECT video_id FROM videos")]
        filtro = ",".join("?" * len(ids))
        args = tuple(ids)
        print("recorte: todos os vídeos do banco")

    print(f"vídeos exportados: {len(ids)}")

    # token identificador -> pseudônimo, para todos os criadores do corpus
    mapa_tokens = {}
    for cid, nome in conn.execute(
        f"SELECT DISTINCT channel_id, channel_name FROM videos "
        f"WHERE video_id IN ({filtro})", args
    ):
        pseudo = pseudonimo("canal", cid)
        for token in _tokens_do_canal(nome or ""):
            mapa_tokens[token] = pseudo
    print(f"tokens de identidade mascarados: {len(mapa_tokens)}")

    os.makedirs(SAIDA, exist_ok=True)
    total = 0

    fingerprint = ", ".join(colunas_do_schema("cultural_fingerprint"))

    for nome, consulta in CONSULTAS.items():
        cur = conn.execute(
            consulta.format(filtro=filtro, fingerprint=fingerprint), args)
        colunas = [d[0] for d in cur.description]
        linhas = cur.fetchall()

        # troca o nome da coluna identificadora pelo rótulo pseudonimizado
        cabecalho = [
            "canal" if c in PSEUDONIMIZAR else c for c in colunas
        ]

        caminho = os.path.join(SAIDA, f"{nome}.csv")
        with open(caminho, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(cabecalho)
            for linha in linhas:
                valores = list(linha)
                for i, col in enumerate(colunas):
                    if col in PSEUDONIMIZAR:
                        valores[i] = pseudonimo(PSEUDONIMIZAR[col], valores[i])
                    elif col in COLUNAS_PALAVRAS:
                        valores[i] = _mascarar_palavras(valores[i], mapa_tokens)
                w.writerow(valores)

        kb = os.path.getsize(caminho) / 1024
        print(f"   {nome:<24} {len(linhas):>6} linhas  {kb:>8.1f} KB")
        total += len(linhas)

    print(f"\n{total} linhas em {len(CONSULTAS)} arquivos, em {SAIDA}/")


if __name__ == "__main__":
    main()
