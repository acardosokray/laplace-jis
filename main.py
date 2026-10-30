"""
Laplace — pipeline determinístico de fingerprint emocional.

Uso: python main.py <URL_DO_VIDEO>

Sete etapas, na ordem descrita na Seção 3 do artigo:
  1. Metadados e comentários      YouTube Data API v3
  2. Áudio e separação de fontes  yt-dlp + Demucs htdemucs_ft
  3. Transcrição                  faster-whisper large-v3
  4. Análise por segmento         (a) regras  (b) BERTimbau + DistilBERT
                                  (c) pysentimiento/bert-pt-emotion
  5. Prosódia                     librosa
  6. Sentimento dos comentários   DistilBERT
  7. Música, fingerprint, export  ShazamIO + agregação + Excel + PNG

A sub-etapa 4c roda depois da persistência: `run_emotion_pipeline` lê os
segmentos já gravados no banco, e não a lista em memória.

Os modelos de GPU carregam e liberam sequencialmente (Demucs → Whisper →
DistilBERT), o que permite rodar o pipeline completo em 6 GB de VRAM. O
classificador de emoção roda em CPU e não disputa VRAM com nenhum deles.
"""

import os
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

# As versões de torch, numpy e scipy fixadas em requirements.txt só têm
# distribuição para 3.11. Em outra versão o erro aparece na instalação, como
# falha de resolução de dependência, e não diz qual é a causa.
if sys.version_info[:2] != (3, 11):
    atual = ".".join(map(str, sys.version_info[:3]))
    sys.exit(
        f"Este pipeline exige Python 3.11 — em uso: {atual}.\n"
        f"   As versões fixadas de torch, numpy e scipy não têm\n"
        f"   distribuição para outras versões.\n"
        f"   Crie o ambiente com o 3.11:\n"
        f"       py -3.11 -m venv .venv        (Windows)\n"
        f"       python3.11 -m venv .venv      (Linux/macOS)"
    )

from modules.extractor import extract_video_id
from modules.youtube_api import get_video_data, get_comments
from modules.normalizer import normalize_video_data, normalize_comments
from modules.audio import download_audio
from modules.demucs import separate as demucs_separate
from modules.transcript import (
    get_transcript, transcript_summary, unload_model as unload_whisper,
)
from modules.speech_analysis import analyze_segments, build_lexical_profile
from modules.bert_analyzer import BERTAnalyzer
from modules.comment_sentiment import analyze_comments_from_list
from modules.emotion import (
    EmotionAnalyzer, run_emotion_pipeline, build_emotion_distribution,
)
from modules.audio_features import extract_features
from modules.music_id import identify_from_file as identify_music
from modules.cultural_fingerprint import calculate as calc_fingerprint
from modules.database import (
    get_connection, create_tables,
    video_exists, insert_video, insert_metrics,
    insert_comments, insert_transcript, insert_transcript_segments,
    insert_emoji_stats, insert_audio_file,
    insert_timeline_events_from_transcript,
    insert_speech_analysis, insert_lexical_profile,
    insert_audio_features, insert_cultural_fingerprint,
    insert_music_events, insert_timeline_events_music,
    insert_comment_sentiments, insert_comment_sentiment_agg,
)
from modules.exporter import export_to_excel, available_sheets
from modules.visualization import generate_triple_view, generate_timeline_view
from config import EXCEL_PATH, VIZ_PATH

SEP = "─" * 58
SEP2 = "┄" * 58


def _fmt_emotions(labels: list) -> str:
    """Distribuição das emoções ordenada por %, com neutral como sufixo."""
    dist = build_emotion_distribution(labels)
    if not dist:
        return "—"
    partes = [f"{lb} {v['pct']:.0f}%" for lb, v in dist.items() if lb != "neutral"]
    neutro = dist.get("neutral")
    sufixo = f"  (neutral: {neutro['pct']:.0f}%)" if neutro else ""
    return (" | ".join(partes) or "só neutral") + sufixo


