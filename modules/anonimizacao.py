"""
modules/anonimizacao.py — Pseudonimização aplicada na coleta.

Nomes de autores de comentários nunca são gravados. Na etapa 1, cada autor
recebe um identificador sequencial estável (`U1`, `U2`, …) e cada canal um
`C1`, `C2`, … O identificador é o mesmo em todas as execuções: a correlação
entre vídeos continua possível, a identidade não é armazenada.

A correspondência entre valor real e pseudônimo fica em
`data/pseudonimos.json`, **fora do banco e fora do controle de versão**. É a
chave que o pesquisador guarda; separá-la dos dados é o que torna a
pseudonimização útil — quem obtiver o banco não obtém as identidades.

O texto dos comentários passa por `limpar_texto()`, que remove menções a
perfis, endereços de e-mail e URLs. O conteúdo analisável permanece: a
polaridade de "@fulano isso aqui é ótimo" não muda quando vira
"@[perfil] isso aqui é ótimo".
"""

import json
import os
import re
import threading
from typing import Optional

from config import DB_PATH

_CAMINHO = os.path.join(os.path.dirname(DB_PATH) or ".", "pseudonimos.json")
_PREFIXOS = {"autor": "U", "canal": "C"}

_lock = threading.Lock()
_mapa: Optional[dict] = None

_RE_MENCAO = re.compile(r"@[\w.\-]{2,}")
_RE_EMAIL = re.compile(r"[\w.\-+]+@[\w\-]+\.[\w.\-]+")
_RE_URL = re.compile(r"https?://\S+|www\.\S+")


def _carregar() -> dict:
    global _mapa
    if _mapa is not None:
        return _mapa
    try:
        with open(_CAMINHO, encoding="utf-8") as f:
            _mapa = json.load(f)
    except (FileNotFoundError, ValueError):
        _mapa = {tipo: {} for tipo in _PREFIXOS}
    for tipo in _PREFIXOS:
        _mapa.setdefault(tipo, {})
    return _mapa


def _gravar() -> None:
    os.makedirs(os.path.dirname(_CAMINHO) or ".", exist_ok=True)
    with open(_CAMINHO, "w", encoding="utf-8") as f:
        json.dump(_mapa, f, ensure_ascii=False, indent=1, sort_keys=True)


def pseudonimo(tipo: str, valor: str) -> str:
    """Identificador estável para `valor`. Atribui um novo na primeira vez.

    `tipo` é "autor" ou "canal". Valores vazios recebem o pseudônimo nulo do
    tipo (`U0`, `C0`), para não consumir um identificador real.
    """
    prefixo = _PREFIXOS.get(tipo)
    if prefixo is None:
        raise ValueError(f"tipo desconhecido: {tipo!r}")

    chave = (valor or "").strip()
    if not chave:
        return f"{prefixo}0"

    with _lock:
        mapa = _carregar()
        atribuidos = mapa[tipo]
        if chave not in atribuidos:
            atribuidos[chave] = f"{prefixo}{len(atribuidos) + 1}"
            _gravar()
        return atribuidos[chave]


def limpar_texto(texto: str) -> str:
    """Remove menções a perfis, e-mails e URLs, preservando o restante."""
    if not texto:
        return texto
    t = _RE_URL.sub("[link]", texto)
    t = _RE_EMAIL.sub("[email]", t)
    t = _RE_MENCAO.sub("@[perfil]", t)
    return t


def total_atribuidos(tipo: str) -> int:
    """Quantos identificadores já foram emitidos para este tipo."""
    return len(_carregar().get(tipo, {}))
