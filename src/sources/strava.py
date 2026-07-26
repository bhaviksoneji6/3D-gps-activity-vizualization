"""
Strava as an activity source.

OAuth2 (authorization-code) with a local redirect catcher, a persisted read-only
refresh token, and activity streams reconstructed into a standard GPX file that
the rest of the pipeline consumes unchanged.

Setup is one-time per person: register a personal API app at
https://www.strava.com/settings/api (Authorization Callback Domain = localhost).
The first run walks you through it and writes the credentials to .env.
"""
import os
import re
import json
import time
import webbrowser
import xml.sax.saxutils as saxutils
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlencode, urlparse, parse_qs

import requests
from dotenv import load_dotenv

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ENV_FILE   = os.path.join(_PROJ_ROOT, ".env")
_TOKEN_FILE = os.path.join(_PROJ_ROOT, ".strava_token.json")

REDIRECT_PORT = 8721
REDIRECT_URI  = f"http://localhost:{REDIRECT_PORT}/"
SCOPE         = "activity:read_all"          # read-only; activities only

_AUTH_URL   = "https://www.strava.com/oauth/authorize"
_TOKEN_URL  = "https://www.strava.com/oauth/token"
_API        = "https://www.strava.com/api/v3"


# ── credentials (Client ID / Secret) ──────────────────────────────────────────

def _credentials():
    """Return (client_id, client_secret), running the one-time wizard if absent."""
    load_dotenv(_ENV_FILE)
    cid  = os.getenv("STRAVA_CLIENT_ID")
    csec = os.getenv("STRAVA_CLIENT_SECRET")
    if not cid or not csec:
        cid, csec = _setup_wizard()
    return cid, csec


def _setup_wizard():
    print("\n── Strava setup (one time) ──────────────────────────────────")
    print("Opening the Strava API settings page in your browser…")
    print("Create an application with these settings:")
    print("  • Application Name:  anything (e.g. 'My GPS Viz')")
    print("  • Category:          Data Importer / Visualizer")
    print("  • Website:           http://localhost")
    print("  • Authorization Callback Domain:  localhost   ← must be exactly this")
    print("Then copy the Client ID and Client Secret it shows you.\n")
    webbrowser.open("https://www.strava.com/settings/api")

    cid  = input("Paste your Client ID: ").strip()
    csec = input("Paste your Client Secret: ").strip()
    if not cid or not csec:
        raise RuntimeError("Client ID and Secret are both required.")
    _write_env(cid, csec)
    print("Saved to .env — you won't be asked again.\n")
    return cid, csec


def _write_env(cid, csec):
    """Upsert the two Strava keys into .env without disturbing other lines."""
    lines = []
    if os.path.exists(_ENV_FILE):
        with open(_ENV_FILE) as f:
            lines = f.read().splitlines()
    kv = {"STRAVA_CLIENT_ID": cid, "STRAVA_CLIENT_SECRET": csec}
    seen = set()
    for i, line in enumerate(lines):
        m = re.match(r"\s*([A-Z_]+)\s*=", line)
        if m and m.group(1) in kv:
            lines[i] = f"{m.group(1)}={kv[m.group(1)]}"
            seen.add(m.group(1))
    for k, v in kv.items():
        if k not in seen:
            lines.append(f"{k}={v}")
    with open(_ENV_FILE, "w") as f:
        f.write("\n".join(lines) + "\n")


# ── token cache + OAuth ───────────────────────────────────────────────────────

def _load_token():
    if os.path.exists(_TOKEN_FILE):
        try:
            with open(_TOKEN_FILE) as f:
                return json.load(f)
        except Exception:
            return None
    return None


def _save_token(tok):
    with open(_TOKEN_FILE, "w") as f:
        json.dump(tok, f)
    os.chmod(_TOKEN_FILE, 0o600)   # owner read/write only


class _RedirectHandler(BaseHTTPRequestHandler):
    code = None
    error = None

    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        _RedirectHandler.code  = qs.get("code",  [None])[0]
        _RedirectHandler.error = qs.get("error", [None])[0]
        ok = _RedirectHandler.code is not None
        msg = ("Strava authorization complete — you can close this tab and "
               "return to the terminal.") if ok else \
              f"Authorization failed: {_RedirectHandler.error}"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            f"<html><body style='font-family:-apple-system,sans-serif;"
            f"padding:3rem;text-align:center'><h2>{msg}</h2></body></html>".encode())

    def log_message(self, *args):
        pass   # silence default request logging


