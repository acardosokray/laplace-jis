"""
modules/bert_analyzer.py — Análise contextual por segmento.

Carrega dois artefatos, com papéis distintos:
  - neuralmind/bert-base-portuguese-cased — apenas o tokenizador, arquivos de
    vocabulário, sem pesos e sem ocupar a placa.
  - lxyuan/distilbert-base-multilingual-cased-sentiments-student — o
    classificador que de fato produz o sentimento contextual
    (positivo | negativo | neutro | misto).

O registro linguístico (gíria | informal | formal) sai de listas lexicais, não
de modelo. O utterance_type (pergunta, CTA, etc.) continua rule-based — é mais
preciso.

Modelo: ~440 MB — carrega sozinho, depois que o Whisper libera a VRAM.
Sequência VRAM: Demucs → libera → Whisper → libera → DistilBERT → libera → librosa (CPU)

Pré-requisito: pip install transformers torch
"""

import sys
import os
import re

SENTIMENT_MODEL = "lxyuan/distilbert-base-multilingual-cased-sentiments-student"


_cuda_paths_added = False


def _add_cuda_dll_paths() -> None:
    """Fix DLLs CUDA no Windows.

    IDEMPOTÊNCIA: flag global garante uma única injeção por processo. Sem isso,
    em batch.py o PATH cresce sem dedup e estoura o limite de 32767 chars do
    Windows depois de ~33 vídeos (causando ERRO FATAL em todos os subprocessos).
    """
    global _cuda_paths_added
    if _cuda_paths_added or sys.platform != "win32":
        return
    candidates: set[str] = set()
    venv_root = os.path.dirname(os.path.dirname(sys.executable))
    candidates.add(os.path.join(venv_root, "Lib", "site-packages"))
    try:
        import site
        for sp in site.getsitepackages():
            candidates.add(sp)
    except Exception:
        pass
    dll_dirs: list[str] = []
    for sp in candidates:
        # Pacotes nvidia-*-cu12 instalados separadamente.
        nvidia_dir = os.path.join(sp, "nvidia")
        if os.path.isdir(nvidia_dir):
            for package in os.listdir(nvidia_dir):
                bin_dir = os.path.join(nvidia_dir, package, "bin")
                if os.path.isdir(bin_dir):
                    dll_dirs.append(bin_dir)

        # As mesmas DLLs vêm embutidas na roda do torch com CUDA. Instalar
        # `torch --index-url .../cu121` não traz os pacotes nvidia-* avulsos,
        # e sem este caminho o ctranslate2 não acha cublas64_12.dll.
        torch_lib = os.path.join(sp, "torch", "lib")
        if os.path.isdir(torch_lib):
            dll_dirs.append(torch_lib)

    for d in dll_dirs:
        try:
            os.add_dll_directory(d)
        except Exception:
            pass
    if dll_dirs:
        os.environ["PATH"] = ";".join(dll_dirs) + ";" + os.environ.get("PATH", "")
    _cuda_paths_added = True


# ── Vocabulário para registro linguístico (feature engineering para BERT) ──────

_GIRIA_MARKERS = {
    # Gírias gerais PT-BR
    "mano", "cara", "véi", "véio", "bro", "trampo", "rolou", "rola",
    "firmeza", "daora", "zoeira", "mitar", "mitou", "cringe", "foda",
    "caralho", "porra", "oxe", "eita", "top", "show", "massa",
    "suave", "papo reto", "é nóis", "tô ligado", "tá ligado",
    # Hip-hop / periférico / paulistano
    "fita", "brau", "pá", "fudeu", "cria", "visão", "nave", "truta",
    "quebrada", "periferia", "favela", "sangue bom", "maluco", "mina",
    "parceiro", "parça", "correr atrás", "corre", "responsa", "neurose",
    "blindado", "na humildade", "humilde", "ostentação", "progresso",
    "caminhada", "jogo", "game", "patrão", "patroa", "rei", "rainha",
    # Nordestino
    "arretado", "visse", "mainha", "painho", "oxente", "aff",
    # Internet/Gen-Z
    "slay", "vibe", "crush", "stalkear", "cancelar", "exposed",
    "shippando", "rankeado", "grindando", "based", "delulu",
}

_INFORMAL_MARKERS = {
    "tô", "tá", "cê", "né", "tava", "tamo", "a gente", "num", "numa",
    "pra", "pro", "pros", "pras", "galera", "pessoal", "gente",
    "tipo", "tipo assim", "sei lá", "literalmente", "basicamente",
    "ó", "aqui ó", "olha só", "vem cá",
}

_FORMAL_MARKERS = {
    # Conectivos e articuladores cultos
    "portanto", "entretanto", "todavia", "contudo", "ademais",
    "outrossim", "porquanto", "conquanto", "não obstante",
    "por conseguinte", "haja vista", "destarte", "em suma",
    "conforme", "mediante", "acerca de", "no que tange",
    "no que diz respeito", "por meio de", "a fim de",
    # Verbos e substantivos de registro culto
    "constatar", "constata", "ressaltar", "ressalta", "salientar",
    "evidencia", "proporcionar", "estabelecer", "imprescindível",
    "pertinente", "âmbito", "premissa", "hipótese", "outrem",
    # Tratamento formal
    "senhor", "senhora", "senhores", "prezado", "prezada",
    "vossa", "excelência", "cordialmente",
}

