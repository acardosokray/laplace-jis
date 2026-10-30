"""
modules/emotion.py — Análise de emoções (fala + comentários) em CPU.

Modelo: `pysentimiento/bert-pt-emotion` — BERT-pt fine-tuned no dataset GoEmotions
(Google Research, 2020). Retorna 28 labels em inglês; mantemos a label bruta sem
nenhum mapeamento — cada tabela guarda diretamente `emotion_raw` e `emotion_score`.

POR QUE USAR transformers DIRETO (SEM pysentimiento)
────────────────────────────────────────────────────
O wrapper oficial do pysentimiento 0.7.3 usa torch.load em pytorch_model.bin e
exige torch >= 2.6. O projeto roda torch 2.5.1+cu121 (faster-whisper + demucs
dependem dessa versão). Força `use_safetensors=True` em `from_pretrained` para
carregar o snapshot novo do HF com safetensors — mesma rede, zero conflito.

RETORNO DA PREDIÇÃO
───────────────────
  {
    "emotion_raw":          str,    # 1 de 28 labels GoEmotions em inglês (ex: "joy")
    "emotion_score":        float,  # probabilidade da label dominante (0.0-1.0)
    "emotion_distribution": dict,   # {label: prob, ...} com TODAS as 28 GoEmotions,
                                    # soma ≈ 1.0, chaves ordenadas alfabeticamente
                                    # (canônica pra DTW-EMD downstream)
  }

IDEMPOTÊNCIA
────────────
Modelo pesa ~440 MB, carrega em CPU (~1 GB RAM, zero VRAM). Load uma vez por
execução do pipeline (`load()`) e libera no fim (`unload()`). Chamador deve
verificar antes de invocar se o vídeo já tem `emotion_raw IS NOT NULL`.
"""

from typing import Optional


# ── Analyzer ──────────────────────────────────────────────────────────────────

class EmotionAnalyzer:
    """Wrapper sobre bert-pt-emotion com cache de modelo e batch inference."""

    MODEL_NAME = "pysentimiento/bert-pt-emotion"
    MAX_LEN = 128
    BATCH_SIZE = 16

    def __init__(self) -> None:
        self._model = None
        self._tokenizer = None
        self._id2label: dict[int, str] = {}
        self._torch = None  # lazy

    # ── ciclo de vida ─────────────────────────────────────────────────────

    def load(self) -> bool:
        """Carrega o modelo em CPU. Retorna True se OK, False se falhou."""
        if self._model is not None:
            return True
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            print(f"   EmotionAnalyzer: carregando {self.MODEL_NAME} no cpu...")
            # `from_pretrained` consulta o Hub para checar se há versão nova,
            # mesmo com os pesos já em cache. Uma indisponibilidade da API
            # (429, queda de rede) derrubaria a etapa inteira por causa de uma
            # verificação dispensável — o cache basta para rodar.
            try:
                self._model = AutoModelForSequenceClassification.from_pretrained(
                    self.MODEL_NAME, use_safetensors=True,
                )
                self._tokenizer = AutoTokenizer.from_pretrained(self.MODEL_NAME)
            except Exception as rede:
                print(f"   EmotionAnalyzer: Hub indisponível "
                      f"({type(rede).__name__}) — usando o cache local.")
                self._model = AutoModelForSequenceClassification.from_pretrained(
                    self.MODEL_NAME, use_safetensors=True, local_files_only=True,
                )
                self._tokenizer = AutoTokenizer.from_pretrained(
                    self.MODEL_NAME, local_files_only=True,
                )
            self._model.eval()
            self._id2label = self._model.config.id2label
            self._torch = torch
            print("   EmotionAnalyzer: modelo carregado.")
            return True
        except Exception as e:
            print(f"   ⚠️  EmotionAnalyzer falhou ao carregar: {e}")
            self._model = None
            self._tokenizer = None
            return False

    def unload(self) -> None:
        """Libera referências. Garbage collector cuida do resto (CPU-only)."""
        if self._model is None:
            return
        try:
            import gc
            self._model = None
            self._tokenizer = None
            self._id2label = {}
            gc.collect()
            print("   EmotionAnalyzer: modelo descarregado.")
        except Exception:
            pass

    # ── predição ──────────────────────────────────────────────────────────

    def predict(self, text: str) -> dict:
        """Classifica 1 texto. Retorna {emotion_raw, emotion_score}."""
        out = self.predict_batch([text])
        return out[0] if out else _empty_result()

    def predict_batch(self, texts: list[str]) -> list[dict]:
        """
        Classifica uma lista de textos em batches.
        Preserva a ordem. Textos vazios → _empty_result() (sem erro).
        """
        if not texts:
            return []

        results: list[Optional[dict]] = [None] * len(texts)

        valid_pairs: list[tuple[int, str]] = []
        for i, t in enumerate(texts):
            if t and t.strip():
                valid_pairs.append((i, t.strip()))
            else:
                results[i] = _empty_result()

        if not valid_pairs or self._model is None:
            return [r if r else _empty_result() for r in results]

        torch = self._torch
        with torch.no_grad():
            for start in range(0, len(valid_pairs), self.BATCH_SIZE):
                batch = valid_pairs[start:start + self.BATCH_SIZE]
                idxs = [idx for idx, _ in batch]
                batch_texts = [t for _, t in batch]

                enc = self._tokenizer(
                    batch_texts,
                    return_tensors="pt",
                    truncation=True,
                    padding=True,
                    max_length=self.MAX_LEN,
                )
                logits = self._model(**enc).logits
                probs = torch.softmax(logits, dim=-1)
                top_scores, top_ids = probs.max(dim=-1)

                probs_list = probs.tolist()
                for idx, tid, score, row_probs in zip(
                    idxs, top_ids.tolist(), top_scores.tolist(), probs_list,
                ):
                    raw_label = self._id2label.get(tid, "unknown")
                    # Constrói o dict 28-dim com chaves em ordem alfabética
                    # canônica — ordem do dict não é estável entre versões do
                    # pysentimiento; sort_keys no dump downstream garante isso,
                    # mas montamos já ordenado aqui pra simplificar inspeção.
                    distribution = {
                        self._id2label[i]: round(float(p), 6)
                        for i, p in enumerate(row_probs)
                    }
                    distribution = dict(sorted(distribution.items()))
                    results[idx] = {
                        "emotion_raw":          raw_label,
                        "emotion_score":        round(float(score), 4),
                        "emotion_distribution": distribution,
                    }

        return [r if r else _empty_result() for r in results]


