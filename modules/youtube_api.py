"""
modules/youtube_api.py — Consulta a YouTube Data API v3.

Endpoint: GET https://www.googleapis.com/youtube/v3/videos
Partes: snippet, statistics, contentDetails

"""

import html
import re
import requests
from typing import Optional
from config import YOUTUBE_API_KEY, YOUTUBE_API_BASE_URL, COMMENTS_MAX_RESULTS
from modules.anonimizacao import pseudonimo, limpar_texto


def get_video_data(video_id: str) -> Optional[dict]:
    """
    Consulta a YouTube Data API e retorna os dados brutos do vídeo.

    Args:
        video_id: ID do vídeo do YouTube.

    Returns:
        Dicionário com os dados brutos do vídeo, ou None em caso de erro.
    """
    if not video_id:
        print("❌ video_id não fornecido.")
        return None

    if YOUTUBE_API_KEY == "COLOQUE_SUA_API_KEY_AQUI":
        print("❌ API key não configurada. Defina YOUTUBE_API_KEY no arquivo .env ou em config.py.")
        return None

    params = {
        "part": "snippet,statistics,contentDetails",
        "id": video_id,
        "key": YOUTUBE_API_KEY,
    }

    try:
        response = requests.get(YOUTUBE_API_BASE_URL, params=params, timeout=10)
        response.raise_for_status()
    except requests.exceptions.ConnectionError:
        print("❌ Sem conexão com a internet ou servidor inacessível.")
        return None
    except requests.exceptions.Timeout:
        print("❌ Timeout: a API do YouTube demorou demais para responder.")
        return None
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response else "?"
        if status == 400:
            print("❌ Requisição inválida (400). Verifique o video_id e os parâmetros.")
        elif status == 403:
            print("❌ Acesso negado (403). Verifique se a API key é válida e tem permissão.")
        elif status == 404:
            print("❌ Vídeo não encontrado (404).")
        else:
            print(f"❌ Erro HTTP {status}: {e}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"❌ Erro na requisição: {e}")
        return None

    data = response.json()
    items = data.get("items", [])

    if not items:
        print(f"❌ Nenhum vídeo encontrado com ID '{video_id}'. O vídeo pode ser privado ou inexistente.")
        return None

    item = items[0]
    snippet        = item.get("snippet", {})
    statistics     = item.get("statistics", {})
    content_details = item.get("contentDetails", {})

    return {
        "video_id":      item.get("id", video_id),
        "title":         snippet.get("title"),
        "description":   snippet.get("description"),
        "channel_id":    snippet.get("channelId"),
        "channel_name":  snippet.get("channelTitle"),
        "publish_date":  snippet.get("publishedAt"),
        "duration":      content_details.get("duration"),   # ISO 8601 (PT4M13S)
        "views":         statistics.get("viewCount"),
        "likes":         statistics.get("likeCount"),
        "comments":      statistics.get("commentCount"),
        "thumbnail_url": _best_thumbnail(snippet.get("thumbnails", {})),
        # ── novos campos ──────────────────────────────────────────────
        "tags":          snippet.get("tags", []),           # lista de tags
        "language":      snippet.get("defaultAudioLanguage") or snippet.get("defaultLanguage"),
        "country":       snippet.get("defaultLanguage"),    # melhor esforço
        "category_id":   snippet.get("categoryId"),
    }


def _texto_do_comentario(snippet: dict) -> str:
    """Texto do comentário em texto puro.

    `textDisplay` vem formatado em HTML: traz `<br>` no lugar das quebras de
    linha e entidades como `&quot;` no lugar das aspas. Tokenizado, o `&quot;`
    virava a palavra `quot` — e chegava a liderar o vocabulário agregado da
    audiência. A API oferece `textOriginal`, que é o texto como a pessoa
    escreveu; `textDisplay` fica como recuo, desescapado e sem as tags.
    """
    texto = snippet.get("textOriginal")
    if texto:
        return texto
    bruto = snippet.get("textDisplay", "")
    if not bruto:
        return bruto
    bruto = re.sub(r"<br\s*/?>", "\n", bruto, flags=re.I)
    bruto = re.sub(r"<[^>]+>", "", bruto)
    return html.unescape(bruto)


def get_comments(video_id: str, max_results: int = COMMENTS_MAX_RESULTS) -> list[dict]:
    """
    Coleta os top comentários de um vídeo via YouTube Data API.

    Args:
        video_id:    ID do vídeo.
        max_results: Número máximo de comentários a coletar (padrão: config).

    Returns:
        Lista de dicionários com dados de cada comentário.
    """
    if YOUTUBE_API_KEY == "COLOQUE_SUA_API_KEY_AQUI":
        print("⚠️  API key não configurada — comentários não coletados.")
        return []

    comments = []
    page_token = None
    collected = 0

    while collected < max_results:
        batch = min(100, max_results - collected)   # API aceita até 100 por página
        params = {
            "part": "snippet",
            "videoId": video_id,
            "maxResults": batch,
            "order": "relevance",
            "key": YOUTUBE_API_KEY,
        }
        if page_token:
            params["pageToken"] = page_token

        try:
            resp = requests.get(
                "https://www.googleapis.com/youtube/v3/commentThreads",
                params=params,
                timeout=10,
            )
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else "?"
            if status == 403:
                print("⚠️  Comentários desativados neste vídeo ou permissão negada.")
            else:
                print(f"⚠️  Erro ao coletar comentários (HTTP {status}).")
            break
        except requests.exceptions.RequestException as e:
            print(f"⚠️  Erro na requisição de comentários: {e}")
            break

        data = resp.json()
        for item in data.get("items", []):
            top = item["snippet"]["topLevelComment"]["snippet"]
            # O nome do autor é convertido em pseudônimo aqui, na fronteira
            # da rede: o valor real não chega a existir fora desta função.
            comments.append({
                "comment_id":   item["id"],
                "text":         limpar_texto(_texto_do_comentario(top)),
                "author_pseudo": pseudonimo("autor", top.get("authorDisplayName", "")),
                "likes":        int(top.get("likeCount", 0)),
                "published_at": top.get("publishedAt", ""),
                "reply_count":  item["snippet"].get("totalReplyCount", 0),
            })
            collected += 1
            if collected >= max_results:
                break

        page_token = data.get("nextPageToken")
        if not page_token:
            break

    return comments


def _iso8601_to_seconds(iso: str) -> int:
    """Converte duração ISO 8601 (PT1H2M3S) para segundos."""
    import re
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    if not match:
        return 0
    h = int(match.group(1) or 0)
    m = int(match.group(2) or 0)
    s = int(match.group(3) or 0)
    return h * 3600 + m * 60 + s


def _best_thumbnail(thumbnails: dict) -> Optional[str]:
    """Retorna a URL da melhor thumbnail disponível."""
    for quality in ("maxres", "standard", "high", "medium", "default"):
        thumb = thumbnails.get(quality)
        if thumb:
            return thumb.get("url")
    return None
