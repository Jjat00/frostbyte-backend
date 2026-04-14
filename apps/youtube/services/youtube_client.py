import hashlib
import html
import logging
from datetime import date
import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

# TTLs de cache para reducir consumo de cuota de la API de YouTube
# (10k unidades/dia free tier; search.list = 100 unidades; videos.list = 1)
SEARCH_CACHE_TTL = 60 * 60 * 24        # 24h
TRENDING_CACHE_TTL = 60 * 60 * 48      # 48h

# Costos de cada llamada (unidades de cuota)
COST_SEARCH_LIST = 100
COST_VIDEOS_LIST = 1

# Limite diario configurable (default 10k - free tier)
QUOTA_DAILY_LIMIT = int(getattr(settings, "YOUTUBE_QUOTA_LIMIT", 10000) or 10000)


def _quota_key():
    return f"youtube_quota:{date.today().isoformat()}"


def _track_quota(units):
    """Registrar uso de cuota del dia"""
    key = _quota_key()
    current = cache.get(key, 0)
    # TTL de 48h para que sobreviva cambios de dia
    cache.set(key, current + units, 60 * 60 * 48)
    return current + units


def get_quota_usage():
    """Uso estimado de cuota del dia"""
    used = cache.get(_quota_key(), 0)
    return {
        "used": used,
        "limit": QUOTA_DAILY_LIMIT,
        "remaining": max(0, QUOTA_DAILY_LIMIT - used),
        "percentage": round(min(100, (used / QUOTA_DAILY_LIMIT) * 100), 1),
    }


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

    # Contabilizar cuota incluso si falla (YouTube la consume en errores genericos)
    if search_resp.status_code == 200:
        _track_quota(COST_SEARCH_LIST)

    if search_resp.status_code != 200:
        logger.error(f"YouTube search error: {search_resp.status_code} - {search_resp.text}")
        if search_resp.status_code == 403 and _is_quota_error(search_resp):
            # Intentar servir cache expirado como fallback
            stale = cache.get(cache_key + ":stale")
            if stale is not None:
                logger.warning("Sirviendo cache expirado por cuota excedida")
                return stale
            # Marcar cuota al maximo localmente para reflejar el estado real
            cache.set(_quota_key(), QUOTA_DAILY_LIMIT, 60 * 60 * 48)
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
        _track_quota(COST_VIDEOS_LIST)
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

    if resp.status_code == 200:
        _track_quota(COST_VIDEOS_LIST)

    if resp.status_code != 200:
        logger.error(f"YouTube trending error: {resp.status_code} - {resp.text}")
        if resp.status_code == 403 and _is_quota_error(resp):
            stale = cache.get(cache_key + ":stale")
            if stale is not None:
                return stale
            cache.set(_quota_key(), QUOTA_DAILY_LIMIT, 60 * 60 * 48)
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