# Ênclise/mesóclise ("torna-se", "trata-se") — traço formal na fala PT-BR.
_ENCLISIS_RE = re.compile(r"\b\w{3,}-(se|lo|la|los|las|lhe|lhes)\b")


class BERTAnalyzer:
    """
    Wrapper do classificador de sentimento, com lazy loading e gestão de VRAM.

    Uso:
        analyzer = BERTAnalyzer()
        analyzer.load()   # carrega modelo na GPU
        results = analyzer.analyze_segments(segments)
        analyzer.unload()  # libera VRAM
    """

    MODEL_NAME = "neuralmind/bert-base-portuguese-cased"

    def __init__(self):
        self._model = None
        self._tokenizer = None
        self._sentiment_pipeline = None
        self._device = None

    def load(self) -> bool:
        """Carrega o tokenizador e o classificador de sentimento.

        O tokenizador do BERTimbau traz apenas arquivos de vocabulário — não
        há pesos e nada é alocado na placa por ele. O que ocupa a GPU nesta
        etapa é o classificador DistilBERT.
        """
        _add_cuda_dll_paths()

        try:
            import torch
            from transformers import AutoTokenizer, pipeline
        except ImportError:
            print("   ⚠️  transformers/torch não instalado — etapa pulada.")
            print("        Para instalar: pip install transformers torch")
            return False

        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"   Tokenizador: {self.MODEL_NAME} (vocabulário, sem pesos)")
        print(f"   Classificador: {SENTIMENT_MODEL} no {self._device}...")

        try:
            self._tokenizer = AutoTokenizer.from_pretrained(self.MODEL_NAME)

            self._sentiment_pipeline = pipeline(
                "sentiment-analysis",
                model=SENTIMENT_MODEL,
                device=0 if self._device == "cuda" else -1,
                truncation=True,
                max_length=128,
                top_k=None,
            )

            print(f"   Classificador de sentimento carregado no {self._device}.")
            return True

        except Exception as e:
            print(f"   ⚠️  Erro ao carregar o classificador de sentimento: {e}")
            return False

    def unload(self) -> None:
        """Libera VRAM/memória."""
        del self._sentiment_pipeline
        del self._tokenizer
        del self._model
        self._sentiment_pipeline = None
        self._tokenizer = None
        self._model = None

        try:
            import torch
            torch.cuda.empty_cache()
            print("   Classificador de sentimento: VRAM liberada.")
        except Exception:
            pass

    def analyze_segments(self, segments: list[dict]) -> list[dict]:
        """
        Enriquece segmentos com sentimento contextual e registro linguístico.

        Recebe: [{t_start, t_end, text, utterance_type, classification_evidence, ...}]
        Retorna a mesma lista com sentiment e language_register atualizados pelo BERT.

        Se o modelo não estiver carregado, faz fallback para rule-based.
        """
        if not segments:
            return segments

        if self._sentiment_pipeline is None:
            print("   ⚠️  Classificador não carregado — usando fallback rule-based.")
            return segments

        enriched = []
        for seg in segments:
            text = seg.get("text", "").strip()
            if not text:
                enriched.append(seg)
                continue

            new_seg = dict(seg)

            # ── Sentimento via BERT ──────────────────────────────────────
            try:
                probas = self._probas(text)
                sentiment = max(probas, key=probas.get)
                score = probas[sentiment]

                # Abaixo do limiar de confiança, preserva-se a classificação
                # rule-based do speech_analysis em vez da do modelo.
                if score < 0.5:
                    sentiment = seg.get("sentiment", "neutro")
                    sentiment_score = seg.get("sentiment_score", 0.0)
                elif sentiment == "positivo":
                    sentiment_score = round(score, 4)
                elif sentiment == "negativo":
                    sentiment_score = round(-score, 4)
                else:
                    sentiment_score = 0.0

                new_seg["sentiment"] = sentiment
                new_seg["sentiment_score"] = sentiment_score
                new_seg["sentiment_conf"] = round(score, 4)
                sent_evidence = f"bert:{sentiment}({score:.2f})"

            except Exception:
                new_seg["sentiment_score"] = 0.0
                new_seg["sentiment_conf"] = None
                sent_evidence = "bert:error→fallback"

            # ── Registro linguístico (BERT embeddings + marcadores) ──────
            register, reg_evidence = self._classify_register(text)
            new_seg["language_register"] = register

            # Atualiza evidence mantendo utterance original
            original_utt_ev = ""
            old_evidence = seg.get("classification_evidence", "")
            if "utterance:" in old_evidence:
                parts = old_evidence.split(" | ")
                for p in parts:
                    if p.startswith("utterance:"):
                        original_utt_ev = p
                        break

            new_seg["classification_evidence"] = (
                f"{original_utt_ev} | "
                f"register:{reg_evidence} | "
                f"sentiment:{sent_evidence}"
            )
            new_seg["classification_method"] = "bert_v1"

            enriched.append(new_seg)

        return enriched

    #: Rótulos do modelo PT nativo → vocabulário do resto do sistema.
    #: O multilingual antigo dizia positive/neutral/negative; este diz
    #: POS/NEU/NEG. Aceita os dois para não quebrar se o modelo for trocado.
    _MAPA_ROTULO = {
        "pos": "positivo", "positive": "positivo", "label_2": "positivo",
        "neu": "neutro",   "neutral":  "neutro",   "label_1": "neutro",
        "neg": "negativo", "negative": "negativo", "label_0": "negativo",
    }

    def _probas(self, text: str) -> dict:
        """Devolve {negativo, neutro, positivo} somando ~1.

        Com `top_k=None` o pipeline devolve todas as classes; a forma exata
        do retorno varia entre versões do transformers (lista de dicts ou
        lista de listas), então normaliza aqui em vez de espalhar `if` pelo
        código que chama.
        """
        bruto = self._sentiment_pipeline(text[:400])
        if bruto and isinstance(bruto[0], list):
            bruto = bruto[0]
        probas = {"negativo": 0.0, "neutro": 0.0, "positivo": 0.0}
        for d in bruto:
            nome = self._MAPA_ROTULO.get(str(d["label"]).lower())
            if nome:
                probas[nome] = float(d["score"])
        total = sum(probas.values())
        if total > 0:
            probas = {k: v / total for k, v in probas.items()}
        return probas

    def analyze_comments(self, comments: list[dict]) -> list[dict]:
        """
        Analisa sentimento de comentários do YouTube.

        Recebe: [{comment_id, text, likes, ...}, ...]
        Retorna: [{comment_id, text, likes, sentiment, sentiment_score, classification_evidence}, ...]

        Score numérico: positivo → +score, negativo → -score, neutro → 0
        Escala resultante: -1.0 a +1.0
        """
        if not comments:
            return []

        if self._sentiment_pipeline is None:
            print("   ⚠️  Classificador não carregado — analyze_comments pulado.")
            return []

        results = []
        for c in comments:
            text = (c.get("text") or "").strip()
            if not text:
                continue

            new = {
                "comment_id": c.get("comment_id"),
                "text": text,
                "likes": c.get("likes", 0),
            }

            try:
                probas = self._probas(text)
                sentiment = max(probas, key=probas.get)
                score = probas[sentiment]

                # Abaixo do limiar de confiança o comentário é rebaixado a
                # neutro, por conservadorismo.
                if score < 0.5:
                    sentiment = "neutro"
                    sentiment_score = 0.0
                elif sentiment == "positivo":
                    sentiment_score = round(score, 4)
                elif sentiment == "negativo":
                    sentiment_score = round(-score, 4)
                else:
                    sentiment_score = 0.0

                new["sentiment"] = sentiment
                new["sentiment_score"] = sentiment_score
                new["classification_evidence"] = f"bert:{sentiment}({score:.2f})"

            except Exception as e:
                new["sentiment"] = "neutro"
                new["sentiment_score"] = 0.0
                new["classification_evidence"] = f"bert:error({e})"

            results.append(new)

        return results

    def _classify_register(self, text: str) -> tuple[str, str]:
        """
        Classifica registro linguístico com vocabulário expandido.
        Mais preciso que o rule-based original — inclui gírias hip-hop,
        periféricas, nordestinas e de internet.
        """
        t = text.lower()

        # Busca gírias (prioridade)
        found_giria = []
        for g in sorted(_GIRIA_MARKERS):
            if g in t:
                found_giria.append(g)
                if len(found_giria) >= 3:
                    break

        if found_giria:
            return "gíria", f"bert_vocab:{','.join(found_giria)}"

        # Busca marcadores informais
        found_informal = []
        for m in sorted(_INFORMAL_MARKERS):
            if m in t:
                found_informal.append(m)
                if len(found_informal) >= 2:
                    break

        if found_informal:
            return "informal", f"bert_vocab:{','.join(found_informal)}"

        # Heurística contextual: frases curtas com exclamações tendem a ser informais
        if len(text.split()) < 6 and ("!" in text or "..." in text):
            return "informal", "bert_heuristic:short_exclamatory"

        # Formal só com evidência positiva (marcador culto ou ênclise)
        found_formal = []
        for m in sorted(_FORMAL_MARKERS):
            if m in t:
                found_formal.append(m)
                if len(found_formal) >= 3:
                    break
        if found_formal:
            return "formal", f"bert_vocab_formal:{','.join(found_formal)}"

        m = _ENCLISIS_RE.search(t)
        if m:
            return "formal", f"bert_heuristic:enclise({m.group(0)})"

        # Nenhum marcador de gíria nem de informalidade: o registro é formal.
        return "formal", "bert_default:sem_marcador_informal"
