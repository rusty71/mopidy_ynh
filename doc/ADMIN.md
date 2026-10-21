# Administration

## The web interface

The interface is Iris, served by Mopidy itself:

* `https://<domain><path>/iris/` - the interface
* `https://<domain><path>/local/` - the album art of the local library

The bare URL of the app (`https://<domain><path>/`) redirects to the
interface.

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

## The album cache

When an album is queued in the interface, the extension downloads it in the
background as tagged FLAC files (with the cover) and adds it to the local
library:

```
/home/yunohost.multimedia/share/Music/Qobuz/<artist>/<album>/01 - <title>.flac
```

The files are indexed by Mopidy-Local, so they show up in the interface and
play like any other track.  The cache can be turned off in the config panel.

## The two streams

The publisher turns the playback into two continuous Icecast streams:

| Mount | Format |
|---|---|
| `/low.mp3` | MP3, 128 kbps CBR, 48 kHz stereo |
| `/high.ogg` | Ogg FLAC, lossless, up to 24-bit/96 kHz |

They are served by the Icecast app, which listens on its own port:

* `http://<domain>:<icecast port>/low.mp3`
* `http://<domain>:<icecast port>/high.ogg`

and through the reverse proxy of the domain:

* `https://<domain><icecast path>/low.mp3`
* `https://<domain><icecast path>/high.ogg`

The publisher keeps the Icecast session open across track changes, so a
listener never notices a change of track.  If Mopidy is not playing, the
mounts serve a silent fallback in exactly the same format.

## The services

```bash
systemctl status mopidy mopidy-publisher
journalctl -u mopidy -f
journalctl -u mopidy-publisher -f
```

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
