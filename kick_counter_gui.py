"""
Kick.com Počítadlo aktivity v chatu — GUI verze
Instalace: pip install customtkinter websocket-client curl_cffi requests
Spuštění:  python kick_counter_gui.py

Od připojení počítá, kolik zpráv napsal každý divák v chatu. Kdykoliv
jde (tlačítkem nebo příkazem v chatu) vyhlásit aktuální žebříček, nebo
sledování zastavit a žebříček zresetovat pro novou sérii.
"""

import os, sys, json, time, hashlib, base64, secrets, webbrowser, threading, uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlencode, urlparse, parse_qs
from pathlib import Path
from datetime import datetime

import customtkinter as ctk
import requests
from curl_cffi import requests as cf_requests
import websocket

BUILD_VERSION = "261016.0000"

DEBUG = False

# ── Kick API konstanty (stejné jako KickBot — soutěžní bot) ─────────────────
KICK_AUTH_URL   = "https://id.kick.com/oauth/authorize"
KICK_TOKEN_URL  = "https://id.kick.com/oauth/token"
KICK_API_URL    = "https://api.kick.com/public/v1"
KICK_SCOPES     = "user:read chat:write"
# Chat Kicku běží od 10/2026 přes Centrifugo (dřív Pusher — starý klíč už neplatí).
# Token hosta z web.kick.com, websocket jen s Origin kick.com (jinak 403).
KICK_RT_AUTH_URL = "https://web.kick.com/api/v1/realtime/auth/connection"
KICK_RT_WS_URLS  = ["wss://realtime.us-west-2.platform.kick.com/connection/websocket",
                    "wss://realtime.us-east-1.platform.kick.com/connection/websocket"]
KICK_ORIGIN      = "https://kick.com"
REDIRECT_URI    = "http://localhost:7878/callback"
TOKEN_FILE      = Path("kick_tokens.json")
CONFIG_FILE     = Path("kick_config.json")
BOT_CONFIG_FILE = Path("bot_config.json")

# ── Barvy ────────────────────────────────────────────────────────────────────
KICK_GREEN  = "#53FC18"
DARK_BG     = "#0d0d0d"
PANEL_BG    = "#141414"
CARD_BG     = "#1a1a1a"
BORDER      = "#2a2a2a"
TEXT_DIM    = "#666666"
TEXT_MID    = "#aaaaaa"
TEXT_BRIGHT = "#f0f0f0"
RED_ERR     = "#ff4444"
YELLOW_WARN = "#ffcc00"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("green")


# ════════════════════════════════════════════════════════════════════════════
#  Načítání bot_config.json
# ════════════════════════════════════════════════════════════════════════════
DEFAULT_BOT_CONFIG = {
    "prikazy": {
        "top":   "!top",
        "stop":  "!stop",
    },
    "zpravy": {
        "bot_online":     "🤖 Počítadlo aktivity je online! Počítám od teď všechny zprávy v chatu.",
        "vysledky":       "📊 Nejaktivnější: {seznam}",
        "vysledky_final": "🏁 Sledování ukončeno! Finální pořadí: {seznam}",
        "zadna_aktivita": "❌ Zatím nikdo nic nenapsal.",
    },
    "nastaveni": {
        "top_n": 5,
    },
    "gui": {
        "log_collapsed": True,
    },
}

BOT_CONFIG_TEMPLATE = {
    "_komentare": {
        "popis": "Konfigurační soubor Kick Počítadla aktivity",
        "poznamka_prikazy": "Příkazy jsou case-insensitive: TOP = top = Top",
        "poznamka_zpravy": "Proměnné v {složených závorkách} se automaticky dosadí — nemazat je!",
    },
    "prikazy": {
        "_vysvetleni": "Změň hodnoty na cokoliv chceš. Příklad: místo !top napiš ŽEBŘÍČEK",
        "top":   "!top",
        "stop":  "!stop",
    },
    "zpravy": {
        "_vysvetleni": "Texty které bot píše do chatu. Proměnné v {závorkách} jsou povinné.",
        "bot_online":     "🤖 Počítadlo aktivity je online! Počítám od teď všechny zprávy v chatu.",
        "vysledky":       "📊 Nejaktivnější: {seznam}",
        "vysledky_final": "🏁 Sledování ukončeno! Finální pořadí: {seznam}",
        "zadna_aktivita": "❌ Zatím nikdo nic nenapsal.",
    },
    "_napoveda_promennych": {
        "popis": "Tyto proměnné musí zůstat v příslušných zprávách:",
        "vysledky":       "{seznam} = automatický seznam Top N (🥇 user: 42 zpráv ...)",
        "vysledky_final": "{seznam} = automatický seznam Top N (🥇 user: 42 zpráv ...)",
    },
    "nastaveni": {
        "_vysvetleni": "top_n = kolik nejaktivnějších diváků se vypisuje do chatu a do {seznam}.",
        "top_n": 5,
    },
    "gui": {
        "_vysvetleni":  "Nastavení vzhledu a chování rozhraní.",
        "log_collapsed": True,
    },
}


