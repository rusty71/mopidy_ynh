# Mopidy for YunoHost (Mopidy + Qobuz + Icecast stack)

A fork of the official [YunoHost-Apps/mopidy_ynh](https://github.com/YunoHost-Apps/mopidy_ynh)
package, adapted to the Mopidy + Qobuz stack:

* **Iris** as the web interface (instead of the MusicBox web client)
* the **Qobuz** extension with hi-res streaming, browser based login and an
  **album cache** that downloads queued albums as tagged FLAC files into the
  local library
* a **320 kbps MP3 Icecast stream** at 44.1 kHz, published by Mopidy itself,
  with the track title announced to Icecast

This package is meant to be installed together with
[`icecast2_ynh`](https://github.com/rusty71/icecast2_ynh), which serves the
streams.

## Installation

Install from the URL of this repository (in the admin web interface: "Install
a custom app"), not from the app catalog entry:

```bash
yunohost app install https://github.com/rusty71/mopidy_ynh \
    --domain="music.example.org" \
    --path="/mopidy" \
    --qobuz_auth_token="<the token of the Qobuz web player>" \
    --icecast_password="<the password of the Icecast app>"
```

The app id is `mopidy`, the same as the official package, so
`yunohost app install mopidy` installs the official one from the catalog.

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

## The stream

Mopidy publishes one stream on the mount of the Icecast app:

| Stream | Format |
|---|---|
| `/low.mp3` | MP3, 320 kbps CBR, 44.1 kHz stereo |

The audio leaves Mopidy through GStreamer's `shout2send`; there is no separate
encoder or publisher process.  A natural track change is gapless, because
Mopidy starts the next track before the current one ends.  A skip tears the
output down and rebuilds it: Icecast then moves the listeners to the silent
fallback and back, which costs about half a second.

The title of the track is published to the admin endpoint of Icecast by the
`icecast_titles` frontend of this package.  The tag events of the stream are
dropped on their way to the output: Qobuz serves tagless FLAC, so they would
keep announcing "Unknown - Unknown" and overwrite the real title.

`shout2send` cannot speak TLS, so Mopidy connects to a plain socket of
Icecast on the loopback interface.  The public socket keeps the certificate
and is for the listeners.

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