def print_result(data: dict, comments_data: dict, transcript: dict,
                 audio_info: dict, excel_path: str, png_path: str) -> None:
    """Ficha final do vídeo, legível no terminal."""
    print(f"\n✅ Vídeo processado com sucesso!\n{SEP}")
    print(f"  Título       : {data['title']}")
    print(f"  Canal        : {data['channel_name']}")
    print(f"  Plataforma   : {data['platform']}  │  Formato: {data['format']}")
    print(f"  Publicado em : {data['publish_date']}  │  Duração: {data['duration_seconds']}s")
    print(f"  Idioma       : {data['language'] or '—'}  │  País: {data['country'] or '—'}")

    if data.get("tags"):
        preview = data["tags"][:80] + ("…" if len(data["tags"]) > 80 else "")
        print(f"  Tags         : {preview}")
    if data.get("hashtags"):
        print(f"  Hashtags     : {data['hashtags']}")

    print(SEP)
    print(f"  Views        : {(data['views'] or 0):,}")
    print(f"  Likes        : {(data['likes'] or 0):,}")
    print(f"  Comentários  : {(data['comments'] or 0):,}")
    print(SEP)

    print(f"  Comentários coletados : {comments_data.get('total_collected', 0)}")
    top_emojis = comments_data.get("top_emojis", [])
    if top_emojis:
        print("  Top emojis            : "
              + "  ".join(f"{e}×{c}" for e, c in top_emojis[:5]))

    print(SEP2)
    if audio_info.get("success"):
        cache = " (cache)" if audio_info.get("cached") else ""
        print(f"  Áudio  : ✅ {audio_info['path']}{cache}  "
              f"({audio_info.get('file_size_mb') or 0:.1f} MB)")
    else:
        print(f"  Áudio  : ❌ {audio_info.get('error', 'falha desconhecida')}")

    print(f"  {transcript_summary(transcript)}")
    if transcript.get("available") and transcript.get("text"):
        preview = transcript["text"][:220].replace("\n", " ")
        print(f'  Prévia : "{preview}..."')

    print(SEP2)
    if excel_path:
        print(f"  Excel  : {excel_path}")
    if png_path:
        print(f"  Figura : {png_path}")
    print(f"  Thumb  : {data['thumbnail_url']}")
    print(f"  ID     : {data['video_id']}")
    print(SEP + "\n")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}

    if not args:
        print("Uso: python main.py <URL_DO_VIDEO> [--no-excel] [--no-png]")
        sys.exit(1)

    url = args[0].strip()
    video_id = extract_video_id(url)
    if not video_id:
        print("❌ URL inválida — não foi possível extrair o video_id.")
        sys.exit(1)

    print(f"\n🔍 Processando URL: {url}")
    print(f"   video_id extraído: {video_id}\n")

    # ── Etapa 1 — Metadados e comentários ─────────────────────────────────
    print("📡 [1/7] Metadados e comentários (YouTube Data API v3)...")
    raw = get_video_data(video_id)
    if not raw:
        print("   ❌ Não foi possível obter os dados do vídeo.")
        sys.exit(1)
    normalized = normalize_video_data(raw)
    print(f"   ✅ {normalized.get('title', '')[:60]}")
    comments_data = normalize_comments(get_comments(video_id, max_results=100))
    print(f"   💬 {comments_data['total_collected']} comentários coletados.")

    # ── Etapa 2 — Áudio e separação de fontes ─────────────────────────────
    print("\n🎵 [2/7] Áudio e separação de fontes (yt-dlp + Demucs htdemucs_ft)...")
    audio_info = download_audio(video_id)
    if not audio_info.get("success"):
        print(f"   ❌ Falha no download: {audio_info.get('error')}")
        sys.exit(1)
    cache = " (já existia, usando cache)" if audio_info.get("cached") else ""
    print(f"   ✅ {audio_info['path']}{cache}  "
          f"({audio_info.get('file_size_mb') or 0:.1f} MB)")
    audio_path = audio_info["path"]

    vocals_path, music_fx_path = audio_path, None
    demucs_result = demucs_separate(audio_path, video_id)
    if demucs_result.get("success"):
        vocals_path = demucs_result["vocals_path"]
        music_fx_path = demucs_result["music_fx_path"]
        print(f"   ✅ Vocals   : {vocals_path}")
        print(f"   ✅ Music+FX : {music_fx_path}")
    else:
        print(f"   ⚠️  {demucs_result.get('error', 'Demucs falhou')} — "
              "usando o áudio original.")

    # ── Etapa 3 — Transcrição ─────────────────────────────────────────────
    print("\n🎙️  [3/7] Transcrição (faster-whisper large-v3)...")
    if vocals_path != audio_path:
        print(f"   (usando os vocais limpos do Demucs)")
    transcript = get_transcript(video_id, vocals_path)
    unload_whisper()
    segments = transcript.get("segments", [])
    print(f"   {transcript_summary(transcript)}")

    # Sem transcrição não há segmento, e sem segmento as etapas 4, 5 e 7 não
    # têm sobre o que operar. Seguir adiante gravaria um fingerprint montado
    # a partir do que já estivesse no banco de uma execução anterior — um
    # registro silenciosamente errado. Melhor parar aqui.
    if not segments:
        print("\n❌ Sem segmentos de fala — o pipeline não pode continuar.")
        print(f"   Motivo: {transcript.get('error') or 'transcrição vazia'}")
        print("   Nada foi gravado no banco.")
        sys.exit(1)

    # ── Etapa 4 — Análise linguística, semântica e emocional ──────────────
    print("\n🔤 [4/7] Analisando fala (ato de fala, registro, sentimento)...")
    speech_segments = analyze_segments(segments)
    lexical = build_lexical_profile(transcript.get("text") or "", segments)
    if speech_segments:
        tipos = Counter(s["utterance_type"] for s in speech_segments)
        print(f"   {len(speech_segments)} segmentos → "
              + " | ".join(f"{t}: {n}" for t, n in tipos.most_common()))
    nao_stop = [w for w in lexical if w["category"] != "stopword"]
    print(f"   {len(nao_stop)} palavras relevantes no léxico.")

    print("\n🧠 Enriquecendo com BERTimbau + DistilBERT (sentimento contextual)...")
    comment_sentiment_result = None
    bert = BERTAnalyzer()
    if bert.load():
        speech_segments = bert.analyze_segments(speech_segments)
        print("   ✅ Sentimento e registro atualizados pelo BERT.")
        # Etapa 6 aproveita o mesmo modelo já residente na GPU.
        comment_sentiment_result = analyze_comments_from_list(
            bert, video_id, comments_data["comments_list"],
        )
        bert.unload()
    else:
        print("   ⚠️  Classificador indisponível — mantendo a classificação por regras.")

    # ── Etapa 5 — Prosódia ────────────────────────────────────────────────
    print("\n🎚️  [5/7] Extraindo prosódia (pitch, volume, energia, velocidade)...")
    audio_feats = extract_features(audio_path, segments)
    if audio_feats:
        import statistics
        vol = statistics.mean(f["volume_db"] for f in audio_feats)
        pitches = [f["pitch_hz"] for f in audio_feats if f["pitch_hz"]]
        p_str = f"{statistics.median(pitches):.0f} Hz" if pitches else "N/A"
        print(f"   ✅ {len(audio_feats)} segmentos — "
              f"vol médio: {vol:.1f} dB | pitch mediano: {p_str}")
    else:
        print("   ⚠️  Sem features de áudio.")

    # ── Etapa 6 — Sentimento dos comentários ──────────────────────────────
    print("\n💬 [6/7] Sentimento dos comentários (DistilBERT)...")
    if comment_sentiment_result:
        agg = comment_sentiment_result["agg"]
        if agg["total_analyzed"]:
            print(f"   ✅ {agg['total_analyzed']} comentários → "
                  f"+{agg['positive_ratio'] * 100:.0f}% / "
                  f"-{agg['negative_ratio'] * 100:.0f}% | "
                  f"média: {agg['sentiment_avg']:+.3f}")
        else:
            print("   ℹ️  Sem comentários para analisar.")
    else:
        print("   ⚠️  Não analisado — classificador indisponível.")

    # ── Etapa 7 — Música, fingerprint e exportação ────────────────────────
    print("\n🎶 [7/7] Identificando músicas (ShazamIO)...")
    music_events = []
    if music_fx_path:
        music_events = identify_music(
            music_fx_path, duration_seconds=normalized.get("duration_seconds"),
        )
        for m in music_events:
            print(f"   🎶 [{m['t_start']:.1f}s-{m['t_end']:.1f}s] "
                  f"{m['track_name'] or '?'} — {m['artist'] or '?'}")
        if not music_events:
            print("   ℹ️  Nenhuma música identificada.")
    else:
        print("   ⏭️  Pulado — sem trilha separada.")

    print("\n💾 Gravando as 16 tabelas no banco...")
    conn = get_connection()
    create_tables(conn)

    if not video_exists(conn, video_id):
        insert_video(conn, normalized)
    insert_metrics(conn, normalized)
    insert_comments(conn, video_id, comments_data["comments_list"])
    insert_transcript(conn, video_id, transcript)
    insert_transcript_segments(conn, video_id, segments)
    insert_emoji_stats(conn, video_id, comments_data["top_emojis"])
    insert_audio_file(conn, video_id, audio_info)
    insert_timeline_events_from_transcript(conn, video_id, segments)
    insert_speech_analysis(conn, video_id, speech_segments)
    insert_lexical_profile(conn, video_id, lexical)
    insert_audio_features(conn, video_id, audio_feats)
    print(f"   {len(segments)} eventos de fala na timeline.")

    if comment_sentiment_result:
        insert_comment_sentiments(conn, video_id, comment_sentiment_result["individual"])
        insert_comment_sentiment_agg(conn, video_id, comment_sentiment_result["agg"])
        print(f"   💬 Sentimento de "
              f"{comment_sentiment_result['agg']['total_analyzed']} comentários salvo.")

    if music_events:
        insert_music_events(conn, video_id, music_events)
        insert_timeline_events_music(conn, video_id, music_events)
        print(f"   🎶 {len(music_events)} evento(s) musical(is) salvo(s).")

    # Terceira parte da etapa 4, executada aqui porque run_emotion_pipeline
    # lê os segmentos do banco: preenche emotion_raw/emotion_score em
    # speech_analysis e comment_sentiments, e popula emotion_curve.
    print("\n🎭 [4c] Emoção por segmento (pysentimiento/bert-pt-emotion, CPU)...")
    emo = EmotionAnalyzer()
    if emo.load():
        resultado = run_emotion_pipeline(
            conn, video_id,
            duration_seconds=normalized.get("duration_seconds"),
            analyzer=emo,
        )
        emo.unload()
        print(f"   ✅ {resultado['n_segments_analyzed']} segmentos + "
              f"{resultado['n_comments_analyzed']} comentários "
              f"classificados em 28 emoções.")
        fala = [r[0] for r in conn.execute(
            "SELECT emotion_raw FROM speech_analysis WHERE video_id = ?", (video_id,))]
        audiencia = [r[0] for r in conn.execute(
            "SELECT emotion_raw FROM comment_sentiments WHERE video_id = ?", (video_id,))]
        print(f"   🎙️  Fala      : {_fmt_emotions(fala)}")
        print(f"   💬 Audiência : {_fmt_emotions(audiencia)}")
    else:
        print("   ⚠️  Classificador de emoção indisponível — etapa pulada.")

    print("\n🧬 Calculando o fingerprint cultural...")
    fingerprint = calc_fingerprint(conn, video_id, normalized.get("duration_seconds"))
    insert_cultural_fingerprint(conn, fingerprint)
    print(f"   ✅ perguntas: {(fingerprint['question_ratio'] or 0) * 100:.0f}% | "
          f"CTAs: {(fingerprint['cta_ratio'] or 0) * 100:.0f}% | "
          f"atenção: {(fingerprint['attention_ratio'] or 0) * 100:.0f}% | "
          f"registro: {fingerprint['dominant_register'] or '—'}")
    print(f"      emoção dominante: {fingerprint['emotion_dominant'] or '—'} | "
          f"top words: {fingerprint['top_5_words'] or '—'}")

    excel_path = png_path = None

    if "--no-excel" not in flags:
        print(f"\n📊 Gerando o Excel auditável ({len(available_sheets())} abas)...")
        os.makedirs(os.path.dirname(EXCEL_PATH) or ".", exist_ok=True)
        export_to_excel(conn, EXCEL_PATH)
        excel_path = EXCEL_PATH
        print(f"   ✅ {EXCEL_PATH}")

    if "--no-png" not in flags:
        print("\n🖼️  Gerando as duas figuras...")
        os.makedirs(VIZ_PATH, exist_ok=True)
        png_path = generate_triple_view(video_id, conn=conn, output_dir=VIZ_PATH)
        print(f"   ✅ curva tríplice : {png_path}")
        png_timeline = generate_timeline_view(video_id, conn=conn, output_dir=VIZ_PATH)
        print(f"   ✅ linha do tempo : {png_timeline}")

    print_result(normalized, comments_data, transcript, audio_info,
                 excel_path, png_path)


if __name__ == "__main__":
    main()
