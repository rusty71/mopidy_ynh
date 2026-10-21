#!/usr/bin/env python3
"""Publish Mopidy's audio to Icecast as one continuous stream per mount.

Why this exists
---------------
Mopidy changes track by taking its GStreamer playbin through READY, which
tears down the audio output - including the Shoutcast client.  The Icecast
source therefore disconnects and reconnects on every track change, and the
listeners get a different bitstream spliced into their stream (Icecast's
fallback file).  Players that care about timing - VLC with FLAC, for example -
fall apart on that: "Timestamp conversion failed" and lost synchronisation.

This service sits between Mopidy and Icecast:

    Mopidy --PCM over TCP--> icecast-publisher --Icecast-->

Mopidy only hands over raw PCM.  The encoding happens here, so the sample
counter, the timestamps and the container keep running across track changes
and the Icecast session stays open.  Gaps (while Mopidy fetches the next
track) are filled with silence, so the stream is continuous in wall clock
time as well.

One instance can publish several mounts at once, e.g. a lossless hi-res
stream and a smaller MP3 for compatibility.
"""

from __future__ import annotations

import argparse
import base64
import errno
import logging
import socket
import sys
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

Gst.init(None)

logger = logging.getLogger("icecast-publisher")

CONTENT_TYPES = {"flac": "application/ogg", "mp3": "audio/mpeg"}


class IcecastSource:
    """A source client connection to Icecast that stays open."""

    def __init__(self, host, port, mount, user, password, content_type, name):
        self.host = host
        self.port = port
        self.mount = mount
        self.user = user
        self.password = password
        self.content_type = content_type
        self.name = name
        self._sock = None
        self._lock = threading.Lock()
        self.written = 0

    def connect(self):
        sock = socket.create_connection((self.host, self.port), timeout=15)
        auth = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
        request = (
            f"SOURCE {self.mount} HTTP/1.0\r\n"
            f"User-Agent: Mopidy Icecast Publisher\r\n"
            f"Authorization: Basic {auth}\r\n"
            f"Ice-Name: {self.name}\r\n"
            "Ice-Public: 1\r\n"
            f"Content-Type: {self.content_type}\r\n"
            "\r\n"
        )
        sock.sendall(request.encode())

        # Icecast answers with the response headers, then streams the body.
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("Icecast closed the connection")
            response += chunk
        status = response.split(b"\r\n", 1)[0].decode(errors="replace")
        if " 200 " not in status:
            raise ConnectionError(f"Icecast refused the source: {status}")
        logger.info("Connected to Icecast: %s (%s)", self.mount, status)
        self._sock = sock

    def write(self, data: bytes):
        if not data:
            return
        with self._lock:
            if self._sock is None:
                raise ConnectionError("not connected")
            self._sock.sendall(data)

    def set_metadata(self, title: str, admin_user: str, admin_password: str):
        """Update the title Icecast shows for this mount.

        Icecast accepts an admin request with the title in the "song"
        parameter.  Note that only the formats with a tag handler support
        this, so it works for the MP3 mount but not for the Ogg one.
        """
        if not title or not admin_password:
            return
        import base64
        import urllib.parse
        import urllib.request

        query = urllib.parse.urlencode(
            {
                "mode": "updinfo",
                "song": title.replace("'", ""),
                "mount": self.mount,
                "charset": "UTF-8",
            }
        )
        url = (
            f"http://{self.host}:{self.port}/admin/metadata?{query}"
        )
        credentials = base64.b64encode(
            f"{admin_user}:{admin_password}".encode()
        ).decode()
        request = urllib.request.Request(
            url, headers={"Authorization": f"Basic {credentials}"}
        )
        try:
            with urllib.request.urlopen(request, timeout=10):
                pass
        except Exception as error:
            logger.debug("Could not set the metadata: %s", error)

    def ensure_connected(self):
        """Reconnect if the session was lost (Icecast times out idle sources)."""
        if self._sock is not None:
            return
        try:
            self.connect()
        except Exception as error:
            logger.error("Could not reconnect to Icecast: %s", error)
            time.sleep(5)

    def close(self):
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None