def _empty_result() -> dict:
    return {
        "emotion_raw":          None,
        "emotion_score":        None,
        "emotion_distribution": None,
    }


# ── Agregação: distribuição das labels brutas ativadas ───────────────────────

def build_emotion_distribution(raw_labels: list[Optional[str]]) -> dict:
    """
    Monta a distribuição das labels GoEmotions brutas ativadas, ordenada por %.
    Exemplo:
      {"joy": {"pct": 34.0, "n": 17}, "anger": {"pct": 14.0, "n": 7}, ...}

    Denominador = somente labels não-None. Apenas labels que apareceram ≥1 vez
    aparecem no dict. Ordenado por pct desc.
    """
    from collections import Counter

    counter: Counter = Counter(l for l in raw_labels if l)
    denom = sum(counter.values())
    if denom == 0:
        return {}

    pairs = [(label, round(n / denom * 100, 1), n) for label, n in counter.items()]
    pairs.sort(key=lambda x: (-x[1], x[0]))

    return {label: {"pct": pct, "n": n} for label, pct, n in pairs}


def dominant_emotion(raw_labels: list[Optional[str]]) -> Optional[str]:
    """Retorna a label GoEmotions bruta mais frequente (ignora None)."""
    from collections import Counter
    counter: Counter = Counter(l for l in raw_labels if l)
    if not counter:
        return None
    return counter.most_common(1)[0][0]


# ── Orquestração: lê do banco, roda analyzer, atualiza, popula emotion_curve ─

