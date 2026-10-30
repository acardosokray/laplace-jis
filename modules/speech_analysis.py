"""
modules/speech_analysis.py — Análise linguística dos segmentos de fala.

Classifica cada segmento em:
  - utterance_type:     pergunta | afirmação | exclamação | chamada_ação | chamada_atenção
  - language_register:  formal | informal | gíria
  - sentiment:          positivo | neutro | negativo | misto

Processo 100% auditável:
  - classification_evidence registra QUAL padrão ativou a classificação
  - classification_method   registra a versão do classificador ('rule_based_v1')
  - Sem caixas-pretas: cada decisão é rastreável

Ordem de prioridade para utterance_type:
  chamada_atenção > chamada_ação > pergunta > exclamação > afirmação
"""

import re
from collections import Counter


# ── Palavras interrogativas ────────────────────────────────────────────────────

_INTERROGATIVE_STARTS = [
    "como ", "quando ", "por que ", "porque ", "quem ", "onde ",
    "qual ", "quais ", "o que ", "quanto ", "quantos ", "quantas ",
    "de onde ", "pra onde ", "pra que ", "pra quê ", "pra quem ",
]

# ── Padrões de chamada_ação (regex, label) ─────────────────────────────────────
# label é o que vai em classification_evidence — explica o que foi encontrado

_CTA_PATTERNS = [
    (r"\b(me\s+)?segue\b",                      "segue"),
    (r"\b(deixa|dá|bota|da)\s+o\s+like\b",      "like"),
    (r"\bcurte\b",                               "curte"),
    (r"\bcomenta\b",                             "comenta"),
    (r"\bcompartilha\b",                         "compartilha"),
    (r"\bsalva\s+(esse|o)?\s*v[íi]deo\b",       "salva_video"),
    (r"\bsalva\b",                               "salva"),
    (r"\b(ativa|bate)\s+(o\s+sino|as\s+notifica[cç][oõ]es|o\s+sininho)\b", "sino"),
    (r"\b(se\s+)?inscreve\b",                    "inscreve"),
    (r"\binscreva[-\s]se\b",                     "inscreva_se"),
    (r"\bclica\b",                               "clica"),
    (r"\bacessa\b",                              "acessa"),
    (r"\bentra\s+(no|na|aqui)\b",                "entra_no"),
    (r"\bcompra\b",                              "compra"),
    (r"\badquire\b",                             "adquire"),
    (r"\baproveita\b",                           "aproveita"),
    (r"\bassiste\s+at[eé]\s+o\s+final\b",        "assiste_final"),
    (r"\bassiste\b",                             "assiste"),
    (r"\bn[aã]o\s+esquece\b",                    "nao_esquece"),
    (r"\bp[eê]ga\b",                             "pega"),
    (r"\blink\s+(na\s+bio|nos\s+coment[aá]rios|abaixo)\b", "link_bio"),
]

# ── Padrões de imperativo/convite (regex, label) ───────────────────────────────
# Distintos do CTA: aqui o falante conduz o interlocutor DENTRO da conversa
# ("vamos lá", "deixa eu te contar", "imagina"), não pede engajamento à
# audiência. Antes tudo isso caía em "afirmação", que absorvia 82% do corpus.
_IMPERATIVO_PATTERNS = [
    (r"vamos?\s+(l[áa]|nessa|embora)",              "vamos_la"),
    (r"vam(o|os)\s+\w+ar",                            "vamos_verbo"),
    (r"deixa\s+(eu|a\s+gente|te)",                     "deixa_eu"),
    (r"(vem|venha)\s+(c[áa]|comigo|aqui)",         "vem_ca"),
    (r"imagina",                                       "imagina"),
    (r"(pensa|repara|escuta|ouve)\s+(nisso|bem|aqui|s[óo])", "pensa_nisso"),
    (r"presta\s+aten[cç][ãa]o",              "presta_atencao"),
    (r"(bora|partiu)",                                  "bora"),
    (r"t[eê]m\s+que\s+\w+ar",                   "tem_que"),
    (r"precisa\s+\w+ar",                              "precisa"),
]

# ── Padrões de chamada_atenção (regex, label) ──────────────────────────────────