class Publisher:
    """Encodes the incoming PCM and writes it to the Icecast mounts.

    Every mount gets its own pipeline with its own appsrc.  A shared tee
    would couple the branches: the MP3 encoder only accepts up to 48 kHz and
    would then force the whole pipeline (and the lossless stream with it) down
    to 48 kHz.
    """

    def __init__(self, targets, rate, channels, fmt):
        self.targets = targets
        self.rate = rate
        self.channels = channels
        self.bytes_per_frame = channels * (4 if fmt == "S32LE" else 2)
        self.branches = []
        self.rejected = 0

        for target in targets:
            self.branches.append(self._make_branch(target, rate, channels, fmt))

    def _make_branch(self, target, rate, channels, fmt):
        source, codec = target["source"], target["codec"]

        appsrc = Gst.ElementFactory.make("appsrc")
        appsrc.set_property("name", "pcm-in")
        appsrc.set_property("format", Gst.Format.TIME)
        # The publisher does the real time pacing itself, so the source does
        # not have to be a live source.
        appsrc.set_property("is-live", False)
        appsrc.set_property(
            "caps",
            Gst.Caps.from_string(
                f"audio/x-raw,format={fmt},rate={rate},channels={channels},"
                "layout=interleaved"
            ),
        )
        appsrc.set_property("max-bytes", 16 * 1024 * 1024)
        appsrc.set_property("block", True)

        pipeline = Gst.Pipeline.new(None)
        sink = Gst.ElementFactory.make("fakesink")
        sink.set_property("sync", False)
        sink.set_property("async", False)

        elements = [appsrc]
        if codec == "flac":
            # The PCM from Mopidy is 32 bit, but FLAC in 32 bit is unusual and
            # some decoders stumble over it.  24 bit is the standard for
            # hi-res audio and every player handles it.
            depth = Gst.ElementFactory.make("capsfilter")
            depth.set_property(
                "caps",
                Gst.Caps.from_string("audio/x-raw,format=S24LE"),
            )
            elements += [
                Gst.ElementFactory.make("audioconvert"),
                depth,
                Gst.ElementFactory.make("flacenc"),
                Gst.ElementFactory.make("oggmux"),
            ]
        else:
            # MP3 tops out at 48 kHz, so resample on the way out.
            encoder = Gst.ElementFactory.make("lamemp3enc")
            encoder.set_property("target", 0)  # BITRATE
            encoder.set_property("cbr", True)
            encoder.set_property("bitrate", target.get("bitrate", 128))
            elements += [
                Gst.ElementFactory.make("audioresample"),
                Gst.ElementFactory.make("audioconvert"),
                encoder,
            ]
        elements += [sink]

        for element in elements:
            pipeline.add(element)
        for a, b in zip(elements, elements[1:]):
            if not a.link(b):
                raise RuntimeError(f"could not link {a} to {b}")

        def write_buffer(pad, info, source=source):
            buffer_ = info.get_buffer()
            if buffer_ is None:
                return Gst.PadProbeReturn.OK
            try:
                source.ensure_connected()
                result = buffer_.extract_dup(0, buffer_.get_size())
                # PyGObject returns either the bytes or a (success, bytes) tuple.
                data = result[1] if isinstance(result, tuple) else result
                if data:
                    source.write(bytes(data))
                    source.written += len(data)
            except Exception as error:
                logger.warning("Lost the connection to Icecast: %s", error)
                source.close()
            return Gst.PadProbeReturn.OK

        sink.get_static_pad("sink").add_probe(Gst.PadProbeType.BUFFER, write_buffer)

        pipeline.set_state(Gst.State.PLAYING)
        return {"pipeline": pipeline, "appsrc": appsrc}

    def push(self, data: bytes, pts_ns: int):
        for branch in self.branches:
            buffer_ = Gst.Buffer.new_wrapped(data)
            buffer_.pts = pts_ns
            buffer_.dts = pts_ns
            buffer_.duration = (
                len(data) // self.bytes_per_frame * Gst.SECOND // self.rate
            )
            result = branch["appsrc"].emit("push-buffer", buffer_)
            if result != Gst.FlowReturn.OK:
                self.rejected += 1

    def stop(self):
        for branch in self.branches:
            branch["pipeline"].set_state(Gst.State.NULL)