def _oauth_authorize(client_id, client_secret):
    params = urlencode({
        "client_id":       client_id,
        "redirect_uri":    REDIRECT_URI,
        "response_type":   "code",
        "scope":           SCOPE,
        "approval_prompt": "auto",
    })
    url = f"{_AUTH_URL}?{params}"
    print("Opening browser to authorize Strava (read-only)…")
    webbrowser.open(url)
    print(f"If it didn't open automatically, visit:\n  {url}\n")

    _RedirectHandler.code = _RedirectHandler.error = None
    server = HTTPServer(("localhost", REDIRECT_PORT), _RedirectHandler)
    server.handle_request()      # blocks until Strava redirects back once
    server.server_close()

    if not _RedirectHandler.code:
        raise RuntimeError(f"Strava authorization failed or was denied "
                           f"({_RedirectHandler.error}).")

    resp = requests.post(_TOKEN_URL, data={
        "client_id":     client_id,
        "client_secret": client_secret,
        "code":          _RedirectHandler.code,
        "grant_type":    "authorization_code",
    }, timeout=20)
    resp.raise_for_status()
    return resp.json()


def _refresh(client_id, client_secret, refresh_token):
    resp = requests.post(_TOKEN_URL, data={
        "client_id":     client_id,
        "client_secret": client_secret,
        "grant_type":    "refresh_token",
        "refresh_token": refresh_token,
    }, timeout=20)
    resp.raise_for_status()
    return resp.json()


def authenticated_token():
    """
    Return a valid access token, doing the one-time browser login if needed and
    silently refreshing when the cached token has expired.
    """
    cid, csec = _credentials()
    tok = _load_token()
    if tok is None:
        tok = _oauth_authorize(cid, csec)
        _save_token(tok)
    elif tok.get("expires_at", 0) <= time.time() + 60:
        tok = _refresh(cid, csec, tok["refresh_token"])
        _save_token(tok)
    return tok["access_token"]


def disconnect():
    """Forget the saved login (revoke fully at Strava → Settings → My Apps)."""
    if os.path.exists(_TOKEN_FILE):
        os.remove(_TOKEN_FILE)
        return True
    return False


# ── activities + streams ──────────────────────────────────────────────────────

def list_activities(token, per_page=30):
    """Most recent activities that have GPS data (indoor/manual are skipped)."""
    resp = requests.get(f"{_API}/athlete/activities",
                        headers={"Authorization": f"Bearer {token}"},
                        params={"per_page": per_page}, timeout=20)
    resp.raise_for_status()
    acts = resp.json()
    return [a for a in acts if (a.get("map") or {}).get("summary_polyline")]


def _fetch_streams(token, activity_id):
    resp = requests.get(f"{_API}/activities/{activity_id}/streams",
                        headers={"Authorization": f"Bearer {token}"},
                        params={"keys": "latlng,altitude,time", "key_by_type": "true"},
                        timeout=20)
    resp.raise_for_status()
    return resp.json()


def streams_to_gpx(streams, start_date_iso, name="Strava Activity"):
    """Build a GPX 1.1 document (the format src/gpx/parser.py reads) from streams."""
    latlng = (streams.get("latlng") or {}).get("data")
    if not latlng:
        raise ValueError("This activity has no GPS track (indoor or manual entry).")
    n     = len(latlng)
    alt   = (streams.get("altitude") or {}).get("data") or [0.0] * n
    tsec  = (streams.get("time") or {}).get("data") or list(range(n))
    start = datetime.fromisoformat(start_date_iso.replace("Z", "+00:00"))

    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<gpx version="1.1" creator="3d-gps-viz" '
           'xmlns="http://www.topografix.com/GPX/1/1">',
           f'  <trk><name>{saxutils.escape(name)}</name><trkseg>']
    for i, (lat, lon) in enumerate(latlng):
        t = (start + timedelta(seconds=tsec[i])).strftime("%Y-%m-%dT%H:%M:%SZ")
        out.append(f'    <trkpt lat="{lat}" lon="{lon}">'
                   f'<ele>{alt[i]}</ele><time>{t}</time></trkpt>')
    out.append('  </trkseg></trk>')
    out.append('</gpx>')
    return "\n".join(out)


def _safe_name(name):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "activity"


def download_activity_gpx(token, activity, dest_dir=None):
    """Fetch an activity's streams, write a GPX file, and return its path."""
    dest_dir = dest_dir or os.path.join(_PROJ_ROOT, "activity-gpx-inputs")
    os.makedirs(dest_dir, exist_ok=True)

    streams = _fetch_streams(token, activity["id"])
    gpx = streams_to_gpx(streams, activity["start_date"],
                         name=activity.get("name", "Strava Activity"))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(dest_dir, f"strava_{_safe_name(activity.get('name',''))}_{stamp}.gpx")
    with open(path, "w") as f:
        f.write(gpx)
    return path
