"""
analise/tabela3.py — Tabela 3: gramática emocional de cada nicho.

Distribuição das 28 labels GoEmotions por grupo, a partir de
`dados/curva_emocional.csv`.

Cada emoção aparece com duas percentagens, e a distinção entre elas é o
argumento da seção: a percentagem sobre o total mede quanto o nicho se
expressa, e a percentagem sobre os não-neutros mede como ele se expressa
quando se expressa. São perguntas diferentes, e a segunda é a que compara
nichos de densidade emocional distinta.

Uso:
    python analise/tabela3.py
"""

import os
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from comum import ler_csv, carregar

LARGURA = 78
NEUTRA = "neutral"


def distribuicao(grupo_de: dict) -> dict:
    """{grupo: Counter de emotion_raw} sobre todos os segmentos do corpus."""
    contagem: dict = {"A": Counter(), "B": Counter()}
    for linha in ler_csv("curva_emocional"):
        grupo = grupo_de.get(linha["video_id"])
        rotulo = (linha["emotion_raw"] or "").strip()
        if grupo and rotulo:
            contagem[grupo][rotulo] += 1
    return contagem


def imprimir(grupo: str, contagem: Counter) -> None:
    total = sum(contagem.values())
    nao_neutros = total - contagem.get(NEUTRA, 0)

    print(f"Grupo {grupo} — {total} segmentos, {nao_neutros} não-neutros")
    print("-" * LARGURA)
    print(f"  {'emoção':18}{'n':>8}{'% total':>12}{'% não-neutros':>16}")

    # neutral primeiro, por ser a referência; o resto por frequência.
    ordem = [(NEUTRA, contagem.get(NEUTRA, 0))] if contagem.get(NEUTRA) else []
    ordem += [(e, n) for e, n in contagem.most_common() if e != NEUTRA]

    for emocao, n in ordem:
        pct_total = n / total * 100 if total else 0.0
        if emocao == NEUTRA:
            print(f"  {emocao:18}{n:>8}{pct_total:>11.1f}%{'—':>16}")
        else:
            pct_nn = n / nao_neutros * 100 if nao_neutros else 0.0
            print(f"  {emocao:18}{n:>8}{pct_total:>11.1f}%{pct_nn:>15.1f}%")
    print()


def main() -> int:
    dados = carregar()
    grupo_de = {vid: d["grupo"] for vid, d in dados.items()}
    contagem = distribuicao(grupo_de)

    print()
    print("=" * LARGURA)
    print("  LAPLACE — Tabela 3 do artigo, reproduzida a partir de dados/")
    print("=" * LARGURA)
    print()
    for grupo in ("A", "B"):
        imprimir(grupo, contagem[grupo])

    # A leitura da seção 4 do artigo depende destas duas linhas.
    print("Comparação")
    print("-" * LARGURA)
    for grupo in ("A", "B"):
        c = contagem[grupo]
        total = sum(c.values())
        nn = total - c.get(NEUTRA, 0)
        top = [(e, n) for e, n in c.most_common() if e != NEUTRA][:3]
        print(f"  Grupo {grupo}: neutral {c.get(NEUTRA, 0) / total * 100:.1f}% do total · "
              + " · ".join(f"{e} {n / nn * 100:.1f}%" for e, n in top))
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