class PcmServer:
    """Accepts the PCM stream from Mopidy."""

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.buffer = bytearray()
        self.lock = threading.Lock()
        self.connected = False

    def serve(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.port))
        server.listen(4)
        logger.info("Listening for Mopidy PCM on %s:%d", self.host, self.port)
        while True:
            try:
                conn, addr = server.accept()
            except OSError as error:
                if error.errno == errno.EINTR:
                    continue
                raise
            with self.lock:
                if self.connected:
                    # Mopidy may reconnect before the old socket is noticed as
                    # dead.  Only the first connection carries the audio, so
                    # extra ones must not be mixed in (that would play the
                    # stream at double speed).
                    logger.warning(
                        "Ignoring extra connection from %s, "
                        "the audio is already being received",
                        addr,
                    )
                    conn.close()
                    continue
                self.connected = True
            logger.info("Mopidy connected from %s", addr)
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            threading.Thread(target=self._read_loop, args=(conn,), daemon=True).start()

    def _read_loop(self, conn):
        try:
            while True:
                data = conn.recv(65536)
                if not data:
                    break
                with self.lock:
                    self.buffer += data
        except OSError:
            pass
        finally:
            logger.info("Mopidy disconnected")
            with self.lock:
                self.connected = False
            try:
                conn.close()
            except OSError:
                pass

    def take(self, count: int) -> bytes:
        with self.lock:
            data = bytes(self.buffer[:count])
            del self.buffer[: len(data)]
            return data

    def pending(self) -> int:
        with self.lock:
            return len(self.buffer)


def parse_target(spec):
    """Parse "mount:codec[:bitrate]"."""
    parts = spec.split(":")
    mount = parts[0]
    codec = parts[1] if len(parts) > 1 else "flac"
    bitrate = int(parts[2]) if len(parts) > 2 else 128
    if codec not in CONTENT_TYPES:
        raise ValueError(f"unknown codec {codec}")
    return {"mount": mount, "codec": codec, "bitrate": bitrate}


