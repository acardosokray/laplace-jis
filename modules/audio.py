"""
modules/audio.py — Download de áudio via yt-dlp.

O YouTube passou a proteger parte dos formatos com desafios em JavaScript. O
yt-dlp precisa de um runtime JS para resolvê-los; sem um, alguns formatos
ficam indisponíveis e o download falha com HTTP 403 de forma intermitente —
depende de qual formato a plataforma oferece naquela requisição.

Ter o runtime instalado não basta: o yt-dlp só o usa se ele for habilitado
explicitamente. Com o Node no PATH e sem a flag, `yt-dlp -v` relata
`JS runtimes: none` e marca `node (unavailable)`. `_runtime_js()` procura os
quatro runtimes que o yt-dlp aceita e passa o primeiro encontrado adiante.
Não achando nenhum, o download é tentado mesmo assim: funciona para os
formatos que não exigem o desafio.
"""

import os
import shutil
import subprocess
import sys
from config import AUDIO_PATH


def _ytdlp() -> str:
    """Caminho do yt-dlp deste ambiente Python, não o que o PATH resolver."""
    binario = "yt-dlp.exe" if os.name == "nt" else "yt-dlp"
    local = os.path.join(os.path.dirname(sys.executable), binario)
    return local if os.path.exists(local) else "yt-dlp"


RUNTIMES_JS = ("deno", "node", "bun", "quickjs")


def _runtime_js() -> list[str]:
    """Flag `--js-runtimes` para o primeiro runtime encontrado, ou vazio."""
    for nome in RUNTIMES_JS:
        caminho = shutil.which(nome)
        if caminho:
            return ["--js-runtimes", f"{nome}:{caminho}"]
    return []


def download_audio(video_id: str) -> dict:
    """
    Baixa o áudio de um vídeo do YouTube em formato MP3.

    Returns:
        {
          path:         str   — caminho do arquivo salvo,
          success:      bool,
          cached:       bool  — True se o arquivo já existia,
          file_size_mb: float — tamanho em MB,
          error:        str | None,
        }
    """
    os.makedirs(AUDIO_PATH, exist_ok=True)
    output_path = os.path.join(AUDIO_PATH, f"{video_id}.mp3")

    if os.path.exists(output_path):
        size_mb = round(os.path.getsize(output_path) / (1024 * 1024), 1)
        return {
            "path": output_path, "success": True,
            "cached": True, "file_size_mb": size_mb, "error": None,
        }

    url = f"https://www.youtube.com/watch?v={video_id}"
    output_template = os.path.join(AUDIO_PATH, f"{video_id}.%(ext)s")

    cmd = [
        _ytdlp(), *_runtime_js(),
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "--output", output_template,
        "--no-playlist",
        "--quiet",
        url,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode == 0 and os.path.exists(output_path):
            size_mb = round(os.path.getsize(output_path) / (1024 * 1024), 1)
            return {
                "path": output_path, "success": True,
                "cached": False, "file_size_mb": size_mb, "error": None,
            }
        error_msg = result.stderr.strip() or result.stdout.strip() or "Falha ao baixar áudio"

        # Um 403 sem runtime JS é a causa mais provável e a mais fácil de
        # confundir com bloqueio da plataforma. Diz o que fazer, em vez de
        # repassar o código HTTP cru.
        if not _runtime_js() and "403" in error_msg:
            error_msg = (
                "HTTP 403 e nenhum runtime JavaScript instalado.\n"
                "        O YouTube protege parte dos formatos com desafios em "
                "JavaScript, que o\n"
                "        yt-dlp só resolve com Deno ou Node disponível. "
                "Instale um deles:\n"
                "            winget install DenoLand.Deno        (Windows)\n"
                "            curl -fsSL https://deno.land/install.sh | sh  "
                "(Linux/macOS)\n"
                "        Depois abra um terminal novo e repita o comando."
            )

        return {
            "path": None, "success": False,
            "cached": False, "file_size_mb": None, "error": error_msg,
        }
    except subprocess.TimeoutExpired:
        return {
            "path": None, "success": False,
            "cached": False, "file_size_mb": None,
            "error": "Timeout ao baixar áudio (>3 minutos)",
        }
    except FileNotFoundError:
        return {
            "path": None, "success": False,
            "cached": False, "file_size_mb": None,
            "error": "yt-dlp não encontrado. Instale com: pip install yt-dlp",
        }
