import base64
import hashlib
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


SPOTIFY_AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_URL = "https://api.spotify.com/v1"
SPOTIFY_SCOPES = " ".join([
    "user-read-currently-playing",
    "user-read-playback-state",
    "user-modify-playback-state",
])


class SpotifyError(RuntimeError):
    pass


class SpotifyClient:
    def __init__(self, client_id, redirect_uri, token_path):
        self.client_id = client_id.strip()
        self.redirect_uri = redirect_uri
        self.token_path = Path(token_path)
        self.token = self._load_token()

    @property
    def connected(self):
        return bool(self.token and self.token.get("refresh_token"))

    def _load_token(self):
        try:
            return json.loads(self.token_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _save_token(self, token):
        tmp_path = self.token_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(token, indent=2), encoding="utf-8")
        os.replace(tmp_path, self.token_path)
        self.token = token

    @staticmethod
    def _code_challenge(verifier):
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def authorize_interactively(self, timeout=180):
        verifier = secrets.token_urlsafe(64)
        state = secrets.token_urlsafe(24)
        parsed = urllib.parse.urlparse(self.redirect_uri)
        if parsed.hostname != "127.0.0.1" or not parsed.port:
            raise SpotifyError("Spotify redirect URI must use 127.0.0.1 with an explicit port.")

        result = {}
        expected_path = parsed.path or "/"
        expected_state = state

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(handler_self):
                callback = urllib.parse.urlparse(handler_self.path)
                params = urllib.parse.parse_qs(callback.query)
                if callback.path != expected_path:
                    handler_self.send_error(404)
                    return
                if params.get("state", [None])[0] != expected_state:
                    result["error"] = "Spotify authorization state did not match."
                    handler_self.send_error(400, "Authorization state mismatch")
                    return
                result["code"] = params.get("code", [None])[0]
                result["error"] = params.get("error", [None])[0]
                body = (
                    b"<html><body><h2>Spotify connected to Sophia.</h2>"
                    b"<p>You can close this tab and return to the console.</p></body></html>"
                )
                handler_self.send_response(200)
                handler_self.send_header("Content-Type", "text/html; charset=utf-8")
                handler_self.send_header("Content-Length", str(len(body)))
                handler_self.end_headers()
                handler_self.wfile.write(body)

            def log_message(self, format, *args):
                pass

        params = urllib.parse.urlencode({
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": SPOTIFY_SCOPES,
            "code_challenge_method": "S256",
            "code_challenge": self._code_challenge(verifier),
            "state": state,
        })
        auth_url = SPOTIFY_AUTHORIZE_URL + "?" + params

        server = HTTPServer((parsed.hostname, parsed.port), CallbackHandler)
        server.timeout = timeout
        print("Opening Spotify authorization in your browser...")
        if not webbrowser.open(auth_url):
            print("Open this URL manually:\n" + auth_url)
        server.handle_request()
        server.server_close()

        if result.get("error"):
            raise SpotifyError("Spotify authorization failed: " + result["error"])
        code = result.get("code")
        if not code:
            raise SpotifyError("Spotify authorization timed out or returned no code.")

        token = self._token_request({
            "client_id": self.client_id,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "code_verifier": verifier,
        })
        self._store_token_response(token)

    def _token_request(self, fields):
        data = urllib.parse.urlencode(fields).encode("ascii")
        request = urllib.request.Request(
            SPOTIFY_TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        return self._open_json(request)

    def _store_token_response(self, token):
        if self.token and not token.get("refresh_token"):
            token["refresh_token"] = self.token.get("refresh_token")
        token["expires_at"] = int(time.time()) + int(token.get("expires_in", 3600)) - 60
        self._save_token(token)

    def _refresh(self):
        refresh_token = (self.token or {}).get("refresh_token")
        if not refresh_token:
            raise SpotifyError("Spotify is not connected. Run setup_spotify.py first.")
        token = self._token_request({
            "client_id": self.client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        })
        self._store_token_response(token)

    def _access_token(self):
        if not self.connected:
            raise SpotifyError("Spotify is not connected. Run setup_spotify.py first.")
        if int((self.token or {}).get("expires_at", 0)) <= int(time.time()):
            self._refresh()
        return self.token["access_token"]

    @staticmethod
    def _open_json(request):
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                body = response.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise SpotifyError(f"Spotify API returned {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise SpotifyError("Could not reach Spotify: " + str(exc.reason)) from exc

    def _api(self, method, path, body=None, retry=True):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            SPOTIFY_API_URL + path,
            data=data,
            headers={
                "Authorization": "Bearer " + self._access_token(),
                "Content-Type": "application/json",
            },
            method=method,
        )
        try:
            return self._open_json(request)
        except SpotifyError as exc:
            if retry and "returned 401" in str(exc):
                self._refresh()
                return self._api(method, path, body, retry=False)
            raise

    def currently_playing(self):
        return self._api("GET", "/me/player/currently-playing")

    def pause(self):
        self._api("PUT", "/me/player/pause", {})

    def resume(self):
        self._api("PUT", "/me/player/play", {})

    def next_track(self):
        self._api("POST", "/me/player/next")

    def previous_track(self):
        self._api("POST", "/me/player/previous")

    def handle_voice_command(self, transcript):
        text = transcript.lower().replace("’", "'")
        text = re.sub(r"[^a-z0-9' ]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        if re.search(r"\b(what(?:'s| is) playing|what (?:song|track) is this)\b", text):
            return True, self._now_playing_reply()
        if re.search(r"\b(?:pause|stop)\b.*\b(?:music|spotify|song|track)\b", text):
            self.pause()
            return True, "Paused."
        if re.search(r"\b(?:resume|unpause|play)\b.*\b(?:music|spotify)\b", text):
            self.resume()
            return True, "Resuming Spotify."
        if re.search(r"\b(?:skip|next)\b.*\b(?:song|track)\b", text):
            self.next_track()
            return True, "Skipping it."
        if re.search(r"\b(?:previous|last)\b.*\b(?:song|track)\b", text) or re.search(
            r"\bgo back (?:a|one) (?:song|track)\b", text
        ):
            self.previous_track()
            return True, "Going back one track."
        return False, None

    def _now_playing_reply(self):
        state = self.currently_playing()
        if not state or not state.get("item"):
            return "Spotify isn't playing anything right now."
        item = state["item"]
        name = item.get("name", "something unnamed")
        artists = ", ".join(a.get("name", "") for a in item.get("artists", []) if a.get("name"))
        if artists:
            return f"This is {name} by {artists}."
        return f"This is {name}."