def resync_mopidy(mopidy_url, state_file, targets, admin_user, admin_password, delay=8.0):
    """Make Mopidy reconnect to the publisher.

    When the publisher restarts, the TCP connection to Mopidy breaks and
    Mopidy's output pipeline dies with it, which stops the playback.  We
    remember whether Mopidy was playing and start it again, which rebuilds
    the pipeline.
    """
    import json
    import urllib.request

    def call(method, params=None):
        payload = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params is not None:
            payload["params"] = params
        request = urllib.request.Request(
            mopidy_url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)

    def get_state():
        try:
            return call("core.playback.get_state").get("result")
        except Exception:
            return None

    def get_title():
        try:
            track = call("core.playback.get_current_track").get("result")
        except Exception:
            return None
        if not track:
            return None
        name = track.get("name") or ""
        artists = ", ".join(
            a.get("name", "")
            for a in (track.get("artists") or [])
            if a.get("name")
        )
        if artists and artists not in name:
            return f"{artists} - {name}"
        return name or None

    def remember(state):
        try:
            state_file.write_text(state or "")
        except OSError:
            pass

    time.sleep(delay)

    try:
        previous = state_file.read_text().strip()
    except OSError:
        previous = ""

    state = get_state()
    if state == "playing":
        call("core.playback.play")
        logger.info("Asked Mopidy to play again to reconnect the output")
    elif previous == "playing" and state in ("stopped", None):
        # Mopidy was playing before and stopped when our socket broke.
        call("core.playback.play")
        logger.info("Mopidy had stopped when the publisher went away, "
                    "starting playback again")
    else:
        logger.info("Mopidy is %s, nothing to resync", state)

    # Keep track of the state so a restart of the publisher knows whether
    # Mopidy was playing, and publish the current track title.
    last_title = None
    while True:
        state = get_state()
        remember(state)
        if state == "playing":
            title = get_title()
            if title and title != last_title:
                last_title = title
                for target in targets:
                    target["source"].set_metadata(title, admin_user, admin_password)
                logger.info("Now playing: %s", title)
        else:
            last_title = None
        time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=9900)
    parser.add_argument("--icecast-host", default="127.0.0.1")
    parser.add_argument("--icecast-port", type=int, default=8000)
    parser.add_argument("--user", default="source")
    parser.add_argument("--password", default="hackme")
    parser.add_argument("--name", default="Mopidy")
    parser.add_argument(
        "--admin-user",
        default="admin",
        help="Icecast admin user, used to publish the track title",
    )
    parser.add_argument(
        "--admin-password",
        default="",
        help="Icecast admin password, used to publish the track title",
    )
    parser.add_argument(
        "--state-file",
        default="/var/lib/mopidy-publisher/publisher-state",
        help="file used to remember whether Mopidy was playing",
    )
    parser.add_argument(
        "--mopidy-url",
        default="http://127.0.0.1:6680/mopidy/rpc",
        help="Mopidy JSON-RPC endpoint, used to reconnect after a restart",
    )
    parser.add_argument("--rate", type=int, default=96000)
    parser.add_argument("--channels", type=int, default=2)
    parser.add_argument("--format", default="S32LE", choices=["S32LE", "S16LE"])
    parser.add_argument(
        "--output",
        action="append",
        required=True,
        help="mount[:codec[:bitrate]], e.g. /high.ogg:flac or /low.mp3:mp3:128",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    targets = []
    for spec in args.output:
        target = parse_target(spec)
        source = IcecastSource(
            args.icecast_host,
            args.icecast_port,
            target["mount"],
            args.user,
            args.password,
            CONTENT_TYPES[target["codec"]],
            args.name,
        )
        while True:
            try:
                source.connect()
                break
            except Exception as error:
                logger.error("Could not connect to Icecast: %s", error)
                time.sleep(5)
        target["source"] = source
        targets.append(target)

    threading.Thread(
        target=resync_mopidy,
        args=(
            args.mopidy_url,
            Path(args.state_file),
            targets,
            args.admin_user,
            args.admin_password,
        ),
        daemon=True,
    ).start()

    publisher = Publisher(targets, args.rate, args.channels, args.format)
    server = PcmServer(args.listen_host, args.listen_port)
    threading.Thread(target=server.serve, daemon=True).start()

    # The publisher is the clock: it hands the samples to the encoder in real
    # time and fills the gaps with silence, so the stream never stutters.
    frame_bytes = publisher.bytes_per_frame
    silence = b"\x00" * (frame_bytes * 1024)
    started = time.monotonic()
    pushed_frames = 0
    pts = 0

    try:
        last_report = time.monotonic()
        last_written = 0
        while True:
            target = int((time.monotonic() - started) * args.rate)
            behind = target - pushed_frames
            if behind > 0:
                want = min(behind, 1024) * frame_bytes
                data = server.take(want)
                if len(data) < want:
                    # Not enough PCM: pad with silence so the timeline keeps
                    # up with the wall clock.
                    data += silence[: want - len(data)]
                frames = len(data) // frame_bytes
                publisher.push(data, pts)
                pts += frames * Gst.SECOND // args.rate
                pushed_frames += frames
            else:
                time.sleep(0.005)

            now = time.monotonic()
            if now - last_report >= 5:
                last_report = now
                out_bytes = sum(t["source"].written for t in targets)
                logger.info(
                    "pushed %.1fs of audio, %.1fs behind, %d buffers rejected, "
                    "%d bytes of PCM waiting, %.0f kB/s to Icecast",
                    pushed_frames / args.rate,
                    -behind / args.rate,
                    publisher.rejected,
                    server.pending(),
                    (out_bytes - last_written) / 5 / 1000,
                )
                last_written = out_bytes
    except KeyboardInterrupt:
        logger.info("Shutting down")
    finally:
        publisher.stop()
        for target in targets:
            target["source"].close()


if __name__ == "__main__":
    sys.exit(main())
