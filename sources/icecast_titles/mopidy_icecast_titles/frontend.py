import base64
import logging
import threading
import urllib.parse
import urllib.request

import pykka
from mopidy import core

logger = logging.getLogger(__name__)

# The source has just connected and ``shout2send`` has sent its own (empty)
# metadata.  The real title is posted a moment later, so that it wins.
DELAY = 1.0
TIMEOUT = 5.0


class IcecastTitlesFrontend(pykka.ThreadingActor, core.CoreListener):
    def __init__(self, config, core):
        super().__init__()
        self.core = core
        self.config = config["icecast_titles"]
        self._timer = None
        self._lock = threading.Lock()
        self._warned = False

    def on_stop(self):
        self._cancel()

    def track_playback_started(self, tl_track):
        self._schedule(tl_track.track)

    def track_playback_resumed(self, tl_track, time_position):
        self._schedule(tl_track.track)

    def _schedule(self, track):
        if not self.config["enabled"]:
            return

        title = _title(track)
        if not title:
            return

        with self._lock:
            self._cancel()
            self._timer = threading.Timer(DELAY, self._publish, args=(title,))
            self._timer.daemon = True
            self._timer.start()

    def _cancel(self):
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _publish(self, title):
        url = self.config["url"]
        password = self.config["password"]
        if not url or not password:
            if not self._warned:
                logger.warning(
                    "icecast_titles: no url or password configured, "
                    "the track title is not published"
                )
                self._warned = True
            return

        query = urllib.parse.urlencode(
            {
                "mount": self.config["mount"],
                "mode": "updinfo",
                "song": title,
            }
        )
        request = urllib.request.Request(f"{url}?{query}")
        credentials = f"{self.config['username']}:{password}".encode("utf-8")
        request.add_header(
            "Authorization",
            "Basic " + base64.b64encode(credentials).decode("ascii"),
        )

        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                response.read()
        except Exception as exc:
            logger.warning(
                "icecast_titles: could not publish %r: %s", title, exc
            )


def _title(track):
    if not track.name:
        return None
    artists = [artist.name for artist in (track.artists or []) if artist.name]
    if artists:
        return f"{', '.join(artists)} - {track.name}"
    return track.name
