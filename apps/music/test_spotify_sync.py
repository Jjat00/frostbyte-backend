from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from spotipy.exceptions import SpotifyException

from apps.orders.models import StoreSettings

from .models import SongRequest
from .services import spotify_client, spotify_sync

FLOOR = 2
NO_DEVICE = SpotifyException(404, -1, "Player command failed: No active device found")
LOCAL = {"id": "pc-barra", "name": "Celular barra", "type": "Smartphone", "is_active": False}
TV = {"id": "tv-lg", "name": "[LG] webOS TV", "type": "TV", "is_active": False}


def request(uri, status=SongRequest.Status.PENDING, minutes_ago=1):
    row = SongRequest.objects.create(
        floor=FLOOR, song_name=uri, artist_name="x", spotify_track_uri=uri, status=status,
    )
    if minutes_ago:
        then = timezone.now() - timedelta(minutes=minutes_ago)
        SongRequest.objects.filter(pk=row.pk).update(created_at=then, updated_at=then)
    return row


def fake_spotify(devices, playing_uri=None):
    """Cliente spotipy falso: sin device_id falla con 404 si no hay equipo activo."""
    sp = mock.Mock()
    active = any(d["is_active"] for d in devices)

    def command(*args, device_id=None, **kwargs):
        if not active and device_id is None:
            raise NO_DEVICE

    sp.start_playback.side_effect = command
    sp.add_to_queue.side_effect = command
    sp.devices.return_value = {"devices": devices}
    sp.current_playback.return_value = (
        {"item": {"uri": playing_uri, "name": "n", "artists": [], "album": {"name": "a", "images": []},
                  "duration_ms": 1}, "is_playing": True, "device": LOCAL}
        if playing_uri else None
    )
    sp.queue.return_value = {"queue": []}
    return sp