def run_emotion_pipeline(
    conn,
    video_id: str,
    duration_seconds: Optional[float],
    analyzer: Optional[EmotionAnalyzer] = None,
    force: bool = False,
) -> dict:
    """
    Orquestra a análise de emoções para 1 vídeo.

    Lê textos já persistidos em speech_analysis e comment_sentiments, roda o
    EmotionAnalyzer (carregando on-demand se nenhum for passado), atualiza as
    linhas no banco com emotion_raw + emotion_score, popula emotion_curve e
    retorna um resumo agregado para o fingerprint.

    Idempotência: se todas as linhas já têm emotion_raw preenchida e force=False,
    pula o analyzer e só recalcula agregados a partir do banco.

    Args:
      conn:              conexão SQLite aberta
      video_id:          ID do vídeo
      duration_seconds:  duração em segundos (para normalizar t_*_pct)
      analyzer:          instância opcional; se None, o módulo carrega e descarrega
      force:             reanalisa mesmo se as colunas já estiverem preenchidas

    Returns:
      {
        "speech_dominant":       str|None,   # label GoEmotions bruta
        "speech_distribution":   str|None,   # JSON string (só labels ativadas)
        "audience_dominant":     str|None,
        "audience_distribution": str|None,
        "n_segments_analyzed":   int,
        "n_comments_analyzed":   int,
      }
    """
    import json
    from modules.database import (
        update_speech_emotions, update_comment_emotions,
        update_comment_sentiment_agg_emotion, insert_emotion_curve,
        speech_emotions_analyzed, comment_emotions_analyzed,
    )

    # ── 1. Decide se precisa rodar o analyzer ─────────────────────────────
    speech_done    = speech_emotions_analyzed(conn, video_id)
    comments_done  = comment_emotions_analyzed(conn, video_id)
    need_run       = force or (not speech_done) or (not comments_done)

    owns_analyzer = False
    if need_run and analyzer is None:
        analyzer = EmotionAnalyzer()
        if not analyzer.load():
            return _empty_pipeline_result()
        owns_analyzer = True

    # ── 2. Fala (speech_analysis) ────────────────────────────────────────
    speech_rows = conn.execute("""
        SELECT t_start, t_end, text, emotion_raw, emotion_score
        FROM   speech_analysis
        WHERE  video_id = ?
        ORDER  BY t_start
    """, (video_id,)).fetchall()

    speech_items = [dict(r) for r in speech_rows]

    if need_run and (force or not speech_done) and speech_items:
        texts = [s["text"] or "" for s in speech_items]
        preds = analyzer.predict_batch(texts)
        updates = []
        for s, p in zip(speech_items, preds):
            s["emotion_raw"]   = p["emotion_raw"]
            s["emotion_score"] = p["emotion_score"]
            dist = p.get("emotion_distribution")
            s["emotion_distribution_json"] = (
                json.dumps(dist, sort_keys=True, ensure_ascii=False)
                if dist else None
            )
            updates.append(s)
        update_speech_emotions(conn, video_id, updates)

    # ── 3. Comentários (comment_sentiments) ──────────────────────────────
    comment_rows = conn.execute("""
        SELECT comment_id, text, emotion_raw, emotion_score
        FROM   comment_sentiments
        WHERE  video_id = ?
    """, (video_id,)).fetchall()

    comment_items = [dict(r) for r in comment_rows]

    if need_run and (force or not comments_done) and comment_items:
        texts = [c["text"] or "" for c in comment_items]
        preds = analyzer.predict_batch(texts)
        updates = []
        for c, p in zip(comment_items, preds):
            c["emotion_raw"]   = p["emotion_raw"]
            c["emotion_score"] = p["emotion_score"]
            dist = p.get("emotion_distribution")
            c["emotion_distribution_json"] = (
                json.dumps(dist, sort_keys=True, ensure_ascii=False)
                if dist else None
            )
            updates.append(c)
        update_comment_emotions(conn, video_id, updates)

    # ── 4. Agregados da fala ─────────────────────────────────────────────
    speech_raw = [s.get("emotion_raw") for s in speech_items]
    speech_dist = build_emotion_distribution(speech_raw)
    speech_dominant = dominant_emotion(speech_raw)
    speech_dist_json = json.dumps(speech_dist, ensure_ascii=False)

    # ── 5. Agregados dos comentários ─────────────────────────────────────
    audience_raw = [c.get("emotion_raw") for c in comment_items]
    audience_dist = build_emotion_distribution(audience_raw)
    audience_dominant = dominant_emotion(audience_raw)
    audience_dist_json = json.dumps(audience_dist, ensure_ascii=False)

    update_comment_sentiment_agg_emotion(conn, video_id, audience_dist_json)

    # ── 6. Curva emocional (uma linha por segmento de fala) ──────────────
    dur = float(duration_seconds or 0)
    curve: list[dict] = []
    for i, s in enumerate(speech_items):
        t_start = float(s["t_start"] or 0)
        t_end   = float(s["t_end"] or 0)
        curve.append({
            "segment_id":                i,
            "t_start":                   t_start,
            "t_end":                     t_end,
            "t_start_pct":               round(t_start / dur * 100, 2) if dur > 0 else None,
            "t_end_pct":                 round(t_end   / dur * 100, 2) if dur > 0 else None,
            "emotion_raw":               s.get("emotion_raw"),
            "emotion_score":             s.get("emotion_score"),
            "emotion_distribution_json": s.get("emotion_distribution_json"),
            "segment_text":              s.get("text"),
        })
    insert_emotion_curve(conn, video_id, curve)

    # ── 7. Libera o analyzer se criamos aqui ─────────────────────────────
    if owns_analyzer and analyzer is not None:
        analyzer.unload()

    return {
        "speech_dominant":       speech_dominant,
        "speech_distribution":   speech_dist_json,
        "audience_dominant":     audience_dominant,
        "audience_distribution": audience_dist_json,
        "n_segments_analyzed":   len(speech_items),
        "n_comments_analyzed":   len(comment_items),
    }


def _empty_pipeline_result() -> dict:
    return {
        "speech_dominant":       None,
        "speech_distribution":   None,
        "audience_dominant":     None,
        "audience_distribution": None,
        "n_segments_analyzed":   0,
        "n_comments_analyzed":   0,
    }
