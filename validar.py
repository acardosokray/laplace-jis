"""
Verifica o determinismo do pipeline: processa o mesmo vídeo duas vezes, em
bancos separados, e compara as 16 tabelas célula a célula.

Uso:
    python validar.py <URL_OU_VIDEO_ID>

Colunas de carimbo de tempo (`collected_at`, `calculated_at`,
`downloaded_at`, `created_at`) e chaves autoincrementais são ignoradas: elas
registram *quando* a execução ocorreu, não o que ela mediu.

Importante: a etapa 1 lê a plataforma ao vivo. Os 100 comentários mais
curtidos mudam com o tempo, então duas execuções separadas por semanas podem
divergir nas tabelas de comentários — a entrada mudou, não o método. Este
script roda as duas execuções em sequência justamente para isolar o pipeline.
"""

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

TABELAS = [
    "videos", "metrics", "comments", "transcripts", "transcript_segments",
    "emoji_stats", "audio_files", "timeline_events", "speech_analysis",
    "lexical_profile", "audio_features", "comment_sentiments",
    "comment_sentiment_agg", "music_events", "cultural_fingerprint",
    "emotion_curve",
]

IGNORADAS = {"id", "collected_at", "calculated_at", "downloaded_at",
             "created_at", "file_path"}


def rodar(url: str, db_path: str) -> None:
    env = dict(os.environ, DB_PATH=db_path, PYTHONIOENCODING="utf-8")
    # `text=True` sozinho decodifica pela página de código do sistema, que no
    # Windows é cp1252 e não cobre os acentos nem os símbolos que o pipeline
    # imprime. A thread que lê a saída morria com UnicodeDecodeError e deixava
    # um traceback no meio do relatório, embora a comparação terminasse certa.
    r = subprocess.run(
        [sys.executable, "main.py", url, "--no-excel", "--no-png"],
        env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        print(r.stdout[-2000:])
        print(r.stderr[-2000:])
        raise SystemExit(f"o pipeline falhou (código {r.returncode})")


def comparar(db_a: str, db_b: str, video_id: str) -> bool:
    a = sqlite3.connect(db_a)
    b = sqlite3.connect(db_b)
    a.row_factory = b.row_factory = sqlite3.Row

    print(f"{'tabela':<24} {'linhas':>7} {'células':>9} {'iguais':>8}")
    print("-" * 62)

    tudo_igual = True
    for tabela in TABELAS:
        cols = [r[1] for r in a.execute(f"PRAGMA table_info({tabela})")
                if r[1] not in IGNORADAS]
        lista = ", ".join(f'"{c}"' for c in cols)
        ra = a.execute(f"SELECT {lista} FROM {tabela} WHERE video_id = ? "
                       f"ORDER BY rowid", (video_id,)).fetchall()
        rb = b.execute(f"SELECT {lista} FROM {tabela} WHERE video_id = ? "
                       f"ORDER BY rowid", (video_id,)).fetchall()

        if len(ra) != len(rb):
            print(f"{tabela:<24} {len(ra)}/{len(rb):<6} "
                  f"{'—':>9} {'nº de linhas difere':>8}")
            tudo_igual = False
            continue

        total = iguais = 0
        primeira = None
        for x, y in zip(ra, rb):
            for c in cols:
                total += 1
                if x[c] == y[c]:
                    iguais += 1
                elif primeira is None:
                    primeira = (c, x[c], y[c])

        marca = "idêntico" if iguais == total else f"{total - iguais} diferem"
        print(f"{tabela:<24} {len(ra):>7} {total:>9} {iguais:>8}  {marca}")
        if primeira:
            c, va, vb = primeira
            print(f"{'':<24} primeira divergência em '{c}':")
            print(f"{'':<26} execução 1: {va!r}")
            print(f"{'':<26} execução 2: {vb!r}")
            tudo_igual = False

    return tudo_igual


def main() -> None:
    if len(sys.argv) < 2:
        print("Uso: python validar.py <URL_OU_VIDEO_ID>")
        raise SystemExit(1)

    alvo = sys.argv[1].strip()
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from modules.extractor import extract_video_id

    video_id = extract_video_id(alvo) or alvo
    url = alvo if alvo.startswith("http") else \
        f"https://www.youtube.com/watch?v={video_id}"

    tmp = tempfile.mkdtemp(prefix="laplace_validacao_")
    db_a = os.path.join(tmp, "execucao_1.db")
    db_b = os.path.join(tmp, "execucao_2.db")

    try:
        print(f"Validando o determinismo para {video_id}\n")
        print("Execução 1 de 2...")
        rodar(url, db_a)
        print("Execução 2 de 2...")
        rodar(url, db_b)
        print()

        igual = comparar(db_a, db_b, video_id)

        print("-" * 62)
        if igual:
            print("RESULTADO: as duas execuções são idênticas nas 16 tabelas.")
        else:
            print("RESULTADO: há divergências — ver acima.")
            raise SystemExit(1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
