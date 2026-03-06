import spotipy

from .spotify_auth import get_valid_access_token


def _get_spotify_client():
    """Crea un cliente de Spotify autenticado"""
    access_token = get_valid_access_token()
    if not access_token:
        raise SpotifyNotConnectedError("Spotify no está conectado")
    return spotipy.Spotify(auth=access_token)


class SpotifyNotConnectedError(Exception):
    pass


def search_tracks(query, limit=10):
    """Busca tracks en Spotify"""
    sp = _get_spotify_client()
    results = sp.search(q=query, type="track", limit=limit)
    tracks = []
    for item in results["tracks"]["items"]:
        tracks.append({
            "uri": item["uri"],
            "name": item["name"],
            "artists": ", ".join(a["name"] for a in item["artists"]),
            "album": item["album"]["name"],
            "image": item["album"]["images"][0]["url"] if item["album"]["images"] else "",
            "duration_ms": item["duration_ms"],
            "preview_url": item.get("preview_url"),
        })
    return tracks


def add_to_queue(track_uri):
    """Agrega un track a la cola de reproducción"""
    sp = _get_spotify_client()
    sp.add_to_queue(uri=track_uri)


def get_currently_playing():
    """Obtiene la canción que está sonando"""
    sp = _get_spotify_client()
    current = sp.current_playback()
    if not current or not current.get("item"):
        return None
    item = current["item"]
    return {
        "uri": item["uri"],
        "name": item["name"],
        "artists": ", ".join(a["name"] for a in item["artists"]),
        "album": item["album"]["name"],
        "image": item["album"]["images"][0]["url"] if item["album"]["images"] else "",
        "duration_ms": item["duration_ms"],
        "progress_ms": current.get("progress_ms", 0),
        "is_playing": current.get("is_playing", False),
    }


def get_queue():
    """Obtiene la cola de reproducción actual"""
    sp = _get_spotify_client()
    queue_data = sp.queue()
    tracks = []
    for item in queue_data.get("queue", []):
        tracks.append({
            "uri": item["uri"],
            "name": item["name"],
            "artists": ", ".join(a["name"] for a in item["artists"]),
            "album": item["album"]["name"],
            "image": item["album"]["images"][0]["url"] if item["album"]["images"] else "",
            "duration_ms": item["duration_ms"],
        })
    return tracks


def is_connected():
    """Verifica si Spotify está conectado y activo"""
    try:
        sp = _get_spotify_client()
        sp.current_user()
        return True
    except Exception:
        return False
