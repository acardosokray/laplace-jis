"""
config.py — Configurações centrais do sistema.
A API key pode ser definida aqui ou via variável de ambiente YOUTUBE_API_KEY.
"""

import os
from dotenv import load_dotenv

load_dotenv()  # carrega .env se existir

DB_PATH: str = os.getenv("DB_PATH", "data/videos.db")

# Prioridade: variável de ambiente > valor direto aqui
YOUTUBE_API_KEY: str = os.getenv("YOUTUBE_API_KEY", "COLOQUE_SUA_API_KEY_AQUI")

YOUTUBE_API_BASE_URL: str = "https://www.googleapis.com/youtube/v3/videos"
YOUTUBE_API_PARTS: str = "snippet,statistics,contentDetails"

# Comentários
COMMENTS_MAX_RESULTS: int = int(os.getenv("COMMENTS_MAX_RESULTS", "100"))

# Mídia gerada pelo pipeline (media/)
AUDIO_PATH: str = os.getenv("AUDIO_PATH", "media/audio")
VIZ_PATH: str = os.getenv("VIZ_PATH", "media/visualizations")

# Whisper
WHISPER_MODEL: str = os.getenv("WHISPER_MODEL", "large-v3")

# Excel
EXCEL_PATH: str = os.getenv("EXCEL_PATH", "media/exports/laplace.xlsx")
