# Administration

## The web interface

The interface is Iris, served by Mopidy itself:

* `https://<domain><path>/iris/` - the interface
* `https://<domain><path>/local/` - the album art of the local library

The bare URL of the app (`https://<domain><path>/`) redirects to the
interface, and so do the old bookmarks (`<path>/index.html`).

A path without a trailing slash is redirected by Mopidy itself, and the
nginx configuration puts the path of the app in front of that redirect again:
`<path>/iris` ends up at `<path>/iris/` and not at `/iris/`, which would be
outside of the app.

**The first visit shows a "Welcome to Iris" dialog.**  It asks for a name
for the browser and shows the host and the port it found; the host has to
be the one the browser uses (`<domain>`) and the port the one of the
website (443 for https).  Press "Save" and the interface comes up.  The
answer is kept in the browser, so it is asked once per browser.

> The Iris frontend always talks to Mopidy over the websocket on
> `/mopidy/ws`, whatever path the app is installed under.  The package adds
> that location to the nginx configuration, so it works out of the box.

## The Qobuz account

Since April 2026 Qobuz rejects programmatic e-mail/password logins, so the
extension logs in with the **auth token** that the Qobuz web player holds.

Get the token on your own machine (the browser flow needs a browser, and the
server usually has none):

```bash
git clone https://github.com/rusty71/mopidy_ynh.git
cd mopidy_ynh/sources/extra_files
./qobuz-login.py --print
```

The script opens the Qobuz sign-in page, catches the redirect and prints the
token.  Copy it into the **Qobuz auth token** field of the config panel of
the app.

If the browser flow does not work, log in at <https://play.qobuz.com>, open
the developer tools -> Application -> Local Storage -> `localuser` ->
`token`, and use that value.

The token is bound to the app id: a token that was issued for one app id is
rejected by the other.  The package uses the `377257687` / `806331c3b0b641da923b890aed01d04a`
pair by default, which is the one the token above is issued for.

## The album art

Mopidy-Local serves the images of the local library under the path
`/local/`, which is outside of the path of the app.  The nginx
configuration of the app serves that path too, and the permission of the
app covers it, so the images are there for the browser that asks the
domain for them.

The album art of the albums in Qobuz comes from Qobuz itself and is
served by them.

## The volume control

The volume belongs to the server, not to the listener: with the control in
the interface, two people listening fight over it.  It is therefore hidden,
and the setting **"Hide the volume control?"** in the config panel of the app
turns it on and off.  A change is picked up when the page is reloaded.

The assets of the interface are kept by the browser for years, so the
package changes the build number in the page (the cache buster of the
assets) whenever it patches them.  Without that, a change would stay
invisible until the cache expires.

## The album cache

When an album is queued in the interface, the extension downloads it in the
background as tagged FLAC files (with the cover) and adds it to the local
library:

```
/home/yunohost.multimedia/share/Music/Qobuz/<artist>/<album>/01 - <title>.flac
```

The files are indexed by Mopidy-Local, so they show up in the interface and
play like any other track.  The cache can be turned off in the config panel.

## The stream

Mopidy publishes one stream, through GStreamer's `shout2send`:

| Mount | Format |
|---|---|
| `/low.mp3` | MP3, 320 kbps CBR, 44.1 kHz stereo |

It is served by the Icecast app, on its own port and through the reverse
proxy of the domain:

* `https://<domain>:<icecast port>/low.mp3`
* `https://<domain><icecast path>/low.mp3`

A natural track change is gapless, because Mopidy starts the next track
before the current one ends.  A skip rebuilds the output, which costs about
half a second; Icecast serves the silent fallback in the meantime.

The title of the track is published to Icecast by the `icecast_titles`
frontend, an extension that is installed from
`src/icecast-titles` (part of this package).  The tag events of the stream
are dropped on their way to the output, because a tagless stream (Qobuz
serves tagless FLAC) would keep announcing "Unknown - Unknown".

`shout2send` cannot speak TLS, so the app connects to the plain socket that
the Icecast app has on `127.0.0.1`.  The port is in the `source_port`
setting of the Icecast app; the public socket keeps the certificate.

## The services

```bash
systemctl status mopidy
journalctl -u mopidy -f
```

An installation from before the publisher was dropped still has a
`mopidy-publisher` service in its unit files; the upgrade of this package
removes it.

## The configuration

The configuration of Mopidy is `/var/www/mopidy/mopidy.conf`.  It holds the Qobuz
credentials, so it is only readable by the Mopidy user (`chmod 400`).

Most of it can be changed from the config panel of the app, which restarts the
services for you.

## Commands

```bash
mopidyctl local scan      # index the local library
mopidyctl local clear     # empty the index
```

## Upgrading

This fork shares the app id `mopidy` with the official package.  Upgrading
without an explicit source uses the official package from the app catalog,
which is older (`3.4.2~ynh6`) and fails on this installation.  Always name the
repository:

```bash
yunohost app upgrade mopidy -u https://github.com/rusty71/mopidy_ynh
```

Do not use the generic upgrade button or `--force` without `-u` until the
catalog points at this fork.

## Where the code lives

The components are installed as editable git checkouts in
`/var/www/mopidy/src/`, so the code can be changed on the server:

```bash
cd /var/www/mopidy/src/mopidy-qobuz
$EDITOR mopidy_qobuz/backend.py
systemctl restart mopidy
```

They come from these repositories, each pinned to a tag and to the commit it
points at:

| Component | Repository |
|---|---|
| Mopidy 3.4.2 | <https://github.com/rusty71/mopidy> (branch `ynh-stack`) |
| Iris | <https://github.com/rusty71/mopidy-iris> (branch `ynh-stack`) |
| Mopidy-Qobuz | <https://github.com/rusty71/mopidy-qobuz> (branch `ynh-stack`) |
| Mopidy-Local | from PyPI, version 3.3.0 |