class SpotifySyncTests(TestCase):
    def setUp(self):
        cache.clear()
        spotify_client._no_device_until.clear()
        spotify_client._rate_limited_until.clear()
        spotify_sync._last_queue_check.clear()
        spotify_sync._track_start.clear()

    def sync(self, sp):
        with mock.patch.object(spotify_client, "_get_spotify_client", return_value=sp), \
                mock.patch("apps.music.consumers.broadcast_music_update"):
            spotify_sync._sync_floor(FLOOR)

    def test_pending_is_queued_once_music_is_playing(self):
        req = request("spotify:track:a")
        sp = fake_spotify([{**LOCAL, "is_active": True}], playing_uri="spotify:track:other")
        self.sync(sp)
        req.refresh_from_db()
        self.assertEqual(req.status, SongRequest.Status.QUEUED)
        sp.add_to_queue.assert_called_once_with(uri="spotify:track:a")

    def test_idle_device_is_woken_to_play_the_next_request(self):
        req = request("spotify:track:a")
        sp = fake_spotify([LOCAL])
        self.sync(sp)
        req.refresh_from_db()
        self.assertEqual(req.status, SongRequest.Status.PLAYING)
        sp.start_playback.assert_called_with(device_id="pc-barra", uris=["spotify:track:a"])

    def test_without_spotify_open_request_stays_pending_and_sync_backs_off(self):
        req = request("spotify:track:a")
        sp = fake_spotify([])
        self.sync(sp)
        self.sync(sp)
        req.refresh_from_db()
        self.assertEqual(req.status, SongRequest.Status.PENDING)
        # El segundo ciclo no vuelve a golpear Spotify mientras dura la espera.
        self.assertEqual(sp.start_playback.call_count, 1)

    def test_queued_request_is_not_marked_played_while_nothing_plays(self):
        req = request("spotify:track:a", status=SongRequest.Status.QUEUED, minutes_ago=5)
        sp = fake_spotify([])
        self.sync(sp)
        req.refresh_from_db()
        self.assertEqual(req.status, SongRequest.Status.QUEUED)
        sp.queue.assert_not_called()

    def test_old_pending_expires_instead_of_blocking_the_song(self):
        req = request("spotify:track:a", minutes_ago=60)
        self.sync(fake_spotify([]))
        req.refresh_from_db()
        self.assertEqual(req.status, SongRequest.Status.FAILED)

    def test_fresh_pending_is_left_to_the_view_that_is_queueing_it(self):
        req = request("spotify:track:a", minutes_ago=0)
        sp = fake_spotify([{**LOCAL, "is_active": True}], playing_uri="spotify:track:other")
        self.sync(sp)
        req.refresh_from_db()
        self.assertEqual(req.status, SongRequest.Status.PENDING)
        sp.add_to_queue.assert_not_called()

    def test_closed_store_does_not_wake_the_device(self):
        StoreSettings.objects.update_or_create(pk=1, defaults={"is_open": False})
        req = request("spotify:track:a")
        sp = fake_spotify([LOCAL])
        self.sync(sp)
        req.refresh_from_db()
        self.assertEqual(req.status, SongRequest.Status.PENDING)
        sp.start_playback.assert_not_called()

    def test_staff_pause_is_respected_until_music_plays_again(self):
        req = request("spotify:track:a", status=SongRequest.Status.QUEUED)
        spotify_client._mark_paused(FLOOR)
        sp = fake_spotify([LOCAL])
        self.sync(sp)
        sp.start_playback.assert_not_called()

        playing = fake_spotify([{**LOCAL, "is_active": True}], playing_uri="spotify:track:other")
        playing.queue.return_value = {"queue": [{"uri": "spotify:track:a"}]}
        self.sync(playing)
        self.assertFalse(spotify_client.is_paused_by_staff(FLOOR))
        req.refresh_from_db()
        self.assertEqual(req.status, SongRequest.Status.QUEUED)

    def test_old_queued_request_expires_instead_of_playing_hours_later(self):
        req = request("spotify:track:a", status=SongRequest.Status.QUEUED, minutes_ago=300)
        sp = fake_spotify([LOCAL])
        self.sync(sp)
        req.refresh_from_db()
        self.assertEqual(req.status, SongRequest.Status.FAILED)
        sp.start_playback.assert_not_called()

    def test_queued_request_older_than_45_min_is_not_auto_played(self):
        req = request("spotify:track:a", status=SongRequest.Status.QUEUED, minutes_ago=60)
        sp = fake_spotify([LOCAL])
        self.sync(sp)
        req.refresh_from_db()
        self.assertEqual(req.status, SongRequest.Status.QUEUED)
        sp.start_playback.assert_not_called()

    def test_request_claimed_by_the_view_is_not_queued_again(self):
        req = request("spotify:track:a")
        # La vista la reclamo y sigue hablando con Spotify.
        self.assertTrue(spotify_client.claim_request(
            req.pk, SongRequest.Status.PENDING, SongRequest.Status.QUEUED))
        sp = fake_spotify([{**LOCAL, "is_active": True}], playing_uri="spotify:track:other")
        self.sync(sp)
        sp.add_to_queue.assert_not_called()

    def test_failed_enqueue_returns_request_to_pending(self):
        req = request("spotify:track:a")
        sp = fake_spotify([{**LOCAL, "is_active": True}], playing_uri="spotify:track:other")
        sp.add_to_queue.side_effect = spotify_client.SpotifyRateLimitedError(60)
        self.sync(sp)
        req.refresh_from_db()
        self.assertEqual(req.status, SongRequest.Status.PENDING)

    def test_request_for_the_song_already_playing_waits_for_its_turn(self):
        # Pedida cuando la cancion ya iba por el minuto 2: es para otra vez.
        req = request("spotify:track:a", status=SongRequest.Status.QUEUED, minutes_ago=0)
        sp = fake_spotify([{**LOCAL, "is_active": True}], playing_uri="spotify:track:a")
        sp.current_playback.return_value["progress_ms"] = 120_000
        self.sync(sp)
        req.refresh_from_db()
        self.assertEqual(req.status, SongRequest.Status.QUEUED)

    def test_request_queued_before_the_track_started_becomes_playing(self):
        req = request("spotify:track:a", status=SongRequest.Status.QUEUED, minutes_ago=5)
        sp = fake_spotify([{**LOCAL, "is_active": True}], playing_uri="spotify:track:a")
        sp.current_playback.return_value["progress_ms"] = 10_000
        self.sync(sp)
        req.refresh_from_db()
        self.assertEqual(req.status, SongRequest.Status.PLAYING)

    def test_just_started_request_survives_a_stale_playback_of_another_track(self):
        req = request("spotify:track:b", status=SongRequest.Status.PLAYING)
        SongRequest.objects.filter(pk=req.pk).update(played_at=timezone.now())
        self.sync(fake_spotify([{**LOCAL, "is_active": True}], playing_uri="spotify:track:a"))
        req.refresh_from_db()
        self.assertEqual(req.status, SongRequest.Status.PLAYING)

    def test_empty_read_right_after_starting_does_not_start_another(self):
        started = request("spotify:track:a", status=SongRequest.Status.PLAYING)
        SongRequest.objects.filter(pk=started.pk).update(played_at=timezone.now())
        waiting = request("spotify:track:b")
        sp = fake_spotify([LOCAL])
        self.sync(sp)
        sp.start_playback.assert_not_called()
        waiting.refresh_from_db()
        self.assertEqual(waiting.status, SongRequest.Status.PENDING)

    def test_request_to_repeat_a_paused_song_is_not_marked_playing(self):
        req = request("spotify:track:a", status=SongRequest.Status.QUEUED, minutes_ago=3)
        sp = fake_spotify([{**LOCAL, "is_active": True}], playing_uri="spotify:track:a")
        sp.current_playback.return_value.update(is_playing=False, progress_ms=120_000)
        sp.queue.return_value = {"queue": [{"uri": "spotify:track:a"}]}
        self.sync(sp)
        req.refresh_from_db()
        self.assertEqual(req.status, SongRequest.Status.QUEUED)

    def test_resuming_after_a_pause_does_not_shift_the_track_start(self):
        # El track arranco hace 5 min, se pauso, y hace 3 min alguien lo pidio
        # para repetirlo. Al reanudar, "ahora - progreso" diria que arranco hace
        # 61 s (despues del pedido); el arranque real sigue siendo hace 5 min.
        spotify_sync._track_start[FLOOR] = {
            "uri": "spotify:track:a",
            "started": timezone.now() - timedelta(minutes=5),
            "progress_ms": 60_000,
        }
        req = request("spotify:track:a", status=SongRequest.Status.QUEUED, minutes_ago=3)
        sp = fake_spotify([{**LOCAL, "is_active": True}], playing_uri="spotify:track:a")
        sp.current_playback.return_value["progress_ms"] = 61_000
        sp.queue.return_value = {"queue": [{"uri": "spotify:track:a"}]}
        self.sync(sp)
        req.refresh_from_db()
        self.assertEqual(req.status, SongRequest.Status.QUEUED)

    def test_same_track_starting_over_counts_as_a_new_play(self):
        spotify_sync._track_start[FLOOR] = {
            "uri": "spotify:track:a",
            "started": timezone.now() - timedelta(minutes=5),
            "progress_ms": 200_000,
        }
        req = request("spotify:track:a", status=SongRequest.Status.QUEUED, minutes_ago=3)
        sp = fake_spotify([{**LOCAL, "is_active": True}], playing_uri="spotify:track:a")
        sp.current_playback.return_value["progress_ms"] = 4_000
        self.sync(sp)
        req.refresh_from_db()
        self.assertEqual(req.status, SongRequest.Status.PLAYING)

    def test_stale_read_does_not_undo_a_pause(self):
        sp = fake_spotify([{**LOCAL, "is_active": True}], playing_uri="spotify:track:a")
        playing = sp.current_playback.return_value

        def paused_while_in_flight():
            spotify_client._mark_paused(FLOOR)
            return playing

        sp.current_playback.side_effect = paused_while_in_flight
        with mock.patch.object(spotify_client, "_get_spotify_client", return_value=sp):
            spotify_client._fetch_current_playback(FLOOR)
        self.assertTrue(spotify_client.is_paused_by_staff(FLOOR))

    def test_unknown_device_is_not_used_when_the_floor_has_history(self):
        spotify_client._remember_device(FLOOR, "pc-barra")
        self.assertIsNone(spotify_client._pick_device(FLOOR, [TV]))

    def test_device_closed_before_retry_is_no_device_not_failure(self):
        sp = fake_spotify([LOCAL])
        sp.add_to_queue.side_effect = NO_DEVICE  # 404 tambien con device_id
        with mock.patch.object(spotify_client, "_get_spotify_client", return_value=sp):
            with self.assertRaises(spotify_client.SpotifyNoDeviceError):
                spotify_client.add_to_queue("spotify:track:a", FLOOR)
        self.assertTrue(spotify_client.is_waiting_for_device(FLOOR))

    def test_just_started_request_survives_an_empty_playback(self):
        req = request("spotify:track:a", status=SongRequest.Status.PLAYING)
        SongRequest.objects.filter(pk=req.pk).update(played_at=timezone.now())
        self.sync(fake_spotify([]))
        req.refresh_from_db()
        self.assertEqual(req.status, SongRequest.Status.PLAYING)

    def test_pause_from_panel_marks_the_floor(self):
        sp = fake_spotify([{**LOCAL, "is_active": True}])
        with mock.patch.object(spotify_client, "_get_spotify_client", return_value=sp):
            spotify_client.pause_playback(FLOOR)
            self.assertTrue(spotify_client.is_paused_by_staff(FLOOR))
            spotify_client.resume_playback(FLOOR)
        self.assertFalse(spotify_client.is_paused_by_staff(FLOOR))

    def test_tv_is_used_only_when_it_is_the_only_spotify_open(self):
        self.assertEqual(spotify_client._pick_device(FLOOR, [TV])["id"], "tv-lg")
        self.assertEqual(spotify_client._pick_device(FLOOR, [TV, LOCAL])["id"], "pc-barra")
        self.assertIsNone(spotify_client._pick_device(FLOOR, []))

    def test_tv_that_played_last_wins_over_the_phone(self):
        spotify_client._remember_device(FLOOR, "tv-lg")
        self.assertEqual(spotify_client._pick_device(FLOOR, [LOCAL, TV])["id"], "tv-lg")

    def test_tv_already_active_is_used(self):
        self.assertEqual(spotify_client._pick_device(FLOOR, [LOCAL, {**TV, "is_active": True}])["id"], "tv-lg")

    def test_last_device_that_played_wins_over_the_rest(self):
        other = {**LOCAL, "id": "celular", "type": "Smartphone"}
        spotify_client._remember_device(FLOOR, "celular")
        self.assertEqual(spotify_client._pick_device(FLOOR, [LOCAL, other])["id"], "celular")


class PlayTrackTests(TestCase):
    def setUp(self):
        cache.clear()
        spotify_client._no_device_until.clear()

    def test_failed_play_does_not_touch_requests(self):
        playing = request("spotify:track:now", status=SongRequest.Status.PLAYING)
        pending = request("spotify:track:a")
        with mock.patch.object(spotify_client, "_get_spotify_client", return_value=fake_spotify([])):
            with self.assertRaises(spotify_client.SpotifyNoDeviceError):
                spotify_client.play_track("spotify:track:a", FLOOR)
        playing.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual(playing.status, SongRequest.Status.PLAYING)
        self.assertEqual(pending.status, SongRequest.Status.PENDING)
