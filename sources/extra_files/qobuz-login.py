#!/usr/bin/env python3
"""Log in to Qobuz with the browser OAuth flow and store the auth token.

Since April 2026 Qobuz rejects programmatic e-mail/password logins with
"401 User authentication is required".  The way in is the OAuth flow used by
the web player:

  1. Open ``https://www.qobuz.com/signin/oauth`` in a browser.
  2. Log in; Qobuz redirects back to a local URL with a ``code``.
  3. Exchange the code for an auth token at ``api.json/0.2/oauth/callback``.

This script does all of that and writes the token into the Mopidy config.

Usage:
    bin/qobuz-login.py                 # interactive, opens a browser
    bin/qobuz-login.py <code>          # use a code you already have
"""

from __future__ import annotations

import re
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

# The configuration file of Mopidy.  On YunoHost the app lives in
# /var/www/mopidy, and the file holds the Qobuz credentials.
CONF = Path("/var/www/mopidy/mopidy.conf")
BASE_URL = "https://www.qobuz.com/api.json/0.2"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)


def get_bundle_values() -> tuple[str, str, str | None]:
    """Fetch app_id, app secret and OAuth private key from the web player."""
    from qobuz_dl.bundle import Bundle

    bundle = Bundle()
    app_id = str(bundle.get_app_id())
    secrets = [s for s in bundle.get_secrets().values() if s]
    secret = secrets[0] if secrets else ""
    private_key = None
    try:
        private_key = bundle.get_private_key()
    except AttributeError:
        match = re.search(
            r'privateKey:\s*"(?P<key>[A-Za-z0-9]{6,30})"', bundle._bundle
        )
        private_key = match.group("key") if match else None
    return app_id, secret, private_key


def exchange_code(code: str, app_id: str, private_key: str) -> str:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "X-App-Id": app_id})
    response = session.get(
        f"{BASE_URL}/oauth/callback",
        params={"code": code, "private_key": private_key, "app_id": app_id},
    )
    if response.status_code != 200:
        raise SystemExit(
            f"Qobuz rejected the OAuth code (HTTP {response.status_code}).\n"
            "The code is single-use and short lived - run the login again and "
            "complete it in one go."
        )
    token = response.json().get("token")
    if not token:
        raise SystemExit("No token in OAuth callback response")
    return token


def capture_code_in_browser(app_id: str) -> str:
    """Open the Qobuz sign-in page and catch the redirect on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        port = s.getsockname()[1]

    url = (
        "https://www.qobuz.com/signin/oauth"
        f"?ext_app_id={app_id}&redirect_url=http://localhost:{port}"
    )

    class Handler(BaseHTTPRequestHandler):
        auth_code = None

        def do_GET(self):  # noqa: N802
            params = parse_qs(urlparse(self.path).query)
            code = params.get("code", params.get("code_autorisation", [""]))[0]
            if code:
                Handler.auth_code = code
                body = (
                    b"<html><body style='font-family:system-ui;text-align:center;"
                    b"padding:60px'><h2>Login successful</h2><p>You can close this "
                    b"tab and return to your terminal.</p></body></html>"
                )
                self.send_response(200)
            else:
                body = b"<html><body><h2>Login failed</h2></body></html>"
                self.send_response(400)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    print(f"Open this URL in your browser and log in:\n\n  {url}\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass

    print("Waiting for the Qobuz redirect (press Ctrl+C to abort)...")
    thread.join(timeout=300)
    server.server_close()

    if not Handler.auth_code:
        raise SystemExit("No OAuth code received")
    return Handler.auth_code


def store_in_config(**values: str) -> None:
    text = CONF.read_text()
    for key, value in values.items():
        # Match "key = value" as well as an empty "key =" - the config has
        # both forms.  Without the \s* the key would be appended a second
        # time and configparser would reject the whole file.
        pattern = rf"^{key}\s*=.*$"
        if re.search(pattern, text, re.M):
            text = re.sub(pattern, f"{key} = {value}", text, flags=re.M)
        else:
            raise SystemExit(
                f"Could not find '{key} =' in {CONF}; add it to the [qobuz] "
                "section and run this script again."
            )
    CONF.write_text(text)


def config_value(key: str) -> str:
    match = re.search(rf"^{key} = (.*)$", CONF.read_text(), re.M)
    return match.group(1).strip() if match else ""


def validate_token(token: str, app_id: str) -> str:
    """Check the token against the API and return the membership label."""
    session = requests.Session()
    session.headers.update(
        {"User-Agent": USER_AGENT, "X-App-Id": app_id, "X-User-Auth-Token": token}
    )
    response = session.post(
        f"{BASE_URL}/user/login",
        headers={"Content-Type": "text/plain;charset=UTF-8"},
        data="extra=partner",
    )
    if response.status_code != 200:
        raise SystemExit(f"Token rejected by Qobuz: HTTP {response.status_code}")
    return (
        response.json()
        .get("user", {})
        .get("credential", {})
        .get("parameters", {})
        .get("short_label", "unknown")
    )


def main() -> None:
    global CONF

    args = sys.argv[1:]
    print_only = False
    if "--print" in args:
        print_only = True
        args.remove("--print")
    if "--config" in args:
        index = args.index("--config")
        CONF = Path(args[index + 1])
        del args[index : index + 2]
    sys.argv = [sys.argv[0]] + args

    bundle_app_id, secret, private_key = get_bundle_values()
    app_id = config_value("app_id") or bundle_app_id
    print(f"app_id = {app_id}")

    configured_app_id = config_value("app_id")
    if configured_app_id and configured_app_id != bundle_app_id:
        print(
            f"Note: the web player bundle now advertises app_id "
            f"{bundle_app_id}, but the config uses {configured_app_id}. Using "
            f"the configured one - the app_id and secret have to match."
        )

    if len(sys.argv) > 2 and sys.argv[1] == "--token":
        # Token copied by hand from the web player's local storage.
        token = sys.argv[2]
    else:
        if len(sys.argv) > 1:
            code = sys.argv[1]
            if "code=" in code or "code_autorisation=" in code:
                params = parse_qs(urlparse(code).query)
                code = params.get("code", params.get("code_autorisation", [""]))[0]
        else:
            if not private_key:
                raise SystemExit(
                    "Could not find the OAuth private key in the Qobuz web "
                    "player bundle. Log in at https://play.qobuz.com and copy "
                    "the token from localStorage (localuser -> token) "
                    "instead, then run:\n"
                    "    bin/qobuz-login.py --token <token>"
                )
            code = capture_code_in_browser(app_id)

        token = exchange_code(code, app_id, private_key)
        print("\nGot an auth token.")

    label = validate_token(token, app_id)
    print(f"Token valid, membership: {label}")

    if print_only:
        print("\nCopy this token into the config panel of the Mopidy app:")
        print(f"\n{token}\n")
        return

    store_in_config(auth_token=token)
    print(f"\nUpdated {CONF}")
    print("Restart the app: yunohost app restart mopidy")


if __name__ == "__main__":
    main()