def load_bot_config() -> dict:
    """Načte bot_config.json. Při prvním spuštění vytvoří soubor s výchozími hodnotami."""
    import copy
    cfg = copy.deepcopy(DEFAULT_BOT_CONFIG)

    if not BOT_CONFIG_FILE.exists():
        try:
            BOT_CONFIG_FILE.write_text(
                json.dumps(BOT_CONFIG_TEMPLATE, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            print(f"[WARN] Nelze vytvořit bot_config.json: {e}")
        return cfg

    try:
        user = json.loads(BOT_CONFIG_FILE.read_text(encoding="utf-8"))
        for section in ("prikazy", "zpravy"):
            if section in user and isinstance(user[section], dict):
                for k, v in user[section].items():
                    if not k.startswith("_") and isinstance(v, str):
                        cfg[section][k] = v
        for section in ("nastaveni", "gui"):
            if section in user and isinstance(user[section], dict):
                for k, v in user[section].items():
                    if not k.startswith("_") and k in cfg[section]:
                        cfg[section][k] = v

        changed = False
        for section, tpl_vals in BOT_CONFIG_TEMPLATE.items():
            if not isinstance(tpl_vals, dict):
                continue
            if section not in user:
                user[section] = {}
                changed = True
            for k, v in tpl_vals.items():
                if k not in user[section]:
                    user[section][k] = v
                    changed = True
        if changed:
            BOT_CONFIG_FILE.write_text(
                json.dumps(user, ensure_ascii=False, indent=2), encoding="utf-8")
            print("[INFO] bot_config.json aktualizován o nové klíče")
    except json.JSONDecodeError as e:
        print(f"[ERROR] bot_config.json má chybu: {e} — používám výchozí hodnoty")
    except Exception as e:
        print(f"[WARN] Nelze načíst bot_config.json: {e} — používám výchozí hodnoty")

    return cfg


# ════════════════════════════════════════════════════════════════════════════
#  Logika bota
# ════════════════════════════════════════════════════════════════════════════
class CounterEngine:
    def __init__(self, log_cb, status_cb, counts_cb, result_cb=None):
        self.log_cb     = log_cb
        self.status_cb  = status_cb
        self.counts_cb  = counts_cb
        self.result_cb  = result_cb or (lambda sorted_counts, final: None)

        self.counts          = {}   # username -> počet zpráv od připojení/resetu
        self.started_at      = None
        self.broadcaster_id  = 0
        self.chatroom_id     = 0
        self.ws              = None
        self.ws_thread       = None
        self.running         = False

        # chat přes Centrifugo
        self.rt_client_id    = ""
        self._rt_id          = 0
        self._refresh_timer  = None
        self._want_connected = False
        self._first_connect  = True
        self._on_connected   = lambda ok: None
        self._creds          = ("", "")

        self.tokens = {"access_token": "", "refresh_token": "", "expires_at": 0}
        self._load_tokens()
        self.bcfg = load_bot_config()

    def reload_bot_config(self):
        self.bcfg = load_bot_config()
        self.log("✅ bot_config.json znovu načten.", "success")

    # ── Přístup ke konfiguraci ───────────────────────────────────────────────
    def _cmd(self, key: str) -> str:
        return self.bcfg["prikazy"].get(key, f"!{key}").strip().lower()

    def _top_n(self) -> int:
        try:
            return max(1, int(self.bcfg["nastaveni"].get("top_n", 5)))
        except (TypeError, ValueError):
            return 5

    def _seznam(self, n=None) -> str:
        n = n or self._top_n()
        top = sorted(self.counts.items(), key=lambda x: (-x[1], x[0].lower()))[:n]
        medals = ["🥇", "🥈", "🥉"]
        parts = []
        for i, (user, cnt) in enumerate(top):
            prefix = medals[i] if i < 3 else f"{i+1}."
            slovo = "zpráva" if cnt == 1 else ("zprávy" if 2 <= cnt <= 4 else "zpráv")
            parts.append(f"{prefix} {user}: {cnt} {slovo}")
        return " | ".join(parts)

    def _msg(self, key: str, **kwargs) -> str:
        template = self.bcfg["zpravy"].get(key, f"[{key}]")
        try:
            return template.format(**kwargs)
        except (KeyError, ValueError):
            return template

    # ── Tokeny ───────────────────────────────────────────────────────────────
    def _load_tokens(self):
        if TOKEN_FILE.exists():
            try:
                self.tokens.update(json.loads(TOKEN_FILE.read_text()))
            except Exception:
                pass

    def _save_tokens(self):
        TOKEN_FILE.write_text(json.dumps(self.tokens, indent=2))

    def _token_valid(self):
        return bool(self.tokens["access_token"]) and time.time() < self.tokens["expires_at"] - 60

    # ── PKCE ─────────────────────────────────────────────────────────────────
    def _pkce_pair(self):
        v = secrets.token_urlsafe(64)
        d = hashlib.sha256(v.encode()).digest()
        c = base64.urlsafe_b64encode(d).rstrip(b"=").decode()
        return v, c

    # ── OAuth ─────────────────────────────────────────────────────────────────
    def do_oauth(self, client_id, client_secret, on_done):
        def run():
            self.tokens = {"access_token": "", "refresh_token": "", "expires_at": 0}

            verifier, challenge = self._pkce_pair()
            params = {
                "response_type": "code", "client_id": client_id,
                "redirect_uri": REDIRECT_URI, "scope": KICK_SCOPES,
                "code_challenge": challenge, "code_challenge_method": "S256",
                "state": secrets.token_urlsafe(16),
            }
            auth_url = f"{KICK_AUTH_URL}?{urlencode(params)}"
            self.log("Otevírám prohlížeč pro přihlášení ...", "info")
            webbrowser.open(auth_url)

            result = {}

            class H(BaseHTTPRequestHandler):
                def do_GET(s):
                    parsed = urlparse(s.path)
                    qs = parse_qs(parsed.query)
                    result["raw"]        = parsed.query
                    result["code"]       = qs.get("code",  [""])[0]
                    result["error"]      = qs.get("error", [""])[0]
                    result["error_desc"] = qs.get("error_description", [""])[0]
                    s.send_response(200)
                    s.send_header("Content-Type", "text/html; charset=utf-8")
                    s.end_headers()
                    if result["code"]:
                        s.wfile.write(b"<html><body style='font-family:sans-serif;"
                            b"text-align:center;padding:60px;background:#0d0d0d;color:#53FC18'>"
                            b"<h2>Bot autorizovan!</h2>"
                            b"<p style='color:#aaa'>Toto okno muzete zavrit.</p>"
                            b"</body></html>")
                    else:
                        s.wfile.write(b"<html><body style='font-family:sans-serif;"
                            b"text-align:center;padding:60px;background:#0d0d0d;color:#ff4444'>"
                            b"<h2>Autorizace selhala</h2>"
                            b"<p style='color:#aaa'>Zkuste to znovu v aplikaci.</p>"
                            b"</body></html>")
                def log_message(s, *a): pass

            try:
                port = int(REDIRECT_URI.split(":")[-1].split("/")[0])
                srv = HTTPServer(("localhost", port), H)
                srv.timeout = 120
                self.log("Čekám na potvrzení v prohlížeči (max 2 min) ...", "info")
                srv.handle_request()
            except Exception as e:
                self.log(f"Chyba callback serveru: {e}", "error")
                on_done(False)
                return

            if result.get("error"):
                self.log(f"Kick vrátil chybu: {result['error']} — {result.get('error_desc','')}", "error")
                on_done(False)
                return

            code = result.get("code", "")
            if not code:
                self.log(
                    "Kick neposlal autorizační kód! "
                    "Zkontroluj Redirect URI na kick.com/settings/developer — "
                    "musí být PŘESNĚ: http://localhost:7878/callback", "error")
                on_done(False)
                return

            try:
                resp = requests.post(KICK_TOKEN_URL, data={
                    "grant_type":    "authorization_code",
                    "client_id":     client_id,
                    "client_secret": client_secret,
                    "redirect_uri":  REDIRECT_URI,
                    "code":          code,
                    "code_verifier": verifier,
                }, timeout=15)
                resp.raise_for_status()
                d = resp.json()
                self.tokens["access_token"]  = d["access_token"]
                self.tokens["refresh_token"] = d.get("refresh_token", "")
                self.tokens["expires_at"]    = time.time() + d.get("expires_in", 3600)
                self._save_tokens()
                self.log("✅ Přihlášení úspěšné! Token uložen.", "success")
                on_done(True)
            except Exception as e:
                self.log(f"Chyba výměny tokenu: {e}", "error")
                on_done(False)

        threading.Thread(target=run, daemon=True).start()

    def refresh_token(self, client_id, client_secret):
        if not self.tokens["refresh_token"]:
            return False
        try:
            resp = requests.post(KICK_TOKEN_URL, data={
                "grant_type": "refresh_token", "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": self.tokens["refresh_token"],
            }, timeout=15)
            if not resp.ok:
                return False
            d = resp.json()
            self.tokens["access_token"]  = d["access_token"]
            self.tokens["refresh_token"] = d.get("refresh_token", self.tokens["refresh_token"])
            self.tokens["expires_at"]    = time.time() + d.get("expires_in", 3600)
            self._save_tokens()
            self.log("Token automaticky obnoven.", "info")
            return True
        except Exception:
            return False

    def ensure_token(self, client_id, client_secret):
        if self._token_valid():
            return True
        return self.refresh_token(client_id, client_secret)

    # ── Kick REST ─────────────────────────────────────────────────────────────
    def get_channel_info(self, slug):
        try:
            r = cf_requests.get(f"https://kick.com/api/v2/channels/{slug}",
                                impersonate="chrome124", timeout=15)
            r.raise_for_status()
            d = r.json()
            return d["id"], d["chatroom"]["id"]
        except Exception as e:
            self.log(f"Kanál nenalezen: {e}", "error")
            return None, None

    def send_chat(self, message, client_id, client_secret):
        if not self.broadcaster_id:
            return
        if not self.ensure_token(client_id, client_secret):
            self.log("Nelze poslat zprávu — chybí token.", "error")
            return
        try:
            payload = {
                "broadcaster_user_id": self.broadcaster_id,
                "content": message,
                "type": "bot",
            }
            resp = requests.post(f"{KICK_API_URL}/chat",
                headers={"Authorization": f"Bearer {self.tokens['access_token']}",
                         "Content-Type": "application/json"},
                json=payload,
                timeout=10)
            if resp.ok:
                self.log(f"[Chat ✓] {message}", "success")
            else:
                self.log(f"Chat API chyba: {resp.status_code} | {resp.text}", "error")
        except Exception as e:
            self.log(f"Chyba odesílání: {e}", "error")

    # ── Počítání zpráv ────────────────────────────────────────────────────────
    def is_moderator(self, sender):
        badges = sender.get("identity", {}).get("badges", [])
        return any(b.get("type", "").lower() in
                   {"moderator", "broadcaster", "editor", "og"} for b in badges)

    def handle_message(self, sender, content, client_id, client_secret):
        username = sender.get("username", "???")
        text  = content.strip()
        lower = text.lower()

        # Zpráva se počítá VŽDY — i moderátorské příkazy se počítají jako zpráva.
        self.counts[username] = self.counts.get(username, 0) + 1
        self.log(f"  {username} → celkem {self.counts[username]}", "dim")
        self.counts_cb(sorted(self.counts.items(), key=lambda x: (-x[1], x[0].lower())))

        if self.is_moderator(sender):
            cmd_top  = self._cmd("top")
            cmd_stop = self._cmd("stop")

            if lower == cmd_top:
                self._announce(final=False, client_id=client_id, client_secret=client_secret)
                return

            if lower == cmd_stop:
                self._announce(final=True, client_id=client_id, client_secret=client_secret)
                self._reset()
                return

    def _announce(self, final, client_id, client_secret):
        if not self.counts:
            self.send_chat(self._msg("zadna_aktivita"), client_id, client_secret)
            return
        seznam = self._seznam()
        key = "vysledky_final" if final else "vysledky"
        self.send_chat(self._msg(key, seznam=seznam), client_id, client_secret)
        self.log(f"━━━ {'Finální' if final else 'Průběžný'} žebříček: {seznam} ━━━", "success")

    def _reset(self):
        sorted_counts = sorted(self.counts.items(), key=lambda x: (-x[1], x[0].lower()))
        self.result_cb(sorted_counts, True)
        self.counts = {}
        self.started_at = time.time()
        self.counts_cb([])
        self.status_cb("reset")

    # ── Panel ovládání (zrcadlí chatové příkazy) ─────────────────────────────
    def panel_announce(self, client_id, client_secret):
        self._announce(final=False, client_id=client_id, client_secret=client_secret)

    def panel_stop(self, client_id, client_secret):
        self._announce(final=True, client_id=client_id, client_secret=client_secret)
        self._reset()

    # ── Chat přes Centrifugo ──────────────────────────────────────────────────
    def _rt_token(self):
        """Token hosta pro realtime (Centrifugo). Platí ~45 min, pak se obnovuje."""
        r = cf_requests.post(KICK_RT_AUTH_URL, impersonate="chrome124", timeout=15,
                             headers={"Origin": KICK_ORIGIN, "Referer": KICK_ORIGIN + "/",
                                      "Content-Type": "application/json"},
                             data=json.dumps({"client_id": self.rt_client_id}))
        r.raise_for_status()
        return r.json()["data"]["token"]

    def _rt_send(self, payload):
        self._rt_id += 1
        payload["id"] = self._rt_id
        self.ws.send(json.dumps(payload))

    def _schedule_refresh(self, ttl):
        """Obnoví token minutu před vypršením (Centrifugo jinak spojení ukončí)."""
        if self._refresh_timer:
            self._refresh_timer.cancel()
        if not ttl:
            return
        def refresh():
            try:
                self._rt_send({"refresh": {"token": self._rt_token()}})
            except Exception as e:
                self.log(f"Obnova tokenu chatu selhala: {e}", "warn")
        self._refresh_timer = threading.Timer(max(ttl - 60, 30), refresh)
        self._refresh_timer.daemon = True
        self._refresh_timer.start()

    def connect(self, slug, client_id, client_secret, on_connected):
        bid, cid = self.get_channel_info(slug)
        if not bid:
            on_connected(False); return
        self.broadcaster_id  = bid
        self.chatroom_id     = cid
        self.rt_client_id    = str(uuid.uuid4())
        self._want_connected = True
        self._first_connect  = True
        self._on_connected   = on_connected
        self._creds          = (client_id, client_secret)
        self._open_socket(0)

    def _open_socket(self, url_idx):
        client_id, client_secret = self._creds
        cid = self.chatroom_id
        try:
            token = self._rt_token()
        except Exception as e:
            self.log(f"Nelze získat token chatu: {e}", "error")
            self._retry_later(url_idx)
            return
        self._rt_id = 0
        url = KICK_RT_WS_URLS[url_idx % len(KICK_RT_WS_URLS)]

        def on_open(ws):
            self._rt_send({"connect": {"token": token, "name": "js"}})

        def handle(frame):
            if frame.get("connect") is not None and "error" not in frame:
                self._schedule_refresh(frame["connect"].get("ttl"))
                self._rt_send({"subscribe": {"channel": f"chatrooms.{cid}.v2", "flag": 1}})
                return
            if "error" in frame:
                self.log(f"Chat (Centrifugo) chyba: {frame['error']}", "error")
                return
            if frame.get("subscribe") is not None:
                self.running = True
                self.status_cb("counting")
                if self._first_connect:
                    self._first_connect = False
                    self.started_at = time.time()
                    self.log("✅ Připojeno k chatu! Počítám zprávy od teď.", "success")
                    self.send_chat(self._msg("bot_online"), client_id, client_secret)
                    self._on_connected(True)
                else:
                    self.log("✅ Znovu připojeno k chatu.", "success")
                return
            if frame.get("refresh") is not None:
                self._schedule_refresh(frame["refresh"].get("ttl"))
                return
            pub = (frame.get("push") or {}).get("pub")
            if not pub:
                return
            data = pub.get("data") or {}
            if data.get("event") != "App\\Events\\ChatMessageEvent":
                return
            rd = data.get("data", "{}")
            d  = json.loads(rd) if isinstance(rd, str) else rd
            sender, content = d.get("sender", {}), d.get("content", "")
            if sender and content:
                self.handle_message(sender, content, client_id, client_secret)

        def on_msg(ws, raw):
            # Centrifugo může poslat víc zpráv v jednom rámci (oddělené novým řádkem).
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                if line == "{}":            # ping serveru (každých 25 s) → pong
                    ws.send("{}")
                    continue
                try:
                    frame = json.loads(line)
                except Exception:
                    continue
                try:
                    handle(frame)
                except Exception as e:
                    self.log(f"Chyba zpracování zprávy: {e}", "error")

        def on_err(ws, err):
            self.log(f"WS chyba: {err}", "error")

        def on_close(ws, code, msg):
            self.running = False
            if self._refresh_timer:
                self._refresh_timer.cancel()
            if self._want_connected:
                self.log(f"Spojení s chatem přerušeno ({code}), zkouším znovu…", "warn")
                self.status_cb("disconnected")
                self._retry_later(url_idx + 1)
            else:
                self.log("Odpojeno od chatu.", "warn")
                self.status_cb("disconnected")

        self.ws = websocket.WebSocketApp(url, header={"User-Agent": "Mozilla/5.0"},
            on_open=on_open, on_message=on_msg,
            on_error=on_err, on_close=on_close)
        self.ws_thread = threading.Thread(
            target=lambda: self.ws.run_forever(origin=KICK_ORIGIN, ping_interval=0),
            daemon=True)
        self.ws_thread.start()

    def _retry_later(self, url_idx, delay=5):
        if not self._want_connected:
            if self._first_connect:
                self._on_connected(False)
            return
        if self._first_connect and url_idx >= len(KICK_RT_WS_URLS):
            # první připojení se nepovedlo na žádný server → ohlásit chybu, nezkoušet donekonečna
            self._want_connected = False
            self._on_connected(False)
            return
        t = threading.Timer(delay, lambda: self._open_socket(url_idx))
        t.daemon = True
        t.start()

    def disconnect(self):
        self._want_connected = False
        if self._refresh_timer:
            self._refresh_timer.cancel()
        if self.ws:
            self.ws.close()
        self.running = False

    def log(self, msg, level="info"):
        self.log_cb(msg, level)


# ════════════════════════════════════════════════════════════════════════════
#  GUI
# ════════════════════════════════════════════════════════════════════════════
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"Kick Počítadlo aktivity v{BUILD_VERSION}")
        self.geometry("1200x700")
        self.minsize(1000, 600)
        self.configure(fg_color=DARK_BG)

        self._load_config()
        self.engine = CounterEngine(
            log_cb    = self._append_log,
            status_cb = self._set_status,
            counts_cb = self._update_counts,
            result_cb = self._update_results,
        )
        if self.engine._token_valid():
            self._token_status = "valid"
        elif self.engine.tokens.get("refresh_token"):
            self._token_status = "refresh"
        else:
            self._token_status = "none"

        self._build_ui()
        self._refresh_token_label()
        if self.engine.bcfg.get("gui", {}).get("log_collapsed", True):
            self.after(100, self._toggle_log)

    # ── Config ────────────────────────────────────────────────────────────────
    def _load_config(self):
        self._cfg = {"client_id": "", "client_secret": "", "channel": ""}
        if CONFIG_FILE.exists():
            try:
                self._cfg.update(json.loads(CONFIG_FILE.read_text()))
            except Exception:
                pass

    def _save_config(self):
        self._cfg["client_id"]     = self.entry_cid.get().strip()
        self._cfg["client_secret"] = self.entry_csecret.get().strip()
        self._cfg["channel"]       = self.entry_channel.get().strip()
        CONFIG_FILE.write_text(json.dumps(self._cfg, indent=2))

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(2, weight=0)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self._build_main()
        self._build_right_panel()

    def _build_sidebar(self):
        outer = ctk.CTkFrame(self, fg_color=PANEL_BG, corner_radius=0, width=280)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.grid_propagate(False)
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(0, weight=1)

        sb = ctk.CTkScrollableFrame(outer, fg_color=PANEL_BG, corner_radius=0,
                                     scrollbar_button_color=BORDER,
                                     scrollbar_fg_color=PANEL_BG)
        sb.grid(row=0, column=0, sticky="nsew")
        sb.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(sb, text="📊 KickCounter", font=ctk.CTkFont("", 22, "bold"),
                     text_color=KICK_GREEN).grid(row=0, column=0, padx=24, pady=(28, 4), sticky="w")
        ctk.CTkLabel(sb, text="Počítadlo aktivity v chatu", font=ctk.CTkFont("", 12),
                     text_color=TEXT_DIM).grid(row=1, column=0, padx=24, pady=(0, 24), sticky="w")
        ctk.CTkFrame(sb, height=1, fg_color=BORDER).grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 20))

        self._section(sb, "NASTAVENÍ", 3)
        self._label(sb, "Client ID", 4)
        self.entry_cid = self._entry(sb, 5, self._cfg["client_id"])
        self._label(sb, "Client Secret", 6)
        self.entry_csecret = self._entry(sb, 7, self._cfg["client_secret"], show="•")
        self._label(sb, "Název kanálu (slug)", 8)
        self.entry_channel = self._entry(sb, 9, self._cfg["channel"])

        self.lbl_token = ctk.CTkLabel(sb, text="", font=ctk.CTkFont("", 11),
                                      wraplength=220, justify="left")
        self.lbl_token.grid(row=10, column=0, padx=16, pady=(8, 4), sticky="w")

        auth_row = ctk.CTkFrame(sb, fg_color="transparent")
        auth_row.grid(row=11, column=0, padx=16, pady=(4, 16), sticky="ew")
        auth_row.grid_columnconfigure(0, weight=1)
        auth_row.grid_columnconfigure(1, weight=0)

        self.btn_auth = ctk.CTkButton(auth_row, text="🔑  Přihlásit bota",
            fg_color="#1e3a1e", hover_color="#2a4f2a", text_color=KICK_GREEN,
            border_color=KICK_GREEN, border_width=1,
            font=ctk.CTkFont("", 12, "bold"), height=38, corner_radius=8,
            command=self._do_auth)
        self.btn_auth.grid(row=0, column=0, padx=(0, 4), sticky="ew")

        self.btn_reset_login = ctk.CTkButton(auth_row, text="🗑",
            fg_color="#2a1a1a", hover_color="#3a2020", text_color="#ff6666",
            border_color="#ff4444", border_width=1,
            font=ctk.CTkFont("", 14), width=38, height=38, corner_radius=8,
            command=self._do_login_reset)
        self.btn_reset_login.grid(row=0, column=1, sticky="e")

        ctk.CTkFrame(sb, height=1, fg_color=BORDER).grid(row=12, column=0, sticky="ew", padx=16, pady=(0, 20))

        self._section(sb, "PŘIPOJENÍ", 13)
        self.btn_connect = ctk.CTkButton(sb, text="▶  Spustit počítání",
            fg_color=KICK_GREEN, hover_color="#45d614", text_color="#000",
            font=ctk.CTkFont("", 13, "bold"), height=44, corner_radius=8,
            command=self._do_connect)
        self.btn_connect.grid(row=14, column=0, padx=16, pady=(8, 6), sticky="ew")

        self.btn_disconnect = ctk.CTkButton(sb, text="⏹  Odpojit",
            fg_color="#2a1a1a", hover_color="#3a2020", text_color="#ff6666",
            border_color="#ff4444", border_width=1,
            font=ctk.CTkFont("", 12), height=36, corner_radius=8,
            state="disabled", command=self._do_disconnect)
        self.btn_disconnect.grid(row=15, column=0, padx=16, pady=(0, 8), sticky="ew")

        self.lbl_status = ctk.CTkLabel(sb, text="⚪ Odpojeno",
            font=ctk.CTkFont("", 11), text_color=TEXT_DIM)
        self.lbl_status.grid(row=16, column=0, padx=16, pady=(0, 8), sticky="w")

        ctk.CTkFrame(sb, height=1, fg_color=BORDER).grid(row=17, column=0, sticky="ew", padx=16, pady=(4, 12))

        self._section(sb, "KONFIGURACE BOTA", 18)
        self.btn_edit_cfg = ctk.CTkButton(sb, text="✏️  Upravit texty a příkazy",
            fg_color="transparent", hover_color=CARD_BG,
            text_color=TEXT_MID, border_color=BORDER, border_width=1,
            font=ctk.CTkFont("", 11), height=34, corner_radius=8,
            command=self._open_bot_config)
        self.btn_edit_cfg.grid(row=19, column=0, padx=16, pady=(4, 4), sticky="ew")

        self.btn_reload_cfg = ctk.CTkButton(sb, text="🔄  Načíst změny konfigurace",
            fg_color="transparent", hover_color=CARD_BG,
            text_color=TEXT_DIM, border_color=BORDER, border_width=1,
            font=ctk.CTkFont("", 11), height=30, corner_radius=8,
            command=self._reload_bot_config)
        self.btn_reload_cfg.grid(row=20, column=0, padx=16, pady=(0, 8), sticky="ew")

        ctk.CTkButton(sb, text="❓ Jak získat Client ID?",
            fg_color="transparent", hover_color=CARD_BG, text_color=TEXT_DIM,
            font=ctk.CTkFont("", 11), height=28, anchor="w",
            command=lambda: webbrowser.open("https://kick.com/settings/developer")
        ).grid(row=22, column=0, padx=16, pady=(0, 16), sticky="ew")

    def _build_main(self):
        main = ctk.CTkFrame(self, fg_color=DARK_BG, corner_radius=0)
        main.grid(row=0, column=1, sticky="nsew", padx=(1, 0))
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=2)
        main.grid_rowconfigure(3, weight=1)

        self.banner = ctk.CTkFrame(main, fg_color=CARD_BG, corner_radius=12, height=64)
        self.banner.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 12))
        self.banner.grid_propagate(False)
        self.banner.grid_columnconfigure(1, weight=1)
        self.banner_icon = ctk.CTkLabel(self.banner, text="⚪", font=ctk.CTkFont("", 28))
        self.banner_icon.grid(row=0, column=0, padx=(20, 12), pady=12)
        self.banner_text = ctk.CTkLabel(self.banner, text="Bot je odpojený",
            font=ctk.CTkFont("", 16, "bold"), text_color=TEXT_MID, anchor="w")
        self.banner_text.grid(row=0, column=1, sticky="w")
        self.banner_sub = ctk.CTkLabel(self.banner,
            text=self._disconnected_sub(),
            font=ctk.CTkFont("", 11), text_color=TEXT_DIM, anchor="e")
        self.banner_sub.grid(row=0, column=2, padx=20, sticky="e")

        gf = ctk.CTkFrame(main, fg_color=CARD_BG, corner_radius=12)
        gf.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 12))
        gf.grid_columnconfigure(0, weight=1)
        gf.grid_rowconfigure(1, weight=1)
        hdr = ctk.CTkFrame(gf, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4))
        hdr.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(hdr, text="Aktivita v chatu (živě)",
                     font=ctk.CTkFont("", 14, "bold"), text_color=TEXT_BRIGHT
                     ).grid(row=0, column=0, sticky="w")
        self.lbl_count = ctk.CTkLabel(hdr, text="0 diváků",
                     font=ctk.CTkFont("", 11), text_color=TEXT_DIM)
        self.lbl_count.grid(row=0, column=1, sticky="e")
        self.guess_scroll = ctk.CTkScrollableFrame(gf, fg_color="transparent",
                                                    scrollbar_button_color=BORDER)
        self.guess_scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.guess_scroll.grid_columnconfigure(0, weight=1)
        self._count_rows = []
        self.count_placeholder = ctk.CTkLabel(self.guess_scroll,
            text="Zatím žádná aktivita.\nPřipoj se na kanál a čekej na první zprávy.",
            font=ctk.CTkFont("", 12), text_color=TEXT_DIM, justify="center")
        self.count_placeholder.grid(row=0, column=0, columnspan=2, pady=30)

        self._log_main      = main
        self._log_collapsed = False
        lf = ctk.CTkFrame(main, fg_color=CARD_BG, corner_radius=12)
        lf.grid(row=3, column=0, sticky="nsew", padx=20, pady=(0, 20))
        lf.grid_columnconfigure(0, weight=1)
        lf.grid_rowconfigure(1, weight=1)
        self._log_frame = lf
        lhdr = ctk.CTkFrame(lf, fg_color="transparent")
        lhdr.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4))
        lhdr.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(lhdr, text="Protokol",
                     font=ctk.CTkFont("", 14, "bold"), text_color=TEXT_BRIGHT
                     ).grid(row=0, column=0, sticky="w")
        self.btn_clear_log = ctk.CTkButton(lhdr, text="Vymazat", width=70, height=24,
            fg_color="transparent", hover_color=BORDER, text_color=TEXT_DIM,
            font=ctk.CTkFont("", 11), command=self._clear_log)
        self.btn_clear_log.grid(row=0, column=1, sticky="e")
        self.btn_log_toggle = ctk.CTkButton(lhdr, text="▼", width=28, height=24,
            fg_color="transparent", hover_color=BORDER, text_color=TEXT_DIM,
            font=ctk.CTkFont("", 13), command=self._toggle_log)
        self.btn_log_toggle.grid(row=0, column=2, sticky="e", padx=(4, 0))
        self.log_box = ctk.CTkTextbox(lf, fg_color="transparent",
            font=ctk.CTkFont("Courier New", 11), text_color=TEXT_MID,
            wrap="word", scrollbar_button_color=BORDER)
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.log_box.configure(state="disabled")

    def _build_right_panel(self):
        rp = ctk.CTkFrame(self, fg_color=PANEL_BG, corner_radius=0, width=240)
        rp.grid(row=0, column=2, sticky="nsew", padx=(1, 0))
        rp.grid_propagate(False)
        rp.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(rp, text="🎮 Ovládání", font=ctk.CTkFont("", 18, "bold"),
                     text_color=TEXT_BRIGHT).grid(row=0, column=0, padx=20, pady=(24, 4), sticky="w")
        ctk.CTkFrame(rp, height=1, fg_color=BORDER).grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 16))

        self._section(rp, "ŽEBŘÍČEK", 2)

        self.btn_panel_top = ctk.CTkButton(rp, text="📊  Vyhlásit průběžný Top",
            fg_color="#1e2a3a", hover_color="#2a3a4f", text_color="#4da6ff",
            border_color="#2a4a6a", border_width=1,
            font=ctk.CTkFont("", 12, "bold"), height=40, corner_radius=8,
            command=self._panel_top)
        self.btn_panel_top.grid(row=3, column=0, padx=16, pady=(4, 6), sticky="ew")

        self.btn_panel_stop = ctk.CTkButton(rp, text="🏁  Vyhlásit a resetovat",
            fg_color=KICK_GREEN, hover_color="#45d614", text_color="#000",
            font=ctk.CTkFont("", 13, "bold"), height=44, corner_radius=8,
            command=self._panel_stop)
        self.btn_panel_stop.grid(row=4, column=0, padx=16, pady=(0, 4), sticky="ew")
        ctk.CTkLabel(rp, text="Pošle finální žebříček do chatu\na vynuluje počítadlo pro novou sérii.",
                     font=ctk.CTkFont("", 10), text_color=TEXT_DIM, justify="left"
                     ).grid(row=5, column=0, padx=16, pady=(0, 16), sticky="w")

        ctk.CTkFrame(rp, height=1, fg_color=BORDER).grid(row=6, column=0, sticky="ew", padx=16, pady=(0, 16))

        self._section(rp, "CHATOVÉ PŘÍKAZY (moderátor)", 7)
        ctk.CTkLabel(rp,
            text="!top   — průběžný žebříček\n!stop  — finální + reset",
            font=ctk.CTkFont("Courier New", 11), text_color=TEXT_MID, justify="left"
            ).grid(row=8, column=0, padx=16, pady=(0, 16), sticky="w")

        ctk.CTkFrame(rp, height=1, fg_color=BORDER).grid(row=9, column=0, sticky="ew", padx=16, pady=(0, 16))

        self._section(rp, "POSLEDNÍ FINÁLNÍ VÝSLEDKY", 10)
        self.results_frame = ctk.CTkFrame(rp, fg_color="transparent")
        self.results_frame.grid(row=11, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.results_frame.grid_columnconfigure(0, weight=1)

        self.lbl_results_placeholder = ctk.CTkLabel(self.results_frame,
            text="Zatím nic — klikni na\n'Vyhlásit a resetovat'.",
            font=ctk.CTkFont("", 11), text_color=TEXT_DIM, justify="center")
        self.lbl_results_placeholder.grid(row=0, column=0, pady=16)

        self._result_rows = []
        rp.grid_rowconfigure(12, weight=1)

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _section(self, parent, text, row):
        ctk.CTkLabel(parent, text=text, font=ctk.CTkFont("", 10, "bold"),
                     text_color=TEXT_DIM).grid(row=row, column=0, padx=16, pady=(0, 4), sticky="w")

    def _label(self, parent, text, row):
        ctk.CTkLabel(parent, text=text, font=ctk.CTkFont("", 12),
                     text_color=TEXT_MID).grid(row=row, column=0, padx=16, pady=(8, 2), sticky="w")

    def _entry(self, parent, row, default="", show=""):
        e = ctk.CTkEntry(parent, fg_color=CARD_BG, border_color=BORDER,
                         text_color=TEXT_BRIGHT, font=ctk.CTkFont("", 12),
                         height=34, corner_radius=6, show=show)
        e.insert(0, default)
        e.grid(row=row, column=0, padx=16, pady=(0, 4), sticky="ew")
        return e

    def _refresh_token_label(self):
        if self._token_status == "valid":
            self.lbl_token.configure(text="✅ Bot je přihlášen", text_color=KICK_GREEN)
        elif self._token_status == "refresh":
            self.lbl_token.configure(text="🔄 Token bude obnoven automaticky", text_color=YELLOW_WARN)
        else:
            self.lbl_token.configure(text="⚠️ Bot není přihlášen — klikni níže", text_color=YELLOW_WARN)

    def _disconnected_sub(self) -> str:
        if self._token_status in ("valid", "refresh"):
            return "Klikni na Spustit počítání"
        return "Klikni na Přihlásit bota"

    # ── Akce tlačítek ─────────────────────────────────────────────────────────
    def _do_auth(self):
        cid = self.entry_cid.get().strip()
        cs  = self.entry_csecret.get().strip()
        if not cid or not cs:
            self._append_log("Vyplň Client ID a Client Secret!", "error"); return
        self._save_config()
        self.btn_auth.configure(state="disabled", text="⏳ Čekám na prohlížeč ...")
        def done(ok):
            self.after(0, lambda: self.btn_auth.configure(state="normal", text="🔑  Přihlásit bota"))
            if ok:
                self._token_status = "valid"
                self.after(0, self._refresh_token_label)
        self.engine.do_oauth(cid, cs, done)

    def _do_login_reset(self):
        self.engine.tokens = {"access_token": "", "refresh_token": "", "expires_at": 0}
        if TOKEN_FILE.exists():
            TOKEN_FILE.unlink()
        self._token_status = "none"
        self._refresh_token_label()
        self._append_log("Přihlášení zrušeno.", "warn")

    def _do_connect(self):
        cid     = self.entry_cid.get().strip()
        cs      = self.entry_csecret.get().strip()
        channel = self.entry_channel.get().strip()
        if not cid or not cs or not channel:
            self._append_log("Vyplň Client ID, Client Secret i kanál!", "error"); return
        if not self.engine._token_valid() and not self.engine.tokens.get("refresh_token"):
            self._append_log("Nejdřív se přihlas tlačítkem 'Přihlásit bota'.", "error"); return
        self._save_config()
        self.btn_connect.configure(state="disabled", text="⏳ Připojuji ...")

        def done(ok):
            def apply():
                if ok:
                    self.btn_connect.configure(state="disabled", text="▶  Spustit počítání")
                    self.btn_disconnect.configure(state="normal")
                    self.banner_icon.configure(text="🟢")
                    self.banner_text.configure(text=f"Počítám zprávy na kanálu {channel}", text_color=KICK_GREEN)
                    self.banner_sub.configure(text="")
                else:
                    self.btn_connect.configure(state="normal", text="▶  Spustit počítání")
            self.after(0, apply)
        self.engine.connect(channel, cid, cs, done)

    def _do_disconnect(self):
        self.engine.disconnect()
        self.btn_connect.configure(state="normal", text="▶  Spustit počítání")
        self.btn_disconnect.configure(state="disabled")
        self.banner_icon.configure(text="⚪")
        self.banner_text.configure(text="Bot je odpojený", text_color=TEXT_MID)
        self.banner_sub.configure(text=self._disconnected_sub())

    def _panel_top(self):
        cid, cs = self.entry_cid.get().strip(), self.entry_csecret.get().strip()
        self.engine.panel_announce(cid, cs)

    def _panel_stop(self):
        cid, cs = self.entry_cid.get().strip(), self.entry_csecret.get().strip()
        self.engine.panel_stop(cid, cs)

    def _open_bot_config(self):
        if not BOT_CONFIG_FILE.exists():
            load_bot_config()
        try:
            if sys.platform.startswith("win"):
                os.startfile(BOT_CONFIG_FILE)
            elif sys.platform == "darwin":
                os.system(f'open "{BOT_CONFIG_FILE}"')
            else:
                os.system(f'xdg-open "{BOT_CONFIG_FILE}"')
        except Exception as e:
            self._append_log(f"Nelze otevřít bot_config.json: {e}", "error")

    def _reload_bot_config(self):
        self.engine.reload_bot_config()

    # ── Callbacky z enginu ───────────────────────────────────────────────────
    def _append_log(self, msg, level="info"):
        def do():
            colors = {"info": TEXT_MID, "success": KICK_GREEN, "error": RED_ERR,
                      "warn": YELLOW_WARN, "dim": TEXT_DIM}
            ts = datetime.now().strftime("%H:%M:%S")
            self.log_box.configure(state="normal")
            self.log_box.insert("end", f"[{ts}] {msg}\n")
            self.log_box.configure(state="disabled")
            self.log_box.see("end")
        self.after(0, do)

    def _clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _toggle_log(self):
        self._log_collapsed = not self._log_collapsed
        if self._log_collapsed:
            self._log_frame.grid_remove()
            self._log_main.grid_rowconfigure(3, weight=0)
            self.btn_log_toggle.configure(text="▲")
        else:
            self._log_frame.grid()
            self._log_main.grid_rowconfigure(3, weight=1)
            self.btn_log_toggle.configure(text="▼")

    def _set_status(self, status):
        def do():
            labels = {
                "counting":     ("🟢 Počítám", KICK_GREEN),
                "disconnected": ("⚪ Odpojeno", TEXT_DIM),
                "reset":        ("🔁 Vynulováno, počítám dál", KICK_GREEN),
            }
            text, color = labels.get(status, ("⚪ ...", TEXT_DIM))
            self.lbl_status.configure(text=text, text_color=color)
        self.after(0, do)

    def _update_counts(self, sorted_counts):
        def do():
            for w in self._count_rows:
                w.destroy()
            self._count_rows = []
            if not sorted_counts:
                self.count_placeholder.grid()
                self.lbl_count.configure(text="0 diváků")
                return
            self.count_placeholder.grid_remove()
            self.lbl_count.configure(text=f"{len(sorted_counts)} diváků")
            medals = ["🥇", "🥈", "🥉"]
            for i, (user, cnt) in enumerate(sorted_counts):
                row = ctk.CTkFrame(self.guess_scroll, fg_color=CARD_BG, corner_radius=8)
                row.grid(row=i, column=0, sticky="ew", padx=4, pady=3)
                row.grid_columnconfigure(1, weight=1)
                prefix = medals[i] if i < 3 else f"{i+1}."
                ctk.CTkLabel(row, text=prefix, font=ctk.CTkFont("", 13),
                             text_color=TEXT_DIM, width=28).grid(row=0, column=0, padx=(10, 2), pady=8)
                ctk.CTkLabel(row, text=user, font=ctk.CTkFont("", 13, "bold"),
                             text_color=TEXT_BRIGHT, anchor="w").grid(row=0, column=1, sticky="w", pady=8)
                ctk.CTkLabel(row, text=f"{cnt} zpráv", font=ctk.CTkFont("", 12),
                             text_color=KICK_GREEN).grid(row=0, column=2, padx=(4, 12), pady=8)
                self._count_rows.append(row)
        self.after(0, do)

    def _update_results(self, sorted_counts, final):
        def do():
            for w in self._result_rows:
                w.destroy()
            self._result_rows = []
            if not sorted_counts:
                self.lbl_results_placeholder.grid()
                return
            self.lbl_results_placeholder.grid_remove()
            medals = ["🥇", "🥈", "🥉"]
            for i, (user, cnt) in enumerate(sorted_counts[:10]):
                row = ctk.CTkFrame(self.results_frame, fg_color=CARD_BG, corner_radius=8)
                row.grid(row=i, column=0, sticky="ew", padx=4, pady=3)
                row.grid_columnconfigure(1, weight=1)
                prefix = medals[i] if i < 3 else f"{i+1}."
                ctk.CTkLabel(row, text=f"{prefix} {user}", font=ctk.CTkFont("", 12, "bold"),
                             text_color=TEXT_BRIGHT, anchor="w").grid(row=0, column=0, sticky="w", padx=10, pady=6)
                ctk.CTkLabel(row, text=str(cnt), font=ctk.CTkFont("", 12),
                             text_color=KICK_GREEN).grid(row=0, column=1, sticky="e", padx=10, pady=6)
                self._result_rows.append(row)
        self.after(0, do)


if __name__ == "__main__":
    app = App()
    app.mainloop()
