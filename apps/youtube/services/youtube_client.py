import hashlib
import html
import logging
import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

# TTLs de cache para reducir consumo de cuota de la API de YouTube
# (10k unidades/dia free tier; search.list = 100 unidades; videos.list = 1)
SEARCH_CACHE_TTL = 60 * 60 * 6        # 6h - mismas busquedas devuelven mismos resultados
TRENDING_CACHE_TTL = 60 * 60 * 12     # 12h - trending cambia lento


class YouTubeAPIError(Exception):
    """Error al comunicarse con la API de YouTube"""
    pass


class YouTubeQuotaExceededError(YouTubeAPIError):
    """Cuota de YouTube Data API excedida"""
    pass


def _get_api_key():
    api_key = settings.YOUTUBE_API_KEY
    if not api_key:
        raise YouTubeAPIError("YOUTUBE_API_KEY no esta configurada")
    return api_key


def _cache_key(prefix, *args):
    key = ":".join(str(a).lower().strip() for a in args)
    digest = hashlib.md5(key.encode()).hexdigest()
    return f"youtube:{prefix}:{digest}"


def _is_quota_error(response):
    try:
        body = response.json()
        reasons = [e.get("reason") for e in body.get("error", {}).get("errors", [])]
        return "quotaExceeded" in reasons
    except Exception:
        return False


def search_videos(query, limit=10):
    """Buscar videos en YouTube Data API v3 (con cache)"""
    # Intentar cache primero
    cache_key = _cache_key("search", query, limit)
    cached = cache.get(cache_key)
    if cached is not None:
        logger.debug(f"YouTube search cache hit: {query}")
        return cached

    api_key = _get_api_key()

    # Paso 1: Buscar videos
    search_resp = requests.get(
        "https://www.googleapis.com/youtube/v3/search",
        params={
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": limit,
            "key": api_key,
        },
        timeout=10,
    )

    if search_resp.status_code != 200:
        logger.error(f"YouTube search error: {search_resp.status_code} - {search_resp.text}")
        if search_resp.status_code == 403 and _is_quota_error(search_resp):
            # Intentar servir cache expirado como fallback
            stale = cache.get(cache_key + ":stale")
            if stale is not None:
                logger.warning("Sirviendo cache expirado por cuota excedida")
                return stale
            raise YouTubeQuotaExceededError("Cuota de YouTube API excedida")
        raise YouTubeAPIError("Error al buscar en YouTube")

    search_data = search_resp.json()
    items = search_data.get("items", [])

    # Filtrar solo videos validos (algunos items pueden no tener videoId)
    items = [item for item in items if item.get("id", {}).get("videoId")]

    if not items:
        return []

    # Paso 2: Obtener duraciones con videos.list
    video_ids = [item["id"]["videoId"] for item in items]
    details_resp = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={
            "part": "contentDetails",
            "id": ",".join(video_ids),
            "key": api_key,
        },
        timeout=10,
    )

    durations = {}
    if details_resp.status_code == 200:
        for detail in details_resp.json().get("items", []):
            durations[detail["id"]] = detail["contentDetails"]["duration"]

    results = []
    for item in items:
        video_id = item["id"]["videoId"]
        snippet = item["snippet"]
        thumbnails = snippet.get("thumbnails", {})
        thumbnail = (
            thumbnails.get("high", {}).get("url")
            or thumbnails.get("medium", {}).get("url")
            or thumbnails.get("default", {}).get("url", "")
        )

        results.append({
            "video_id": video_id,
            "title": html.unescape(snippet.get("title", "")),
            "channel_name": html.unescape(snippet.get("channelTitle", "")),
            "thumbnail": thumbnail,
            "duration": durations.get(video_id, ""),
        })

    # Guardar en cache (fresco + fallback expirado por 7 dias)
    cache.set(cache_key, results, SEARCH_CACHE_TTL)
    cache.set(cache_key + ":stale", results, 60 * 60 * 24 * 7)
    return results


def get_trending_music(limit=15, region="CO"):
    """Obtener videos musicales populares (trending) para iniciar la playlist (con cache)"""
    cache_key = _cache_key("trending", region, limit)
    cached = cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Trending music cache hit: {region}")
        return cached

    api_key = _get_api_key()

    resp = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={
            "part": "snippet,contentDetails",
            "chart": "mostPopular",
            "videoCategoryId": "10",  # Music
            "regionCode": region,
            "maxResults": limit,
            "key": api_key,
        },
        timeout=10,
    )

    if resp.status_code != 200:
        logger.error(f"YouTube trending error: {resp.status_code} - {resp.text}")
        if resp.status_code == 403 and _is_quota_error(resp):
            stale = cache.get(cache_key + ":stale")
            if stale is not None:
                return stale
            raise YouTubeQuotaExceededError("Cuota de YouTube API excedida")
        raise YouTubeAPIError("Error al obtener videos populares")

    items = resp.json().get("items", [])
    results = []
    for item in items:
        snippet = item.get("snippet", {})
        thumbnails = snippet.get("thumbnails", {})
        thumbnail = (
            thumbnails.get("high", {}).get("url")
            or thumbnails.get("medium", {}).get("url")
            or thumbnails.get("default", {}).get("url", "")
        )

        results.append({
            "video_id": item["id"],
            "title": html.unescape(snippet.get("title", "")),
            "channel_name": html.unescape(snippet.get("channelTitle", "")),
            "thumbnail": thumbnail,
            "duration": item.get("contentDetails", {}).get("duration", ""),
        })

    cache.set(cache_key, results, TRENDING_CACHE_TTL)
    cache.set(cache_key + ":stale", results, 60 * 60 * 24 * 7)
    return results
