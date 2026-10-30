"""
analise/tabela2.py — Tabela 2: comparação estatística entre os grupos.

Mann-Whitney U bilateral sobre as nove métricas, com tamanho de efeito
rank-biserial, a partir de `dados/*.csv`.

Imprime três blocos:

  1. A Tabela 2 como está no artigo, mais a correção de Benjamini-Hochberg
     para as nove comparações simultâneas.
  2. Sensibilidade: o mesmo teste refeito removendo um criador por vez.
  3. Criador como unidade de análise: mediana por criador, 5 contra 5.

Os blocos 2 e 3 respondem à crítica de pseudo-replicação. Os 100 vídeos vêm
de 10 criadores, e tratá-los como 100 observações independentes supõe que
vídeos do mesmo canal não partilham voz, edição nem público — o que é falso.
Com 5 contra 5 o p mínimo do Mann-Whitney é 0,0079, de modo que o bloco 3 é
um teste deliberadamente severo: o que sobrevive a ele é diferença de nicho,
não de criador.

Uso:
    python analise/tabela2.py
"""

import os
import statistics
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from comum import METRICAS, carregar, por_grupo

from scipy import stats

LARGURA = 78


def mann_whitney(a: list, b: list):
    """U, p bilateral e rank-biserial. None quando a amostra é pequena demais."""
    if len(a) < 3 or len(b) < 3:
        return None, None, None
    u, p = stats.mannwhitneyu(a, b, alternative="two-sided")
    # Rank-biserial a partir de U, na convenção de sinal do artigo: negativo
    # quando o grupo A tem os postos mais altos.
    rb = 1.0 - 2.0 * u / (len(a) * len(b))
    return float(u), float(p), float(rb)


def benjamini_hochberg(ps: list) -> list:
    """q-valores de BH: controla a taxa de falsas descobertas.

    Nove testes no mesmo corpus inflam a chance de ao menos um p < 0,05 por
    acaso. Bonferroni seria conservador demais para métricas correlacionadas
    entre si (views, likes e comentários medem engajamento), e BH é o
    procedimento usual nesse caso.
    """
    k = len(ps)
    ordem = sorted(range(k), key=lambda i: ps[i])
    q, menor = [1.0] * k, 1.0
    for posto in range(k - 1, -1, -1):
        i = ordem[posto]
        menor = min(menor, ps[i] * k / (posto + 1))
        q[i] = menor
    return q


def testar(dados: dict, vids=None) -> list:
    """Uma linha por métrica, com U, p, rank-biserial e q de BH."""
    linhas, ps = [], []
    for metrica in METRICAS:
        a, b = por_grupo(dados, metrica, vids)
        u, p, rb = mann_whitney(a, b)
        if p is None:
            continue
        linhas.append({
            "metrica": metrica, "u": u, "p": p, "rb": rb,
            "mediana_a": statistics.median(a), "mediana_b": statistics.median(b),
            "na": len(a), "nb": len(b),
        })
        ps.append(p)
    for linha, q in zip(linhas, benjamini_hochberg(ps)):
        linha["q"] = q
    return linhas


def _estrelas(p: float) -> str:
    return "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else "ns"


def bloco_tabela2(dados: dict) -> None:
    print("TABELA 2 — Mann-Whitney U, 50 vídeos A contra 50 vídeos B")
    print("-" * LARGURA)
    print(f"  {'métrica':28}{'Mdn A':>12}{'Mdn B':>12}{'U':>7}{'p':>11}{'r':>8}  sig")
    linhas = testar(dados)
    for d in linhas:
        print(f"  {d['metrica'][:28]:28}{d['mediana_a']:>12.3f}{d['mediana_b']:>12.3f}"
              f"{d['u']:>7.0f}{d['p']:>11.2e}{d['rb']:>+8.3f}  {_estrelas(d['p'])}")

    print()
    print("Correção de Benjamini-Hochberg para as nove comparações")
    print("-" * LARGURA)
    print(f"  {'métrica':28}{'p':>12}{'q (BH)':>12}  veredito")
    for d in sorted(linhas, key=lambda x: x["p"]):
        print(f"  {d['metrica'][:28]:28}{d['p']:>12.2e}{d['q']:>12.2e}"
              f"  {'sobrevive' if d['q'] < 0.05 else 'cai'}")
    sobrevivem = sum(1 for d in linhas if d["q"] < 0.05)
    print()
    print(f"  {sobrevivem} de {len(linhas)} sobrevivem à correção.")


def bloco_sensibilidade(dados: dict) -> None:
    print()
    print("SENSIBILIDADE — o resultado depende de um único criador?")
    print("-" * LARGURA)
    print("  Cada linha remove um criador e refaz as nove comparações.")
    print()
    criadores = sorted({d["criador"] for d in dados.values()})
    for criador in criadores:
        sub = [v for v, d in dados.items() if d["criador"] != criador]
        linhas = testar(dados, sub)
        sig = sum(1 for d in linhas if d["q"] < 0.05)
        grupo = next(d["grupo"] for d in dados.values() if d["criador"] == criador)
        rotulo = f"sem {criador} (grupo {grupo})"
        print(f"  {rotulo:26}{sig}/{len(linhas)} sobrevivem a BH")


def bloco_criador_como_unidade(dados: dict) -> None:
    print()
    print("CRIADOR COMO UNIDADE DE ANÁLISE — 5 contra 5")
    print("-" * LARGURA)
    print("  Mediana de cada criador, uma observação por criador. Com 5 contra 5")
    print("  o menor p possível do Mann-Whitney é 0,0079.")
    print()

    medianas: dict = {}
    grupo_de: dict = {}
    for d in dados.values():
        grupo_de[d["criador"]] = d["grupo"]
    for metrica in METRICAS:
        for vid, d in dados.items():
            if d[metrica] is not None:
                medianas.setdefault(d["criador"], {}).setdefault(metrica, []).append(d[metrica])

    criadores = sorted(medianas)
    linhas, ps = [], []
    for metrica in METRICAS:
        a = [statistics.median(medianas[c][metrica]) for c in criadores
             if grupo_de[c] == "A" and medianas[c].get(metrica)]
        b = [statistics.median(medianas[c][metrica]) for c in criadores
             if grupo_de[c] == "B" and medianas[c].get(metrica)]
        u, p, rb = mann_whitney(a, b)
        if p is None:
            continue
        linhas.append({"metrica": metrica, "p": p, "rb": rb})
        ps.append(p)
    for linha, q in zip(linhas, benjamini_hochberg(ps)):
        linha["q"] = q

    print(f"  {'métrica':28}{'p':>10}{'q (BH)':>10}{'r':>9}")
    for d in sorted(linhas, key=lambda x: x["p"]):
        print(f"  {d['metrica'][:28]:28}{d['p']:>10.4f}{d['q']:>10.4f}{d['rb']:>+9.3f}")
    print()
    print(f"  {sum(1 for d in linhas if d['p'] < 0.05)} de {len(linhas)} com p < 0,05 · "
          f"{sum(1 for d in linhas if d['q'] < 0.05)} sobrevivem a BH.")


def main() -> int:
    dados = carregar()
    criadores = {d["criador"] for d in dados.values()}
    print()
    print("=" * LARGURA)
    print("  LAPLACE — Tabela 2 do artigo, reproduzida a partir de dados/")
    print("=" * LARGURA)
    print(f"  {len(dados)} vídeos · {len(criadores)} criadores · "
          f"{len(METRICAS)} métricas")
    print()
    bloco_tabela2(dados)
    bloco_sensibilidade(dados)
    bloco_criador_como_unidade(dados)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
