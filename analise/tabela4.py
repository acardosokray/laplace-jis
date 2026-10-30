"""
analise/tabela4.py — Tabela 4: reação da audiência, por criador.

Agrega por criador os comentários analisados de seus dez vídeos, a partir de
`dados/comentarios_agregados.csv` e `dados/cultural_fingerprint.csv`.

Duas colunas da Tabela 4 do artigo não são reproduzíveis aqui, e a omissão é
deliberada: a média e a mediana de curtidas por comentário exigiriam publicar
a curtida de cada comentário individual. Um comentário é localizável na
plataforma pelo texto e pelo número de curtidas, o que reidentifica quem o
escreveu — por isso `dados/` publica apenas agregados por vídeo. As demais
colunas saem completas.

Uso:
    python analise/tabela4.py
"""

import os
import statistics
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from comum import ler_csv, numero, carregar

LARGURA = 96


def agregar(dados: dict) -> list:
    """Uma linha por criador, com os dez vídeos somados."""
    agregados = {r["video_id"]: r for r in ler_csv("comentarios_agregados")}
    fingerprint = {r["video_id"]: r for r in ler_csv("cultural_fingerprint")}

    por_criador: dict = {}
    for vid, d in dados.items():
        agg = agregados.get(vid)
        if not agg:
            continue
        chave = (d["grupo"], d["criador"])
        por_criador.setdefault(chave, []).append((agg, fingerprint.get(vid, {})))

    linhas = []
    for (grupo, criador), itens in sorted(por_criador.items()):
        total = sum(int(numero(a["total_analyzed"]) or 0) for a, _ in itens)

        # Média ponderada pelo número de comentários do vídeo: um vídeo com
        # 100 comentários não pode pesar igual a um com 12.
        def ponderada(campo):
            pares = [(numero(a[campo]), numero(a["total_analyzed"])) for a, _ in itens]
            pares = [(v, n) for v, n in pares if v is not None and n]
            return sum(v * n for v, n in pares) / sum(n for _, n in pares) if pares else None

        emocoes = [f.get("audience_emotion_dominant") for _, f in itens
                   if f.get("audience_emotion_dominant")]
        palavras: Counter = Counter()
        for a, _ in itens:
            for p in (a.get("top_words") or "").split(","):
                p = p.strip()
                if p:
                    palavras[p] += 1

        linhas.append({
            "grupo": grupo, "criador": criador, "total": total,
            "sentimento": ponderada("sentiment_avg"),
            "pos": ponderada("positive_ratio"),
            "neg": ponderada("negative_ratio"),
            "emocao": Counter(emocoes).most_common(1)[0][0] if emocoes else "—",
            "palavras": ", ".join(p for p, _ in palavras.most_common(5)),
        })
    return linhas


def main() -> int:
    dados = carregar()
    linhas = agregar(dados)

    print()
    print("=" * LARGURA)
    print("  LAPLACE — Tabela 4 do artigo, reproduzida a partir de dados/")
    print("=" * LARGURA)
    print()
    print(f"  {'criador':9}{'gr':>3}{'total':>8}{'sent.':>9}{'% pos':>8}{'% neg':>8}"
          f"  {'emoção':13}top 5 palavras")
    print("  " + "-" * (LARGURA - 4))
    for d in linhas:
        print(f"  {d['criador']:9}{d['grupo']:>3}{d['total']:>8}"
              f"{d['sentimento']:>+9.3f}{d['pos'] * 100:>8.1f}{d['neg'] * 100:>8.1f}"
              f"  {d['emocao'][:13]:13}{d['palavras']}")

    print()
    for grupo in ("A", "B"):
        do_grupo = [d for d in linhas if d["grupo"] == grupo]
        print(f"  Grupo {grupo}: sentimento médio "
              f"{statistics.mean(d['sentimento'] for d in do_grupo):+.3f} · "
              f"{sum(d['total'] for d in do_grupo)} comentários")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