_ATTENTION_PATTERNS = [
    (r"\bvoc[eê]\s+sabia\b",                    "voce_sabia"),
    (r"\bolha\s+(isso|aqui|s[oó]|l[aá])?\b",    "olha"),
    (r"\b(espera|per[aá][\s-]?[aá]i|perai)\b",  "espera"),
    (r"\bpara\s+tudo\b",                         "para_tudo"),
    (r"\bimposs[íi]vel\b",                       "impossivel"),
    (r"\binacredit[aá]vel\b",                    "inacreditavel"),
    (r"\babsurdo\b",                             "absurdo"),
    (r"\bsegredo\b",                             "segredo"),
    (r"\bningu[eé]m\s+(sabe|faz|conta|te\s+conta)\b", "ninguem_sabe"),
    (r"\ba\s+verdade\s+(sobre|[eé])\b",          "a_verdade"),
    (r"\bnunca\s+(vi|fiz|aprendi|imaginei)\b",   "nunca_vi"),
    (r"\b(fiz|aprendi|ensinaram)\s+errado\b",    "ensinaram_errado"),
    (r"\bvai\s+mudar\b",                         "vai_mudar"),
    (r"\bcuidado\b",                             "cuidado"),
    (r"\berramos\s+todos\b",                     "erramos_todos"),
    (r"\bnote\s+isso\b",                         "note_isso"),
    (r"\bprestou?\s+aten[cç][aã]o\b",            "presta_atencao"),
    (r"\bsurpreendente\b",                       "surpreendente"),
]

# ── Vocabulário ────────────────────────────────────────────────────────────────

_SLANG_WORDS = {
    "mano", "cara", "véi", "véio", "bro", "trampo", "rolou", "rola",
    "tá ligado", "firmeza", "daora", "zoeira", "mitar", "mitou",
    "cringe", "foda", "puta", "caralho", "porra", "oxe", "eita",
    "tô", "cê", "hj", "tb", "tmb", "vlw", "valeu", "beleza", "suave",
    "tipo assim", "aqui ó", "né não", "top", "show", "massa",
    "sei lá", "literalmente", "basicamente",
}

_POSITIVE_WORDS = {
    "ótimo", "incrível", "maravilhoso", "perfeito", "excelente",
    "bom", "boa", "gostei", "amei", "adorei", "fantástico", "demais",
    "top", "show", "legal", "massa", "lindo", "linda", "bonito",
    "sucesso", "ganhou", "melhor", "sensacional", "bacana", "incrivel",
}

_NEGATIVE_WORDS = {
    "ruim", "péssimo", "horrível", "terrível", "odeio", "detestei",
    "problema", "falha", "erro", "errado", "decepcionante", "chato",
    "pior", "perdeu", "lixo", "ridículo", "vergonha", "horivel",
}

_STOPWORDS_PT = {
    "a", "o", "e", "de", "do", "da", "em", "um", "uma", "que", "se",
    "na", "no", "por", "com", "para", "ao", "os", "as", "dos",
    "das", "nos", "nas", "ou", "mais", "mas", "eu", "não", "me", "meu",
    "minha", "você", "ele", "ela", "isso", "esse", "essa", "aqui", "já",
    "também", "muito", "bem", "vai", "ter", "ser", "tem", "foi", "são",
    "este", "esta", "quando", "como", "porque", "então", "aí", "né",
    "pra", "pro", "num", "numa", "tá", "tô", "lá", "só", "até",
    "esses", "essas", "vou", "vamos", "agora", "sempre", "ainda",
    "depois", "antes", "assim", "ali", "aquele", "aquela", "aquilo",
    "nesse", "nessa", "naquele", "naquela", "foi", "tinha", "estava",
    "ele", "ela", "eles", "elas", "meu", "minha", "seu", "sua",
    "um", "uns", "umas", "isso", "esse", "aqui", "lá", "aquilo",
}

_CTA_WORDS_LEXICAL = {
    "segue", "curte", "comenta", "compartilha", "salva", "inscreve",
    "clica", "acessa", "compra", "adquire", "aproveita", "assiste",
    "ativa", "bate", "inscreva",
}


# ── Classificadores ────────────────────────────────────────────────────────────

