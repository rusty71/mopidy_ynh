# Mopidy for YunoHost (Mopidy + Qobuz + Icecast stack)

A fork of the official [YunoHost-Apps/mopidy_ynh](https://github.com/YunoHost-Apps/mopidy_ynh)
package, adapted to the Mopidy + Qobuz stack:

* **Iris** as the web interface (instead of the MusicBox web client)
* the **Qobuz** extension with hi-res streaming, browser based login and an
  **album cache** that downloads queued albums as tagged FLAC files into the
  local library
* an **Icecast publisher**, so the playback becomes two continuous streams
  (MP3 and lossless Ogg FLAC) that survive the track changes

This package is meant to be installed together with
[`icecast2_ynh`](https://github.com/rusty71/icecast2_ynh), which serves the
streams.

## Installation

From the YunoHost admin web interface, or:

```bash
yunohost app install mopidy \
    --domain="music.example.org" \
    --path="/mopidy" \
    --qobuz_auth_token="<the token of the Qobuz web player>" \
    --icecast_password="<the password of the Icecast app>"
```

The Qobuz auth token is the token that the Qobuz web player holds; see
[doc/ADMIN.md](doc/ADMIN.md) for how to get it.

## Upgrading

The app keeps the id `mopidy`, the same id as the official
[YunoHost-Apps/mopidy_ynh](https://github.com/YunoHost-Apps/mopidy_ynh)
package.  YunoHost resolves a plain

```bash
yunohost app upgrade mopidy
```

through the app catalog, and that catalog points at the official package
(`3.4.2~ynh6`).  For an installation of this fork that is a *downgrade*, and
the upgrade script of the official package then fails with
`mpd_ext: unbound variable`.  The same happens when an upgrade is forced
without a URL: `yunohost app upgrade mopidy --force`.

Always pass the URL of this repository:

```bash
yunohost app upgrade mopidy -u https://github.com/rusty71/mopidy_ynh
```

A packaging change must also bump `version` in `manifest.toml`; otherwise
YunoHost considers the app up to date and does nothing.  When only the
version is missing during development, `--force` can be added to the command
above, but never leave out `-u`.

## The two streams

The publisher encodes the playback twice and publishes it on the mounts of the
Icecast app:

| Stream | Format | Content |
|---|---|---|
| `/low.mp3` | MP3, 128 kbps CBR, 48 kHz stereo | low bandwidth |
| `/high.ogg` | Ogg FLAC, lossless, up to 24-bit/96 kHz | high resolution |

Mopidy itself does not publish: it tears down its audio output on every track
change, which makes Icecast splice a differently formatted fallback into the
stream.  The publisher keeps one permanent Icecast session open and fills the
gaps with silence, so a listener hears nothing but music.

## The album cache

Queue an album in the interface and it is downloaded in the background as
tagged FLAC files (with the cover) into the local library:

```
/home/yunohost.multimedia/share/Music/Qobuz/<artist>/<album>/01 - <title>.flac
```

Mopidy-Local indexes them right away, so they show up in the interface and
play like any other track.

## The web interface

* `https://<domain><path>/iris/` - Iris, the web interface
* `https://<domain><path>/local/` - the album art of the local library

## Credits

* Original package: [YunoHost-Apps/mopidy_ynh](https://github.com/YunoHost-Apps/mopidy_ynh),
  by siwinter and the YunoHost community.
* Mopidy: <https://www.mopidy.com>
* Iris: <https://github.com/jaedb/iris>
* Mopidy-Qobuz: <https://github.com/dabooze/mopidy-qobuz>
