"""
analise/dtw.py — Similaridade narrativa intra-grupo por Dynamic Time Warping.

Compara as curvas emocionais de `dados/curva_emocional.csv` duas a duas
dentro de cada grupo. O DTW alinha sequências de comprimentos diferentes,
que é o caso aqui: os vídeos têm de três a mais de cem segmentos, e a
pergunta é se a *forma* da trajetória emocional se repete dentro do nicho,
não se os vídeos têm a mesma duração.

As emoções entram por codificação ordinal, e essa é uma limitação assumida
no artigo: a escala trata as distâncias entre rótulos como euclidianas, de
modo que `joy`(3) e `amusement`(4) ficam vizinhos por construção, enquanto
`admiration`(1) e `love`(10) ficam distantes sem que isso signifique algo
semântico. A substituição por distância vetorial consta como trabalho
futuro.

Uso:
    python analise/dtw.py
"""

import os
import statistics
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from comum import ler_csv, carregar

LARGURA = 78
MIN_SEGMENTOS = 3

#: Codificação ordinal das emoções, fixa. Qualquer rótulo fora da lista cai
#: em OUTROS, para que a escala não mude conforme o corpus.
CODIGO_EMOCAO = {
    "neutral": 0, "admiration": 1, "curiosity": 2, "joy": 3,
    "amusement": 4, "approval": 5, "excitement": 6, "sadness": 7,
    "anger": 8, "fear": 9, "love": 10, "surprise": 11,
    "disapproval": 12, "annoyance": 13,
}
OUTROS = 14


def sequencias(grupo_de: dict) -> dict:
    """{grupo: {video_id: [códigos]}} em ordem temporal."""
    bruto: dict = {}
    for linha in ler_csv("curva_emocional"):
        bruto.setdefault(linha["video_id"], []).append(
            (float(linha["t_start_pct"] or 0), linha["emotion_raw"] or ""))

    saida: dict = {"A": {}, "B": {}}
    for vid, pares in bruto.items():
        grupo = grupo_de.get(vid)
        if not grupo or len(pares) < MIN_SEGMENTOS:
            continue
        pares.sort(key=lambda p: p[0])
        saida[grupo][vid] = [CODIGO_EMOCAO.get(e.strip(), OUTROS) for _, e in pares]
    return saida


def analisar(grupo: str, seqs: dict, criador_de: dict) -> None:
    import numpy as np
    from dtaidistance import dtw

    ids = sorted(seqs)
    print(f"Grupo {grupo} — {len(ids)} vídeos com curva emocional")
    print("-" * LARGURA)

    arrays = [np.array(seqs[v], dtype=np.double) for v in ids]
    matriz = dtw.distance_matrix_fast(arrays)

    distancias = []
    mesmo_criador, criadores_diferentes = [], []
    melhor = (float("inf"), None, None)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            d = float(matriz[i][j])
            if d == float("inf"):
                continue
            distancias.append(d)
            par = (criador_de.get(ids[i]), criador_de.get(ids[j]))
            (mesmo_criador if par[0] == par[1] else criadores_diferentes).append(d)
            if d < melhor[0]:
                melhor = (d, ids[i], ids[j])

    print(f"  distância média   : {statistics.mean(distancias):.2f}")
    print(f"  mediana           : {statistics.median(distancias):.2f}")
    print(f"  par mais similar  : {melhor[1]} ({criador_de.get(melhor[1])}) ↔ "
          f"{melhor[2]} ({criador_de.get(melhor[2])}) — {melhor[0]:.2f}")

    # Se a assinatura narrativa fosse do criador e não do nicho, os pares do
    # mesmo canal seriam sistematicamente mais próximos entre si.
    if mesmo_criador and criadores_diferentes:
        print(f"  pares do mesmo criador     : {statistics.mean(mesmo_criador):.2f} "
              f"(n={len(mesmo_criador)})")
        print(f"  pares de criadores distintos: "
              f"{statistics.mean(criadores_diferentes):.2f} "
              f"(n={len(criadores_diferentes)})")
    print()


def main() -> int:
    import importlib.util
    faltando = [m for m in ("dtaidistance", "numpy")
                if importlib.util.find_spec(m) is None]
    if faltando:
        print(f"Falta instalar: {', '.join(faltando)}")
        print("Rode: pip install -r requirements.txt")
        return 1

    dados = carregar()
    grupo_de = {vid: d["grupo"] for vid, d in dados.items()}
    criador_de = {vid: d["criador"] for vid, d in dados.items()}
    seqs = sequencias(grupo_de)

    print()
    print("=" * LARGURA)
    print("  LAPLACE — DTW sobre as sequências emocionais")
    print("=" * LARGURA)
    print(f"  Codificação ordinal com {len(CODIGO_EMOCAO)} rótulos + OUTROS; "
          f"mínimo de {MIN_SEGMENTOS} segmentos por vídeo.")
    print()
    for grupo in ("A", "B"):
        analisar(grupo, seqs[grupo], criador_de)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