def _classify_utterance(text: str) -> tuple[str, str]:
    """
    Retorna (utterance_type, evidence).
    Prioridade: chamada_atenção > chamada_ação > pergunta > imperativo >
                exclamação > afirmação

    `imperativo` fica depois de pergunta porque "vamos ver o que aconteceu?"
    é antes de tudo uma pergunta; e antes de exclamação/afirmação porque
    conduzir o interlocutor é mais informativo do que registrar que houve
    uma frase.
    """
    t = text.lower().strip()

    for pattern, label in _ATTENTION_PATTERNS:
        if re.search(pattern, t, re.IGNORECASE):
            return "chamada_atenção", f"attention_pattern:{label}"

    for pattern, label in _CTA_PATTERNS:
        if re.search(pattern, t, re.IGNORECASE):
            return "chamada_ação", f"cta_pattern:{label}"

    if text.strip().endswith("?"):
        return "pergunta", "ends_with_?"
    t_stripped = t.lstrip()
    for iw in _INTERROGATIVE_STARTS:
        if t_stripped.startswith(iw):
            return "pergunta", f"starts_with:{iw.strip()}"

    for pattern, label in _IMPERATIVO_PATTERNS:
        if re.search(pattern, t, re.IGNORECASE):
            return "imperativo", f"imperativo_pattern:{label}"

    if text.strip().endswith("!"):
        return "exclamação", "ends_with_!"

    return "afirmação", "default"


def _classify_register(text: str) -> tuple[str, str]:
    """Retorna (language_register, evidence)."""
    t = text.lower()
    found_slang = [s for s in sorted(_SLANG_WORDS) if s in t]
    if found_slang:
        return "gíria", f"slang:{','.join(found_slang[:3])}"
    informal_markers = ["tô", "tá", "cê", "né", "tava", "tamo", "a gente", "num ", "numa "]
    found = [m for m in informal_markers if m in t]
    if found:
        return "informal", f"marker:{','.join(found[:2])}"
    return "formal", "default"


def _classify_sentiment(text: str) -> tuple[str, str]:
    """Retorna (sentiment, evidence)."""
    t = text.lower()
    pos = [w for w in sorted(_POSITIVE_WORDS) if w in t]
    neg = [w for w in sorted(_NEGATIVE_WORDS) if w in t]
    if pos and not neg:
        return "positivo", f"pos:{','.join(pos[:3])}"
    if neg and not pos:
        return "negativo", f"neg:{','.join(neg[:3])}"
    if pos and neg:
        return "misto", f"pos:{','.join(pos[:2])};neg:{','.join(neg[:2])}"
    return "neutro", "default"


# ── API pública ────────────────────────────────────────────────────────────────

def analyze_segments(segments: list[dict]) -> list[dict]:
    """
    Recebe segmentos do Whisper [{start, end, text}, ...]
    Retorna lista enriquecida com classificações auditáveis.
    """
    results = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue

        u_type,   u_evidence = _classify_utterance(text)
        register, r_evidence = _classify_register(text)
        sentiment, s_evidence = _classify_sentiment(text)

        results.append({
            "t_start":                seg["start"],
            "t_end":                  seg["end"],
            "text":                   text,
            "utterance_type":         u_type,
            "classification_evidence": (
                f"utterance:{u_evidence} | "
                f"register:{r_evidence} | "
                f"sentiment:{s_evidence}"
            ),
            "classification_method":  "rule_based_v1",
            "language_register":      register,
            "sentiment":              sentiment,
        })
    return results


def build_lexical_profile(full_text: str, segments: list[dict]) -> list[dict]:
    """
    Constrói perfil lexical do vídeo a partir do texto completo + timestamps.
    Retorna lista de {word, frequency, category, first_occurrence_t}.

    Categorias:
      stopword  — palavra funcional (a, o, de, que...)
      cta       — verbo de chamada para ação (segue, curte, comenta...)
      gíria     — gíria ou termo informal
      common    — palavra de conteúdo sem categoria especial
    """
    if not full_text:
        return []

    # Mapa word → primeiro timestamp de ocorrência
    first_occurrence: dict[str, float] = {}
    for seg in segments:
        seg_words = re.findall(
            r"\b[a-záàãâéêíóôõúüç]{3,}\b",
            (seg.get("text") or "").lower(),
        )
        for w in seg_words:
            if w not in first_occurrence:
                first_occurrence[w] = float(seg.get("start", 0.0))

    all_words = re.findall(r"\b[a-záàãâéêíóôõúüç]{3,}\b", full_text.lower())
    freq = Counter(all_words)

    results = []
    for word, count in freq.most_common(200):
        if word in _STOPWORDS_PT:
            category = "stopword"
        elif word in _CTA_WORDS_LEXICAL:
            category = "cta"
        elif word in _SLANG_WORDS:
            category = "gíria"
        else:
            category = "common"

        results.append({
            "word":               word,
            "frequency":          count,
            "category":           category,
            "first_occurrence_t": first_occurrence.get(word),
        })
    return results
