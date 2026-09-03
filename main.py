from flask import Flask, request, jsonify, session, g
from flask_cors import CORS
from supabase import create_client
from werkzeug.security import generate_password_hash, check_password_hash
import io
import os
import logging
import math
import random
import re
import secrets
from time import time
from datetime import timedelta, datetime, timezone
from urllib.parse import unquote
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from google import genai
import segno


# ============================================================
#  CONFIG
# ============================================================
recent_registrations = {}
REGISTRATION_LIMIT_WINDOW = 10   # min seconds between registrations from one IP

# In-memory chat presence + typing (ephemeral — fine to lose on restart)
_presence = {}      # username -> last-active unix ts
_typing = {}        # convo key -> {username: expiry ts}
PRESENCE_TTL = 45   # seconds since last activity to still count as "online"
TYPING_TTL = 6      # seconds a "typing…" ping stays live

# Athena counter-intelligence: recent firewall-blocked "probes" (e.g. AIA scans)
_threats = []       # newest first: {ip, path, ua, ts}
_last_op = {}       # username -> last field-operation ts (op cooldown)


def _log_threat(ip, path, ua):
    _threats.insert(0, {"ip": ip, "path": (path or "")[:120], "ua": (ua or "")[:90], "ts": time()})
    del _threats[60:]

# --- Economy constants -------------------------------------
PUFB_PER_CYBUCK      = 1        # Treaty peg: 1 Pufferbuck = 1 Cybuck
AQUILINES_PER_PUFB   = 10       # 10 Aquilines   = 1 Pufferbuck
CYBITS_PER_CYBUCK    = 50       # 50 Cybits      = 1 Cybuck
# Value of one unit expressed in Cybucks:
CYBUCK_VALUE = {
    "cybucks":   1.0,
    "pufb":      1.0 / PUFB_PER_CYBUCK,                        # 1.0
    "aquilines": 1.0 / (PUFB_PER_CYBUCK * AQUILINES_PER_PUFB), # 0.1
    "cybit":     1.0 / CYBITS_PER_CYBUCK,                      # 0.02
}

# Maps a currency code -> the actual DB column. Cybucks live in "balance".
CURRENCY_COLUMN = {
    "cybucks":   "balance",
    "pufb":      "pufb",
    "aquilines": "aquilines",
    "cybit":     "cybits",
}

STARTING_GRANT   = 100          # new citizens get 100 of EACH currency
REFERRAL_BONUS   = 500          # Cybucks paid to a referrer when their invite is approved
COMPANY_FEE      = 1000         # cost in Cybucks to found a company
LOAN_MAX         = 5000         # max loan in Cybucks
LOAN_DAYS        = 30           # repay within 30 days
VAT_RATE         = 0.10         # 10% monthly VAT
TAX_PERIOD_DAYS  = 30
SALARY_PERIOD_DAYS = 7
SAVINGS_RATE     = 0.05         # 5% monthly interest on savings
BOND_RATE        = 0.10         # 10% return on government bonds
BOND_DAYS        = 30           # bond maturity period
GDP              = 500000       # fallback GDP if auto-calc fails
GDP_MULTIPLIER   = 1            # President-controlled scaling of the auto GDP

# These economic levers are stored in the `config` table and can be changed
# live by the President from the admin panel. Maps config column -> global.
_CONFIG_KEYS = {
    "vat_rate": "VAT_RATE", "tax_period_days": "TAX_PERIOD_DAYS",
    "salary_period_days": "SALARY_PERIOD_DAYS", "savings_rate": "SAVINGS_RATE",
    "bond_rate": "BOND_RATE", "bond_days": "BOND_DAYS", "company_fee": "COMPANY_FEE",
    "loan_max": "LOAN_MAX", "loan_days": "LOAN_DAYS", "starting_grant": "STARTING_GRANT",
    "gdp": "GDP", "gdp_multiplier": "GDP_MULTIPLIER",
    "record_expiry_days": "RECORD_EXPIRY_DAYS",
    "ministry_min_applicants": "MINISTRY_MIN_APPLICANTS",
    "fine_bars_office": "FINE_BARS_OFFICE",
    "courier_wage": "COURIER_WAGE", "delivery_levy": "DELIVERY_LEVY",
    "delivery_open": "DELIVERY_OPEN",
    "insurance_open": "INSURANCE_OPEN", "insurance_levy": "INSURANCE_LEVY",
    "lend_open": "LEND_OPEN", "lend_max_deposit": "LEND_MAX_DEPOSIT",
    "pen_rate": "PEN_RATE", "pen_open": "PEN_OPEN",
}

# Justice levers. RECORD_EXPIRY_DAYS = 0 means a conviction bars a citizen
# from office permanently; set it above 0 to let convictions become spent.
RECORD_EXPIRY_DAYS      = 0
MINISTRY_MIN_APPLICANTS = 4
FINE_BARS_OFFICE        = True

# Cyvazon — the national delivery service. Delivery is free to whoever
# requests it, so the nation funds it: DELIVERY_LEVY rides on top of VAT
# while the service is open, and couriers draw COURIER_WAGE each salary
# period. All three are President-tunable from the admin panel.
COURIER_WAGE   = 500        # CB per salary period — deliberately above a Citizen's
DELIVERY_LEVY  = 0.05       # extra tax that pays for free delivery
DELIVERY_OPEN  = True

COMPANY_CATEGORIES = ["Finance", "Selling", "Service", "Technology", "Other"]

# Weekly salary (Cybucks) by designation.
# The President draws nothing: they hold the Treasury and spend it on the
# nation, so paying themselves a wage out of it would be circular.
SALARY_TABLE = {
    "President":         0,
    "Prime Minister":    1000,
    "Judge":             900,
    "Minister":          900,
    "Security Minister": 900,
    "Head of Coding":    800,
    "Head of Hacking":   800,
    "Founder":           800,
    "Employee":          500,
    "Citizen":           100,
}
# Couriers draw COURIER_WAGE on their own clock, on top of the above.

# Accounts allowed to open/view the national Treasury
TREASURY_ADMINS = {"Prathyay", "Cyvathon"}


app = Flask(__name__, static_folder="static", static_url_path="/static")
# Session signing key. A known/default key lets anyone forge a session cookie and
# impersonate the President — so fall back to a random key (and warn) if unset.
_secret = os.getenv("SECRET_KEY")
if not _secret or _secret == "change-this-secret-vro":
    _secret = secrets.token_hex(32)
    logging.warning("SECRET_KEY not set — using a random key (everyone is logged out on each "
                    "restart). Set a stable SECRET_KEY in Render env for persistent, secure sessions.")
app.config["SECRET_KEY"] = _secret
app.config["SESSION_COOKIE_NAME"] = "cyvathon_session"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=6)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024   # 50 MB — allows short video uploads (images/JSON stay small)

CORS(app, supports_credentials=True)
logging.basicConfig(level=logging.INFO)

SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
SUPABASE_KEY = (os.getenv("SUPABASE_KEY") or "").strip()
# Guard against a common misconfig: URL must be the bare project URL only.
if SUPABASE_URL.endswith("/rest/v1"):
    SUPABASE_URL = SUPABASE_URL[:-len("/rest/v1")]
logging.info("Supabase URL in use: %s", SUPABASE_URL)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
genai_client = None
if GEMINI_API_KEY:
    try:
        genai_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        logging.warning("Gemini client init failed (AI assistant disabled): %s", e)
else:
    logging.warning("GEMINI_API_KEY not set — Cyvathon AI assistant is disabled.")

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    # Per-IP throttle. The per-MINUTE cap is the real anti-hammer defense:
    # ~3 req/sec is plenty for a human browsing + live chat polling, but
    # stops a script flooding the server. Hourly is a secondary backstop.
    # Sensitive write routes keep their own tighter per-route limits below.
    default_limits=["180 per minute", "4000 per hour"]
)


# Always return JSON for errors on API calls so the frontend can show a real
# message instead of failing to parse an HTML error page ("Bad server response").
from werkzeug.exceptions import HTTPException

# Route-enumeration detection: a burst of 404s from one IP = someone probing
# for hidden endpoints. We surface it in Athena and auto-ban on a flood.
_recon_404 = {}                 # ip -> (count, window_start)
_RECON_404_LIMIT = 15
_RECON_404_WINDOW = 300
_BENIGN_404 = ("/favicon.ico", "/robots.txt", "/sitemap.xml", "/apple-touch-icon",
               "/.well-known", "/service-worker.js", "/manifest.json")


def _note_404(ip, path, ua):
    if not path or any(b in path for b in _BENIGN_404):
        return
    _log_threat(ip, "404 " + path, ua)      # shows route-enumeration in the Athena feed
    now = time()
    cnt, start = _recon_404.get(ip, (0, now))
    if now - start > _RECON_404_WINDOW:
        cnt, start = 0, now
    cnt += 1
    _recon_404[ip] = (cnt, start)
    if cnt >= _RECON_404_LIMIT:
        _strike(ip)                          # enumeration flood → strike toward auto-ban
        _recon_404.pop(ip, None)


@app.errorhandler(Exception)
def handle_any_error(e):
    if isinstance(e, HTTPException):
        if e.code == 404:
            _note_404(client_ip(), request.path, request.headers.get("User-Agent", ""))
        # Let real static-page routes keep their normal HTML responses.
        if request.path == "/" or request.path.endswith((".html", ".css", ".js", ".pdf", ".ico")):
            return e
        return jsonify(success=False, error=e.description or e.name), e.code
    # Log full details server-side, but never leak internals (SQL, stack) to clients.
    logging.exception("Unhandled server error on %s", request.path)
    return jsonify(success=False, error="Something went wrong. Please try again."), 500


# ============================================================
#  IP BLOCKING  (admin can ban abusive IPs)
# ============================================================
def client_ip():
    # Behind Cloudflare, the true visitor IP is in CF-Connecting-IP.
    cf = request.headers.get("CF-Connecting-IP")
    if cf:
        return cf.strip()
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() if fwd else request.remote_addr) or "unknown"


BLOCKED_IPS = set()

def load_blocked_ips():
    global BLOCKED_IPS
    try:
        rows = supabase.table("blocked_ips").select("ip").execute().data or []
        BLOCKED_IPS = {r["ip"] for r in rows}
    except Exception as e:
        logging.warning("blocked-IP load skipped: %s", e)


# ----- WAF (web application firewall) -----
_WAF_PATTERNS = re.compile(
    r"(\.\./|\.\.\\|/etc/passwd|/etc/shadow|<script|onerror=|onload=|javascript:"
    r"|union\s+select|information_schema|or\s+1=1|sleep\(|benchmark\(|xp_cmdshell"
    r"|base64_decode|/wp-(admin|login|content)|/\.env|/\.git|/phpmyadmin|/vendor/"
    r"|\.php|\.asp|\.aspx|\.jsp|/cgi-bin/|%00|\x00)", re.I)
_BAD_AGENTS = re.compile(
    r"(sqlmap|nikto|nmap|masscan|nessus|acunetix|dirbuster|gobuster|wpscan|havij"
    r"|netsparker|zgrab|fuzz|hydra|metasploit)", re.I)

_strikes = {}      # ip -> (count, window_start)
_temp_block = {}   # ip -> unblock_timestamp
STRIKE_LIMIT = 8
STRIKE_WINDOW = 300
TEMP_BLOCK_SECS = 3600


def _strike(ip):
    """Record a suspicious action; auto-temp-ban an IP that racks up too many."""
    now = time()
    cnt, start = _strikes.get(ip, (0, now))
    if now - start > STRIKE_WINDOW:
        cnt, start = 0, now
    cnt += 1
    _strikes[ip] = (cnt, start)
    if cnt >= STRIKE_LIMIT:
        _temp_block[ip] = now + TEMP_BLOCK_SECS
        _strikes.pop(ip, None)
        logging.warning("FIREWALL auto-banned %s for 1h (%d strikes)", ip, cnt)


@app.before_request
def _firewall():
    g._t0 = time()
    ip = client_ip()
    now = time()
    # 1. Permanent admin blocklist
    if BLOCKED_IPS and ip in BLOCKED_IPS:
        return jsonify(success=False, error="Your IP address has been blocked from Cyvathon."), 403
    # 2. Temporary auto-ban
    ub = _temp_block.get(ip)
    if ub and now < ub:
        return jsonify(success=False, error="Temporarily blocked — too many suspicious requests."), 429
    if ub:
        _temp_block.pop(ip, None)
    # 3. WAF: scan path + query (URL-decoded) + user-agent for attack signatures
    target = unquote(request.path + "?" + request.query_string.decode("utf-8", "ignore"))
    ua = request.headers.get("User-Agent", "")
    if _WAF_PATTERNS.search(target) or _BAD_AGENTS.search(ua):
        _strike(ip)
        _log_threat(ip, target, ua)
        logging.warning("FIREWALL blocked %s from %s (UA=%s)", target[:160], ip, ua[:100])
        return jsonify(success=False, error="Request blocked by the Cyvathon firewall."), 403


@app.after_request
def _security_headers(resp):
    try:
        resp.headers["X-Response-Time-ms"] = str(round((time() - getattr(g, "_t0", time())) * 1000))
    except Exception:
        pass
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Permissions-Policy"] = "geolocation=(), microphone=(self), camera=()"
    resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://challenges.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
        "font-src 'self' https://cdnjs.cloudflare.com https://fonts.gstatic.com; "
        "img-src 'self' data: https:; "
        "media-src 'self' https:; "
        "frame-src https://challenges.cloudflare.com https://www.youtube.com https://www.youtube-nocookie.com; "
        "connect-src 'self' https://challenges.cloudflare.com; "
        "frame-ancestors 'none'; base-uri 'self'; object-src 'none'")
    return resp


# ============================================================
#  HELPERS
# ============================================================
def refresh_config():
    """Load President-tunable economic levers from the DB into module globals.
       Safe no-op if the config table doesn't exist yet."""
    try:
        r = supabase.table("config").select("*").eq("id", 1).execute().data
        if not r:
            supabase.table("config").insert({"id": 1}).execute()
            return
        row, g = r[0], globals()
        for col, name in _CONFIG_KEYS.items():
            v = row.get(col)
            if v is not None:
                # day/grant fields are whole numbers
                if name in ("FINE_BARS_OFFICE", "DELIVERY_OPEN", "INSURANCE_OPEN",
                            "LEND_OPEN", "PEN_OPEN"):
                    g[name] = bool(v)
                elif name.endswith(("_DAYS", "GRANT")) or name in ("LOAN_MAX", "MINISTRY_MIN_APPLICANTS"):
                    g[name] = int(v)
                else:
                    g[name] = v
    except Exception as e:
        logging.warning("config load skipped (using defaults): %s", e)


refresh_config()    # load policy at startup
load_blocked_ips()  # load IP blocklist at startup


def _now():
    return datetime.now(timezone.utc)


def _parse(ts):
    """Parse a Supabase timestamptz string into an aware datetime."""
    if not ts:
        return None
    if isinstance(ts, datetime):
        return ts
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def is_treasury_admin(user):
    if not user:
        return False
    return user["username"] in TREASURY_ADMINS or user.get("designation") == "President"


def is_court_judge(user):
    """The elected Judge presides over the courts; the President is the fallback."""
    if not user:
        return False
    if is_treasury_admin(user):
        return True
    if user.get("designation") == "Judge":
        return True
    g = supabase.table("government").select("holder").eq("position", "Judge").execute().data
    return bool(g and g[0].get("holder") == user["username"])


# Short-TTL caches for rarely-changing per-request flags, so /me and every
# access-gated endpoint don't re-query Supabase on every page load.
FLAG_TTL = 30            # seconds
_cia_cache = {}          # username -> (rank, ts)
_war_cache = {"row": None, "at": 0.0}


def cia_rank(user):
    """Return the user's Athena rank, or None if not an agent (cached briefly)."""
    if not user:
        return None
    u = user["username"]
    hit = _cia_cache.get(u)
    if hit and time() - hit[1] < FLAG_TTL:
        return hit[0]
    try:
        r = supabase.table("cia_agents").select("rank").eq("username", u).execute().data
        rank = r[0]["rank"] if r else ("Director" if is_treasury_admin(user) else None)
    except Exception:
        rank = "Director" if is_treasury_admin(user) else None
    _cia_cache[u] = (rank, time())
    return rank


def is_cia(user):
    return cia_rank(user) is not None


def is_cia_director(user):
    return is_treasury_admin(user) or cia_rank(user) == "Director"


def get_treasury():
    res = supabase.table("treasury").select("*").eq("id", 1).execute()
    if res.data:
        return res.data[0]
    supabase.table("treasury").insert({"id": 1}).execute()
    return supabase.table("treasury").select("*").eq("id", 1).execute().data[0]


# Short-TTL response cache: shields the database from request floods.
_resp_cache = {}
def cached_json(key, ttl, builder):
    now = time()
    hit = _resp_cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    data = builder()
    _resp_cache[key] = (now + ttl, data)
    return data


_gdp_cache = {"v": None, "t": 0}

def compute_gdp():
    """National GDP, auto-calculated from total wealth in CB-equivalent
    (citizens + treasury + companies + market caps) x the President's multiplier.
    Cached for 5 minutes. Falls back to the static GDP figure on error."""
    if _gdp_cache["v"] is not None and time() - _gdp_cache["t"] < 300:
        return _gdp_cache["v"]
    try:
        vp, va, vc = CYBUCK_VALUE["pufb"], CYBUCK_VALUE["aquilines"], CYBUCK_VALUE["cybit"]
        total = 0.0
        for c in (supabase.table("cybucks").select("balance,pufb,aquilines,cybits,savings,banned").execute().data or []):
            if c.get("banned"):      # frozen accounts aren't part of the economy
                continue
            total += (c.get("balance") or 0) + (c.get("savings") or 0) \
                   + (c.get("pufb") or 0) * vp + (c.get("aquilines") or 0) * va + (c.get("cybits") or 0) * vc
        t = get_treasury()
        total += (t.get("balance") or 0) + (t.get("pufb") or 0) * vp \
               + (t.get("aquilines") or 0) * va + (t.get("cybits") or 0) * vc
        # Armoury stock is national materiel, valued at what the Republic
        # pays for a round.
        total += (t.get("pens") or 0) * PEN_RATE
        for c in (supabase.table("companies")
                  .select("balance,pufb,aquilines,cybits,shares,last_price,ipo_price,is_public")
                  .execute().data or []):
            total += (c.get("balance") or 0) + (c.get("pufb") or 0) * vp \
                   + (c.get("aquilines") or 0) * va + (c.get("cybits") or 0) * vc
            if c.get("is_public"):
                total += (c.get("last_price") or c.get("ipo_price") or 0) * (c.get("shares") or 0)
        gdp = round(total * (GDP_MULTIPLIER or 1), 2)
        _gdp_cache["v"], _gdp_cache["t"] = gdp, time()
        return gdp
    except Exception as e:
        logging.warning("GDP auto-calc failed, using fallback: %s", e)
        return GDP


def treasury_add(cybucks=0, pufb=0, aquilines=0, cybits=0, counterparty=None, kind="manual"):
    """Move money in/out of the Treasury (atomically) and log each currency as a flow.
    The Treasury may run a deficit (negative) — that records true national debt
    instead of silently 'minting' the shortfall by flooring at zero."""
    get_treasury()   # ensure the row exists
    for col, delta in (("balance", cybucks), ("pufb", pufb), ("aquilines", aquilines), ("cybits", cybits)):
        if delta:
            cas_num("treasury", [("id", 1)], col, delta, allow_negative=True)
    for cur, delta in (("cybucks", cybucks), ("pufb", pufb), ("aquilines", aquilines), ("cybit", cybits)):
        if delta:
            try:
                supabase.table("treasury_flows").insert({
                    "direction": "IN" if delta > 0 else "OUT",
                    "counterparty": counterparty,
                    "currency": cur,
                    "amount": round(abs(delta), 2),
                    "kind": kind,
                }).execute()
            except Exception as ex:
                logging.warning("flow log failed: %s", ex)


def add_record(username, entry):
    supabase.table("records").insert({"username": username, "entry": entry}).execute()


def notify(username, message, link=""):
    """Send a notification to a citizen."""
    try:
        supabase.table("notifications").insert({
            "username": username, "message": message, "link": link
        }).execute()
    except Exception as ex:
        logging.warning("notify failed: %s", ex)


def notify_all(message, link="", exclude=None):
    """Broadcast a notification to every (non-banned) citizen. Used for
    nation-wide events like a news post or an election opening."""
    try:
        rows = supabase.table("cybucks").select("username,banned").execute().data or []
    except Exception:
        return
    payload = [{"username": r["username"], "message": message, "link": link}
               for r in rows if not r.get("banned") and r.get("username") != exclude]
    if not payload:
        return
    try:
        supabase.table("notifications").insert(payload).execute()
    except Exception as ex:
        logging.warning("notify_all failed: %s", ex)


def log_txn(kind, from_party, to_party, amount, currency, detail=""):
    """Record a citizen-level transaction for the national activity feed."""
    try:
        supabase.table("transactions").insert({
            "kind": kind, "from_party": from_party, "to_party": to_party,
            "amount": round(amount, 2), "currency": currency, "detail": detail
        }).execute()
    except Exception as ex:
        logging.warning("txn log failed: %s", ex)


_econ_seen = {}          # username -> last full-run timestamp
ECON_MIN_INTERVAL = 60   # seconds; tax/salary are daily/weekly so this is harmless

def apply_economics(user):
    """Run economics, but never let a failure (e.g. a missing table/column
    before schema.sql has been applied) break login or page loads.
    Throttled so rapid page-loads don't re-hit the DB every time."""
    try:
        username = (user or {}).get("username")
        if username:
            if time() - _econ_seen.get(username, 0) < ECON_MIN_INTERVAL:
                return user            # ran very recently — skip the heavy checks
            _econ_seen[username] = time()
        return _run_economics(user)
    except Exception as e:
        logging.exception("economics skipped for %s: %s",
                          (user or {}).get("username"), e)
        return user


def _run_economics(user):
    """Lazily run VAT, salary and loan-default checks for this citizen."""
    if not user:
        return user
    username = user["username"]
    now = _now()
    updates = {}

    # ---- 1. Monthly VAT (10%) -> Treasury -------------------
    last_tax = _parse(user.get("last_tax"))
    if last_tax is None:
        updates["last_tax"] = now.isoformat()
    elif (now - last_tax).days >= TAX_PERIOD_DAYS:
        # Cyvazon is free to use, so the levy that pays for it is collected
        # alongside VAT — charged to everyone, whether they posted a parcel
        # or not, and logged separately so the Treasury shows what it cost.
        levy = DELIVERY_LEVY if DELIVERY_OPEN else 0
        rate = VAT_RATE + levy
        tax_cb = round((user.get("balance")   or 0) * rate, 2)
        tax_pf = round((user.get("pufb")      or 0) * rate, 2)
        tax_aq = round((user.get("aquilines") or 0) * rate, 2)
        tax_cy = round((user.get("cybits")    or 0) * rate, 2)
        if tax_cb: updates["balance"]   = round((user.get("balance")   or 0) - tax_cb, 2)
        if tax_pf: updates["pufb"]      = round((user.get("pufb")      or 0) - tax_pf, 2)
        if tax_aq: updates["aquilines"] = round((user.get("aquilines") or 0) - tax_aq, 2)
        if tax_cy: updates["cybits"]    = round((user.get("cybits")    or 0) - tax_cy, 2)
        if tax_cb or tax_pf or tax_aq or tax_cy:
            share = (levy / rate) if rate else 0          # portion of the take that is the levy
            lev_cb = round(tax_cb * share, 2)
            if lev_cb:
                treasury_add(cybucks=lev_cb, counterparty=username, kind="delivery_levy")
            treasury_add(cybucks=round(tax_cb - lev_cb, 2), pufb=tax_pf,
                         aquilines=tax_aq, cybits=tax_cy,
                         counterparty=username, kind="vat")
            note = f"Paid monthly VAT: {tax_cb} CB / {tax_pf} PUFB / {tax_aq} AQ / {tax_cy} CBT to the Treasury."
            if lev_cb:
                note += f" ({lev_cb} CB of it the Cyvazon delivery levy.)"
            add_record(username, note)
        updates["last_tax"] = now.isoformat()

    # ---- 2. Weekly salary (from Treasury) -------------------
    last_salary = _parse(user.get("last_salary"))
    if last_salary is None:
        updates["last_salary"] = now.isoformat()
    else:
        weeks = (now - last_salary).days // SALARY_PERIOD_DAYS
        if weeks >= 1:
            pay = SALARY_TABLE.get(user.get("designation", "Citizen"), 100) * weeks
            if pay:
                base = updates.get("balance", user.get("balance") or 0)
                updates["balance"] = round(base + pay, 2)
                treasury_add(cybucks=-pay, counterparty=username, kind="salary")
                add_record(username,
                           f"Received {pay} CB salary ({user.get('designation','Citizen')}).")
            # Advance the clock either way, or an unpaid post re-checks forever.
            updates["last_salary"] = now.isoformat()

    # ---- 2a. Courier wage (Cyvazon) -------------------------
    # Paid on its own clock and on top of the citizen's ordinary salary, so a
    # Minister who also runs parcels keeps both.
    if DELIVERY_OPEN and COURIER_WAGE:
        try:
            crow = supabase.table("couriers").select("active,status") \
                .eq("username", username).eq("active", True) \
                .eq("status", "approved").execute().data
        except Exception:
            crow = None
        if crow:
            last_cp = _parse(user.get("last_courier_pay"))
            if last_cp is None:
                updates["last_courier_pay"] = now.isoformat()
            else:
                cw = (now - last_cp).days // SALARY_PERIOD_DAYS
                if cw >= 1:
                    pay = round(COURIER_WAGE * cw, 2)
                    base = updates.get("balance", user.get("balance") or 0)
                    updates["balance"] = round(base + pay, 2)
                    treasury_add(cybucks=-pay, counterparty=username, kind="courier_wage")
                    add_record(username, f"Received {pay:g} CB Cyvazon courier wage.")
                    updates["last_courier_pay"] = now.isoformat()

    # ---- 2b. Monthly savings interest --------------------------
    savings = user.get("savings") or 0
    last_sv = _parse(user.get("savings_updated"))
    if last_sv is None:
        updates["savings_updated"] = now.isoformat()
    elif savings > 0 and (now - last_sv).days >= TAX_PERIOD_DAYS:
        months = (now - last_sv).days // TAX_PERIOD_DAYS
        interest = round(savings * SAVINGS_RATE * months, 2)
        if interest > 0:
            updates["savings"] = round(savings + interest, 2)
            treasury_add(cybucks=-interest, counterparty=username, kind="interest")
            add_record(username, f"Earned {interest} CB savings interest.")
        updates["savings_updated"] = now.isoformat()

    # ---- 3. Loan default check ------------------------------
    loans = supabase.table("loans").select("*") \
        .eq("username", username).eq("repaid", False).eq("defaulted", False).execute()
    for loan in (loans.data or []):
        due = _parse(loan.get("due_at"))
        if due and now > due:
            # Seize EVERYTHING and record it
            seized_cb  = updates.get("balance", user.get("balance") or 0)
            seized_pufb = user.get("pufb") or 0
            seized_aq   = user.get("aquilines") or 0
            treasury_add(cybucks=seized_cb, pufb=seized_pufb, aquilines=seized_aq,
                         counterparty=username, kind="seizure")
            updates["balance"] = 0
            updates["pufb"] = 0
            updates["aquilines"] = 0
            supabase.table("loans").update({"defaulted": True}).eq("id", loan["id"]).execute()
            add_record(username,
                       f"DEFAULTED on a {loan['amount']} CB loan. All assets seized by the Treasury.")
            notify(username, f"You defaulted on a {loan['amount']} CB loan — all assets seized by the Treasury.", "/loans")

    # ---- 4. Employer: flag unpaid staff on the founder's record ----
    my_companies = supabase.table("companies").select("id,name").eq("founder", username).execute().data or []
    for comp in my_companies:
        staff = supabase.table("employment").select("*").eq("company_id", comp["id"]) \
            .eq("status", "employed").execute().data or []
        for e in staff:
            lp = _parse(e.get("last_paid"))
            if lp and (now - lp).days >= 30:
                lf = _parse(e.get("last_flagged"))
                if lf is None or (now - lf).days >= 30:
                    add_record(username,
                               f"Failed to pay {e['username']}'s monthly salary at '{comp['name']}' — recorded.")
                    supabase.table("employment").update({"last_flagged": now.isoformat()}) \
                        .eq("id", e["id"]).execute()

    if updates:
        supabase.table("cybucks").update(updates).eq("username", username).execute()
        user = {**user, **updates}
    return user


def _sweep_cybits(user):
    """Keep Cybuck balances whole: sweep any fractional Cybuck into Cybits
    (1 CB = 50 CBT). Only writes when there's actually a fraction to move, and
    uses a compare-and-swap so it never clobbers a concurrent balance change."""
    try:
        bal = user.get("balance")
        if bal is None:
            return user
        cyb = user.get("cybits") or 0
        whole = math.floor(bal + 1e-9)
        frac_cbt = round((bal - whole) * CYBITS_PER_CYBUCK)     # fractional CB -> whole Cybits
        if frac_cbt >= CYBITS_PER_CYBUCK:                       # rounded up to a full Cybuck
            whole += 1
            frac_cbt -= CYBITS_PER_CYBUCK
        new_cyb = round(cyb) + frac_cbt
        if whole == bal and new_cyb == cyb:
            return user                                        # already clean — no write
        upd = supabase.table("cybucks").update({"balance": whole, "cybits": new_cyb}) \
            .eq("username", user["username"]).eq("balance", bal).eq("cybits", cyb).execute()
        if upd.data:
            user["balance"] = whole
            user["cybits"] = new_cyb
    except Exception:
        pass
    return user


def get_current_user(run_economics=True):
    username = session.get("username")
    if not username:
        return None
    result = supabase.table("cybucks").select("*").eq("username", username).execute()
    if not result.data:
        return None
    user = result.data[0]
    if user.get("banned"):        # banned accounts are logged out everywhere
        return None
    if user.get("approved", True) is False:   # not yet approved by the President
        return None
    _presence[username] = time()               # any authenticated request = "online"
    user = _sweep_cybits(user)                  # Cybucks whole; fraction -> Cybits
    if run_economics:
        user = apply_economics(user)
    return user


def public_user(user):
    return {
        "username":    user["username"],
        "balance":     user.get("balance") or 0,
        "pufb":        user.get("pufb") or 0,
        "aquilines":   user.get("aquilines") or 0,
        "cybits":      user.get("cybits") or 0,
        "designation": user.get("designation") or "Citizen",
        "avatar":      user.get("avatar"),
        "email":       user.get("email"),
        "bio":         user.get("bio"),
        "company_id":  user.get("company_id"),
        "interests":   _parse_interests(user.get("interests")),
        "account_type": user.get("account_type") or "citizen",
        "ally_interest": bool(user.get("ally_interest")),
        "allied": bool(user.get("allied")),
    }


# ============================================================
#  INTERESTS  — powers signup, dashboard recs & interest group chats
# ============================================================
INTEREST_CHANNEL_PREFIX = "Interest — "
INTERESTS = [
    {"key": "business",  "label": "Business & Trading", "icon": "fa-briefcase",       "color": "#1fd6a6", "chat": "Entrepreneurs",
     "recs": [["/company", "fa-briefcase", "Found a Company", "Start your own business"],
              ["/marketplace", "fa-store", "Import & Export", "Trade goods nationwide"]]},
    {"key": "investing", "label": "Investing & Stocks", "icon": "fa-chart-line",      "color": "#22d3ee", "chat": "Investors",
     "recs": [["/exchange", "fa-chart-line", "Stock Exchange", "Trade company shares"],
              ["/portfolio", "fa-chart-pie", "Portfolio", "Track your investments"]]},
    {"key": "writing",   "label": "Writing & Blogging", "icon": "fa-feather-pointed", "color": "#a78bfa", "chat": "Writers",
     "recs": [["/blogs", "fa-feather-pointed", "Blogs", "Write posts & get followers"]]},
    {"key": "video",     "label": "Video & Content",    "icon": "fa-video",           "color": "#ff5d6c", "chat": "Creators",
     "recs": [["/videos", "fa-video", "Videos", "Share & watch videos"]]},
    {"key": "gaming",    "label": "Gaming & Luck",       "icon": "fa-dice",            "color": "#ff8fb0", "chat": "Gamers",
     "recs": [["/casino", "fa-dice", "Casino", "Test your luck vs the House"]]},
    {"key": "politics",  "label": "Politics & Law",      "icon": "fa-landmark",        "color": "#ffce56", "chat": "Politics Hall",
     "recs": [["/voting", "fa-check-to-slot", "Elections", "Vote & run for office"],
              ["/legislature", "fa-scale-balanced", "Legislature", "Debate & pass bills"]]},
    {"key": "social",    "label": "Socializing",         "icon": "fa-comments",        "color": "#58c4ff", "chat": "Social Lounge",
     "recs": [["/chat", "fa-comments", "Chat", "Meet the community"],
              ["/citizens", "fa-users", "Citizens", "Find & DM people"]]},
    {"key": "tech",      "label": "Coding & Tech",       "icon": "fa-code",            "color": "#8b5cf6", "chat": "Techies",
     "recs": [["/ai", "fa-robot", "Cyvathon AI", "Explore & learn how it works"],
              ["/exchange", "fa-chart-line", "Stock Exchange", "Trade the market"]]},
    {"key": "cards",     "label": "Football & Cards",    "icon": "fa-futbol",          "color": "#1fd6a6", "chat": "Card Collectors",
     "recs": [["/packet", "fa-futbol", "Card Packets", "Trade Match Attax cards"],
              ["/marketplace", "fa-store", "Import & Export", "Buy and sell anything"]]},
    {"key": "delivery",  "label": "Delivery & Logistics", "icon": "fa-truck-fast",     "color": "#ff9900", "chat": "Couriers",
     "recs": [["/cyvazon", "fa-truck-fast", "Cyvazon", "Deliver parcels, earn 500 CB a week"],
              ["/jobs", "fa-clipboard-list", "Jobs Board", "Find more paid work"]]},
]
INTEREST_KEYS = {i["key"] for i in INTERESTS}
INTEREST_BY_KEY = {i["key"]: i for i in INTERESTS}


def _parse_interests(val):
    """Accepts a list or comma-separated string; returns known keys only."""
    if isinstance(val, list):
        seq = val
    elif isinstance(val, str):
        seq = [p.strip() for p in val.split(",")]
    else:
        return []
    out = []
    for k in seq:
        if k in INTEREST_KEYS and k not in out:
            out.append(k)
    return out


def _interest_group_id(interest, create=True):
    """The shared chat channel for an interest. Created on demand."""
    name = INTEREST_CHANNEL_PREFIX + interest["chat"]
    g = supabase.table("chat_groups").select("id").eq("name", name).execute().data
    if g:
        return g[0]["id"]
    if not create:
        return None
    try:
        return supabase.table("chat_groups").insert(
            {"name": name, "owner": "Cyvathon"}).execute().data[0]["id"]
    except Exception:
        return None


def _join_interest_channels(username, keys):
    for k in keys:
        it = INTEREST_BY_KEY.get(k)
        if not it:
            continue
        try:
            gid = _interest_group_id(it)
            if gid and not _group_member(gid, username):
                supabase.table("chat_group_members").insert(
                    {"group_id": gid, "username": username}).execute()
        except Exception:
            pass


def _set_interests(username, keys):
    """Persist a citizen's interests (fail-open) and auto-join the matching group chats."""
    keys = _parse_interests(keys)[:8]
    try:
        supabase.table("cybucks").update({"interests": ",".join(keys)}) \
            .eq("username", username).execute()
    except Exception:
        return keys      # column not migrated yet — best effort
    _join_interest_channels(username, keys)
    return keys


@app.route("/interests_catalog")
def interests_catalog():
    return jsonify(success=True, interests=[
        {"key": i["key"], "label": i["label"], "icon": i["icon"],
         "color": i["color"], "chat": i["chat"]} for i in INTERESTS])


@app.route("/recommended")
def recommended():
    """Big personalized feature cards for the dashboard, from the citizen's interests."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    keys = _parse_interests(user.get("interests"))
    cards, seen = [], set()
    for k in keys:
        it = INTEREST_BY_KEY.get(k)
        if not it:
            continue
        for (href, icon, title, desc) in it["recs"]:
            if href in seen:
                continue
            seen.add(href)
            cards.append({"href": href, "icon": icon, "title": title,
                          "desc": desc, "color": it["color"]})
    return jsonify(success=True, interests=keys, recommended=cards[:6])


@app.route("/interests", methods=["POST"])
@limiter.limit("30/minute")
def set_interests_endpoint():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    keys = _parse_interests((request.get_json() or {}).get("interests"))
    if not keys:
        return jsonify(success=False, error="Pick at least one thing you love."), 400
    saved = _set_interests(user["username"], keys)
    return jsonify(success=True, interests=saved)


# ============================================================
#  FOREIGN AFFAIRS  — micronations & alliances
# ============================================================
@app.route("/foreign_data")
def foreign_data():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    try:
        rows = supabase.table("cybucks") \
            .select("username,avatar,account_type,ally_interest,allied,banned,created_at") \
            .eq("account_type", "micronation").execute().data or []
    except Exception:      # columns not migrated yet
        rows = []
    nations = [{
        "username": r["username"], "avatar": r.get("avatar"),
        "ally_interest": bool(r.get("ally_interest")), "allied": bool(r.get("allied")),
        "member_since": r.get("created_at"),
    } for r in rows if not r.get("banned")]
    nations.sort(key=lambda n: (not n["allied"], not n["ally_interest"], n["username"].lower()))
    return jsonify(success=True, nations=nations, is_president=is_treasury_admin(user))


@app.route("/foreign/set_ally", methods=["POST"])
@limiter.limit("30/minute")
def foreign_set_ally():
    user = get_current_user(run_economics=False)
    if not user or not is_treasury_admin(user):
        return jsonify(success=False, error="President only"), 403
    d = request.get_json() or {}
    target = (d.get("username") or "").strip()
    allied = bool(d.get("allied"))
    row = supabase.table("cybucks").select("account_type").eq("username", target).execute().data
    if not row:
        return jsonify(success=False, error="No such nation"), 404
    if (row[0].get("account_type") or "citizen") != "micronation":
        return jsonify(success=False, error="That account isn't a micronation"), 400
    try:
        supabase.table("cybucks").update({"allied": allied}).eq("username", target).execute()
    except Exception:
        return jsonify(success=False, error="Alliance column not migrated yet."), 503
    if allied:
        add_record(target, "Signed a formal alliance with Cyvathon. 🤝")
        notify(target, "🤝 Cyvathon has formally allied with your nation!", "/foreign")
    else:
        add_record(target, "Alliance with Cyvathon was dissolved.")
        notify(target, "Your alliance with Cyvathon has been dissolved.", "/foreign")
    return jsonify(success=True, allied=allied)


# ============================================================
#  FLIGHT SIM  — Cyvathon Airways mini-game
# ============================================================
def _flight_board(limit=10):
    try:
        rows = supabase.table("cybucks").select("username,flight_best,avatar,banned") \
            .order("flight_best", desc=True).limit(limit).execute().data or []
    except Exception:
        return []
    return [{"username": r["username"], "score": int(r.get("flight_best") or 0),
             "avatar": r.get("avatar")}
            for r in rows if not r.get("banned") and (r.get("flight_best") or 0) > 0]


@app.route("/flight/board")
def flight_board():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    best = 0
    try:
        r = supabase.table("cybucks").select("flight_best").eq("username", user["username"]).execute().data
        best = int((r[0].get("flight_best") if r else 0) or 0)
    except Exception:
        pass
    return jsonify(success=True, board=_flight_board(), best=best, me=user["username"])


@app.route("/flight/presence", methods=["POST"])
@limiter.limit("240/minute")
def flight_presence():
    """Live multiplayer presence for the flight sim: report my aircraft's
    position/orientation and get back the other pilots currently flying
    (anyone who pinged in the last 6 seconds). Fail-open if not migrated."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    d = request.get_json() or {}
    if d.get("gone"):
        try:
            supabase.table("flight_presence").delete().eq("username", me).execute()
        except Exception:
            pass
        return jsonify(success=True, pilots=[])
    now = time()

    def _f(k, dv=0.0, lo=-1e6, hi=1e6):
        try:
            return max(lo, min(hi, float(d.get(k, dv))))
        except (TypeError, ValueError):
            return dv
    row = {"username": me,
           "x": _f("x", 0, -6000, 6000), "y": _f("y", 0, -100, 2500), "z": _f("z", 0, -6000, 6000),
           "qx": _f("qx"), "qy": _f("qy"), "qz": _f("qz"), "qw": _f("qw", 1.0),
           "spd": _f("spd", 0, 0, 2000), "updated_at": now}
    try:
        supabase.table("flight_presence").upsert(row).execute()
        others = supabase.table("flight_presence").select("*") \
            .neq("username", me).gt("updated_at", now - 6).limit(24).execute().data or []
    except Exception:
        return jsonify(success=True, pilots=[])
    pilots = [{"username": o["username"], "x": o["x"], "y": o["y"], "z": o["z"],
               "qx": o["qx"], "qy": o["qy"], "qz": o["qz"], "qw": o["qw"],
               "spd": o.get("spd", 0)} for o in others]
    return jsonify(success=True, pilots=pilots)


@app.route("/flight/score", methods=["POST"])
@limiter.limit("40/minute")
def flight_score():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    try:
        score = int((request.get_json() or {}).get("score") or 0)
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(score, 1_000_000))

    awarded, best = 0, score
    try:
        row = supabase.table("cybucks").select("flight_best,flight_bonus_day") \
            .eq("username", me).execute().data
        prev = row[0] if row else {}
        prev_best = int(prev.get("flight_best") or 0)
        best = max(prev_best, score)
        upd = {}
        if score > prev_best:
            upd["flight_best"] = score
        # A once-a-day fixed bonus for completing a real flight — safe to grant even
        # if a score were faked (capped, once per day), and it drives the daily habit.
        today = _now().date().isoformat()
        if score >= 50 and (prev.get("flight_bonus_day") or "") != today:
            awarded = 25
            upd["flight_bonus_day"] = today
        if upd:
            supabase.table("cybucks").update(upd).eq("username", me).execute()
        if awarded:
            cas_adjust(me, "balance", awarded, allow_negative=True)
            add_record(me, f"Flew a Cyvathon Airways test flight (score {score}) — earned {awarded} CB pilot bonus.")
    except Exception:
        pass
    return jsonify(success=True, best=best, awarded=awarded, board=_flight_board())


# ============================================================
#  THE REGISTRY — Athena's classified dossier reading room
# ============================================================
REG_DIRECTORATES = ["Counter-Intelligence", "Economic", "Foreign Affairs",
                    "Internal Security", "Cyber", "Archives"]
REG_CLASS = ["UNCLASSIFIED", "RESTRICTED", "CONFIDENTIAL", "SECRET", "EYES ONLY"]
REG_GRADES = ["C-1", "C-2", "C-3", "C-4", "C-5"]
_reg_cache = {"officers": None, "officers_at": 0.0, "public": None, "public_at": 0.0}


def _reg_officers():
    now = time()
    if _reg_cache["officers"] is None or now - _reg_cache["officers_at"] > 15:
        try:
            _reg_cache["officers"] = supabase.table("registry_officers").select("*").execute().data or []
        except Exception:
            _reg_cache["officers"] = []
        _reg_cache["officers_at"] = now
    return _reg_cache["officers"]


def _reg_public_exists():
    now = time()
    if _reg_cache["public"] is None or now - _reg_cache["public_at"] > 15:
        try:
            c = supabase.table("registry_files").select("id", count="exact") \
                .eq("visibility", "public").limit(1).execute().count or 0
            _reg_cache["public"] = c > 0
        except Exception:
            _reg_cache["public"] = False
        _reg_cache["public_at"] = now
    return _reg_cache["public"]


def _reg_is_officer(user):
    if not user:
        return False
    if is_treasury_admin(user):
        return True
    return any(o["username"] == user["username"] for o in _reg_officers())


def _reg_grade(user):
    if is_treasury_admin(user):
        return "C-5"
    o = next((x for x in _reg_officers() if x["username"] == user["username"]), None)
    return (o.get("grade") if o else None)


def _reg_parse_allowed(val):
    if isinstance(val, list):
        return [a for a in val if a]
    return [a.strip() for a in (val or "").split(",") if a.strip()]


def _reg_can_read(user, f):
    if is_treasury_admin(user):
        return True
    if user["username"] == (f.get("author") or ""):
        return True
    vis = f.get("visibility") or "named"
    if vis == "public":
        return True
    if vis == "cleared":
        return _reg_is_officer(user)
    return user["username"] in _reg_parse_allowed(f.get("allowed"))


def _reg_can_manage(user):
    return _reg_is_officer(user)      # cleared officers + the President may file


def _me_registry(user):
    """Whether the citizen sees the Registry nav link at all."""
    try:
        return bool(is_treasury_admin(user) or _reg_is_officer(user) or _reg_public_exists())
    except Exception:
        return False


_reg_seeded = False


def _reg_seed_if_empty():
    """First-run starter content so every section is already populated (like a
    real reading room). Runs once per process; only seeds when the tables exist
    and are empty, so it never clobbers real files."""
    global _reg_seeded
    if _reg_seeded:
        return
    try:
        fcount = supabase.table("registry_files").select("id", count="exact").limit(1).execute().count or 0
    except Exception:
        return  # tables not migrated yet — try again on a later load
    _reg_seeded = True  # tables exist; never auto-seed again this process
    if fcount > 0:
        return
    author = "Prathyay" if "Prathyay" in TREASURY_ADMINS else next(iter(TREASURY_ADMINS), "Registry")
    files = [
        {"title": "Reading Room Charter", "subject": "The Registry",
         "directorate": "Archives", "classification": "UNCLASSIFIED", "visibility": "public", "allowed": "",
         "author": author, "body":
         "The Registry is the archive of the Republic of Cyvathon.\n\n"
         "Every citizen may read the files circulated to them. What you see on your "
         "desk is governed by your clearance; what you do not see is, by design, not "
         "for you to know that you do not see.\n\n"
         "Files are filed by cleared officers under one of six directorates and one "
         "of five classifications, from UNCLASSIFIED to EYES ONLY. Bulletins carry the "
         "day-to-day wire traffic of the service.\n\n"
         "The Registry keeps what the Republic knows. Read carefully."},
        {"title": "Foreign Assessment — The Aquilithian Republic", "subject": "Aquilithia",
         "directorate": "Foreign Affairs", "classification": "CONFIDENTIAL", "visibility": "cleared", "allowed": "",
         "author": author, "body":
         "SUMMARY. Aquilithia is a neighbouring micronation of comparable ambition and "
         "a rival tradition of statecraft. Relations are correct but cool.\n\n"
         "POSTURE. Their intelligence apparatus presents a public reading room of its own. "
         "The Republic's standing instruction is plain: we out-build them in record and in "
         "lore, never on their servers. We do not probe, breach, or retaliate against foreign "
         "systems. Our advantage is that we keep the better archive.\n\n"
         "ASSESSMENT. No hostile action anticipated. Watch, record, and file."},
        {"title": "Directorate Athena — Standing Orders", "subject": "Directorate Athena",
         "directorate": "Counter-Intelligence", "classification": "SECRET", "visibility": "cleared", "allowed": "",
         "author": author, "body":
         "1. Officers file under their true grade. Over-classification is a fault, not a virtue.\n"
         "2. A file names its subject plainly and states what is known, what is inferred, and "
         "what is unknown.\n"
         "3. Clearance is a trust. It is granted by the President and revoked without appeal.\n"
         "4. The Registry does not act. It records. Action is a matter for the Cabinet."},
    ]
    bulletins = [
        {"title": "The Registry is open", "priority": "ROUTINE", "visibility": "public", "allowed": "",
         "author": author, "body":
         "The Reading Room is now open to all citizens. Public files are available to everyone; "
         "cleared officers may read classified dossiers and file their own. Petition the Office of "
         "Records through Recruitment if you wish to be sworn in."},
        {"title": "Standing posture toward foreign services", "priority": "IMMEDIATE", "visibility": "cleared", "allowed": "",
         "author": author, "body":
         "Officers are reminded that the Republic keeps records — it does not conduct offensive "
         "operations against foreign systems. Report, assess, and file. All contact with foreign "
         "officials is reportable to this directorate."},
    ]
    try:
        supabase.table("registry_files").insert(files).execute()
        supabase.table("registry_bulletins").insert(bulletins).execute()
        _reg_cache["public"] = None
    except Exception:
        pass


@app.route("/registry")
def registry_page():
    return app.send_static_file("registry.html")


@app.route("/registry/data")
def registry_data():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    _reg_seed_if_empty()
    officer, prez = _reg_is_officer(user), is_treasury_admin(user)
    can_manage = officer or prez

    try:
        files = supabase.table("registry_files").select("*").order("id", desc=True).limit(250).execute().data or []
    except Exception:
        files = []
    readable, hidden = [], 0
    for f in files:
        if _reg_can_read(user, f):
            readable.append({"id": f["id"], "title": f["title"], "subject": f.get("subject"),
                             "directorate": f.get("directorate"), "classification": f.get("classification"),
                             "visibility": f.get("visibility"), "author": f.get("author"),
                             "created_at": f.get("created_at")})
        else:
            hidden += 1

    try:
        buls = supabase.table("registry_bulletins").select("*").order("id", desc=True).limit(40).execute().data or []
    except Exception:
        buls = []
    bulletins = [{"id": b["id"], "title": b.get("title"), "body": b.get("body"),
                  "priority": b.get("priority"), "author": b.get("author"), "created_at": b.get("created_at")}
                 for b in buls if _reg_can_read(user, b)]

    officers = []
    if can_manage:
        officers = [{"username": o["username"], "grade": o.get("grade"), "role": o.get("role")} for o in _reg_officers()]
    requests = []
    my_request = None
    if prez:
        try:
            requests = supabase.table("registry_requests").select("*").eq("status", "pending") \
                .order("id", desc=True).execute().data or []
        except Exception:
            requests = []
    try:
        r = supabase.table("registry_requests").select("status").eq("username", me) \
            .order("id", desc=True).limit(1).execute().data
        my_request = r[0]["status"] if r else None
    except Exception:
        pass

    return jsonify(success=True, me=me, is_officer=officer, is_president=prez, can_manage=can_manage,
                   grade=_reg_grade(user), directorates=REG_DIRECTORATES, classifications=REG_CLASS, grades=REG_GRADES,
                   files=readable, hidden=hidden, bulletins=bulletins, officers=officers,
                   requests=requests, my_request=my_request)


@app.route("/registry/file/<int:fid>")
def registry_file_get(fid):
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    f = supabase.table("registry_files").select("*").eq("id", fid).execute().data
    if not f:
        return jsonify(success=False, error="File not found"), 404
    f = f[0]
    if not _reg_can_read(user, f):
        return jsonify(success=False, error="ACCESS DENIED — insufficient clearance for this file."), 403
    out = {"id": f["id"], "title": f["title"], "subject": f.get("subject"), "directorate": f.get("directorate"),
           "classification": f.get("classification"), "body": f.get("body"), "visibility": f.get("visibility"),
           "author": f.get("author"), "created_at": f.get("created_at")}
    if _reg_can_manage(user):
        out["allowed"] = _reg_parse_allowed(f.get("allowed"))
    return jsonify(success=True, file=out, can_manage=_reg_can_manage(user), me=user["username"])


@app.route("/registry/file", methods=["POST"])
@limiter.limit("30/minute")
def registry_file_create():
    user = get_current_user(run_economics=False)
    if not user or not _reg_can_manage(user):
        return jsonify(success=False, error="Cleared officers only"), 403
    d = request.get_json() or {}
    title = (d.get("title") or "").strip()[:160]
    subject = (d.get("subject") or "").strip()[:120]
    body = (d.get("body") or "").strip()[:20000]
    directorate = d.get("directorate") if d.get("directorate") in REG_DIRECTORATES else "Archives"
    classification = d.get("classification") if d.get("classification") in REG_CLASS else "CONFIDENTIAL"
    vis = d.get("visibility") if d.get("visibility") in ("public", "cleared", "named") else "named"
    if not title or not body:
        return jsonify(success=False, error="A file needs a title and a body."), 400
    allowed = []
    if vis == "named":
        want = [str(x).strip() for x in (d.get("allowed") or []) if str(x).strip()]
        want = list(dict.fromkeys(want))[:40]
        if want:
            valid = {r["username"] for r in (supabase.table("cybucks").select("username")
                     .in_("username", want).execute().data or [])}
            allowed = [w for w in want if w in valid]
        if not allowed:
            return jsonify(success=False, error="Pick who may read this file (or choose a wider visibility)."), 400
    allowed_str = ("," + ",".join(allowed) + ",") if allowed else ""
    try:
        row = supabase.table("registry_files").insert({
            "title": title, "subject": subject, "body": body, "directorate": directorate,
            "classification": classification, "visibility": vis, "allowed": allowed_str,
            "author": user["username"]}).execute().data[0]
    except Exception:
        return jsonify(success=False, error="The Registry isn't set up yet — run the migration."), 500
    _reg_cache["public"] = None      # a new public file may now exist
    for u in allowed:
        notify(u, f"📁 A Registry file has been shared with you: {title}", "/registry")
    return jsonify(success=True, file=row)


@app.route("/registry/file/<int:fid>/delete", methods=["POST"])
@limiter.limit("30/minute")
def registry_file_delete(fid):
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    f = supabase.table("registry_files").select("author").eq("id", fid).execute().data
    if not f:
        return jsonify(success=False, error="File not found"), 404
    if not (is_treasury_admin(user) or f[0].get("author") == user["username"]):
        return jsonify(success=False, error="Only the author or the President may delete a file"), 403
    supabase.table("registry_files").delete().eq("id", fid).execute()
    _reg_cache["public"] = None
    return jsonify(success=True)


@app.route("/registry/bulletin", methods=["POST"])
@limiter.limit("20/minute")
def registry_bulletin_create():
    user = get_current_user(run_economics=False)
    if not user or not _reg_can_manage(user):
        return jsonify(success=False, error="Cleared officers only"), 403
    d = request.get_json() or {}
    title = (d.get("title") or "").strip()[:160]
    body = (d.get("body") or "").strip()[:6000]
    priority = d.get("priority") if d.get("priority") in ("IMMEDIATE", "ROUTINE") else "ROUTINE"
    vis = d.get("visibility") if d.get("visibility") in ("public", "cleared") else "cleared"
    if not title or not body:
        return jsonify(success=False, error="A bulletin needs a title and body."), 400
    try:
        row = supabase.table("registry_bulletins").insert({
            "title": title, "body": body, "priority": priority, "visibility": vis,
            "allowed": "", "author": user["username"]}).execute().data[0]
    except Exception:
        return jsonify(success=False, error="The Registry isn't set up yet — run the migration."), 500
    return jsonify(success=True, bulletin=row)


@app.route("/registry/officer", methods=["POST"])
@limiter.limit("30/minute")
def registry_officer():
    user = get_current_user(run_economics=False)
    if not user or not is_treasury_admin(user):
        return jsonify(success=False, error="President only"), 403
    d = request.get_json() or {}
    target = (d.get("username") or "").strip()
    action = d.get("action") or "grant"
    if not supabase.table("cybucks").select("id").eq("username", target).execute().data:
        return jsonify(success=False, error="No such citizen"), 404
    if action == "revoke":
        supabase.table("registry_officers").delete().eq("username", target).execute()
        notify(target, "Your Registry clearance has been revoked.", "/registry")
    else:
        grade = d.get("grade") if d.get("grade") in REG_GRADES else "C-2"
        role = (d.get("role") or "Officer").strip()[:40]
        try:
            supabase.table("registry_officers").delete().eq("username", target).execute()
            supabase.table("registry_officers").insert(
                {"username": target, "grade": grade, "role": role}).execute()
        except Exception:
            return jsonify(success=False, error="The Registry isn't set up yet — run the migration."), 500
        # if they had a pending clearance request, mark it approved
        try:
            supabase.table("registry_requests").update({"status": "approved"}) \
                .eq("username", target).eq("status", "pending").execute()
        except Exception:
            pass
        notify(target, f"🦉 You have been cleared into the Registry at grade {grade} ({role}).", "/registry")
    _reg_cache["officers"] = None
    return jsonify(success=True)


@app.route("/registry/apply", methods=["POST"])
@limiter.limit("6/minute")
def registry_apply():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    if _reg_is_officer(user):
        return jsonify(success=False, error="You already hold Registry clearance."), 400
    note = ((request.get_json() or {}).get("note") or "").strip()[:400]
    try:
        ex = supabase.table("registry_requests").select("id").eq("username", user["username"]) \
            .eq("status", "pending").execute().data
        if ex:
            return jsonify(success=False, error="Your clearance application is already pending."), 400
        supabase.table("registry_requests").insert(
            {"username": user["username"], "note": note, "status": "pending"}).execute()
    except Exception:
        return jsonify(success=False, error="The Registry isn't set up yet — run the migration."), 500
    for adm in TREASURY_ADMINS:
        notify(adm, f"🗂️ {user['username']} has applied for Registry clearance.", "/registry")
    return jsonify(success=True)


@app.route("/registry/request/deny", methods=["POST"])
@limiter.limit("30/minute")
def registry_request_deny():
    user = get_current_user(run_economics=False)
    if not user or not is_treasury_admin(user):
        return jsonify(success=False, error="President only"), 403
    target = ((request.get_json() or {}).get("username") or "").strip()
    try:
        supabase.table("registry_requests").update({"status": "denied"}) \
            .eq("username", target).eq("status", "pending").execute()
    except Exception:
        pass
    return jsonify(success=True)


def _gov_role(username):
    """The government office(s) this citizen holds, if any (cabinet + ministries)."""
    roles = []
    try:
        for r in (supabase.table("government").select("position,holder")
                  .eq("holder", username).execute().data or []):
            if r.get("position"):
                roles.append(r["position"])
    except Exception:
        pass
    try:
        for r in (supabase.table("ministries").select("name,minister")
                  .eq("minister", username).execute().data or []):
            if r.get("name") and r["name"] not in roles:
                roles.append(r["name"])
    except Exception:
        pass
    return roles


def company_founders(c):
    """All citizens with founder privileges: the founder + any co-founders."""
    cf = c.get("cofounders") or []
    if not isinstance(cf, list):
        cf = []
    return [c["founder"]] + [x for x in cf if x != c["founder"]]


def user_net_worth(username):
    u = supabase.table("cybucks").select("balance,pufb,aquilines,cybits").eq("username", username).execute().data
    if not u:
        return 0
    u = u[0]
    nw = (u.get("balance") or 0) + (u.get("pufb") or 0) * CYBUCK_VALUE["pufb"] \
        + (u.get("aquilines") or 0) * CYBUCK_VALUE["aquilines"] \
        + (u.get("cybits") or 0) * CYBUCK_VALUE["cybit"]
    hs = supabase.table("holdings").select("shares,company_id").eq("username", username).execute().data or []
    for h in hs:
        if (h.get("shares") or 0) > 0:
            c = supabase.table("companies").select("balance,shares,last_price,ipo_price") \
                .eq("id", h["company_id"]).execute().data
            if c:
                nw += _share_value(c[0]) * h["shares"]
    return round(nw, 2)


def _share_value(c):
    """A share is worth what the company can actually back — its book value per
    share (NAV) — capped by the market price. This makes net worth un-inflatable:
    an empty company with an arbitrarily high founder-set price is worth ~0, so no
    one can pump a sham stock to fake a fortune."""
    comp_shares = c.get("shares") or 0
    if comp_shares <= 0:
        return 0
    nav = max(c.get("balance") or 0, 0) / comp_shares
    price = c.get("last_price") or c.get("ipo_price") or 0
    return min(price, nav)


# ============================================================
#  PAGES
# ============================================================
@app.route("/favicon.ico")
def favicon():
    # Browsers request this at the root; serve it instead of logging a 404.
    return app.send_static_file("favicon.ico")


@app.route("/sitemap.xml")
def sitemap():
    """List the public pages for search engines. Private and classified
    pages are deliberately absent — see robots() above."""
    pages = ["", "/rules", "/news", "/blogs", "/videos", "/citizens",
             "/leaderboard", "/government", "/ministries", "/legislature",
             "/court", "/gazette", "/states", "/foreign", "/treasury",
             "/exchange", "/company", "/jobs", "/marketplace", "/casino",
             "/flightsim", "/packet", "/cyvazon", "/shield", "/cyvalend", "/pens",
             "/cabinet", "/passport", "/login"]
    urls = "".join(f"<url><loc>https://cyvathon.onrender.com{p}</loc></url>"
                   for p in pages)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           f'{urls}</urlset>')
    return app.response_class(xml, mimetype="application/xml")


@app.route("/robots.txt")
def robots():
    # Keep private / classified pages out of search indexes.
    body = "User-agent: *\n"
    for p in ("/admin", "/athena", "/warroom", "/registry", "/mail",
              "/notifications", "/profile", "/portfolio", "/search"):
        body += f"Disallow: {p}\n"
    body += "Allow: /\nSitemap: https://cyvathon.onrender.com/sitemap.xml\n"
    return app.response_class(body, mimetype="text/plain")


@app.route("/")
def home():
    return app.send_static_file("index.html")

@app.route("/bank")
def bank_page():
    return app.send_static_file("bank.html")

@app.route("/chat")
def chat_page():
    return app.send_static_file("chat.html")

@app.route("/mail")
def mail_page():
    return app.send_static_file("mail.html")

@app.route("/ai")
def ai_page():
    return app.send_static_file("ai.html")

@app.route("/company")
def company_page():
    return app.send_static_file("company.html")

@app.route("/jobs")
def jobs_page():
    return app.send_static_file("jobs.html")

@app.route("/invite")
def invite_page():
    return app.send_static_file("invite.html")

@app.route("/loans")
def loans_page():
    return app.send_static_file("loans.html")

@app.route("/profile")
def profile_page():
    return app.send_static_file("profile.html")

@app.route("/passport")
def passport_page():
    return app.send_static_file("passport.html")

@app.route("/voting")
def voting_page():
    return app.send_static_file("voting.html")

@app.route("/government")
def government_page():
    return app.send_static_file("government.html")

@app.route("/rules")
def rules_page():
    return app.send_static_file("rules.html")

@app.route("/treasury")
def treasury_page():
    return app.send_static_file("treasury.html")

@app.route("/login", methods=["GET"])
def login_page():
    return app.send_static_file("login.html")

@app.route("/portfolio")
def portfolio_page():
    return app.send_static_file("portfolio.html")

@app.route("/exchange")
def exchange_page():
    return app.send_static_file("exchange.html")

@app.route("/marketplace")
def marketplace_page():
    return app.send_static_file("marketplace.html")

@app.route("/states")
def states_page():
    return app.send_static_file("states.html")

@app.route("/state")
def state_page():
    return app.send_static_file("state.html")

@app.route("/casino")
def casino_page():
    return app.send_static_file("casino.html")

@app.route("/news")
def news_page():
    return app.send_static_file("news.html")

@app.route("/notifications")
def notifications_page():
    return app.send_static_file("notifications.html")

@app.route("/citizens")
def citizens_page():
    return app.send_static_file("citizens.html")

@app.route("/leaderboard")
def leaderboard_page():
    return app.send_static_file("leaderboard.html")

@app.route("/videos")
def videos_page():
    return app.send_static_file("videos.html")

@app.route("/blogs")
def blogs_page():
    return app.send_static_file("blogs.html")

@app.route("/foreign")
def foreign_page():
    return app.send_static_file("foreign.html")

@app.route("/flightsim")
def flightsim_page():
    return app.send_static_file("flightsim.html")

@app.route("/court")
def court_page():
    return app.send_static_file("court.html")

@app.route("/legislature")
def legislature_page():
    return app.send_static_file("legislature.html")

@app.route("/gazette")
def gazette_page():
    return app.send_static_file("gazette.html")

@app.route("/admin")
def admin_page():
    return app.send_static_file("admin.html")

@app.route("/ministries")
def ministries_page():
    return app.send_static_file("ministries.html")

@app.route("/fir")
def fir_page():
    return app.send_static_file("fir.html")

@app.route("/athena")
def athena_page():
    return app.send_static_file("athena.html")

@app.route("/warroom")
def warroom_page():
    return app.send_static_file("warroom.html")


# ============================================================
#  AUTH  (single account gates bank + chat + everything)
# ============================================================
_captchas = {}   # token -> (answer:int, expires:float)

MAX_ACCOUNTS_PER_IP = 6      # one household/network may hold a handful of citizens — not hundreds

# --- Cloudflare Turnstile (invisible bot protection) --------------------------
# Enabled only when BOTH env vars are set (site key is public, secret stays in env).
# When unset, signup falls back to the built-in math CAPTCHA so nothing breaks.
TURNSTILE_SITEKEY = os.environ.get("TURNSTILE_SITEKEY", "").strip()
TURNSTILE_SECRET  = os.environ.get("TURNSTILE_SECRET", "").strip()

def _turnstile_on():
    return bool(TURNSTILE_SITEKEY and TURNSTILE_SECRET)

def _verify_turnstile(token, ip):
    """Verify a Turnstile token with Cloudflare. Returns True to allow the signup.
    Fails OPEN if the token is missing only-when the feature is off, or if
    Cloudflare itself is unreachable (real users aren't locked out; every signup
    still needs Presidential approval, plus honeypot/IP-cap/strike all still apply)."""
    if not _turnstile_on():
        return True
    if not token:
        return False
    try:
        import requests
        r = requests.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={"secret": TURNSTILE_SECRET, "response": token, "remoteip": ip or ""},
            timeout=8,
        )
        return bool(r.json().get("success"))
    except Exception as ex:
        logging.warning("Turnstile verify unreachable, allowing: %s", ex)
        return True


@app.route("/public_config")
def public_config():
    """Non-secret front-end config (safe to expose). Lets the static login page
    know whether to render the Turnstile widget and which site key to use."""
    return jsonify(success=True,
                   turnstile=_turnstile_on(),
                   turnstile_sitekey=TURNSTILE_SITEKEY if _turnstile_on() else "")


@app.route("/captcha")
def captcha():
    a, b = random.randint(2, 9), random.randint(2, 9)
    token = secrets.token_hex(8)
    now = time()
    _captchas[token] = (a + b, now + 600, now)      # (answer, expires, issued_at)
    for t in list(_captchas):            # drop expired
        if _captchas[t][1] < now:
            _captchas.pop(t, None)
    return jsonify(success=True, token=token, question=f"What is {a} + {b}?")


def _ip_account_count(ip):
    if not ip:
        return 0
    try:
        r = supabase.table("cybucks").select("id", count="exact").eq("reg_ip", ip).execute()
        return r.count or 0
    except Exception:
        return 0


@app.route("/register", methods=["POST"])
@limiter.limit("25/minute")
def register():
    ip = client_ip()
    now = time()
    if now - recent_registrations.get(ip, 0) < REGISTRATION_LIMIT_WINDOW:
        return jsonify(success=False, error="Too many accounts"), 429

    data = request.get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    # Honeypot: a hidden field real users never see. Bots that fill every field trip it.
    if (data.get("website") or data.get("hp") or "").strip():
        _strike(ip)
        return jsonify(success=False, error="Signup blocked"), 400

    # Human-check. Cloudflare Turnstile when configured; otherwise the built-in
    # math CAPTCHA. Either way the honeypot, IP cap and strike system still apply.
    if _turnstile_on():
        token = (data.get("cf_turnstile_response") or data.get("cf-turnstile-response") or "").strip()
        if not _verify_turnstile(token, ip):
            _strike(ip)
            return jsonify(success=False, error="Human check failed — please try again"), 400
    else:
        entry = _captchas.pop(data.get("captcha_token", ""), None)
        if (not entry or entry[1] < now
                or str(data.get("captcha_answer", "")).strip() != str(entry[0])):
            _strike(ip)      # scripts hammering the endpoint auto-temp-ban themselves
            return jsonify(success=False, error="Incorrect human-check answer — try again"), 400
        # Instant submits (< 1.5s after the challenge loaded) are bots, not humans.
        if now - entry[2] < 1.5:
            _strike(ip)
            return jsonify(success=False, error="Slow down — try again"), 400

    # Hard cap on accounts per signup IP — the decisive block against mass farming.
    if _ip_account_count(ip) >= MAX_ACCOUNTS_PER_IP:
        _strike(ip)
        return jsonify(success=False,
                       error="Too many accounts have been created from your network."), 429

    if not username or not password:
        return jsonify(success=False, error="Missing credentials"), 400
    if len(username) > 32 or len(password) > 200:
        return jsonify(success=False, error="Username (max 32) or password too long"), 400
    if not re.fullmatch(r"[A-Za-z0-9 _.\-]{3,32}", username):
        return jsonify(success=False, error="Username must be 3–32 letters, numbers, spaces, . _ -"), 400
    email = (data.get("email") or "").strip()[:120]
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return jsonify(success=False, error="That doesn't look like a valid email"), 400

    exists = supabase.table("cybucks").select("id").eq("username", username).execute()
    if exists.data:
        return jsonify(success=False, error="Username exists"), 400

    recent_registrations[ip] = now      # start the per-IP cooldown only on a real signup

    hashed = generate_password_hash(password)
    is_admin = username in TREASURY_ADMINS
    designation = "President" if is_admin else "Citizen"
    supabase.table("cybucks").insert({
        "username":    username,
        "password":    hashed,
        "balance":     STARTING_GRANT,
        "pufb":        STARTING_GRANT,
        "aquilines":   STARTING_GRANT,
        "cybits":      STARTING_GRANT,
        "designation": designation,
        "last_tax":    _now().isoformat(),
        "last_salary": _now().isoformat(),
        "reg_ip":      ip,
    }).execute()

    # Every new citizen must be approved by the President before they can enter.
    # (The President's own accounts are auto-approved.) Fail-open if the column
    # isn't present yet, so signups never hard-error.
    if email:      # store email (fail-open if the column isn't there yet)
        try:
            supabase.table("cybucks").update({"email": email}).eq("username", username).execute()
        except Exception:
            pass
    # Account type: an individual citizen, or a fellow micronation (diplomacy).
    acct = (data.get("account_type") or "citizen").strip().lower()
    if acct not in ("citizen", "micronation"):
        acct = "citizen"
    ally = bool(data.get("ally")) and acct == "micronation"
    try:      # fail-open if the columns aren't migrated yet
        supabase.table("cybucks").update(
            {"account_type": acct, "ally_interest": ally}).eq("username", username).execute()
    except Exception:
        pass

    picks = _parse_interests(data.get("interests"))      # favorite things, from signup
    if picks:
        _set_interests(username, picks)
    ref = (data.get("ref") or "").strip()[:32]      # who invited them
    if ref and ref != username and \
            supabase.table("cybucks").select("id").eq("username", ref).execute().data:
        try:
            supabase.table("cybucks").update({"referred_by": ref}).eq("username", username).execute()
        except Exception:
            pass
    pending = not is_admin
    if pending:
        try:
            supabase.table("cybucks").update({"approved": False}).eq("username", username).execute()
        except Exception:
            pending = False        # column missing → approval not enforced yet
    if acct == "micronation":
        add_record(username, "Registered as a foreign micronation"
                   + (" — seeking an alliance with Cyvathon." if ally else "."))
    add_record(username, "Applied for citizenship of Cyvathon — awaiting Presidential approval."
               if pending else
               f"Granted citizenship of Cyvathon with {STARTING_GRANT} of each currency.")

    if pending:
        if acct == "micronation":
            msg = f"🌐 Micronation application: {username}" + (" — wants an ALLIANCE 🤝" if ally else "")
        else:
            msg = f"New citizenship application: {username}"
        notify("Cyvathon", msg, "/admin")
        for _adm in TREASURY_ADMINS:      # make sure the President actually sees nations
            if acct == "micronation" and _adm != username:
                notify(_adm, msg, "/admin")
        return jsonify(success=True, pending=True,
                       message="Application submitted. You'll be able to log in once the President approves your citizenship.")

    session.permanent = True
    session["username"] = username
    new_user = {
        "username": username, "balance": STARTING_GRANT, "pufb": STARTING_GRANT,
        "aquilines": STARTING_GRANT, "cybits": STARTING_GRANT, "designation": designation, "company_id": None
    }
    return jsonify(success=True, user=public_user(new_user), admin=is_treasury_admin(new_user), cia=is_cia(new_user))


@app.route("/login", methods=["POST"])
@limiter.limit("10/minute")
def login():
    data = request.get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    res = supabase.table("cybucks").select("*").eq("username", username).execute()
    user = res.data[0] if res.data else None
    # One generic answer for both unknown-user and wrong-password: no username
    # enumeration. Both strike the IP and surface in the Athena threat feed.
    if not user or not check_password_hash(user["password"], password):
        _strike(client_ip())
        _log_threat(client_ip(), "login-fail:" + username[:40],
                    request.headers.get("User-Agent", ""))
        return jsonify(success=False, error="Incorrect username or password"), 401
    if user.get("banned"):
        return jsonify(success=False, error="This account has been banned from Cyvathon."), 403
    if user.get("approved", True) is False:
        return jsonify(success=False,
                       error="Your citizenship application is awaiting Presidential approval. Please check back later."), 403

    session.permanent = True
    session["username"] = username
    user = apply_economics(user)
    return jsonify(success=True, user=public_user(user), admin=is_treasury_admin(user), cia=is_cia(user))


@app.route("/logout", methods=["POST"])
def logout():
    session.pop("username", None)
    return jsonify(success=True)


@app.route("/me")
def me():
    user = get_current_user()
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    return jsonify(success=True, user=public_user(user), admin=is_treasury_admin(user),
                   cia=is_cia(user), war=_war_cleared(user), registry=_me_registry(user))


@app.route("/users")
def users():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    res = supabase.table("cybucks").select("username,banned") \
        .neq("username", user["username"]).execute()
    return jsonify(success=True, users=[u["username"] for u in res.data if not u.get("banned")])


@app.route("/u/<username>")
def public_profile(username):
    viewer = get_current_user(run_economics=False)
    if not viewer:
        return jsonify(success=False, error="Not logged in"), 401
    res = supabase.table("cybucks").select("*").eq("username", username).execute().data
    if not res:
        return jsonify(success=False, error="Citizen not found"), 404
    u = res[0]

    founded = supabase.table("companies").select("id,name").eq("founder", username).execute().data or []
    jobs = supabase.table("employment").select("company_id,role").eq("username", username) \
        .eq("status", "employed").execute().data or []
    job_list = []
    for j in jobs:
        c = supabase.table("companies").select("name").eq("id", j["company_id"]).execute().data
        job_list.append({"company_id": j["company_id"],
                         "company": c[0]["name"] if c else "?", "role": j["role"]})
    records = supabase.table("records").select("entry,created_at").eq("username", username) \
        .order("created_at", desc=True).limit(10).execute().data or []

    nw = user_net_worth(username)
    badges = [a for a in _achievements(u, records, nw) if a["earned"]]
    return jsonify(success=True, profile={
        "username": u["username"],
        "designation": u.get("designation") or "Citizen",
        "avatar": u.get("avatar"),
        "bio": u.get("bio"),
        "banner": u.get("banner"),
        "badges": badges,
        "pinned": _pinned_blog(u.get("pinned_blog")),
        "cabinet_role": _gov_role(username),
        "member_since": u.get("created_at"),
        "net_worth": nw,
        "account_type": u.get("account_type") or "citizen",
        "ally_interest": bool(u.get("ally_interest")),
        "allied": bool(u.get("allied")),
        "companies": founded, "jobs": job_list, "records": records,
        "is_self": viewer["username"] == username,
    })


@app.route("/bio", methods=["POST"])
@limiter.limit("20/minute")
def set_bio():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    bio = (request.get_json() or {}).get("bio", "")
    bio = (bio or "").strip()[:300]
    try:
        supabase.table("cybucks").update({"bio": bio or None}).eq("username", user["username"]).execute()
    except Exception:
        return jsonify(success=False, error="Descriptions aren't enabled yet — the database needs a quick update."), 503
    return jsonify(success=True, bio=bio)


def _pinned_blog(bid):
    """A trimmed view of the blog a citizen has pinned to their profile."""
    if not bid:
        return None
    try:
        r = supabase.table("blogs").select("id,title,body,created_at,username").eq("id", bid).execute().data
        if not r:
            return None
        p = r[0]; body = p.get("body") or ""
        return {"id": p["id"], "title": p.get("title"), "username": p.get("username"),
                "created_at": p.get("created_at"),
                "excerpt": body[:220] + ("…" if len(body) > 220 else "")}
    except Exception:
        return None


@app.route("/banner", methods=["POST"])
@limiter.limit("20/minute")
def set_banner():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    url = ((request.get_json() or {}).get("url") or "").strip()[:500]
    if url and not re.match(r"^https://[A-Za-z0-9._~:/?#@!$&*+,;=%-]+$", url):
        return jsonify(success=False, error="Use a direct https:// image link."), 400
    try:
        supabase.table("cybucks").update({"banner": url or None}).eq("username", user["username"]).execute()
    except Exception:
        return jsonify(success=False, error="Banners aren't enabled yet — run the migration."), 503
    return jsonify(success=True, banner=url or None)


@app.route("/profile/pin", methods=["POST"])
@limiter.limit("20/minute")
def pin_blog():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    bid = (request.get_json() or {}).get("blog_id")
    if bid:
        try:
            bid = int(bid)
        except (TypeError, ValueError):
            return jsonify(success=False, error="Bad post"), 400
        own = supabase.table("blogs").select("id").eq("id", bid).eq("username", user["username"]).execute().data
        if not own:
            return jsonify(success=False, error="You can only pin your own post"), 403
    else:
        bid = None
    try:
        supabase.table("cybucks").update({"pinned_blog": bid}).eq("username", user["username"]).execute()
    except Exception:
        return jsonify(success=False, error="Pinning isn't enabled yet — run the migration."), 503
    return jsonify(success=True, pinned=_pinned_blog(bid))


@app.route("/health/timing")
def health_timing():
    """Diagnostic: time a few trivial Supabase round-trips. If each is >80ms,
    the app server and the database are almost certainly in different regions."""
    import time as _t
    samples = []
    for _ in range(4):
        s = _t.perf_counter()
        try:
            supabase.table("cybucks").select("username").limit(1).execute()
        except Exception:
            pass
        samples.append(round((_t.perf_counter() - s) * 1000))
    warm = samples[1:] or samples          # drop the first (connection warm-up)
    return jsonify(success=True, db_query_ms=samples, avg_ms=round(sum(warm) / len(warm)),
                   note="Each value = one round-trip to Supabase. Under ~25ms = same region (good). "
                        "Over ~80ms = Render and Supabase are in different regions — that's the slowness.")


@app.route("/search")
def search_page():
    return app.send_static_file("search.html")


@app.route("/search_data")
@limiter.limit("60/minute")
def search_data():
    """One-box search across citizens, blogs, videos and companies."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify(success=True, q=q, citizens=[], blogs=[], videos=[], companies=[])
    safe = re.sub(r"[%,()*.]", " ", q).strip()
    like = f"%{safe}%"

    def find(table, cols, select, order=None, limit=8):
        try:
            qy = supabase.table(table).select(select)
            qy = qy.ilike(cols[0], like) if len(cols) == 1 \
                else qy.or_(",".join(f"{c}.ilike.{like}" for c in cols))
            if order:
                qy = qy.order(order, desc=True)
            return qy.limit(limit).execute().data or []
        except Exception:
            return []

    citizens = find("cybucks", ["username", "bio"], "username,designation,avatar,bio")
    blogs = find("blogs", ["title", "body"], "id,title,username,body", order="id")
    videos = find("videos", ["title", "description"], "id,title,username,kind,description", order="id")
    companies = find("companies", ["name"], "id,name,founder")
    for b in blogs:
        body = b.pop("body", "") or ""
        b["excerpt"] = body[:140] + ("…" if len(body) > 140 else "")
    for v in videos:
        dsc = v.pop("description", "") or ""
        v["excerpt"] = dsc[:120] + ("…" if len(dsc) > 120 else "")
    return jsonify(success=True, q=q, citizens=citizens, blogs=blogs, videos=videos, companies=companies)


# ============================================================
#  BANKING  +  CURRENCY CONVERTER
# ============================================================
def _outstanding_loan(username):
    """Total unpaid loan principal — these Cybucks are LOCKED (can't be moved out)."""
    try:
        loans = supabase.table("loans").select("amount").eq("username", username) \
            .eq("repaid", False).eq("defaulted", False).execute().data or []
        return sum((l["amount"] or 0) for l in loans)
    except Exception:
        return 0


def _available_cb(username, balance):
    """Cybucks the citizen may freely move/spend = balance minus locked loan funds."""
    return (balance or 0) - _outstanding_loan(username)


def _transferable_value(sender):
    """Total wealth a citizen may GIFT to others, in CB-equivalent.
    The welcome grant (and any unpaid loan) is locked — you can spend it in the
    economy, but not transfer it to another citizen. This kills grant-farming
    (make accounts, funnel the free grant to one account, repeat) and works
    across all three currencies, so converting first doesn't bypass it."""
    wealth = ((sender.get("balance") or 0)
              + (sender.get("pufb") or 0) * CYBUCK_VALUE["pufb"]
              + (sender.get("aquilines") or 0) * CYBUCK_VALUE["aquilines"]
              + (sender.get("cybits") or 0) * CYBUCK_VALUE["cybit"])
    grant_locked = STARTING_GRANT * (1 + CYBUCK_VALUE["pufb"] + CYBUCK_VALUE["aquilines"] + CYBUCK_VALUE["cybit"])
    return wealth - grant_locked - _outstanding_loan(sender["username"])


@app.route("/transfer", methods=["POST"])
@limiter.limit("20/minute")
def transfer():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401

    data = request.get_json()
    to_username = data.get("to_username", "").strip()
    currency = data.get("currency", "cybucks")
    amount = float(data.get("amount") or 0)

    if currency not in CURRENCY_COLUMN:
        return jsonify(success=False, error="Unknown currency"), 400
    if not math.isfinite(amount) or amount <= 0:
        return jsonify(success=False, error="Invalid amount"), 400
    if to_username == user["username"]:
        return jsonify(success=False, error="You can't transfer to yourself"), 400

    col = CURRENCY_COLUMN[currency]
    sender = supabase.table("cybucks").select("*").eq("username", user["username"]).execute().data[0]
    if (sender.get(col) or 0) < amount:
        return jsonify(success=False, error="Insufficient funds"), 400
    # You may only transfer money you've EARNED — the welcome grant (and any
    # unpaid loan) is locked from gifting to other citizens.
    if amount * CYBUCK_VALUE[currency] > _transferable_value(sender) + 1e-9:
        return jsonify(success=False, error="You can only transfer money you've earned — your welcome grant (and any unpaid loan) can't be sent to other citizens. Earn it through jobs, sales or trading first."), 400

    receiver_res = supabase.table("cybucks").select("id").eq("username", to_username).execute()
    if not receiver_res.data:
        return jsonify(success=False, error="User not found"), 404

    # Atomic deduct (compare-and-swap) — blocks race-condition double-spend.
    if not cas_adjust(user["username"], col, -amount):
        return jsonify(success=False, error="Insufficient funds or a conflicting transfer — try again."), 400
    cas_adjust(to_username, col, amount, allow_negative=True)

    log_txn("transfer", user["username"], to_username, amount, currency, "Bank transfer")
    notify(to_username, f"{user['username']} sent you {amount} {currency}.", "/bank")
    fresh = supabase.table("cybucks").select("*").eq("username", user["username"]).execute().data[0]
    return jsonify(success=True, user=public_user(fresh))


@app.route("/convert", methods=["POST"])
@limiter.limit("30/minute")
def convert():
    """Convert between cybucks / pufb / aquilines.
       1 Cybuck = 1 Pufferbuck = 10 Aquilines."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401

    data = request.get_json()
    src = data.get("from")
    dst = data.get("to")
    amount = float(data.get("amount") or 0)

    if src not in CURRENCY_COLUMN or dst not in CURRENCY_COLUMN:
        return jsonify(success=False, error="Unknown currency"), 400
    if src == dst:
        return jsonify(success=False, error="Pick two different currencies"), 400
    if not math.isfinite(amount) or amount <= 0:
        return jsonify(success=False, error="Invalid amount"), 400

    scol, dcol = CURRENCY_COLUMN[src], CURRENCY_COLUMN[dst]
    if (user.get(scol) or 0) < amount:
        return jsonify(success=False, error="Insufficient funds"), 400
    # Can't convert away borrowed Cybucks (would bypass the loan-lock).
    if src == "cybucks" and amount > _available_cb(user["username"], user.get("balance")):
        return jsonify(success=False, error="Borrowed Cybucks can't be converted. Repay your loan first."), 400

    converted = round(amount * CYBUCK_VALUE[src] / CYBUCK_VALUE[dst], 2)
    # Atomic: deduct source first (CAS), then credit destination — no race double-mint.
    if not cas_adjust(user["username"], scol, -amount):
        return jsonify(success=False, error="Insufficient funds or a conflicting request — try again."), 400
    cas_adjust(user["username"], dcol, converted, allow_negative=True)

    log_txn("convert", user["username"], user["username"], amount, src,
            f"Converted to {converted} {dst}")
    fresh = supabase.table("cybucks").select("*").eq("username", user["username"]).execute().data[0]
    return jsonify(success=True, converted=converted, user=public_user(fresh))


@app.route("/treasury_data")
def treasury_data():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    # Treasury is public to all citizens (read-only).

    t = get_treasury()
    reserves = {"cybucks": t["balance"] or 0, "pufb": t["pufb"] or 0,
                "aquilines": t["aquilines"] or 0, "cybit": t.get("cybits") or 0}

    # --- Money supply: what citizens hold + what the Treasury holds ---
    # Banned accounts are frozen — exclude their currency and holder count.
    citizens = supabase.table("cybucks").select("balance,pufb,aquilines,cybits,banned").execute().data or []
    held = {"cybucks": 0.0, "pufb": 0.0, "aquilines": 0.0, "cybit": 0.0}
    holders = 0
    for c in citizens:
        if c.get("banned"):
            continue
        cb, pf, aq, cy = (c.get("balance") or 0), (c.get("pufb") or 0), (c.get("aquilines") or 0), (c.get("cybits") or 0)
        held["cybucks"] += cb; held["pufb"] += pf; held["aquilines"] += aq; held["cybit"] += cy
        if cb or pf or aq or cy:
            holders += 1
    supply = {k: round(held[k] + reserves[k], 2) for k in reserves}

    # --- VAT collected & salary paid, this calendar year, per currency ---
    year_start = datetime(_now().year, 1, 1, tzinfo=timezone.utc).isoformat()
    def totals(kind):
        rows = supabase.table("treasury_flows").select("currency,amount") \
            .eq("kind", kind).gte("created_at", year_start).execute().data or []
        out = {"cybucks": 0.0, "pufb": 0.0, "aquilines": 0.0, "cybit": 0.0}
        for r in rows:
            out[r["currency"]] = round(out.get(r["currency"], 0) + (r["amount"] or 0), 2)
        return out

    flows = supabase.table("treasury_flows").select("*") \
        .order("created_at", desc=True).limit(25).execute().data or []
    transactions = supabase.table("transactions").select("*") \
        .order("created_at", desc=True).limit(25).execute().data or []

    return jsonify(
        success=True,
        year=_now().year,
        reserves=reserves,
        held=held,
        supply=supply,
        holders=holders,
        vat=totals("vat"),
        salary=totals("salary"),
        flows=flows,
        transactions=transactions,
        gdp=compute_gdp(),
    )


# ============================================================
#  COMPANIES
# ============================================================
@app.route("/companies", methods=["GET"])
def list_companies():
    res = supabase.table("companies").select("*").order("created_at", desc=True).execute()
    return jsonify(success=True, companies=res.data or [], categories=COMPANY_CATEGORIES)


@app.route("/companies", methods=["POST"])
@limiter.limit("10/minute")
def create_company():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401

    data = request.get_json()
    name = data.get("name", "").strip()
    category = data.get("category", "").strip()
    description = data.get("description", "").strip()

    if not name or category not in COMPANY_CATEGORIES:
        return jsonify(success=False, error="Need a name and a valid category"), 400
    if len(name) > 60 or len(description) > 500:
        return jsonify(success=False, error="Name or description too long"), 400
    if (user.get("balance") or 0) < COMPANY_FEE:
        return jsonify(success=False, error=f"You need {COMPANY_FEE} CB to found a company"), 400

    # Anti-spam: cap how many companies one citizen can found
    mine = supabase.table("companies").select("id", count="exact") \
        .eq("founder", user["username"]).execute()
    if (mine.count or 0) >= 5:
        return jsonify(success=False, error="You can found at most 5 companies"), 400

    exists = supabase.table("companies").select("id").eq("name", name).execute()
    if exists.data:
        return jsonify(success=False, error="A company with that name already exists"), 400

    # Charge the founding fee -> Treasury (atomic deduct)
    if not cas_adjust(user["username"], "balance", -COMPANY_FEE):
        return jsonify(success=False, error="Insufficient funds or a conflicting request — try again."), 400
    supabase.table("cybucks").update({"designation": "Founder"}).eq("username", user["username"]).execute()
    treasury_add(cybucks=COMPANY_FEE, counterparty=user["username"], kind="company_fee")

    company = supabase.table("companies").insert({
        "name": name, "founder": user["username"],
        "category": category, "description": description
    }).execute().data[0]

    supabase.table("cybucks").update({"company_id": company["id"]}) \
        .eq("username", user["username"]).execute()
    add_record(user["username"], f"Founded the company '{name}' ({category}). {COMPANY_FEE} CB taken from your balance and paid to the Treasury.")

    return jsonify(success=True, company=company)


# ============================================================
#  STOCK EXCHANGE
# ============================================================
def _holding_shares(username, company_id):
    r = supabase.table("holdings").select("shares").eq("username", username) \
        .eq("company_id", company_id).execute().data
    return (r[0]["shares"] or 0) if r else 0


def cas_num(table, filters, col, delta, allow_negative=False, places=2):
    """Atomically change a numeric column by `delta` using compare-and-swap:
    the update only applies if the value is unchanged since we read it.
    `filters` is a list of (column, value) identifying the row. Returns True/False."""
    for _ in range(6):
        q = supabase.table(table).select(col)
        for k, v in filters:
            q = q.eq(k, v)
        row = q.execute().data
        if not row:
            return False
        old = row[0].get(col) or 0
        new = round(old + delta, places)
        if new < -1e-9 and not allow_negative:
            return False                       # would overdraw — refuse
        u = supabase.table(table).update({col: new})
        for k, v in filters:
            u = u.eq(k, v)
        if u.eq(col, old).execute().data:      # stuck = nobody changed it first
            return True
    return False


def cas_adjust(username, col, delta, allow_negative=False):
    """Atomic change to a citizen's currency column (compare-and-swap)."""
    return cas_num("cybucks", [("username", username)], col, delta, allow_negative, places=2)


def cas_shares(username, company_id, delta, allow_negative=False):
    """Atomically change a share holding; creates the row on first credit."""
    for _ in range(6):
        row = supabase.table("holdings").select("id,shares").eq("username", username) \
            .eq("company_id", company_id).execute().data
        if not row:
            if delta < 0 and not allow_negative:
                return False
            try:
                supabase.table("holdings").insert({
                    "username": username, "company_id": company_id, "shares": round(delta, 4)
                }).execute()
                return True
            except Exception:
                continue                       # concurrent insert — retry finds the row
        old = row[0].get("shares") or 0
        new = round(old + delta, 4)
        if new < -1e-9 and not allow_negative:
            return False
        if supabase.table("holdings").update({"shares": new}) \
                .eq("id", row[0]["id"]).eq("shares", old).execute().data:
            return True
    return False


def _add_shares(username, company_id, delta):
    cas_shares(username, company_id, delta, allow_negative=True)


def _add_cash(username, delta):
    cas_adjust(username, "balance", delta, allow_negative=True)


def _best_bid(company_id):
    r = supabase.table("orders").select("price").eq("company_id", company_id) \
        .eq("side", "buy").eq("status", "open").order("price", desc=True).limit(1).execute().data
    return r[0]["price"] if r else None


def _best_ask(company_id):
    r = supabase.table("orders").select("price").eq("company_id", company_id) \
        .eq("side", "sell").eq("status", "open").order("price").limit(1).execute().data
    return r[0]["price"] if r else None


def _match(taker):
    """Match a freshly placed (and escrowed) limit order against the book,
       with price-time priority and price improvement for the taker."""
    cid, side = taker["company_id"], taker["side"]
    if side == "buy":
        makers = supabase.table("orders").select("*").eq("company_id", cid).eq("side", "sell") \
            .eq("status", "open").lte("price", taker["price"]) \
            .order("price").order("created_at").execute().data or []
    else:
        makers = supabase.table("orders").select("*").eq("company_id", cid).eq("side", "buy") \
            .eq("status", "open").gte("price", taker["price"]) \
            .order("price", desc=True).order("created_at").execute().data or []

    remaining = taker["quantity"] - taker["filled"]
    for m in makers:
        if remaining <= 0:
            break
        if m["username"] == taker["username"]:
            continue  # no self-trades
        avail = m["quantity"] - m["filled"]
        if avail <= 0:
            continue
        fill = min(remaining, avail)
        exec_price = m["price"]                       # resting order sets the price
        buyer  = taker["username"] if side == "buy" else m["username"]
        seller = m["username"]     if side == "buy" else taker["username"]

        # Atomically CLAIM this fill on the maker order first; if another taker
        # grabbed it concurrently, skip (no double-fill / share minting).
        nf = m["filled"] + fill
        claim = supabase.table("orders").update({
            "filled": nf, "status": "filled" if nf >= m["quantity"] else "open"
        }).eq("id", m["id"]).eq("filled", m["filled"]).eq("status", "open").execute()
        if not claim.data:
            continue

        _add_shares(buyer, cid, fill)                 # shares -> buyer
        _add_cash(seller, round(exec_price * fill, 2))# cash -> seller
        if side == "buy":                             # refund taker's price improvement
            refund = round((taker["price"] - exec_price) * fill, 2)
            if refund > 0:
                _add_cash(taker["username"], refund)

        supabase.table("trades").insert({
            "company_id": cid, "buyer": buyer, "seller": seller,
            "price": exec_price, "quantity": fill
        }).execute()
        supabase.table("companies").update({"last_price": exec_price}).eq("id", cid).execute()
        # notify the resting (maker) party that their order filled
        notify(m["username"],
               f"Your {('sell' if side=='buy' else 'buy')} order filled: {fill} shares @ {exec_price} CB.",
               "/exchange?company=" + str(cid))
        remaining -= fill

    tf = taker["quantity"] - remaining
    supabase.table("orders").update({
        "filled": tf, "status": "filled" if remaining <= 0 else "open"
    }).eq("id", taker["id"]).execute()
    return tf


@app.route("/exchange/listings")
def exchange_listings():
    res = supabase.table("companies").select("*").eq("is_public", True) \
        .order("created_at", desc=True).execute().data or []
    out = []
    for c in res:
        shares = c.get("shares") or 0
        lp = c.get("last_price") or c.get("ipo_price") or 0
        nav = round((c.get("balance") or 0) / shares, 4) if shares else 0
        out.append({
            "id": c["id"], "name": c["name"], "category": c["category"],
            "description": c.get("description", ""),
            "last_price": lp, "ipo_price": c.get("ipo_price") or 0,
            "market_cap": round(lp * shares, 2), "nav": nav, "shares": shares,
            "best_bid": _best_bid(c["id"]), "best_ask": _best_ask(c["id"]),
        })
    return jsonify(success=True, listings=out)


@app.route("/exchange/company/<int:cid>")
def exchange_company(cid):
    user = get_current_user(run_economics=False)
    c_res = supabase.table("companies").select("*").eq("id", cid).execute().data
    if not c_res:
        return jsonify(success=False, error="Company not found"), 404
    c = c_res[0]
    shares = c.get("shares") or 0
    lp = c.get("last_price") or c.get("ipo_price") or 0

    # Aggregate the order book by price level
    open_orders = supabase.table("orders").select("*").eq("company_id", cid) \
        .eq("status", "open").execute().data or []
    bids, asks = {}, {}
    for o in open_orders:
        rem = (o["quantity"] or 0) - (o["filled"] or 0)
        if rem <= 0:
            continue
        book = bids if o["side"] == "buy" else asks
        book[o["price"]] = book.get(o["price"], 0) + rem
    bids = sorted(([p, q] for p, q in bids.items()), key=lambda x: -x[0])
    asks = sorted(([p, q] for p, q in asks.items()), key=lambda x: x[0])

    trades = supabase.table("trades").select("*").eq("company_id", cid) \
        .order("created_at", desc=True).limit(40).execute().data or []
    history = list(reversed([{"t": t["created_at"], "p": t["price"]} for t in trades]))

    my_orders, my_shares, my_cash = [], 0, 0
    if user:
        my_shares = _holding_shares(user["username"], cid)
        my_cash = user.get("balance") or 0
        my_orders = supabase.table("orders").select("*").eq("company_id", cid) \
            .eq("username", user["username"]).eq("status", "open") \
            .order("created_at", desc=True).execute().data or []

    return jsonify(
        success=True,
        company={
            "id": c["id"], "name": c["name"], "category": c["category"],
            "description": c.get("description", ""), "founder": c["founder"],
            "is_public": c.get("is_public", False), "shares": shares,
            "last_price": lp, "ipo_price": c.get("ipo_price") or 0,
            "market_cap": round(lp * shares, 2),
            "nav": round((c.get("balance") or 0) / shares, 4) if shares else 0,
            "balance": c.get("balance") or 0,
        },
        best_bid=_best_bid(cid), best_ask=_best_ask(cid),
        bids=bids, asks=asks, history=history, recent_trades=trades[:12],
        my_shares=my_shares, my_cash=my_cash, my_orders=my_orders,
        is_founder=bool(user and user["username"] == c["founder"]),
    )


@app.route("/exchange/ipo", methods=["POST"])
@limiter.limit("10/minute")
def exchange_ipo():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401

    d = request.get_json()
    cid = d.get("company_id")
    shares = int(d.get("shares") or 0)
    price = float(d.get("price") or 0)

    c_res = supabase.table("companies").select("*").eq("id", cid).execute().data
    if not c_res:
        return jsonify(success=False, error="Company not found"), 404
    c = c_res[0]
    if user["username"] not in company_founders(c):
        return jsonify(success=False, error="Only a founder can take a company public"), 403
    if c.get("is_public"):
        return jsonify(success=False, error="Company is already public"), 400
    if (shares <= 0 or shares > 1_000_000 or not math.isfinite(price)
            or price <= 0 or price > 100_000):
        return jsonify(success=False, error="Shares 1–1,000,000 and IPO price 0–100,000 CB"), 400

    # IMPORTANT: do NOT fabricate company capital here. Setting balance = shares*price
    # would let a founder pay themselves a dividend from money that never existed
    # (the "IPO money printer"). A company's capital only comes from real earnings.
    supabase.table("companies").update({
        "is_public": True, "shares": shares, "ipo_price": price, "last_price": price
    }).eq("id", cid).execute()
    _add_shares(user["username"], cid, shares)         # founder owns 100% at IPO
    add_record(user["username"],
               f"Took '{c['name']}' public: {shares} shares at {price} CB (IPO).")
    return jsonify(success=True)


@app.route("/exchange/order", methods=["POST"])
@limiter.limit("40/minute")
def exchange_order():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401

    d = request.get_json()
    cid = d.get("company_id")
    side = d.get("side")
    qty = int(d.get("quantity") or 0)
    try:
        price = float(d.get("price") or 0)
    except (TypeError, ValueError):
        price = 0

    if side not in ("buy", "sell"):
        return jsonify(success=False, error="Pick buy or sell"), 400
    if qty <= 0 or qty > 10_000_000 or not math.isfinite(price) or price <= 0 or price > 1_000_000:
        return jsonify(success=False, error="Price 0–1,000,000 CB and a positive quantity"), 400

    c_res = supabase.table("companies").select("is_public").eq("id", cid).execute().data
    if not c_res or not c_res[0].get("is_public"):
        return jsonify(success=False, error="Company is not listed"), 400

    # Escrow
    fresh = supabase.table("cybucks").select("balance").eq("username", user["username"]).execute().data[0]
    if side == "buy":
        cost = round(price * qty, 2)
        if (fresh["balance"] or 0) < cost:
            return jsonify(success=False, error="Not enough CB to escrow this buy order"), 400
        if cost > _available_cb(user["username"], fresh.get("balance")):
            return jsonify(success=False, error="Borrowed Cybucks cannot be used to trade. Repay your loan first."), 400
        if not cas_adjust(user["username"], "balance", -cost):
            return jsonify(success=False, error="Insufficient funds or a conflicting order — try again."), 400
    else:
        # Atomic share escrow — prevents overselling via concurrent sell orders.
        if not cas_shares(user["username"], cid, -qty):
            return jsonify(success=False, error="You don't own that many shares"), 400

    order = supabase.table("orders").insert({
        "company_id": cid, "username": user["username"], "side": side,
        "price": price, "quantity": qty, "filled": 0, "status": "open"
    }).execute().data[0]

    filled = _match(order)
    return jsonify(success=True, filled=filled, quantity=qty)


@app.route("/exchange/cancel", methods=["POST"])
@limiter.limit("30/minute")
def exchange_cancel():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401

    oid = request.get_json().get("order_id")
    o_res = supabase.table("orders").select("*").eq("id", oid) \
        .eq("username", user["username"]).execute().data
    if not o_res:
        return jsonify(success=False, error="Order not found"), 404
    o = o_res[0]
    if o["status"] != "open":
        return jsonify(success=False, error="Order is not open"), 400

    # Atomically claim the cancellation first, so a double-cancel (or a cancel
    # racing the matcher) can't refund the escrow twice.
    claim = supabase.table("orders").update({"status": "cancelled"}) \
        .eq("id", oid).eq("status", "open").execute()
    if not claim.data:
        return jsonify(success=False, error="Order is no longer open"), 400

    # Re-read the now-final fill so we refund only the genuinely unfilled remainder.
    fresh = supabase.table("orders").select("quantity,filled").eq("id", oid).execute().data[0]
    rem = (fresh["quantity"] or 0) - (fresh["filled"] or 0)
    if rem > 0:
        if o["side"] == "buy":
            _add_cash(user["username"], round(o["price"] * rem, 2))   # refund escrow
        else:
            _add_shares(user["username"], o["company_id"], rem)       # return shares
    return jsonify(success=True)


@app.route("/exchange/portfolio")
def exchange_portfolio():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401

    rows = supabase.table("holdings").select("*").eq("username", user["username"]).execute().data or []
    out, total = [], 0
    for h in rows:
        if (h["shares"] or 0) <= 0:
            continue
        c = supabase.table("companies").select("name,last_price,ipo_price") \
            .eq("id", h["company_id"]).execute().data
        if not c:
            continue
        c = c[0]
        lp = c.get("last_price") or c.get("ipo_price") or 0
        value = round(lp * h["shares"], 2)
        total += value
        out.append({
            "company_id": h["company_id"], "name": c["name"],
            "shares": h["shares"], "price": lp, "value": value
        })
    return jsonify(success=True, holdings=out, total_value=round(total, 2),
                   cash=user.get("balance") or 0)


@app.route("/portfolio_data")
def portfolio_data():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    rows = supabase.table("holdings").select("*").eq("username", me).execute().data or []
    holdings, market_total, book_total = [], 0.0, 0.0
    for h in rows:
        sh = h.get("shares") or 0
        if sh <= 0:
            continue
        c = supabase.table("companies").select("id,name,founder,cofounders,balance,shares,last_price,ipo_price") \
            .eq("id", h["company_id"]).execute().data
        if not c:
            continue
        c = c[0]
        price = c.get("last_price") or c.get("ipo_price") or 0
        comp_shares = c.get("shares") or 0
        nav = (max(c.get("balance") or 0, 0) / comp_shares) if comp_shares > 0 else 0
        mv, bv = round(price * sh, 2), round(nav * sh, 2)
        market_total += mv
        book_total += bv
        holdings.append({
            "company_id": c["id"], "name": c["name"], "shares": sh,
            "price": round(price, 2), "market_value": mv, "book_value": bv,
            "founder": c["founder"], "is_founder": me in company_founders(c),
        })
    holdings.sort(key=lambda x: -x["market_value"])
    cash = {"cybucks": user.get("balance") or 0, "pufb": user.get("pufb") or 0,
            "aquilines": user.get("aquilines") or 0, "cybits": user.get("cybits") or 0}
    cash_cb = round(cash["cybucks"] + cash["pufb"] * CYBUCK_VALUE["pufb"]
                    + cash["aquilines"] * CYBUCK_VALUE["aquilines"]
                    + cash["cybits"] * CYBUCK_VALUE["cybit"], 2)
    return jsonify(success=True, holdings=holdings,
                   market_total=round(market_total, 2), book_total=round(book_total, 2),
                   cash=cash, cash_cb=cash_cb, net_worth=user_net_worth(me))


@app.route("/exchange/dividend", methods=["POST"])
@limiter.limit("10/minute")
def exchange_dividend():
    """Founder pays a per-share dividend to all shareholders from company capital."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401

    d = request.get_json()
    cid = d.get("company_id")
    per_share = float(d.get("per_share") or 0)

    c_res = supabase.table("companies").select("*").eq("id", cid).execute().data
    if not c_res:
        return jsonify(success=False, error="Company not found"), 404
    c = c_res[0]
    if user["username"] not in company_founders(c):
        return jsonify(success=False, error="Only a founder can pay a dividend"), 403
    if not math.isfinite(per_share) or per_share <= 0:
        return jsonify(success=False, error="Enter a positive amount per share"), 400

    holders = supabase.table("holdings").select("*").eq("company_id", cid).execute().data or []
    total = round(sum((h["shares"] or 0) for h in holders) * per_share, 2)
    if total <= 0:
        return jsonify(success=False, error="No shares to pay a dividend on"), 400
    # Atomically debit the company's capital FIRST; only pay if it covered the bill.
    if not cas_num("companies", [("id", cid)], "balance", -total):
        return jsonify(success=False, error=f"Company needs {total} CB capital to pay this"), 400

    for h in holders:
        if (h["shares"] or 0) > 0:
            _add_cash(h["username"], round(h["shares"] * per_share, 2))
    add_record(user["username"], f"Paid a {per_share} CB/share dividend for '{c['name']}' ({total} CB).")
    return jsonify(success=True, total=total)


# ============================================================
#  MARKETPLACE  (citizens & companies sell / donate goods)
# ============================================================
def _add_company_currency(company_id, currency, delta):
    cas_num("companies", [("id", company_id)], CURRENCY_COLUMN[currency], delta, allow_negative=True)


def _seller_label(item):
    if item.get("company_id"):
        c = supabase.table("companies").select("name").eq("id", item["company_id"]).execute().data
        if c:
            return c[0]["name"] + " (company)"
    return item["seller"]


def _fetch_market(state):
    """Available listings for a given state, or the stateless Import/Export
    pool when `state` is None. Falls back gracefully if the `state` column
    hasn't been added to market_items yet."""
    base = supabase.table("market_items").select("*").eq("status", "available")
    try:
        q = base.eq("state", state) if state else base.is_("state", "null")
        return q.order("created_at", desc=True).execute().data or []
    except Exception:        # column not migrated yet
        if state:
            return []        # cannot scope to a state without the column
        return base.order("created_at", desc=True).execute().data or []


@app.route("/market", methods=["GET"])
def market_list():
    user = get_current_user(run_economics=False)
    state = request.args.get("state") or None
    if state:
        if state not in STATE_IDS:
            return jsonify(success=False, error="Unknown state"), 404
        if not user:
            return jsonify(success=False, error="Not logged in"), 401
        ok, err = _state_gate(user, state)
        if not ok:
            return jsonify(success=False, error=err), 403

    items = _fetch_market(state)
    for it in items:
        it["seller_label"] = _seller_label(it)
    # companies this citizen can sell on behalf of
    my_companies = []
    if user:
        my_companies = supabase.table("companies").select("id,name") \
            .eq("founder", user["username"]).execute().data or []
    return jsonify(success=True, items=items, me=user["username"] if user else None,
                   my_companies=my_companies, state=state)


@app.route("/market", methods=["POST"])
@limiter.limit("15/minute")
def market_create():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401

    d = request.get_json()
    title = (d.get("title") or "").strip()
    description = (d.get("description") or "").strip()
    image_url = (d.get("image_url") or "").strip()
    currency = d.get("currency", "cybucks")
    kind = d.get("kind", "sale")
    company_id = d.get("company_id") or None
    state = d.get("state") or None
    try:
        price = float(d.get("price") or 0)
    except (TypeError, ValueError):
        price = 0

    if not title:
        return jsonify(success=False, error="Give your item a title"), 400
    if currency not in CURRENCY_COLUMN:
        return jsonify(success=False, error="Unknown currency"), 400
    if kind not in ("sale", "donation"):
        kind = "sale"
    if kind == "sale" and (not math.isfinite(price) or price <= 0):
        return jsonify(success=False, error="Set a price (or list it as a donation)"), 400
    if kind == "donation":
        price = 0

    if state:                       # listing inside a state market needs clearance
        if state not in STATE_IDS:
            return jsonify(success=False, error="Unknown state"), 404
        ok, err = _state_gate(user, state)
        if not ok:
            return jsonify(success=False, error=err), 403

    if company_id:
        c = supabase.table("companies").select("*").eq("id", company_id).execute().data
        if not c or user["username"] not in company_founders(c[0]):
            return jsonify(success=False, error="You can only sell for your own company"), 403

    row = {
        "seller": user["username"], "company_id": company_id,
        "title": title, "description": description, "image_url": image_url,
        "price": price, "currency": currency, "kind": kind,
    }
    if state:
        row["state"] = state
    try:
        item = supabase.table("market_items").insert(row).execute().data[0]
    except Exception:
        if state:                   # column not migrated yet
            return jsonify(success=False,
                           error="State marketplaces aren't enabled yet — the database needs a quick update."), 503
        raise
    return jsonify(success=True, item=item)


@app.route("/market/buy", methods=["POST"])
@limiter.limit("30/minute")
def market_buy():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401

    item_id = request.get_json().get("item_id")
    r = supabase.table("market_items").select("*").eq("id", item_id) \
        .eq("status", "available").execute().data
    if not r:
        return jsonify(success=False, error="Item no longer available"), 404
    item = r[0]
    if item["seller"] == user["username"] and not item.get("company_id"):
        return jsonify(success=False, error="You can't buy your own item"), 400
    if item.get("state"):            # must be cleared into the state to trade there
        ok, err = _state_gate(user, item["state"])
        if not ok:
            return jsonify(success=False, error=err), 403

    if item["kind"] == "sale":
        col = CURRENCY_COLUMN[item["currency"]]
        buyer = supabase.table("cybucks").select("*").eq("username", user["username"]).execute().data[0]
        if (buyer.get(col) or 0) < item["price"]:
            return jsonify(success=False, error="Not enough " + item["currency"]), 400
        if item["currency"] == "cybucks" and item["price"] > _available_cb(user["username"], buyer.get("balance")):
            return jsonify(success=False, error="Borrowed Cybucks cannot be spent here. Repay your loan first."), 400
        # pay (atomic — prevents buying the same item twice via a race)
        if not cas_adjust(user["username"], col, -item["price"]):
            return jsonify(success=False, error="Insufficient funds or a conflicting purchase — try again."), 400
        if item.get("company_id"):
            _add_company_currency(item["company_id"], item["currency"], item["price"])
        else:
            _add_cash_currency(item["seller"], item["currency"], item["price"])
        add_record(user["username"], f"Bought '{item['title']}' for {item['price']} {item['currency']}.")
        log_txn("market", user["username"], _seller_label(item), item["price"],
                item["currency"], f"Bought '{item['title']}'")
        notify(item["seller"], f"{user['username']} bought your '{item['title']}' for {item['price']} {item['currency']}.", "/marketplace")
    else:
        add_record(user["username"], f"Received donated item '{item['title']}'.")
        log_txn("market", _seller_label(item), user["username"], 0,
                item["currency"], f"Donated '{item['title']}'")

    supabase.table("market_items").update({"status": "sold", "buyer": user["username"]}) \
        .eq("id", item_id).execute()

    # Cyvazon: money has already changed hands, so raise a parcel unless the
    # buyer says they'll collect it themselves. Without a tracked handover
    # there'd be nothing stopping a seller taking the payment and keeping the
    # goods — which is exactly what the delivery record is for.
    d = request.get_json() or {}
    parcel = None
    if d.get("collect") is not True:
        parcel = raise_parcel(
            "market", item_id, item["title"], item["seller"], user["username"],
            user["username"],
            pickup={"class": d.get("pickup_class"), "area": d.get("pickup_area")},
            dropoff={"class": d.get("dropoff_class"), "area": d.get("dropoff_area")},
            notes=d.get("delivery_notes"))
    return jsonify(success=True, delivery=(_delivery_public(parcel) if parcel else None))


def _add_cash_currency(username, currency, delta):
    cas_adjust(username, CURRENCY_COLUMN[currency], delta, allow_negative=True)


@app.route("/market/delete", methods=["POST"])
@limiter.limit("20/minute")
def market_delete():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    item_id = request.get_json().get("item_id")
    r = supabase.table("market_items").select("*").eq("id", item_id) \
        .eq("seller", user["username"]).execute().data
    if not r:
        return jsonify(success=False, error="Listing not found"), 404
    supabase.table("market_items").update({"status": "sold"}).eq("id", item_id).execute()
    return jsonify(success=True)


# ============================================================
#  JOBS / EMPLOYMENT
# ============================================================
@app.route("/company_info/<int:cid>")
def company_info(cid):
    user = get_current_user(run_economics=False)
    c_res = supabase.table("companies").select("*").eq("id", cid).execute().data
    if not c_res:
        return jsonify(success=False, error="Company not found"), 404
    c = c_res[0]
    founders = company_founders(c)
    is_founder = bool(user and user["username"] in founders)

    employees = supabase.table("employment").select("*").eq("company_id", cid) \
        .eq("status", "employed").execute().data or []
    applications = []
    if is_founder:
        applications = supabase.table("employment").select("*").eq("company_id", cid) \
            .eq("status", "pending").execute().data or []

    my = None
    if user:
        mr = supabase.table("employment").select("*").eq("company_id", cid) \
            .eq("username", user["username"]).execute().data
        my = mr[0] if mr else None

    goods = supabase.table("market_items").select("*").eq("company_id", cid) \
        .eq("status", "available").execute().data or []

    shares = c.get("shares") or 0
    lp = c.get("last_price") or c.get("ipo_price") or 0
    trades = supabase.table("trades").select("created_at,price").eq("company_id", cid) \
        .order("created_at").limit(50).execute().data or []
    history = [{"t": t["created_at"], "p": t["price"]} for t in trades]
    net_worth = round((c.get("balance") or 0) + (c.get("pufb") or 0) * CYBUCK_VALUE["pufb"]
                      + (c.get("aquilines") or 0) * CYBUCK_VALUE["aquilines"], 2)

    return jsonify(
        success=True,
        company={
            "id": c["id"], "name": c["name"], "category": c["category"],
            "description": c.get("description", ""), "founder": c["founder"],
            "cofounders": [x for x in founders if x != c["founder"]],
            "balance": c.get("balance") or 0,
            "pufb": c.get("pufb") or 0, "aquilines": c.get("aquilines") or 0,
            "is_public": c.get("is_public", False), "shares": shares,
            "last_price": lp, "ipo_price": c.get("ipo_price") or 0,
            "market_cap": round(lp * shares, 2),
            "nav": round((c.get("balance") or 0) / shares, 4) if shares else 0,
            "net_worth": net_worth, "history": history,
        },
        employees=employees, applications=applications, my_employment=my,
        is_founder=is_founder, goods=goods,
    )


@app.route("/company/cofounder", methods=["POST"])
@limiter.limit("10/minute")
def add_cofounder():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    d = request.get_json()
    cid = d.get("company_id")
    target = (d.get("username") or "").strip()

    c = supabase.table("companies").select("*").eq("id", cid).execute().data
    if not c:
        return jsonify(success=False, error="Company not found"), 404
    c = c[0]
    if user["username"] not in company_founders(c):
        return jsonify(success=False, error="Only a founder can add co-founders"), 403
    if not target:
        return jsonify(success=False, error="Enter a username"), 400
    if not supabase.table("cybucks").select("id").eq("username", target).execute().data:
        return jsonify(success=False, error="No such citizen"), 404
    if target in company_founders(c):
        return jsonify(success=False, error="Already a founder of this company"), 400

    cf = c.get("cofounders") or []
    if not isinstance(cf, list):
        cf = []
    cf.append(target)
    supabase.table("companies").update({"cofounders": cf}).eq("id", cid).execute()
    add_record(target, f"Became a co-founder of '{c['name']}'.")
    notify(target, f"You are now a co-founder of '{c['name']}'!", "/company?id=" + str(cid))
    return jsonify(success=True)


@app.route("/referrals")
@limiter.limit("10/minute")
def referrals():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    counts, my_count = {}, 0
    try:
        rows = supabase.table("cybucks").select("referred_by,banned").execute().data or []
        for r in rows:
            rb = r.get("referred_by")
            if rb and not r.get("banned"):
                counts[rb] = counts.get(rb, 0) + 1
        my_count = counts.get(me, 0)
    except Exception:
        pass      # column not migrated yet
    leaderboard = [{"username": k, "count": v}
                   for k, v in sorted(counts.items(), key=lambda x: -x[1])[:10]]
    return jsonify(success=True, me=me, my_count=my_count,
                   earned=my_count * REFERRAL_BONUS, bonus=REFERRAL_BONUS,
                   leaderboard=leaderboard)


@app.route("/jobs/board")
def jobs_board():
    """Nationwide careers board: every company, its size, and your standing with it."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    companies = supabase.table("companies") \
        .select("id,name,category,description,founder,cofounders").execute().data or []
    emps = supabase.table("employment").select("company_id,username,status").execute().data or []
    count, my_status = {}, {}
    for e in emps:
        if e["status"] == "employed":
            count[e["company_id"]] = count.get(e["company_id"], 0) + 1
        if e["username"] == me:
            my_status[e["company_id"]] = e["status"]
    out = []
    for c in companies:
        out.append({
            "id": c["id"], "name": c["name"], "category": c["category"],
            "description": c.get("description", ""), "founder": c["founder"],
            "employees": count.get(c["id"], 0),
            "is_founder": me in company_founders(c),
            "my_status": my_status.get(c["id"]),
        })
    out.sort(key=lambda x: (-x["employees"], x["name"].lower()))
    return jsonify(success=True, companies=out, me=me)


@app.route("/jobs/apply", methods=["POST"])
def jobs_apply():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    cid = request.get_json().get("company_id")

    c = supabase.table("companies").select("*").eq("id", cid).execute().data
    if not c:
        return jsonify(success=False, error="Company not found"), 404
    if user["username"] in company_founders(c[0]):
        return jsonify(success=False, error="You're a founder of this company"), 400

    existing = supabase.table("employment").select("*").eq("company_id", cid) \
        .eq("username", user["username"]).execute().data
    if existing:
        st = existing[0]["status"]
        if st in ("pending", "employed"):
            return jsonify(success=False, error="You already have an application/role here"), 400
        # re-apply after reject/fire
        supabase.table("employment").update({"status": "pending", "applied_at": _now().isoformat()}) \
            .eq("id", existing[0]["id"]).execute()
    else:
        supabase.table("employment").insert({
            "company_id": cid, "username": user["username"], "status": "pending"
        }).execute()
    return jsonify(success=True)


def _assert_founder(user, cid):
    c = supabase.table("companies").select("*").eq("id", cid).execute().data
    if not c or user["username"] not in company_founders(c[0]):
        return None
    return c[0]


@app.route("/jobs/decide", methods=["POST"])
@limiter.limit("20/minute")
def jobs_decide():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    d = request.get_json()
    emp = supabase.table("employment").select("*").eq("id", d.get("id")).execute().data
    if not emp:
        return jsonify(success=False, error="Application not found"), 404
    emp = emp[0]
    comp = _assert_founder(user, emp["company_id"])
    if not comp:
        return jsonify(success=False, error="Only the founder can decide"), 403

    if d.get("accept"):
        try:
            salary = float(d.get("salary") or 0)
        except (TypeError, ValueError):
            salary = 0
        role = (d.get("role") or "Employee").strip() or "Employee"
        supabase.table("employment").update({
            "status": "employed", "salary": salary, "role": role,
            "hired_at": _now().isoformat(), "last_paid": _now().isoformat(),
            "last_flagged": None
        }).eq("id", emp["id"]).execute()
        add_record(emp["username"], f"Hired as {role} at '{comp['name']}' ({salary} CB/month).")
        notify(emp["username"], f"You were hired as {role} at '{comp['name']}' ({salary} CB/month).", "/company?id=" + str(emp["company_id"]))
    else:
        supabase.table("employment").update({"status": "rejected"}).eq("id", emp["id"]).execute()
        notify(emp["username"], f"Your application to '{comp['name']}' was rejected.", "/company")
    return jsonify(success=True)


@app.route("/jobs/pay", methods=["POST"])
@limiter.limit("30/minute")
def jobs_pay():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    emp = supabase.table("employment").select("*").eq("id", request.get_json().get("id")).execute().data
    if not emp:
        return jsonify(success=False, error="Employee not found"), 404
    emp = emp[0]
    comp = _assert_founder(user, emp["company_id"])
    if not comp:
        return jsonify(success=False, error="Only the founder can pay"), 403
    if emp["status"] != "employed":
        return jsonify(success=False, error="Not an active employee"), 400

    salary = emp.get("salary") or 0
    cb = supabase.table("companies").select("balance").eq("id", emp["company_id"]).execute().data[0]
    if (cb.get("balance") or 0) < salary:
        return jsonify(success=False, error="Company doesn't have enough CB to pay this salary"), 400

    # Atomically debit company capital first; only pay if it covered the salary.
    if salary > 0 and not cas_num("companies", [("id", emp["company_id"])], "balance", -salary):
        return jsonify(success=False, error="Company doesn't have enough CB to pay this salary"), 400
    _add_cash(emp["username"], salary)
    supabase.table("employment").update({
        "last_paid": _now().isoformat(), "last_flagged": None
    }).eq("id", emp["id"]).execute()
    add_record(emp["username"], f"Received {salary} CB salary from '{comp['name']}'.")
    notify(emp["username"], f"You were paid {salary} CB salary by '{comp['name']}'.", "/bank")
    return jsonify(success=True)


@app.route("/jobs/fire", methods=["POST"])
@limiter.limit("20/minute")
def jobs_fire():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    emp = supabase.table("employment").select("*").eq("id", request.get_json().get("id")).execute().data
    if not emp:
        return jsonify(success=False, error="Employee not found"), 404
    emp = emp[0]
    comp = _assert_founder(user, emp["company_id"])
    if not comp:
        return jsonify(success=False, error="Only the founder can fire"), 403
    supabase.table("employment").update({"status": "fired"}).eq("id", emp["id"]).execute()
    add_record(emp["username"], f"Fired from '{comp['name']}'.")
    notify(emp["username"], f"You were fired from '{comp['name']}'.", "/company")
    return jsonify(success=True)


# ============================================================
#  SAVINGS  (earns monthly interest)
# ============================================================
@app.route("/savings")
def savings_view():
    user = get_current_user()
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    return jsonify(success=True, savings=user.get("savings") or 0,
                   balance=user.get("balance") or 0, rate=SAVINGS_RATE)


@app.route("/savings/deposit", methods=["POST"])
@limiter.limit("20/minute")
def savings_deposit():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    amount = float(request.get_json().get("amount") or 0)
    if not math.isfinite(amount) or amount <= 0:
        return jsonify(success=False, error="Enter a positive amount"), 400
    if (user.get("balance") or 0) < amount:
        return jsonify(success=False, error="Not enough CB"), 400
    if amount > _available_cb(user["username"], user.get("balance")):
        return jsonify(success=False, error="Borrowed Cybucks cannot be moved to savings. Repay your loan first."), 400
    if not cas_adjust(user["username"], "balance", -amount):
        return jsonify(success=False, error="Insufficient funds or a conflicting request — try again."), 400
    cas_adjust(user["username"], "savings", amount, allow_negative=True)
    if not user.get("savings_updated"):
        supabase.table("cybucks").update({"savings_updated": _now().isoformat()}) \
            .eq("username", user["username"]).execute()
    return jsonify(success=True)


@app.route("/savings/withdraw", methods=["POST"])
@limiter.limit("20/minute")
def savings_withdraw():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    amount = float(request.get_json().get("amount") or 0)
    if not math.isfinite(amount) or amount <= 0:
        return jsonify(success=False, error="Enter a positive amount"), 400
    if (user.get("savings") or 0) < amount:
        return jsonify(success=False, error="Not enough in savings"), 400
    if not cas_adjust(user["username"], "savings", -amount):
        return jsonify(success=False, error="Not enough in savings or a conflicting request — try again."), 400
    cas_adjust(user["username"], "balance", amount, allow_negative=True)
    return jsonify(success=True)


# ============================================================
#  GOVERNMENT BONDS
# ============================================================
@app.route("/bonds")
def bonds_view():
    user = get_current_user()
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    rows = supabase.table("bonds").select("*").eq("username", user["username"]) \
        .order("bought_at", desc=True).execute().data or []
    now = _now()
    for b in rows:
        m = _parse(b.get("matures_at"))
        b["matured"] = bool(m and now >= m)
        b["payout"] = round(b["principal"] * (1 + (b.get("rate") or BOND_RATE)), 2)
    return jsonify(success=True, bonds=rows, rate=BOND_RATE, days=BOND_DAYS,
                   balance=user.get("balance") or 0)


@app.route("/bonds/buy", methods=["POST"])
@limiter.limit("15/minute")
def bonds_buy():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    amount = float(request.get_json().get("amount") or 0)
    if not math.isfinite(amount) or amount <= 0:
        return jsonify(success=False, error="Enter a positive amount"), 400
    if (user.get("balance") or 0) < amount:
        return jsonify(success=False, error="Not enough CB"), 400
    if amount > _available_cb(user["username"], user.get("balance")):
        return jsonify(success=False, error="Borrowed Cybucks cannot be invested. Repay your loan first."), 400

    if not cas_adjust(user["username"], "balance", -amount):
        return jsonify(success=False, error="Insufficient funds or a conflicting request — try again."), 400
    matures = _now() + timedelta(days=BOND_DAYS)
    treasury_add(cybucks=amount, counterparty=user["username"], kind="bond_sale")
    bond = supabase.table("bonds").insert({
        "username": user["username"], "principal": amount,
        "rate": BOND_RATE, "matures_at": matures.isoformat()
    }).execute().data[0]
    add_record(user["username"], f"Bought a {amount} CB government bond (matures {matures.date()}).")
    return jsonify(success=True, bond=bond)


@app.route("/bonds/redeem", methods=["POST"])
@limiter.limit("15/minute")
def bonds_redeem():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    bid = request.get_json().get("bond_id")
    r = supabase.table("bonds").select("*").eq("id", bid) \
        .eq("username", user["username"]).execute().data
    if not r:
        return jsonify(success=False, error="Bond not found"), 404
    b = r[0]
    if b.get("redeemed"):
        return jsonify(success=False, error="Already redeemed"), 400
    if _now() < _parse(b["matures_at"]):
        return jsonify(success=False, error="Bond hasn't matured yet"), 400

    # Atomically claim the redemption FIRST — only the request that flips
    # redeemed False->True may pay out. Kills the concurrent double-redeem mint.
    claim = supabase.table("bonds").update({"redeemed": True}) \
        .eq("id", bid).eq("redeemed", False).execute()
    if not claim.data:
        return jsonify(success=False, error="Already redeemed"), 400

    payout = round(b["principal"] * (1 + (b.get("rate") or BOND_RATE)), 2)
    _add_cash(user["username"], payout)
    treasury_add(cybucks=-payout, counterparty=user["username"], kind="bond_redeem")
    add_record(user["username"], f"Redeemed a bond for {payout} CB.")
    notify(user["username"], f"Your government bond matured — {payout} CB paid out.", "/bank")
    return jsonify(success=True, payout=payout)


# ============================================================
#  COURTS & JUSTICE
# ============================================================
@app.route("/court/list")
def court_list():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    cases = supabase.table("court_cases").select("*") \
        .order("created_at", desc=True).limit(60).execute().data or []
    g = supabase.table("government").select("holder").eq("position", "Judge").execute().data
    judge = g[0]["holder"] if g else "Vacant"
    return jsonify(success=True, cases=cases, is_judge=is_court_judge(user),
                   judge=judge, me=user["username"])


@app.route("/court/case/<int:cid>")
def court_case(cid):
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    r = supabase.table("court_cases").select("*").eq("id", cid).execute().data
    if not r:
        return jsonify(success=False, error="Case not found"), 404
    debate = supabase.table("case_debate").select("*").eq("case_id", cid) \
        .order("created_at").limit(100).execute().data or []
    return jsonify(success=True, case=r[0], debate=debate,
                   is_judge=is_court_judge(user), me=user["username"])


@app.route("/court/debate", methods=["POST"])
@limiter.limit("30/minute")
def court_debate():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    d = request.get_json()
    comment = (d.get("comment") or "").strip()
    if not comment:
        return jsonify(success=False, error="Empty remark"), 400
    supabase.table("case_debate").insert({
        "case_id": d.get("case_id"), "author": user["username"], "comment": comment
    }).execute()
    return jsonify(success=True)


@app.route("/court/file", methods=["POST"])
@limiter.limit("10/minute")
def court_file():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    d = request.get_json()
    defendant = (d.get("defendant") or "").strip()
    title = (d.get("title") or "").strip()
    description = (d.get("description") or "").strip()
    try:
        claim = float(d.get("claim") or 0)
    except (TypeError, ValueError):
        claim = 0

    if not defendant or not title:
        return jsonify(success=False, error="Pick a defendant and a title"), 400
    if defendant == user["username"]:
        return jsonify(success=False, error="You can't sue yourself"), 400
    if not supabase.table("cybucks").select("id").eq("username", defendant).execute().data:
        return jsonify(success=False, error="No such citizen"), 404

    supabase.table("court_cases").insert({
        "plaintiff": user["username"], "defendant": defendant,
        "title": title, "description": description, "claim": claim
    }).execute()
    notify(defendant, f"⚖️ {user['username']} filed a court case against you: '{title}'.", "/court")
    return jsonify(success=True)


@app.route("/court/rule", methods=["POST"])
@limiter.limit("20/minute")
def court_rule():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    if not is_court_judge(user):
        return jsonify(success=False, error="Only the elected Judge (or the President) may rule on cases"), 403

    d = request.get_json()
    r = supabase.table("court_cases").select("*").eq("id", d.get("case_id")).execute().data
    if not r:
        return jsonify(success=False, error="Case not found"), 404
    case = r[0]
    if case["status"] != "open":
        return jsonify(success=False, error="Case already ruled"), 400
    # Conflict of interest: a judge may not rule on a case they are party to.
    if user["username"] in (case.get("plaintiff"), case.get("defendant")):
        return jsonify(success=False, error="You cannot rule on a case you are a party to"), 403
    # Atomically claim the ruling so concurrent rulings can't double-fine.
    if not supabase.table("court_cases").update({"status": "ruling"}) \
            .eq("id", case["id"]).eq("status", "open").execute().data:
        return jsonify(success=False, error="Case already being ruled"), 400

    verdict = d.get("verdict")          # 'guilty' | 'dismissed'
    note = (d.get("note") or "").strip()
    try:
        fine = float(d.get("fine") or 0)
        if not math.isfinite(fine) or fine < 0:
            fine = 0
    except (TypeError, ValueError):
        fine = 0
    # A sentence may be a fine, jail time, or both.
    try:
        jail_days = float(d.get("jail_days") or 0)
        if not math.isfinite(jail_days) or jail_days < 0:
            jail_days = 0
        jail_days = min(jail_days, MAX_JAIL_DAYS)
    except (TypeError, ValueError):
        jail_days = 0

    if verdict == "guilty":
        paid = 0
        if fine > 0:
            defu = supabase.table("cybucks").select("balance").eq("username", case["defendant"]).execute().data[0]
            paid = round(min(fine, defu.get("balance") or 0), 2)   # take what they have
            if paid > 0 and not cas_adjust(case["defendant"], "balance", -paid):
                paid = 0                                  # balance moved — collect nothing
            if paid > 0:
                _add_cash(case["plaintiff"], paid)        # damages to the plaintiff
            log_txn("court", case["defendant"], case["plaintiff"], paid, "cybucks", "Court fine")
        supabase.table("court_cases").update({
            "status": "guilty", "verdict": note, "fine": paid,
            "jail_days": jail_days,
            "judge": user["username"], "ruled_at": _now().isoformat()
        }).eq("id", case["id"]).execute()
        # The sentence goes on the permanent criminal record, which is what
        # bars the defendant from standing for office.
        if paid > 0 or jail_days > 0:
            add_criminal_record(case["defendant"], user["username"],
                                f"{case['title']}: {note}".strip(" :"),
                                fine=paid, jail_days=jail_days, case_id=case["id"])
        if jail_days > 0:
            send_to_jail(case["defendant"], jail_days,
                         f"Sentenced in '{case['title']}'", user["username"])
        sentence = []
        if paid > 0:
            sentence.append(f"fined {paid} CB")
        if jail_days > 0:
            sentence.append(f"jailed for {jail_days:g} day(s)")
        sent_txt = " and ".join(sentence) if sentence else "convicted without penalty"
        add_record(case["defendant"], f"Found GUILTY in '{case['title']}' — {sent_txt}. {note}".strip())
        notify(case["defendant"], f"⚖️ You were found guilty in '{case['title']}' — {sent_txt}.", "/court")
        notify(case["plaintiff"], f"⚖️ You won '{case['title']}' — awarded {paid} CB.", "/court")
    else:
        supabase.table("court_cases").update({
            "status": "dismissed", "verdict": note,
            "judge": user["username"], "ruled_at": _now().isoformat()
        }).eq("id", case["id"]).execute()
        notify(case["plaintiff"], f"⚖️ Your case '{case['title']}' was dismissed.", "/court")
        notify(case["defendant"], f"⚖️ The case '{case['title']}' against you was dismissed.", "/court")

    return jsonify(success=True)


# ============================================================
#  LOANS
# ============================================================
@app.route("/loans/list", methods=["GET"])
def my_loans():
    user = get_current_user()
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    res = supabase.table("loans").select("*").eq("username", user["username"]) \
        .order("taken_at", desc=True).execute()
    return jsonify(success=True, loans=res.data or [], max_loan=LOAN_MAX, balance=user.get("balance") or 0)


@app.route("/loans", methods=["POST"])
@limiter.limit("10/minute")
def take_loan():
    user = get_current_user()
    if not user:
        return jsonify(success=False, error="Not logged in"), 401

    amount = float(request.get_json().get("amount") or 0)
    if not math.isfinite(amount) or amount <= 0 or amount > LOAN_MAX:
        return jsonify(success=False, error=f"Loans must be between 1 and {LOAN_MAX} CB"), 400

    active = supabase.table("loans").select("id").eq("username", user["username"]) \
        .eq("repaid", False).eq("defaulted", False).execute()
    if active.data:
        return jsonify(success=False, error="Repay your active loan first"), 400

    due = _now() + timedelta(days=LOAN_DAYS)
    loan = supabase.table("loans").insert({
        "username": user["username"], "amount": amount, "due_at": due.isoformat()
    }).execute().data[0]

    supabase.table("cybucks").update({"balance": round((user.get("balance") or 0) + amount, 2)}) \
        .eq("username", user["username"]).execute()
    add_record(user["username"], f"Took a {amount} CB loan (due {due.date()}).")

    return jsonify(success=True, loan=loan)


@app.route("/loans/repay", methods=["POST"])
@limiter.limit("10/minute")
def repay_loan():
    user = get_current_user()
    if not user:
        return jsonify(success=False, error="Not logged in"), 401

    loan_res = supabase.table("loans").select("*").eq("username", user["username"]) \
        .eq("repaid", False).eq("defaulted", False).execute()
    if not loan_res.data:
        return jsonify(success=False, error="No active loan"), 400
    loan = loan_res.data[0]

    if (user.get("balance") or 0) < loan["amount"]:
        return jsonify(success=False, error="Not enough CB to repay"), 400

    if not cas_adjust(user["username"], "balance", -loan["amount"]):
        return jsonify(success=False, error="Insufficient funds or a conflicting request — try again."), 400
    supabase.table("loans").update({"repaid": True}).eq("id", loan["id"]).execute()
    treasury_add(cybucks=loan["amount"], counterparty=user["username"], kind="loan_repay")
    add_record(user["username"], f"Repaid a {loan['amount']} CB loan. Good standing.")

    return jsonify(success=True)


# ============================================================
#  PROFILE  /  ID CARD
# ============================================================
@app.route("/profile_data")
def profile_data():
    user = get_current_user()
    if not user:
        return jsonify(success=False, error="Not logged in"), 401

    company = None
    if user.get("company_id"):
        c = supabase.table("companies").select("*").eq("id", user["company_id"]).execute()
        company = c.data[0] if c.data else None

    records = supabase.table("records").select("*").eq("username", user["username"]) \
        .order("created_at", desc=True).limit(25).execute()
    loans = supabase.table("loans").select("*").eq("username", user["username"]) \
        .order("taken_at", desc=True).execute()

    nw = user_net_worth(user["username"])
    badges = [a for a in _achievements(user, records.data or [], nw) if a["earned"]]
    try:
        my_blogs = supabase.table("blogs").select("id,title").eq("username", user["username"]) \
            .order("id", desc=True).limit(30).execute().data or []
    except Exception:
        my_blogs = []

    return jsonify(
        success=True,
        profile=public_user(user),
        cabinet_role=_gov_role(user["username"]),
        salary=SALARY_TABLE.get(user.get("designation", "Citizen"), 100),
        company=company,
        records=records.data or [],
        loans=loans.data or [],
        member_since=user.get("created_at"),
        banner=user.get("banner"),
        badges=badges,
        pinned=_pinned_blog(user.get("pinned_blog")),
        my_blogs=my_blogs,
    )


# ============================================================
#  PASSPORT  &  CITIZENSHIP
#  (schema-free: issuance + oath are stored as official Records,
#   the passport/citizen numbers derive deterministically from id)
# ============================================================
PASSPORT_MARK = "national passport"
OATH_MARK     = "oath of allegiance"


def _passport_number(user):
    n = int(user.get("id") or 0)
    base = f"{n:06d}"
    chk = sum(int(d) for d in base) % 10
    return f"CYV{base}{chk}"


def _citizen_number(user):
    return f"CYV-{int(user.get('id') or 0):05d}"


def _add_years_iso(iso, years):
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:
        return None
    try:
        return dt.replace(year=dt.year + years).isoformat()
    except ValueError:        # 29 Feb
        return (dt + timedelta(days=365 * years)).isoformat()


def _mrz(username, pno):
    name = re.sub(r"[^A-Z]", "<", (username or "").upper()) or "CITIZEN"
    line1 = ("P<CYV" + name).ljust(44, "<")[:44]
    num = re.sub(r"[^A-Z0-9]", "", pno.upper())
    line2 = (num + "CYV").ljust(44, "<")[:44]
    return [line1, line2]


def _passport_stamps(user, records):
    stamps = [{"label": "BUREAU OF CITIZENSHIP", "sub": "NATURALIZED",
               "date": user.get("created_at"), "color": "#2b6cb0"}]
    low = [(r.get("entry") or "").lower() for r in records]
    if any(OATH_MARK in e for e in low):
        stamps.append({"label": "OATH OF ALLEGIANCE", "sub": "SWORN CITIZEN",
                       "date": None, "color": "#0f9d72"})
    desig = user.get("designation") or "Citizen"
    if desig != "Citizen":
        stamps.append({"label": "OFFICE OF STATE", "sub": desig.upper(),
                       "date": None, "color": "#b8860b"})
    try:
        if supabase.table("companies").select("id").eq("founder", user["username"]).limit(1).execute().data:
            stamps.append({"label": "CHAMBER OF COMMERCE", "sub": "REGISTERED FOUNDER",
                           "date": None, "color": "#7b4bc0"})
        if supabase.table("employment").select("id").eq("username", user["username"]) \
                .eq("status", "employed").limit(1).execute().data:
            stamps.append({"label": "MINISTRY OF LABOUR", "sub": "IN EMPLOYMENT",
                           "date": None, "color": "#c0563a"})
    except Exception:
        pass
    return stamps


def _count_rows(table, col, val):
    try:
        return supabase.table(table).select("id", count="exact").eq(col, val).execute().count or 0
    except Exception:
        return 0


def _achievements(user, records, net_worth):
    """Medals earned by this citizen — computed live. Never raises."""
    try:
        me = user["username"]
        low = " ".join((r.get("entry") or "").lower() for r in records)
        has_passport = PASSPORT_MARK in low
        has_oath = OATH_MARK in low
        visas = len(set(re.findall(r"\[visa:([a-z0-9_]+)\]", low)))
        companies = _count_rows("companies", "founder", me)
        referrals = _count_rows("cybucks", "referred_by", me)
        try:
            has_shares = any((h.get("shares") or 0) > 0 for h in
                             (supabase.table("holdings").select("shares").eq("username", me).execute().data or []))
        except Exception:
            has_shares = False
        try:
            employed = bool(supabase.table("employment").select("id").eq("username", me)
                            .eq("status", "employed").limit(1).execute().data)
        except Exception:
            employed = False
        office = bool(_gov_role(me))
    except Exception:
        return []
    defs = [
        ("passport", "Passport Holder", "fa-passport", "#58c4ff", "Issue a national passport", has_passport),
        ("oath", "Sworn Citizen", "fa-hand-fist", "#1fd6a6", "Swear the Oath of Allegiance", has_oath),
        ("founder", "Entrepreneur", "fa-briefcase", "#ffce56", "Found a company", companies >= 1),
        ("mogul", "Business Mogul", "fa-city", "#ff8fb0", "Found 3 companies", companies >= 3),
        ("investor", "Investor", "fa-arrow-trend-up", "#22d3ee", "Own shares in a company", has_shares),
        ("worker", "Working Citizen", "fa-user-tie", "#a78bfa", "Get hired at a company", employed),
        ("millionaire", "Millionaire", "fa-sack-dollar", "#ffce56", "Reach 1,000,000 CB net worth", net_worth >= 1_000_000),
        ("globetrotter", "Globetrotter", "fa-earth-americas", "#34d399", "Visit all 5 states", visas >= 5),
        ("recruiter", "Recruiter", "fa-user-plus", "#ff8fb0", "Invite a friend who joins", referrals >= 1),
        ("patriot", "Patriot", "fa-flag", "#ff5d6c", "Recruit 5+ citizens", referrals >= 5),
        ("statesman", "Statesman", "fa-landmark", "#ffce56", "Hold a government office", office),
    ]
    return [{"key": k, "name": n, "icon": i, "color": c, "desc": d, "earned": bool(e)}
            for (k, n, i, c, d, e) in defs]


@app.route("/passport_data")
def passport_data():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    records = supabase.table("records").select("entry,created_at") \
        .eq("username", user["username"]).order("created_at").execute().data or []
    pno = _passport_number(user)
    issued = next((r.get("created_at") for r in records
                   if PASSPORT_MARK in (r.get("entry") or "").lower()), None)
    oath = any(OATH_MARK in (r.get("entry") or "").lower() for r in records)
    nw = user_net_worth(user["username"])
    return jsonify(success=True, passport={
        "number": pno,
        "citizen_no": _citizen_number(user),
        "name": user["username"],
        "designation": user.get("designation") or "Citizen",
        "nationality": "Republic of Cyvathon",
        "member_since": user.get("created_at"),
        "issued": issued,
        "expiry": _add_years_iso(issued, 5) if issued else None,
        "oath": oath,
        "avatar": user.get("avatar"),
        "net_worth": nw,
        "mrz": _mrz(user["username"], pno),
        "stamps": _passport_stamps(user, records),
    }, achievements=_achievements(user, records, nw))


@app.route("/passport/issue", methods=["POST"])
@limiter.limit("10/minute")
def passport_issue():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    recs = supabase.table("records").select("entry") \
        .eq("username", user["username"]).execute().data or []
    if any(PASSPORT_MARK in (r.get("entry") or "").lower() for r in recs):
        return jsonify(success=False, error="You already hold a valid passport."), 400
    pno = _passport_number(user)
    add_record(user["username"], f"Issued National Passport No. {pno}.")
    notify(user["username"], "Your Cyvathon passport has been issued.", "/passport")
    return jsonify(success=True)


@app.route("/citizenship/oath", methods=["POST"])
@limiter.limit("10/minute")
def citizenship_oath():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    recs = supabase.table("records").select("entry") \
        .eq("username", user["username"]).execute().data or []
    if any(OATH_MARK in (r.get("entry") or "").lower() for r in recs):
        return jsonify(success=False, error="You have already sworn the Oath of Allegiance."), 400
    add_record(user["username"], "Swore the Oath of Allegiance to the Republic of Cyvathon.")
    notify(user["username"], "Oath of Allegiance recorded. Welcome, sworn citizen.", "/passport")
    return jsonify(success=True)


# ============================================================
#  STATES  &  BORDER CONTROL
#  Each state has its own local marketplace. Entry requires
#  passing border control (citizenship, passport, screening...).
# ============================================================
STATES = [
    {"id": "neonhaven", "name": "Neonhaven", "flag": "\U0001F303", "color": "#58c4ff",
     "capital": "Lumina", "security": "Standard", "need_passport": True, "need_oath": False,
     "tagline": "Coastal metropolis of finance, neon nightlife and high tech."},
    {"id": "cryptvale", "name": "Cryptvale", "flag": "⛰️", "color": "#a78bfa",
     "capital": "Hashford", "security": "Standard", "need_passport": True, "need_oath": False,
     "tagline": "Highland mining colonies and the crypto frontier."},
    {"id": "silica", "name": "Silica Plains", "flag": "\U0001F33E", "color": "#34d399",
     "capital": "Greenport", "security": "Basic", "need_passport": True, "need_oath": False,
     "tagline": "Agri-tech fields and the manufacturing heartland."},
    {"id": "portusmare", "name": "Portus Mare", "flag": "\U0001F30A", "color": "#22d3ee",
     "capital": "Tidewell", "security": "Standard", "need_passport": True, "need_oath": False,
     "tagline": "The coastal free-trade port and shipping gateway."},
    {"id": "aetheris", "name": "Aetheris", "flag": "\U0001F6F0️", "color": "#fbbf24",
     "capital": "Skyreach", "security": "Maximum", "need_passport": True, "need_oath": True,
     "is_capital": True,
     "tagline": "The national capital — seat of the President, aerospace, research and defense."},
]
STATE_IDS = {s["id"] for s in STATES}
STATE_CH_PREFIX = "State Channel — "


def _state_by_id(sid):
    return next((s for s in STATES if s["id"] == sid), None)


def _state_public(s):
    d = {k: s[k] for k in ("id", "name", "flag", "color", "capital",
                           "security", "tagline", "need_passport", "need_oath")}
    d["is_capital"] = s.get("is_capital", False)
    return d


def _is_president(user):
    return user.get("designation") == "President" or user["username"] in TREASURY_ADMINS


def _state_group_id(s, create=True):
    """The chat channel that IS the state's resident roster. Created on demand."""
    name = STATE_CH_PREFIX + s["name"]
    g = supabase.table("chat_groups").select("id").eq("name", name).execute().data
    if g:
        return g[0]["id"]
    if not create:
        return None
    return supabase.table("chat_groups").insert(
        {"name": name, "owner": "Cyvathon"}).execute().data[0]["id"]


def _residents(s):
    gid = _state_group_id(s, create=False)
    if not gid:
        return []
    rows = supabase.table("chat_group_members").select("username") \
        .eq("group_id", gid).execute().data or []
    return [r["username"] for r in rows]


def _home_state(username):
    """A citizen's state of residence = the state channel they belong to."""
    mine = supabase.table("chat_group_members").select("group_id") \
        .eq("username", username).execute().data or []
    for m in mine:
        g = supabase.table("chat_groups").select("name").eq("id", m["group_id"]).execute().data
        if g and g[0]["name"].startswith(STATE_CH_PREFIX):
            nm = g[0]["name"][len(STATE_CH_PREFIX):]
            s = next((x for x in STATES if x["name"] == nm), None)
            if s:
                return s["id"]
    return None


def _join_state(username, s):
    gid = _state_group_id(s)
    if not _group_member(gid, username):
        try:
            supabase.table("chat_group_members").insert(
                {"group_id": gid, "username": username}).execute()
        except Exception:
            pass
    return gid


def _leave_state(username, sid):
    gid = _state_group_id(_state_by_id(sid), create=False)
    if gid:
        supabase.table("chat_group_members").delete().eq("group_id", gid) \
            .eq("username", username).execute()


def _ensure_president_home(user):
    """The President resides in the capital, Aetheris — always."""
    if _is_president(user) and _home_state(user["username"]) != "aetheris":
        cur = _home_state(user["username"])
        if cur:
            _leave_state(user["username"], cur)
        _join_state(user["username"], _state_by_id("aetheris"))


def _clearance(username):
    """One pass over a citizen's official Records → travel clearance facts."""
    recs = supabase.table("records").select("entry").eq("username", username).execute().data or []
    low = " ".join((r.get("entry") or "").lower() for r in recs)
    return {
        "passport": PASSPORT_MARK in low,
        "oath": OATH_MARK in low,
        "visas": set(re.findall(r"\[visa:([a-z0-9_]+)\]", low)),
    }


def _entry_check(user, s, cl):
    """Border-control checklist for a state. Returns (checks, all_ok)."""
    checks = [
        {"key": "citizen", "label": "Citizenship verification", "ok": True,
         "note": "Verified citizen of the Republic of Cyvathon"},
        {"key": "passport", "label": "Passport control", "ok": cl["passport"],
         "note": "Passport verified" if cl["passport"]
                 else "No passport on file — issue one at Passport & Citizenship"},
    ]
    if s["need_oath"]:
        checks.append({"key": "oath", "label": "Security clearance — Oath of Allegiance",
                       "ok": cl["oath"],
                       "note": "Sworn citizen — clearance granted" if cl["oath"]
                               else "Restricted: requires the Oath of Allegiance"})
    checks.append({"key": "sanctions", "label": "Sanctions & watchlist screening",
                   "ok": not user.get("banned"),
                   "note": "No matches — clear" if not user.get("banned") else "Flagged"})
    return checks, all(c["ok"] for c in checks)


def _state_gate(user, sid):
    """(ok, error) — may this citizen trade in state `sid`?"""
    s = _state_by_id(sid)
    if not s:
        return False, "Unknown state"
    checks, ok = _entry_check(user, s, _clearance(user["username"]))
    if ok:
        return True, None
    fail = next(c for c in checks if not c["ok"])
    return False, f"You haven't cleared {s['name']} border control: {fail['note']}."


@app.route("/states/list")
def states_list():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    _ensure_president_home(user)
    home = _home_state(user["username"])
    cl = _clearance(user["username"])
    out = []
    for s in STATES:
        _checks, ok = _entry_check(user, s, cl)
        d = _state_public(s)
        d.update(can_enter=ok, visited=(s["id"] in cl["visas"]),
                 population=len(_residents(s)), is_home=(home == s["id"]))
        out.append(d)
    return jsonify(success=True, states=out, home=home)


@app.route("/state_info/<sid>")
def state_info(sid):
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    s = _state_by_id(sid)
    if not s:
        return jsonify(success=False, error="State not found"), 404
    _ensure_president_home(user)
    cl = _clearance(user["username"])
    checks, ok = _entry_check(user, s, cl)
    residents = _residents(s)
    d = _state_public(s)
    d.update(checks=checks, can_enter=ok, visited=(sid in cl["visas"]),
             population=len(residents), residents=residents[:40],
             is_home=(_home_state(user["username"]) == sid),
             is_president=_is_president(user))
    return jsonify(success=True, state=d)


@app.route("/state/<sid>/settle", methods=["POST"])
@limiter.limit("15/minute")
def state_settle(sid):
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    s = _state_by_id(sid)
    if not s:
        return jsonify(success=False, error="State not found"), 404
    if _is_president(user) and sid != "aetheris":
        return jsonify(success=False,
                       error="As President you reside in the national capital, Aetheris."), 403
    ok, err = _state_gate(user, sid)
    if not ok:
        return jsonify(success=False, error=err), 403
    cur = _home_state(user["username"])
    if cur == sid:
        return jsonify(success=False, error=f"You already reside in {s['name']}."), 400
    if cur:
        _leave_state(user["username"], cur)
    _join_state(user["username"], s)
    add_record(user["username"], f"Settled in {s['name']} — now a resident. [HOME:{sid}]")
    notify(user["username"],
           f"You are now a resident of {s['name']}. Meet fellow residents in the {s['name']} channel.",
           "/chat")
    return jsonify(success=True, home=sid, state=_state_public(s))


@app.route("/state/<sid>/enter", methods=["POST"])
@limiter.limit("20/minute")
def state_enter(sid):
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    s = _state_by_id(sid)
    if not s:
        return jsonify(success=False, error="State not found"), 404
    cl = _clearance(user["username"])
    checks, ok = _entry_check(user, s, cl)
    if not ok:
        fail = next(c for c in checks if not c["ok"])
        return jsonify(success=False, error=f"Entry denied at {s['name']}: {fail['note']}.",
                       checks=checks), 403
    if sid not in cl["visas"]:        # stamp a visa into the passport on first entry
        add_record(user["username"],
                   f"Cleared border control — entry visa to {s['name']}. [VISA:{sid}]")
    return jsonify(success=True, state=_state_public(s), checks=checks)


# ============================================================
#  VOTING
# ============================================================
@app.route("/polls", methods=["GET"])
def list_polls():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401

    polls = supabase.table("polls").select("*").order("created_at", desc=True).execute()
    out = []
    for p in (polls.data or []):
        ballots = supabase.table("ballots").select("*").eq("poll_id", p["id"]).execute().data or []
        tally = {opt: 0 for opt in (p.get("options") or [])}
        for b in ballots:
            tally[b["choice"]] = tally.get(b["choice"], 0) + 1
        mine = next((b["choice"] for b in ballots if b["voter"] == user["username"]), None)
        out.append({**p, "tally": tally, "total": len(ballots), "my_vote": mine})

    return jsonify(success=True, polls=out, is_president=is_treasury_admin(user))


@app.route("/polls", methods=["POST"])
@limiter.limit("10/minute")
def create_poll():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    if not is_treasury_admin(user):
        return jsonify(success=False, error="Only the President may create a vote"), 403

    data = request.get_json()
    title = data.get("title", "").strip()
    position = data.get("position", "Prime Minister").strip() or "Prime Minister"
    options = [o.strip() for o in data.get("options", []) if o and o.strip()]

    if not title or len(options) < 2:
        return jsonify(success=False, error="Need a title and at least 2 options"), 400

    poll = supabase.table("polls").insert({
        "title": title, "position": position,
        "options": options, "created_by": user["username"]
    }).execute().data[0]
    notify_all(f"🗳️ Election opened — {position}: {title}. Cast your vote!",
               "/voting", exclude=user["username"])
    return jsonify(success=True, poll=poll)


@app.route("/polls/vote", methods=["POST"])
@limiter.limit("30/minute")
def cast_vote():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401

    data = request.get_json()
    poll_id = data.get("poll_id")
    choice = data.get("choice", "").strip()

    poll_res = supabase.table("polls").select("*").eq("id", poll_id).execute()
    if not poll_res.data:
        return jsonify(success=False, error="Poll not found"), 404
    poll = poll_res.data[0]
    if not poll.get("open"):
        return jsonify(success=False, error="Voting is closed"), 400
    if choice not in (poll.get("options") or []):
        return jsonify(success=False, error="Invalid choice"), 400

    existing = supabase.table("ballots").select("id").eq("poll_id", poll_id) \
        .eq("voter", user["username"]).execute()
    if existing.data:
        return jsonify(success=False, error="You already voted"), 400

    supabase.table("ballots").insert({
        "poll_id": poll_id, "voter": user["username"], "choice": choice
    }).execute()
    return jsonify(success=True)


@app.route("/polls/close", methods=["POST"])
@limiter.limit("10/minute")
def close_poll():
    user = get_current_user(run_economics=False)
    if not user or not is_treasury_admin(user):
        return jsonify(success=False, error="Only the President may close a vote"), 403

    poll_id = request.get_json().get("poll_id")
    poll_res = supabase.table("polls").select("*").eq("id", poll_id).execute()
    if not poll_res.data:
        return jsonify(success=False, error="Poll not found"), 404
    poll = poll_res.data[0]

    # Tally winner and install them into the government cabinet
    ballots = supabase.table("ballots").select("*").eq("poll_id", poll_id).execute().data or []
    if ballots:
        tally = {}
        for b in ballots:
            tally[b["choice"]] = tally.get(b["choice"], 0) + 1
        winner = max(tally, key=tally.get)
        # A ministry election installs a minister; everything else fills a
        # seat in the government table.
        if poll.get("ministry_id"):
            supabase.table("ministries").update({"minister": winner}) \
                .eq("id", poll["ministry_id"]).execute()
            supabase.table("ministry_applications").update({"status": "elected"}) \
                .eq("ministry_id", poll["ministry_id"]).eq("username", winner).execute()
            supabase.table("ministry_applications").update({"status": "closed"}) \
                .eq("ministry_id", poll["ministry_id"]).eq("status", "pending").execute()
            supabase.table("cybucks").update({"designation": "Minister"}) \
                .eq("username", winner).execute()
            notify_all(f"\U0001F3DB\uFE0F {winner} has been elected {poll['position']}.",
                       "/ministries", exclude=winner)
        else:
            supabase.table("government").update({"holder": winner}) \
                .eq("position", poll["position"]).execute()
        # update winner's designation if the position maps to one
        if not poll.get("ministry_id") and poll["position"] in SALARY_TABLE:
            supabase.table("cybucks").update({"designation": poll["position"]}) \
                .eq("username", winner).execute()
        add_record(winner, f"Elected {poll['position']} of Cyvathon by national vote.")
        notify(winner, f"You were elected {poll['position']} of Cyvathon!", "/government")

    supabase.table("polls").update({"open": False}).eq("id", poll_id).execute()
    return jsonify(success=True)


# ============================================================
#  NEWS  (President posts national announcements)
# ============================================================
@app.route("/news/list", methods=["GET"])
def news_list():
    res = supabase.table("news").select("*").order("created_at", desc=True).limit(30).execute()
    user = get_current_user(run_economics=False)
    return jsonify(success=True, news=res.data or [],
                   is_president=is_treasury_admin(user) if user else False)


@app.route("/news", methods=["POST"])
@limiter.limit("20/minute")
def news_create():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    if not is_treasury_admin(user):
        return jsonify(success=False, error="Only the President may post news"), 403

    d = request.get_json()
    title = (d.get("title") or "").strip()
    body = (d.get("body") or "").strip()
    if not title:
        return jsonify(success=False, error="Give the announcement a title"), 400

    item = supabase.table("news").insert({
        "title": title, "body": body, "author": user["username"]
    }).execute().data[0]
    notify_all(f"📰 National News: {title}", "/news", exclude=user["username"])
    return jsonify(success=True, item=item)


@app.route("/news/delete", methods=["POST"])
@limiter.limit("20/minute")
def news_delete():
    user = get_current_user(run_economics=False)
    if not user or not is_treasury_admin(user):
        return jsonify(success=False, error="Only the President may delete news"), 403
    supabase.table("news").delete().eq("id", request.get_json().get("id")).execute()
    return jsonify(success=True)


# ============================================================
#  NOTIFICATIONS
# ============================================================
# Official / government notifications jump the queue on the notifications page.
_GOV_NOTIF_PREFIXES = ("/voting", "/news", "/legislature", "/court", "/fir",
                       "/gazette", "/ministries", "/treasury", "/admin", "/warroom")

def _notif_priority(n):
    link = (n.get("link") or "")
    return any(link.startswith(p) for p in _GOV_NOTIF_PREFIXES)


@app.route("/notifications/list", methods=["GET"])
def notifications_list():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    rows = supabase.table("notifications").select("*").eq("username", user["username"]) \
        .order("created_at", desc=True).limit(60).execute().data or []
    if request.args.get("unread") == "1":      # notifications page: only what's new
        rows = [r for r in rows if not r.get("read")]
    for r in rows:
        r["priority"] = _notif_priority(r)
    # Stable sort keeps newest-first within each group; government first overall.
    rows.sort(key=lambda r: 0 if r["priority"] else 1)
    unread = sum(1 for r in rows if not r.get("read"))
    return jsonify(success=True, notifications=rows, unread=unread)


@app.route("/notifications/count")
def notifications_count():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=True, unread=0)
    rows = supabase.table("notifications").select("id").eq("username", user["username"]) \
        .eq("read", False).execute().data or []
    return jsonify(success=True, unread=len(rows))


@app.route("/notifications/read", methods=["POST"])
def notifications_read():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    supabase.table("notifications").update({"read": True}) \
        .eq("username", user["username"]).eq("read", False).execute()
    return jsonify(success=True)


# ============================================================
#  LEGISLATURE  (bills -> vote -> assent -> law)
# ============================================================
def _bill_tally(bill_id):
    votes = supabase.table("bill_votes").select("vote,voter").eq("bill_id", bill_id).execute().data or []
    aye = sum(1 for v in votes if v["vote"] == "aye")
    nay = sum(1 for v in votes if v["vote"] == "nay")
    return aye, nay, votes


@app.route("/legislature/list")
def legislature_list():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    bills = supabase.table("bills").select("*").order("created_at", desc=True).limit(60).execute().data or []
    out = []
    for b in bills:
        aye, nay, votes = _bill_tally(b["id"])
        mine = next((v["vote"] for v in votes if v["voter"] == user["username"]), None)
        out.append({**b, "aye": aye, "nay": nay, "my_vote": mine})
    return jsonify(success=True, bills=out, is_president=is_treasury_admin(user),
                   me=user["username"])


@app.route("/legislature/bill/<int:bid>")
def legislature_bill(bid):
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    r = supabase.table("bills").select("*").eq("id", bid).execute().data
    if not r:
        return jsonify(success=False, error="Bill not found"), 404
    b = r[0]
    aye, nay, votes = _bill_tally(bid)
    mine = next((v["vote"] for v in votes if v["voter"] == user["username"]), None)
    debate = supabase.table("bill_debate").select("*").eq("bill_id", bid) \
        .order("created_at").limit(100).execute().data or []
    return jsonify(success=True, bill={**b, "aye": aye, "nay": nay, "my_vote": mine},
                   debate=debate, is_president=is_treasury_admin(user),
                   is_sponsor=user["username"] == b["sponsor"], me=user["username"])


@app.route("/legislature/table", methods=["POST"])
@limiter.limit("10/minute")
def legislature_table():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    d = request.get_json()
    title = (d.get("title") or "").strip()
    summary = (d.get("summary") or "").strip()
    full_text = (d.get("full_text") or "").strip()
    if not title:
        return jsonify(success=False, error="A bill needs a title"), 400
    bill = supabase.table("bills").insert({
        "title": title, "summary": summary, "full_text": full_text,
        "sponsor": user["username"], "status": "voting"
    }).execute().data[0]
    return jsonify(success=True, bill=bill)


@app.route("/legislature/vote", methods=["POST"])
@limiter.limit("40/minute")
def legislature_vote():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    d = request.get_json()
    bid = d.get("bill_id")
    vote = d.get("vote")
    if vote not in ("aye", "nay"):
        return jsonify(success=False, error="Vote aye or nay"), 400
    b = supabase.table("bills").select("status").eq("id", bid).execute().data
    if not b or b[0]["status"] != "voting":
        return jsonify(success=False, error="This bill is not open for voting"), 400
    existing = supabase.table("bill_votes").select("id").eq("bill_id", bid) \
        .eq("voter", user["username"]).execute().data
    if existing:
        supabase.table("bill_votes").update({"vote": vote}).eq("id", existing[0]["id"]).execute()
    else:
        supabase.table("bill_votes").insert({
            "bill_id": bid, "voter": user["username"], "vote": vote
        }).execute()
    return jsonify(success=True)


@app.route("/legislature/debate", methods=["POST"])
@limiter.limit("30/minute")
def legislature_debate():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    d = request.get_json()
    comment = (d.get("comment") or "").strip()
    if not comment:
        return jsonify(success=False, error="Empty remark"), 400
    supabase.table("bill_debate").insert({
        "bill_id": d.get("bill_id"), "author": user["username"], "comment": comment
    }).execute()
    return jsonify(success=True)


@app.route("/legislature/withdraw", methods=["POST"])
@limiter.limit("10/minute")
def legislature_withdraw():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    bid = request.get_json().get("bill_id")
    b = supabase.table("bills").select("*").eq("id", bid).execute().data
    if not b:
        return jsonify(success=False, error="Bill not found"), 404
    if b[0]["sponsor"] != user["username"]:
        return jsonify(success=False, error="Only the sponsor can withdraw a bill"), 403
    if b[0]["status"] != "voting":
        return jsonify(success=False, error="Bill is no longer open"), 400
    supabase.table("bills").update({"status": "withdrawn"}).eq("id", bid).execute()
    return jsonify(success=True)


def _next_gazette_no(kind):
    year_start = datetime(_now().year, 1, 1, tzinfo=timezone.utc).isoformat()
    rows = supabase.table("gazette").select("id").eq("kind", kind) \
        .gte("created_at", year_start).execute().data or []
    return len(rows) + 1


@app.route("/legislature/assent", methods=["POST"])
@limiter.limit("15/minute")
def legislature_assent():
    """The Head of State grants assent (enact) or rejects a bill."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    if not is_treasury_admin(user):
        return jsonify(success=False, error="Only the President may grant assent"), 403

    d = request.get_json()
    bid = d.get("bill_id")
    enact = bool(d.get("enact"))
    r = supabase.table("bills").select("*").eq("id", bid).execute().data
    if not r:
        return jsonify(success=False, error="Bill not found"), 404
    b = r[0]
    if b["status"] != "voting":
        return jsonify(success=False, error="Bill already concluded"), 400

    aye, nay, _ = _bill_tally(bid)
    if enact:
        if aye < nay or aye == 0:
            return jsonify(success=False, error="A bill needs more Ayes than Nays to receive assent"), 400
        no = _next_gazette_no("act")
        ref = f"Act No. {no} of {_now().year}"
        supabase.table("bills").update({
            "status": "enacted", "number": no, "enacted_at": _now().isoformat()
        }).eq("id", bid).execute()
        supabase.table("gazette").insert({
            "ref": ref, "kind": "act", "title": b["title"],
            "body": b.get("full_text") or b.get("summary") or "",
            "issued_by": user["username"], "bill_id": bid
        }).execute()
        notify(b["sponsor"], f"⚖️ Your bill '{b['title']}' received Royal Assent — enacted as {ref}.", "/gazette")
        add_record(b["sponsor"], f"Sponsored {ref}: '{b['title']}'.")
    else:
        supabase.table("bills").update({"status": "rejected"}).eq("id", bid).execute()
        notify(b["sponsor"], f"Your bill '{b['title']}' was rejected and will not become law.", "/legislature")
    return jsonify(success=True)


# ============================================================
#  OFFICIAL GAZETTE
# ============================================================
@app.route("/gazette/list")
def gazette_listing():
    rows = supabase.table("gazette").select("*").order("created_at", desc=True).limit(100).execute().data or []
    user = get_current_user(run_economics=False)
    return jsonify(success=True, entries=rows,
                   is_president=is_treasury_admin(user) if user else False)


@app.route("/gazette/decree", methods=["POST"])
@limiter.limit("15/minute")
def gazette_decree():
    """The Head of State issues a binding executive decree."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    if not is_treasury_admin(user):
        return jsonify(success=False, error="Only the President may issue a decree"), 403
    d = request.get_json()
    title = (d.get("title") or "").strip()
    body = (d.get("body") or "").strip()
    if not title:
        return jsonify(success=False, error="A decree needs a title"), 400
    no = _next_gazette_no("decree")
    ref = f"Decree No. {no} of {_now().year}"
    supabase.table("gazette").insert({
        "ref": ref, "kind": "decree", "title": title, "body": body,
        "issued_by": user["username"]
    }).execute()
    return jsonify(success=True, ref=ref)


# ============================================================
#  MINISTRIES  (cabinet departments, budgets & spending)
# ============================================================
@app.route("/ministries/list")
def ministries_list():
    user = get_current_user(run_economics=False)
    rows = supabase.table("ministries").select("*").order("rank").execute().data or []
    me = user["username"] if user else None
    for m in rows:
        m["is_minister"] = bool(me and m.get("minister") == me)
    return jsonify(success=True, ministries=rows,
                   is_president=is_treasury_admin(user) if user else False, me=me)


@app.route("/ministries/create", methods=["POST"])
@limiter.limit("15/minute")
def ministries_create():
    user = get_current_user(run_economics=False)
    if not user or not is_treasury_admin(user):
        return jsonify(success=False, error="President only"), 403
    d = request.get_json()
    name = (d.get("name") or "").strip()
    mandate = (d.get("mandate") or "").strip()
    icon = (d.get("icon") or "fa-landmark").strip()
    if not name:
        return jsonify(success=False, error="Name the ministry"), 400
    if supabase.table("ministries").select("id").eq("name", name).execute().data:
        return jsonify(success=False, error="A ministry with that name exists"), 400
    supabase.table("ministries").insert({"name": name, "mandate": mandate, "icon": icon}).execute()
    return jsonify(success=True)


def _set_designation_safe(username, desig):
    if username in TREASURY_ADMINS:
        return
    supabase.table("cybucks").update({"designation": desig}).eq("username", username).execute()


@app.route("/ministries/appoint", methods=["POST"])
@limiter.limit("20/minute")
def ministries_appoint():
    user = get_current_user(run_economics=False)
    if not user or not is_treasury_admin(user):
        return jsonify(success=False, error="President only"), 403
    d = request.get_json()
    mid = d.get("ministry_id")
    username = (d.get("username") or "").strip()
    if not supabase.table("cybucks").select("id").eq("username", username).execute().data:
        return jsonify(success=False, error="No such citizen"), 404
    m = supabase.table("ministries").select("*").eq("id", mid).execute().data
    if not m:
        return jsonify(success=False, error="Ministry not found"), 404
    prev = m[0].get("minister")
    supabase.table("ministries").update({"minister": username}).eq("id", mid).execute()
    if prev and prev != "Vacant" and prev != username:
        _set_designation_safe(prev, "Citizen")
    _set_designation_safe(username, "Minister")
    add_record(username, f"Appointed Minister, heading the {m[0]['name']}.")
    notify(username, f"You have been appointed Minister of the {m[0]['name']}.", "/ministries")
    return jsonify(success=True)


@app.route("/ministries/dismiss", methods=["POST"])
@limiter.limit("20/minute")
def ministries_dismiss():
    user = get_current_user(run_economics=False)
    if not user or not is_treasury_admin(user):
        return jsonify(success=False, error="President only"), 403
    mid = request.get_json().get("ministry_id")
    m = supabase.table("ministries").select("*").eq("id", mid).execute().data
    if not m:
        return jsonify(success=False, error="Ministry not found"), 404
    prev = m[0].get("minister")
    supabase.table("ministries").update({"minister": "Vacant"}).eq("id", mid).execute()
    if prev and prev != "Vacant":
        _set_designation_safe(prev, "Citizen")
        notify(prev, f"You have been dismissed as Minister of the {m[0]['name']}.", "/ministries")
    return jsonify(success=True)


@app.route("/ministries/fund", methods=["POST"])
@limiter.limit("20/minute")
def ministries_fund():
    """The President allocates Treasury funds to a ministry's budget."""
    user = get_current_user(run_economics=False)
    if not user or not is_treasury_admin(user):
        return jsonify(success=False, error="President only"), 403
    d = request.get_json()
    mid = d.get("ministry_id")
    amount = float(d.get("amount") or 0)
    if not math.isfinite(amount) or amount <= 0:
        return jsonify(success=False, error="Enter a positive amount"), 400
    m = supabase.table("ministries").select("*").eq("id", mid).execute().data
    if not m:
        return jsonify(success=False, error="Ministry not found"), 404
    t = get_treasury()
    if (t["balance"] or 0) < amount:
        return jsonify(success=False, error="The Treasury lacks the funds"), 400
    treasury_add(cybucks=-amount, counterparty=m[0]["name"], kind="budget")
    cas_num("ministries", [("id", mid)], "budget", amount, allow_negative=True)
    return jsonify(success=True)


@app.route("/ministries/mandate", methods=["POST"])
@limiter.limit("20/minute")
def ministries_mandate():
    user = get_current_user(run_economics=False)
    if not user or not is_treasury_admin(user):
        return jsonify(success=False, error="President only"), 403
    d = request.get_json()
    supabase.table("ministries").update({"mandate": (d.get("mandate") or "").strip()}) \
        .eq("id", d.get("ministry_id")).execute()
    return jsonify(success=True)


@app.route("/ministries/spend", methods=["POST"])
@limiter.limit("30/minute")
def ministries_spend():
    """A minister (or the President) spends the ministry budget to pay a citizen."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    d = request.get_json()
    mid = d.get("ministry_id")
    to = (d.get("to") or "").strip()
    reason = (d.get("reason") or "").strip()
    amount = float(d.get("amount") or 0)

    m = supabase.table("ministries").select("*").eq("id", mid).execute().data
    if not m:
        return jsonify(success=False, error="Ministry not found"), 404
    m = m[0]
    if not (is_treasury_admin(user) or m.get("minister") == user["username"]):
        return jsonify(success=False, error="Only this ministry's minister may spend its budget"), 403
    if not math.isfinite(amount) or amount <= 0:
        return jsonify(success=False, error="Enter a positive amount"), 400
    if (m.get("budget") or 0) < amount:
        return jsonify(success=False, error="The ministry budget is insufficient"), 400
    if not supabase.table("cybucks").select("id").eq("username", to).execute().data:
        return jsonify(success=False, error="No such recipient"), 404

    # Atomically debit the ministry budget first; only pay if it covered the bill.
    if not cas_num("ministries", [("id", mid)], "budget", -amount):
        return jsonify(success=False, error="The ministry budget is insufficient"), 400
    _add_cash(to, amount)
    log_txn("ministry", m["name"], to, amount, "cybucks", reason or "Ministry disbursement")
    add_record(to, f"Received {amount} CB from the {m['name']}{(' — ' + reason) if reason else ''}.")
    notify(to, f"The {m['name']} paid you {amount} CB{(' — ' + reason) if reason else ''}.", "/bank")
    return jsonify(success=True)


@app.route("/ministries/delete", methods=["POST"])
@limiter.limit("15/minute")
def ministries_delete():
    user = get_current_user(run_economics=False)
    if not user or not is_treasury_admin(user):
        return jsonify(success=False, error="President only"), 403
    mid = request.get_json().get("ministry_id")
    m = supabase.table("ministries").select("*").eq("id", mid).execute().data
    if m and (m[0].get("budget") or 0) > 0:
        treasury_add(cybucks=m[0]["budget"], counterparty=m[0]["name"], kind="budget_return")
    supabase.table("ministries").delete().eq("id", mid).execute()
    return jsonify(success=True)


# ============================================================
#  FIRs  +  ATHENA  (Cyvathon Intelligence Agency)
# ============================================================
CIA_RANKS = ["Director", "Spy", "Detective", "Agent"]


def _fir_log(fir_id, agent_view):
    log = supabase.table("fir_log").select("*").eq("fir_id", fir_id) \
        .order("created_at").execute().data or []
    if not agent_view:
        log = [x for x in log if not x.get("secret")]
    return log


# ----- Public: file FIRs & submit evidence -----
@app.route("/fir/list")
def fir_list():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    a = supabase.table("firs").select("*").eq("complainant", me).execute().data or []
    b = supabase.table("firs").select("*").eq("accused", me).execute().data or []
    seen, mine = set(), []
    for f in sorted(a + b, key=lambda x: x["id"], reverse=True):
        if f["id"] not in seen:
            seen.add(f["id"]); mine.append({**f, "log": _fir_log(f["id"], agent_view=False)})
    return jsonify(success=True, firs=mine, me=me)


@app.route("/fir/file", methods=["POST"])
@limiter.limit("10/minute")
def fir_file():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    d = request.get_json()
    accused = (d.get("accused") or "").strip()
    title = (d.get("title") or "").strip()
    description = (d.get("description") or "").strip()
    if not title:
        return jsonify(success=False, error="Give the report a title"), 400
    if accused and not supabase.table("cybucks").select("id").eq("username", accused).execute().data:
        return jsonify(success=False, error="No such citizen as the accused"), 404
    fir = supabase.table("firs").insert({
        "complainant": user["username"], "accused": accused or None,
        "title": title, "description": description
    }).execute().data[0]
    if accused:
        notify(accused, f"⚠️ An FIR naming you was filed: '{title}'.", "/fir")
    return jsonify(success=True, fir=fir)


@app.route("/fir/evidence", methods=["POST"])
@limiter.limit("20/minute")
def fir_evidence():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    d = request.get_json()
    fid = d.get("fir_id")
    content = (d.get("content") or "").strip()
    if not content:
        return jsonify(success=False, error="Describe the evidence or paste a link"), 400
    f = supabase.table("firs").select("*").eq("id", fid).execute().data
    if not f:
        return jsonify(success=False, error="FIR not found"), 404
    f = f[0]
    allowed = user["username"] in (f["complainant"], f.get("accused")) or is_cia(user)
    if not allowed:
        return jsonify(success=False, error="You are not party to this report"), 403
    supabase.table("fir_log").insert({
        "fir_id": fid, "author": user["username"], "entry": content,
        "kind": "evidence", "secret": False
    }).execute()
    return jsonify(success=True)


# ----- Classified: Athena agency operations -----
@app.route("/athena/data")
def athena_data():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    if not is_cia(user):
        return jsonify(success=False, error="CLASSIFIED — clearance denied"), 403
    roster = supabase.table("cia_agents").select("*").order("created_at").execute().data or []
    firs = supabase.table("firs").select("*").order("created_at", desc=True).limit(100).execute().data or []
    firs = [{**f, "log": _fir_log(f["id"], agent_view=True)} for f in firs]
    now = time()
    ops = [{"key": k, **{x: v[x] for x in ("name", "desc", "min", "max", "success")}}
           for k, v in ATHENA_OPS.items()]
    threats = [{"ip": t["ip"], "path": t["path"], "ua": t["ua"], "ago": int(now - t["ts"])}
               for t in _threats[:20]]
    return jsonify(success=True, roster=roster, firs=firs,
                   is_director=is_cia_director(user), my_rank=cia_rank(user),
                   me=user["username"], ranks=CIA_RANKS,
                   ops=ops, intel=_athena_intel(25), threats=threats, surveil=_surveil_payload(),
                   op_cooldown=max(0, int(ATHENA_OP_COOLDOWN - (now - _last_op.get(user["username"], 0)))))


@app.route("/athena/recruit", methods=["POST"])
@limiter.limit("15/minute")
def athena_recruit():
    user = get_current_user(run_economics=False)
    if not user or not is_cia_director(user):
        return jsonify(success=False, error="Director clearance required"), 403
    d = request.get_json()
    username = (d.get("username") or "").strip()
    rank = d.get("rank") if d.get("rank") in CIA_RANKS else "Agent"
    if not supabase.table("cybucks").select("id").eq("username", username).execute().data:
        return jsonify(success=False, error="No such citizen"), 404
    existing = supabase.table("cia_agents").select("id").eq("username", username).execute().data
    if existing:
        supabase.table("cia_agents").update({"rank": rank}).eq("username", username).execute()
    else:
        supabase.table("cia_agents").insert({
            "username": username, "rank": rank, "added_by": user["username"]
        }).execute()
        notify(username, "🦉 You have been recruited into Athena, the Cyvathon Intelligence Agency.", "/athena")
    _cia_cache.pop(username, None)      # reflect the change immediately
    return jsonify(success=True)


@app.route("/athena/dismiss", methods=["POST"])
@limiter.limit("15/minute")
def athena_dismiss():
    user = get_current_user(run_economics=False)
    if not user or not is_cia_director(user):
        return jsonify(success=False, error="Director clearance required"), 403
    username = (request.get_json().get("username") or "").strip()
    if username in TREASURY_ADMINS:
        return jsonify(success=False, error="The Director cannot be removed"), 400
    supabase.table("cia_agents").delete().eq("username", username).execute()
    _cia_cache.pop(username, None)
    return jsonify(success=True)


@app.route("/athena/case", methods=["POST"])
@limiter.limit("30/minute")
def athena_case():
    """Agents act on an FIR: assign, change status, or add a secret note."""
    user = get_current_user(run_economics=False)
    if not user or not is_cia(user):
        return jsonify(success=False, error="Clearance denied"), 403
    d = request.get_json()
    fid = d.get("fir_id")
    action = d.get("action")
    f = supabase.table("firs").select("*").eq("id", fid).execute().data
    if not f:
        return jsonify(success=False, error="FIR not found"), 404

    if action == "assign":
        supabase.table("firs").update({"assigned_to": user["username"], "status": "investigating"}) \
            .eq("id", fid).execute()
    elif action == "status":
        st = d.get("status")
        if st in ("filed", "investigating", "closed"):
            supabase.table("firs").update({"status": st}).eq("id", fid).execute()
    elif action == "note":
        note = (d.get("note") or "").strip()
        if note:
            supabase.table("fir_log").insert({
                "fir_id": fid, "author": user["username"], "entry": note,
                "kind": "note", "secret": True
            }).execute()
    else:
        return jsonify(success=False, error="Unknown action"), 400
    return jsonify(success=True)


@app.route("/athena/escalate", methods=["POST"])
@limiter.limit("15/minute")
def athena_escalate():
    """Take an FIR to the Courts. Evidence must already be on file."""
    user = get_current_user(run_economics=False)
    if not user or not is_cia(user):
        return jsonify(success=False, error="Clearance denied"), 403
    fid = request.get_json().get("fir_id")
    f = supabase.table("firs").select("*").eq("id", fid).execute().data
    if not f:
        return jsonify(success=False, error="FIR not found"), 404
    f = f[0]
    if f.get("court_case_id"):
        return jsonify(success=False, error="Already before the Courts"), 400
    if not f.get("accused"):
        return jsonify(success=False, error="No accused named — cannot prosecute"), 400
    evidence = [x for x in _fir_log(fid, agent_view=False) if x["kind"] == "evidence"]
    if not evidence:
        return jsonify(success=False, error="Evidence must be submitted before escalation"), 400

    ev_text = "\n".join(f"• {e['entry']} (filed by {e['author']})" for e in evidence)
    case = supabase.table("court_cases").insert({
        "plaintiff": f["complainant"], "defendant": f["accused"],
        "title": "FIR: " + f["title"],
        "description": (f.get("description") or "") + "\n\nEVIDENCE ON RECORD:\n" + ev_text,
        "claim": 0
    }).execute().data[0]
    supabase.table("firs").update({"status": "escalated", "court_case_id": case["id"]}) \
        .eq("id", fid).execute()
    notify(f["accused"], f"⚖️ FIR '{f['title']}' has been escalated to the Courts against you.", "/court")
    notify(f["complainant"], f"Your report '{f['title']}' was taken to court by Athena.", "/court")
    return jsonify(success=True, case_id=case["id"])


# ----- Classified: Espionage — field operations, intel dossier, threat feed -----
ATHENA_OPS = {
    "surveil": {"name": "Surveillance Sweep", "min": 40, "max": 110, "success": 0.9,
                "desc": "Monitor foreign chatter for loose intelligence."},
    "counter": {"name": "Counter-Intrusion", "min": 70, "max": 180, "success": 0.75,
                "desc": "Trace and sever an active AIA probe against Cyvathon."},
    "recon":   {"name": "Deep Cyber-Recon", "min": 110, "max": 260, "success": 0.6,
                "desc": "Infiltrate a rival network for high-value intel."},
}
ATHENA_OP_COOLDOWN = 600      # seconds between operations, per agent
RANK_MULT = {"Director": 2.0, "Spy": 1.6, "Detective": 1.3, "Agent": 1.0}
_OP_WIN = ["Intel recovered: intercepted AIA field chatter.",
           "Probe traced to its source and neutralized.",
           "Recovered a fragment of the enemy's playbook.",
           "Turned a foreign asset — a clean operation.",
           "Decrypted a batch of intercepted traffic."]
_OP_LOSS = ["Operation compromised — the asset went dark.",
            "Counter-surveillance detected us; we pulled out clean but empty-handed.",
            "The trail went cold. No intel recovered.",
            "Enemy encryption held. Mission aborted."]


def _athena_intel(limit=25):
    try:
        return supabase.table("athena_intel").select("*") \
            .order("created_at", desc=True).limit(limit).execute().data or []
    except Exception:
        return []      # table not migrated yet


@app.route("/athena/op", methods=["POST"])
@limiter.limit("30/minute")
def athena_op():
    user = get_current_user(run_economics=False)
    if not user or not is_cia(user):
        return jsonify(success=False, error="Clearance denied"), 403
    me = user["username"]
    key = (request.get_json() or {}).get("op")
    op = ATHENA_OPS.get(key)
    if not op:
        return jsonify(success=False, error="Unknown operation"), 400
    now = time()
    wait = ATHENA_OP_COOLDOWN - (now - _last_op.get(me, 0))
    if wait > 0:
        return jsonify(success=False, error=f"Agents must lie low. Next operation in {int(wait)}s."), 429
    _last_op[me] = now

    mult = RANK_MULT.get(cia_rank(user) or "Agent", 1.0)
    success = random.random() < op["success"]
    reward = round(random.uniform(op["min"], op["max"]) * mult) if success else 0
    detail = (random.choice(_OP_WIN) if success else random.choice(_OP_LOSS))
    if reward:
        cas_adjust(me, "cybits", reward, allow_negative=True)   # paid in Cybits
    try:
        supabase.table("athena_intel").insert({
            "agent": me, "kind": "operation",
            "title": op["name"] + (" — SUCCESS" if success else " — FAILED"),
            "detail": detail, "reward": reward,
        }).execute()
    except Exception:
        pass          # log table not migrated — op still resolves
    return jsonify(success=True, op_success=success, reward=reward, detail=detail)


@app.route("/athena/report", methods=["POST"])
@limiter.limit("20/minute")
def athena_report():
    user = get_current_user(run_economics=False)
    if not user or not is_cia(user):
        return jsonify(success=False, error="Clearance denied"), 403
    d = request.get_json() or {}
    title = (d.get("title") or "").strip()[:120]
    detail = (d.get("detail") or "").strip()[:1000]
    if not title:
        return jsonify(success=False, error="Give the report a title"), 400
    try:
        supabase.table("athena_intel").insert({
            "agent": user["username"], "kind": "report", "title": title, "detail": detail, "reward": 0
        }).execute()
    except Exception:
        return jsonify(success=False, error="Intel dossier isn't enabled yet — the database needs a quick update."), 503
    return jsonify(success=True)


# ----- Classified: External surveillance (public-site uptime/version monitor) -----
SURVEIL_TARGETS = {
    "aquilithia": os.environ.get("SURVEIL_AQUILITHIA", "https://aquilithia.onrender.com/"),
}
SURVEIL_MIN_GAP = 15          # never re-ping a target more often than this (be a good netizen)
_surveil = {}                 # target key -> {"state": last check, "log": [recent checks]}


def _do_surveil(key):
    """One respectful GET to a rival's PUBLIC homepage — reconnaissance from
    freely-served content only: status, latency, version, tech stack, and the
    routes they advertise. No auth bypass, no hidden-route probing."""
    import requests
    url = SURVEIL_TARGETS[key]
    slot = _surveil.setdefault(key, {"state": None, "log": []})
    t0 = time()
    try:
        r = requests.get(url, timeout=12, headers={"User-Agent": "Cyvathon-Athena-Monitor/1.0"})
        ms = int((time() - t0) * 1000)
        html = r.text or ""
        ver = re.search(r"v(\d+\.\d+(?:\.\d+)?)", html)
        title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
        links = sorted(set(re.findall(r'href="(/[a-zA-Z0-9_\-/]*)"', html)))[:40]
        server = r.headers.get("Server", "")
        cloudflare = bool(r.headers.get("CF-RAY") or "cloudflare" in server.lower())
        # Defensive posture — which security headers they serve (passive audit)
        h = r.headers
        sec = {"hsts": bool(h.get("Strict-Transport-Security")),
               "csp": bool(h.get("Content-Security-Policy")),
               "xfo": bool(h.get("X-Frame-Options")),
               "nosniff": bool(h.get("X-Content-Type-Options")),
               "referrer": bool(h.get("Referrer-Policy"))}
        # Advertised national stats + detected features, from their public text
        text = re.sub(r"<[^>]+>", " ", html)
        stats = dict(re.findall(r"(\d{1,4})\s+(Currencies|Branches[A-Za-z ]*|State Offices|Citizens|Companies)", text))
        FEATURES = ["Parliament", "Court", "Stock Exchange", "Marketplace", "Mail",
                    "Elections", "Gazette", "AI Assistant", "Companies", "Dividends", "Bonds", "Passport"]
        features = [w for w in FEATURES if w.lower() in text.lower()]
        res = {"up": r.status_code < 400, "code": r.status_code, "ms": ms,
               "version": ver.group(1) if ver else None,
               "title": (title.group(1).strip()[:80] if title else None),
               "links": links, "server": server[:60], "cloudflare": cloudflare,
               "powered": r.headers.get("X-Powered-By", "")[:60],
               "sec": sec, "sec_score": sum(sec.values()),
               "stats": {v: k for k, v in stats.items()}, "features": features,
               "size": len(html), "ts": time()}
    except Exception as ex:
        res = {"up": False, "code": 0, "ms": int((time() - t0) * 1000),
               "version": None, "title": None, "links": [], "server": "",
               "cloudflare": False, "powered": "", "sec": {}, "sec_score": 0,
               "stats": {}, "features": [], "size": 0, "ts": time(), "err": str(ex)[:80]}

    prev = slot["state"]
    if prev:      # auto-file intel when the target changes
        if res.get("version") and prev.get("version") and res["version"] != prev["version"]:
            _surveil_intel(key, f"{key.title()} updated: v{prev['version']} → v{res['version']}",
                           "Surveillance detected a version change on the target's homepage.")
        elif res["up"] != prev.get("up"):
            _surveil_intel(key, f"{key.title()} is now {'ONLINE' if res['up'] else 'OFFLINE'}",
                           f"Target status changed (HTTP {res['code']}).")
        newlinks = set(res.get("links") or []) - set(prev.get("links") or [])
        if newlinks:
            _surveil_intel(key, f"{key.title()}: new public route(s) detected",
                           "Newly advertised on their homepage: " + ", ".join(sorted(newlinks)[:8]))
    slot["state"] = res
    slot["log"].insert(0, res)
    del slot["log"][30:]
    return res


def _surveil_intel(key, title, detail):
    try:
        supabase.table("athena_intel").insert({
            "agent": "Athena", "kind": "report", "title": title, "detail": detail, "reward": 0
        }).execute()
    except Exception:
        pass


def _surveil_payload():
    now = time()
    def fmt(x):
        if not x:
            return None
        return {"up": x["up"], "code": x["code"], "ms": x["ms"], "version": x.get("version"),
                "title": x.get("title"), "links": x.get("links") or [], "server": x.get("server"),
                "cloudflare": x.get("cloudflare"), "powered": x.get("powered"),
                "sec": x.get("sec") or {}, "sec_score": x.get("sec_score", 0),
                "stats": x.get("stats") or {}, "features": x.get("features") or [],
                "recon": x.get("recon"), "size": x.get("size"), "err": x.get("err"), "ago": int(now - x["ts"])}
    out = {}
    for key in SURVEIL_TARGETS:
        slot = _surveil.get(key, {"state": None, "log": []})
        log = slot["log"]
        uptime = round(100 * sum(1 for x in log if x["up"]) / len(log)) if log else None
        lat = [x["ms"] for x in log if x["up"] and x.get("ms")]
        out[key] = {"target": SURVEIL_TARGETS[key], "state": fmt(slot["state"]),
                    "log": [fmt(x) for x in log[:15]], "checks": len(log),
                    "uptime": uptime, "avg_ms": int(sum(lat) / len(lat)) if lat else None,
                    "recon": slot.get("recon") or []}
    return out


_recon_last = {}
RECON_MIN_GAP = 60      # deep recon (robots/sitemap) at most once/min per target


@app.route("/athena/recon", methods=["POST"])
@limiter.limit("10/minute")
def athena_recon():
    """Deep recon: read the target's robots.txt & sitemap.xml — files a site
    intentionally publishes for crawlers — to map the routes they expose."""
    user = get_current_user(run_economics=False)
    if not user or not is_cia(user):
        return jsonify(success=False, error="Clearance denied"), 403
    key = (request.get_json(silent=True) or {}).get("target") or "aquilithia"
    if key not in SURVEIL_TARGETS:
        return jsonify(success=False, error="Unknown target"), 400
    now = time()
    if now - _recon_last.get(key, 0) < RECON_MIN_GAP:
        return jsonify(success=False, error=f"Recon cooling down — retry in {int(RECON_MIN_GAP-(now-_recon_last[key]))}s."), 429
    _recon_last[key] = now

    import requests
    base = SURVEIL_TARGETS[key].rstrip("/")
    found = set()
    for path in ("/robots.txt", "/sitemap.xml"):
        try:
            rr = requests.get(base + path, timeout=8, headers={"User-Agent": "Cyvathon-Athena-Monitor/1.0"})
            if rr.status_code < 400:
                txt = rr.text or ""
                found |= set(re.findall(r"(?:Disallow|Allow):\s*(/[^\s]*)", txt))
                found |= set(re.findall(r"<loc>\s*https?://[^/]+(/[^<\s]*)", txt))
        except Exception:
            pass
    routes = sorted(r for r in found if r and len(r) < 60)[:60]
    slot = _surveil.setdefault(key, {"state": None, "log": []})
    prev = set(slot.get("recon") or [])
    slot["recon"] = routes
    if routes and prev and (set(routes) - prev):
        _surveil_intel(key, f"{key.title()}: routes discovered via robots/sitemap",
                       ", ".join(sorted(set(routes) - prev)[:10]))
    return jsonify(success=True, surveil=_surveil_payload(), found=len(routes))


@app.route("/athena/surveil", methods=["GET", "POST"])
@limiter.limit("40/minute")
def athena_surveil():
    user = get_current_user(run_economics=False)
    if not user or not is_cia(user):
        return jsonify(success=False, error="Clearance denied"), 403
    key = (request.args.get("target") or (request.get_json(silent=True) or {}).get("target") or "aquilithia")
    if key in SURVEIL_TARGETS:
        st = _surveil.get(key, {}).get("state")
        if not st or time() - st["ts"] >= SURVEIL_MIN_GAP:   # only actually ping when stale
            _do_surveil(key)
    return jsonify(success=True, surveil=_surveil_payload())


# ============================================================
#  WAR ROOM  (President-controlled, restricted access)
# ============================================================
def _war_room():
    """The single war-room state row (id=1). Creates it on demand. Fail-open.
    Cached briefly so gating checks don't re-query on every page load."""
    now = time()
    if _war_cache["row"] is not None and now - _war_cache["at"] < FLAG_TTL:
        return _war_cache["row"]
    try:
        r = supabase.table("war_room").select("*").eq("id", 1).execute().data
        if r:
            _war_cache["row"] = r[0]; _war_cache["at"] = now
            return r[0]
        row = {"id": 1, "alert_level": "peace", "message": "", "members": []}
        try:
            supabase.table("war_room").insert(row).execute()
        except Exception:
            pass
        _war_cache["row"] = row; _war_cache["at"] = now
        return row
    except Exception:
        return None      # table not migrated yet


def _war_members(wr):
    m = (wr or {}).get("members")
    return [str(x) for x in m] if isinstance(m, list) else []


def _war_cleared(user):
    if not user:
        return False
    if is_treasury_admin(user):
        return True
    return user["username"] in _war_members(_war_room())


@app.route("/warroom/data")
def warroom_data():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    if not _war_cleared(user):
        return jsonify(success=False, error="CLASSIFIED — War Room access is restricted."), 403
    wr = _war_room() or {}
    now = time()
    threats = [{"ip": t["ip"], "path": t["path"], "ua": t["ua"], "ago": int(now - t["ts"])}
               for t in _threats[:20]]
    return jsonify(success=True,
                   alert_level=wr.get("alert_level") or "peace",
                   message=wr.get("message") or "",
                   updated_by=wr.get("updated_by"),
                   members=_war_members(wr),
                   is_president=is_treasury_admin(user),
                   threats=threats, surveil=_surveil_payload(), me=user["username"])


@app.route("/warroom/set", methods=["POST"])
@limiter.limit("30/minute")
def warroom_set():
    user = get_current_user(run_economics=False)
    if not user or not is_treasury_admin(user):
        return jsonify(success=False, error="President only"), 403
    d = request.get_json() or {}
    upd = {"updated_by": user["username"]}
    if d.get("alert_level") in ("peace", "alert", "war"):
        upd["alert_level"] = d["alert_level"]
    if "message" in d:
        upd["message"] = (d.get("message") or "").strip()[:400]
    try:
        _war_room()
        supabase.table("war_room").update(upd).eq("id", 1).execute()
        _war_cache["at"] = 0.0
    except Exception:
        return jsonify(success=False, error="War Room isn't enabled yet — the database needs a quick update."), 503
    return jsonify(success=True)


@app.route("/warroom/grant", methods=["POST"])
@limiter.limit("30/minute")
def warroom_grant():
    user = get_current_user(run_economics=False)
    if not user or not is_treasury_admin(user):
        return jsonify(success=False, error="President only"), 403
    target = (request.get_json().get("username") or "").strip()
    if not supabase.table("cybucks").select("id").eq("username", target).execute().data:
        return jsonify(success=False, error="No such citizen"), 404
    wr = _war_room() or {}
    members = sorted(set(_war_members(wr)) | {target})
    try:
        supabase.table("war_room").update({"members": members}).eq("id", 1).execute()
        _war_cache["at"] = 0.0
    except Exception:
        return jsonify(success=False, error="War Room isn't enabled yet."), 503
    notify(target, "⚔️ You have been granted access to the Cyvathon War Room.", "/warroom")
    return jsonify(success=True)


@app.route("/warroom/revoke", methods=["POST"])
@limiter.limit("30/minute")
def warroom_revoke():
    user = get_current_user(run_economics=False)
    if not user or not is_treasury_admin(user):
        return jsonify(success=False, error="President only"), 403
    target = (request.get_json().get("username") or "").strip()
    wr = _war_room() or {}
    members = [m for m in _war_members(wr) if m != target]
    supabase.table("war_room").update({"members": members}).eq("id", 1).execute()
    _war_cache["at"] = 0.0
    return jsonify(success=True)


# ============================================================
#  GOVERNMENT
# ============================================================
@app.route("/gov")
def gov():
    def build():
        res = supabase.table("government").select("*").order("rank").execute()
        return {"success": True, "government": res.data or []}
    return jsonify(cached_json("gov", 60, build))


# ============================================================
#  ADMIN PANEL  (President-only economic policy)
# ============================================================
CONFIG_FIELDS = ["vat_rate", "tax_period_days", "salary_period_days", "savings_rate",
                 "bond_rate", "bond_days", "company_fee", "loan_max", "loan_days",
                 "starting_grant", "gdp", "gdp_multiplier",
                 "courier_wage", "delivery_levy", "insurance_levy", "lend_max_deposit",
                 "pen_rate"]


@app.route("/admin/config", methods=["GET"])
def admin_config_get():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    if not is_treasury_admin(user):
        return jsonify(success=False, error="President only"), 403
    refresh_config()
    r = supabase.table("config").select("*").eq("id", 1).execute().data
    return jsonify(success=True, config=(r[0] if r else {}))


@app.route("/admin/config", methods=["POST"])
@limiter.limit("20/minute")
def admin_config_set():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    if not is_treasury_admin(user):
        return jsonify(success=False, error="President only"), 403

    d = request.get_json() or {}
    upd = {}
    for f in CONFIG_FIELDS:
        if f in d and d[f] not in (None, ""):
            try:
                upd[f] = float(d[f])
            except (TypeError, ValueError):
                pass
    if not upd:
        return jsonify(success=False, error="No valid values supplied"), 400

    # ensure the row exists, then update + reload globals live
    refresh_config()
    supabase.table("config").update(upd).eq("id", 1).execute()
    refresh_config()
    _gdp_cache["v"] = None          # recompute GDP with the new multiplier
    r = supabase.table("config").select("*").eq("id", 1).execute().data
    return jsonify(success=True, config=(r[0] if r else {}))


# ============================================================
#  ADMIN — SECURITY (IP blocking)
# ============================================================
@app.route("/admin/security")
def admin_security():
    user = get_current_user(run_economics=False)
    if not user or not is_treasury_admin(user):
        return jsonify(success=False, error="President only"), 403
    blocked = supabase.table("blocked_ips").select("*").order("created_at", desc=True).execute().data or []
    signups = supabase.table("cybucks").select("username,reg_ip,created_at,banned") \
        .order("created_at", desc=True).limit(40).execute().data or []
    banned = supabase.table("cybucks").select("username,reg_ip").eq("banned", True).execute().data or []
    try:
        pending = supabase.table("cybucks").select("username,reg_ip,created_at,referred_by") \
            .eq("approved", False).order("created_at", desc=True).execute().data or []
    except Exception:
        try:
            pending = supabase.table("cybucks").select("username,reg_ip,created_at") \
                .eq("approved", False).order("created_at", desc=True).execute().data or []
        except Exception:
            pending = []      # columns not migrated yet
    return jsonify(success=True, blocked=blocked, signups=signups, banned=banned, pending=pending)


@app.route("/admin/approve", methods=["POST"])
@limiter.limit("60/minute")
def admin_approve():
    user = get_current_user(run_economics=False)
    if not user or not is_treasury_admin(user):
        return jsonify(success=False, error="President only"), 403
    target = (request.get_json().get("username") or "").strip()
    row = supabase.table("cybucks").select("approved,referred_by").eq("username", target).execute().data
    if not row:
        return jsonify(success=False, error="No such applicant"), 404
    was_pending = row[0].get("approved") is False      # only reward on the FIRST approval
    supabase.table("cybucks").update({"approved": True}).eq("username", target).execute()
    add_record(target, "Citizenship application approved by the President.")
    notify(target, "\U0001F389 Your Cyvathon citizenship has been approved — you can now log in!", "/login")

    ref = (row[0].get("referred_by") or "").strip()
    if was_pending and ref and ref != target and \
            supabase.table("cybucks").select("id").eq("username", ref).execute().data:
        cas_adjust(ref, "balance", REFERRAL_BONUS, allow_negative=True)
        add_record(ref, f"Referral bonus: {target} joined Cyvathon on your invite (+{REFERRAL_BONUS} CB).")
        notify(ref, f"\U0001F389 {target} joined on your invite! You earned {REFERRAL_BONUS} CB.", "/invite")
    return jsonify(success=True, referral_paid=bool(was_pending and ref))


@app.route("/admin/reject", methods=["POST"])
@limiter.limit("60/minute")
def admin_reject():
    """Reject a pending applicant: delete the account and block their signup IP."""
    user = get_current_user(run_economics=False)
    if not user or not is_treasury_admin(user):
        return jsonify(success=False, error="President only"), 403
    target = (request.get_json().get("username") or "").strip()
    if target in TREASURY_ADMINS:
        return jsonify(success=False, error="You cannot reject the President"), 400
    row = supabase.table("cybucks").select("reg_ip,approved").eq("username", target).execute().data
    if not row:
        return jsonify(success=False, error="No such applicant"), 404
    if row[0].get("approved", True) is not False:
        return jsonify(success=False, error="That citizen is already approved — ban them instead."), 400
    for tbl, col in [("records", "username"), ("notifications", "username"),
                     ("chat_group_members", "username")]:
        try:
            supabase.table(tbl).delete().eq(col, target).execute()
        except Exception:
            pass
    supabase.table("cybucks").delete().eq("username", target).execute()
    ip = row[0].get("reg_ip")
    block = bool(request.get_json().get("block")) and ip
    if block:
        try:
            supabase.table("blocked_ips").upsert({"ip": ip, "reason": "rejected applicant"}).execute()
        except Exception:
            supabase.table("blocked_ips").insert({"ip": ip, "reason": "rejected applicant"}).execute()
        load_blocked_ips()
    return jsonify(success=True, ip_blocked=bool(block))


@app.route("/admin/block_user", methods=["POST"])
@limiter.limit("30/minute")
def admin_block_user():
    user = get_current_user(run_economics=False)
    if not user or not is_treasury_admin(user):
        return jsonify(success=False, error="President only"), 403
    d = request.get_json()
    target = (d.get("username") or "").strip()
    reason = (d.get("reason") or "").strip() or ("Banned citizen: " + target)
    if target in TREASURY_ADMINS:
        return jsonify(success=False, error="You cannot ban the President"), 400
    row = supabase.table("cybucks").select("reg_ip").eq("username", target).execute().data
    if not row:
        return jsonify(success=False, error="No such citizen"), 404

    supabase.table("cybucks").update({"banned": True}).eq("username", target).execute()
    ip = row[0].get("reg_ip")
    if ip:
        try:
            supabase.table("blocked_ips").upsert({"ip": ip, "reason": reason}).execute()
        except Exception:
            supabase.table("blocked_ips").insert({"ip": ip, "reason": reason}).execute()
        load_blocked_ips()
    return jsonify(success=True, ip_blocked=bool(ip), ip=ip)


@app.route("/admin/unban", methods=["POST"])
@limiter.limit("30/minute")
def admin_unban():
    user = get_current_user(run_economics=False)
    if not user or not is_treasury_admin(user):
        return jsonify(success=False, error="President only"), 403
    target = (request.get_json().get("username") or "").strip()
    row = supabase.table("cybucks").select("reg_ip").eq("username", target).execute().data
    supabase.table("cybucks").update({"banned": False}).eq("username", target).execute()
    if row and row[0].get("reg_ip"):
        supabase.table("blocked_ips").delete().eq("ip", row[0]["reg_ip"]).execute()
        load_blocked_ips()
    return jsonify(success=True)


@app.route("/admin/block", methods=["POST"])
@limiter.limit("30/minute")
def admin_block():
    user = get_current_user(run_economics=False)
    if not user or not is_treasury_admin(user):
        return jsonify(success=False, error="President only"), 403
    d = request.get_json()
    ip = (d.get("ip") or "").strip()
    reason = (d.get("reason") or "").strip()
    if not ip:
        return jsonify(success=False, error="Enter an IP"), 400
    try:
        supabase.table("blocked_ips").upsert({"ip": ip, "reason": reason}).execute()
    except Exception:
        supabase.table("blocked_ips").insert({"ip": ip, "reason": reason}).execute()
    load_blocked_ips()
    return jsonify(success=True)


@app.route("/admin/unblock", methods=["POST"])
@limiter.limit("30/minute")
def admin_unblock():
    user = get_current_user(run_economics=False)
    if not user or not is_treasury_admin(user):
        return jsonify(success=False, error="President only"), 403
    ip = (request.get_json().get("ip") or "").strip()
    supabase.table("blocked_ips").delete().eq("ip", ip).execute()
    load_blocked_ips()
    return jsonify(success=True)


# ============================================================
#  ECONOMY STATS  (public)
# ============================================================
@app.route("/economy")
def economy():
    def build():
        t = get_treasury()
        total = supabase.table("cybucks").select("id", count="exact").execute().count or 0
        try:      # exclude banned citizens, matching the Citizens page (null banned = still a citizen)
            banned = supabase.table("cybucks").select("id", count="exact") \
                .eq("banned", True).execute().count or 0
        except Exception:      # banned column not migrated — count everyone
            banned = 0
        citizen_count = max(0, total - banned)
        companies = supabase.table("companies").select("id", count="exact").execute()
        return {
            "success": True, "gdp": compute_gdp(), "treasury": t["balance"],
            "citizens": citizen_count, "companies": companies.count or 0,
            "rates": {"pufb_per_cybuck": PUFB_PER_CYBUCK, "aquilines_per_pufb": AQUILINES_PER_PUFB},
        }
    return jsonify(cached_json("economy", 45, build))


# ============================================================
#  CASINO  (the Treasury is the house)
# ============================================================
CASINO_MAX_BET = 5000        # cap per spin — limits variance & abuse
_SLOT_SYMBOLS = ["🍒", "🍋", "🔔", "💎", "7️⃣"]
_SLOT_MULT = {"7️⃣": 20, "💎": 10}     # three-of-a-kind jackpots; others pay 5x


def _casino_resolve(game, bet, choice):
    """Server-side RNG. Returns (payout, outcome, detail). payout=0 means a loss."""
    if game == "coin":
        flip = random.choice(["heads", "tails"])
        if choice == flip:
            return round(bet * 1.95, 2), flip, f"The coin landed {flip} — you won!"
        return 0, flip, f"The coin landed {flip}. Better luck next time."
    if game == "dice":
        roll = random.randint(1, 6)
        if choice == roll:
            return round(bet * 5, 2), roll, f"Rolled a {roll} — dead on! 5×!"
        return 0, roll, f"Rolled a {roll}. Not your number."
    # slots
    reels = [random.choice(_SLOT_SYMBOLS) for _ in range(3)]
    face = " ".join(reels)
    if reels[0] == reels[1] == reels[2]:
        mult = _SLOT_MULT.get(reels[0], 5)
        return round(bet * mult, 2), face, f"{face} — THREE OF A KIND! {mult}×!"
    if reels[0] == reels[1] or reels[1] == reels[2] or reels[0] == reels[2]:
        return round(bet * 1.2, 2), face, f"{face} — a pair! 1.2×."
    return 0, face, f"{face} — no match."


@app.route("/casino/play", methods=["POST"])
@limiter.limit("60/minute")
def casino_play():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    d = request.get_json() or {}
    game = d.get("game")
    if game not in ("coin", "dice", "slots"):
        return jsonify(success=False, error="Unknown game"), 400
    try:
        bet = float(d.get("bet") or 0)
    except (TypeError, ValueError):
        bet = float("nan")
    if not math.isfinite(bet) or bet <= 0:
        return jsonify(success=False, error="Enter a valid bet"), 400
    bet = round(bet, 2)
    if bet > CASINO_MAX_BET:
        return jsonify(success=False, error=f"Table limit is {CASINO_MAX_BET} CB per play"), 400

    # validate the player's pick
    choice = d.get("choice")
    if game == "coin" and choice not in ("heads", "tails"):
        return jsonify(success=False, error="Pick heads or tails"), 400
    if game == "dice":
        try:
            choice = int(choice)
        except (TypeError, ValueError):
            choice = 0
        if not 1 <= choice <= 6:
            return jsonify(success=False, error="Pick a number from 1 to 6"), 400

    me = user["username"]
    fresh = supabase.table("cybucks").select("balance").eq("username", me).execute().data[0]
    if bet > _available_cb(me, fresh.get("balance")):
        return jsonify(success=False, error="Not enough spendable Cybucks (borrowed funds can't be gambled)"), 400
    # take the stake atomically — no race double-spend
    if not cas_adjust(me, "balance", -bet):
        return jsonify(success=False, error="Insufficient funds or a conflicting bet — try again"), 400

    payout, outcome, detail = _casino_resolve(game, bet, choice)
    if payout > 0:
        cas_adjust(me, "balance", payout, allow_negative=True)
    treasury_add(cybucks=round(bet - payout, 2), counterparty=me, kind="casino")  # house = Treasury
    net = round(payout - bet, 2)
    log_txn("casino", "The House" if net >= 0 else "Casino", me, abs(net), "cybucks",
            f"{game}: {detail}")
    fresh2 = supabase.table("cybucks").select("*").eq("username", me).execute().data[0]
    return jsonify(success=True, win=payout > 0, bet=bet, payout=payout, net=net,
                   outcome=outcome, detail=detail, user=public_user(fresh2))


# ============================================================
#  BLUFF  — multiplayer card game (a.k.a. Cheat / BS), bet in Cybucks
# ============================================================
BLUFF_RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
BLUFF_SUITS = ["S", "H", "D", "C"]
BLUFF_MAX_ANTE = 1000
BLUFF_MAX_PLAYERS = 4


def _bluff_rank_of(card):
    return card[:-1]


def _bluff_deal(players):
    deck = [r + s for r in BLUFF_RANKS for s in BLUFF_SUITS]
    random.shuffle(deck)
    hands = {p: [] for p in players}
    for i, c in enumerate(deck):
        hands[players[i % len(players)]].append(c)
    return hands


def _bluff_play(s, me, idxs, rank=None):
    if s.get("winner"):
        return "The game is over"
    players = s["players"]
    if s.get("pending"):                       # declining to challenge => the pending player wins
        s["winner"] = s["pending"]; s["pending"] = None
        s["log"] = (s.get("log", []) + [f"{s['winner']} wins — nobody challenged the last play!"])[-10:]
        return None
    if players[s["turn"]] != me:
        return "It's not your turn"
    hand = s["hands"][me]
    try:
        idxs = sorted(set(int(i) for i in idxs))
    except (TypeError, ValueError):
        return "Bad card selection"
    if not (1 <= len(idxs) <= 7):
        return "Play between 1 and 7 cards"
    if any(i < 0 or i >= len(hand) for i in idxs):
        return "Bad card selection"
    if s["claim"] is None:
        if rank not in BLUFF_RANKS:
            return "Choose a rank to claim"
        s["claim"] = rank
    claim = s["claim"]
    cards = [hand[i] for i in idxs]
    sel = set(idxs)
    s["hands"][me] = [c for j, c in enumerate(hand) if j not in sel]
    s["pile"].extend(cards)
    s["last"] = {"player": me, "count": len(cards), "claim": claim}
    s["log"] = (s.get("log", []) + [f"{me} played {len(cards)} × {claim}"])[-10:]
    if not s["hands"][me]:
        s["pending"] = me
    s["turn"] = (s["turn"] + 1) % len(players)
    return None


def _bluff_call(s, me):
    if s.get("winner"):
        return "The game is over"
    players = s["players"]
    if players[s["turn"]] != me:
        return "It's not your turn"
    last = s.get("last")
    if not last or s["claim"] is None:
        return "There's nothing to call yet"
    reveal = s["pile"][-last["count"]:]
    bluffing = any(_bluff_rank_of(c) != last["claim"] for c in reveal)
    if bluffing:
        taker = last["player"]; s["pending"] = None
        s["log"] = (s.get("log", []) + [f"{me} called bluff on {last['player']} — CAUGHT! {last['player']} takes the pile"])[-10:]
    else:
        if s.get("pending") == last["player"]:
            s["winner"] = last["player"]
            s["log"] = (s.get("log", []) + [f"{me} called bluff on {last['player']} — but it was true. {last['player']} wins!"])[-10:]
            return None
        taker = me
        s["log"] = (s.get("log", []) + [f"{me} called bluff on {last['player']} — wrong! {me} takes the pile"])[-10:]
    s["hands"][taker].extend(s["pile"])
    s["pile"] = []; s["claim"] = None; s["last"] = None; s["pending"] = None
    ti = players.index(taker)
    s["turn"] = (ti + 1) % len(players)
    return None


def _bluff_view(g, me):
    s = g.get("state") or {}
    players = s.get("players", [])
    turn_user = players[s["turn"]] if (g["status"] == "playing" and players) else None
    hands = s.get("hands", {})
    pcount = len(players) if players else 1
    return {
        "gid": g["id"], "status": g["status"], "host": g["host"],
        "ante": g["ante"], "pot": g["ante"] * pcount, "me": me,
        "players": [{"username": p, "count": len(hands.get(p, [])),
                     "is_turn": p == turn_user, "you": p == me} for p in players],
        "turn": turn_user, "claim": s.get("claim"), "pile_count": len(s.get("pile", [])),
        "last": s.get("last"), "pending": s.get("pending"), "winner": g.get("winner"),
        "log": s.get("log", []), "hand": hands.get(me, []),
        "you_in": me in players,
        "can_start": g["status"] == "waiting" and g["host"] == me and len(players) >= 2,
    }


@app.route("/casino/bluff")
def bluff_page():
    return app.send_static_file("bluff.html")


@app.route("/casino/bluff/list")
def bluff_list():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    try:
        rows = supabase.table("bluff_games").select("*").eq("status", "waiting") \
            .order("id", desc=True).limit(30).execute().data or []
    except Exception:
        return jsonify(success=True, games=[], me=user["username"])
    games = [{"gid": g["id"], "host": g["host"], "ante": g["ante"],
              "players": len((g.get("state") or {}).get("players", []))} for g in rows]
    # if you're already seated in a game, tell the client so it can jump back in
    mine = None
    try:
        act = supabase.table("bluff_games").select("id,state,status") \
            .in_("status", ["waiting", "playing"]).order("id", desc=True).limit(60).execute().data or []
        for g in act:
            if user["username"] in ((g.get("state") or {}).get("players", [])):
                mine = g["id"]; break
    except Exception:
        pass
    return jsonify(success=True, games=games, me=user["username"], mine=mine)


@app.route("/casino/bluff/create", methods=["POST"])
@limiter.limit("20/minute")
def bluff_create():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    try:
        ante = int(float((request.get_json() or {}).get("ante") or 0))
    except (TypeError, ValueError):
        ante = 0
    if not (1 <= ante <= BLUFF_MAX_ANTE):
        return jsonify(success=False, error=f"Ante must be 1–{BLUFF_MAX_ANTE} CB"), 400
    fresh = supabase.table("cybucks").select("balance").eq("username", me).execute().data
    if not fresh or ante > _available_cb(me, fresh[0].get("balance")):
        return jsonify(success=False, error="Not enough spendable Cybucks for that ante"), 400
    if not cas_adjust(me, "balance", -ante):
        return jsonify(success=False, error="Couldn't place your ante — try again"), 400
    state = {"players": [me], "hands": {}, "turn": 0, "claim": None,
             "pile": [], "last": None, "pending": None, "winner": None,
             "log": [f"{me} opened a table (ante {ante} CB)"]}
    try:
        g = supabase.table("bluff_games").insert(
            {"status": "waiting", "host": me, "ante": ante, "state": state}).execute().data[0]
    except Exception:
        cas_adjust(me, "balance", ante, allow_negative=True)      # refund on failure
        return jsonify(success=False, error="Bluff isn't set up yet — run the migration."), 500
    return jsonify(success=True, gid=g["id"])


def _bluff_get(gid):
    r = supabase.table("bluff_games").select("*").eq("id", gid).execute().data
    return r[0] if r else None


@app.route("/casino/bluff/join", methods=["POST"])
@limiter.limit("30/minute")
def bluff_join():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    gid = (request.get_json() or {}).get("gid")
    g = _bluff_get(gid)
    if not g:
        return jsonify(success=False, error="Table not found"), 404
    if g["status"] != "waiting":
        return jsonify(success=False, error="That game has already started"), 400
    state = g["state"]; players = state["players"]
    if me in players:
        return jsonify(success=True, gid=gid)
    if len(players) >= BLUFF_MAX_PLAYERS:
        return jsonify(success=False, error="That table is full (4 players max)"), 400
    ante = g["ante"]
    fresh = supabase.table("cybucks").select("balance").eq("username", me).execute().data
    if not fresh or ante > _available_cb(me, fresh[0].get("balance")):
        return jsonify(success=False, error="Not enough spendable Cybucks for that ante"), 400
    if not cas_adjust(me, "balance", -ante):
        return jsonify(success=False, error="Couldn't place your ante — try again"), 400
    players.append(me)
    state["log"] = (state.get("log", []) + [f"{me} joined the table"])[-10:]
    supabase.table("bluff_games").update({"state": state}).eq("id", gid).execute()
    return jsonify(success=True, gid=gid)


@app.route("/casino/bluff/leave", methods=["POST"])
@limiter.limit("30/minute")
def bluff_leave():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    gid = (request.get_json() or {}).get("gid")
    g = _bluff_get(gid)
    if not g or me not in (g["state"].get("players") or []):
        return jsonify(success=True)
    state = g["state"]; players = state["players"]; ante = g["ante"]
    if g["status"] == "waiting":
        players.remove(me)
        cas_adjust(me, "balance", ante, allow_negative=True)      # refund the ante
        if not players:
            supabase.table("bluff_games").delete().eq("id", gid).execute()
            return jsonify(success=True)
        host = me == g["host"] and players[0] or g["host"]
        state["log"] = (state.get("log", []) + [f"{me} left the table"])[-10:]
        supabase.table("bluff_games").update({"state": state, "host": host}).eq("id", gid).execute()
        return jsonify(success=True)
    if g["status"] == "playing":
        # forfeit: game ends, the pot goes to the remaining player closest to winning
        others = [p for p in players if p != me]
        if not others:
            supabase.table("bluff_games").update({"status": "done"}).eq("id", gid).execute()
            return jsonify(success=True)
        hands = state.get("hands", {})
        winner = min(others, key=lambda p: (len(hands.get(p, [])), players.index(p)))
        pot = ante * len(players)
        cas_adjust(winner, "balance", pot, allow_negative=True)
        state["winner"] = winner
        state["log"] = (state.get("log", []) + [f"{me} forfeited — {winner} wins the pot ({pot} CB)"])[-10:]
        supabase.table("bluff_games").update({"status": "done", "winner": winner, "state": state}).eq("id", gid).execute()
        log_txn("bluff", "Bluff table", winner, pot, "cybucks", "won the pot (forfeit)")
        return jsonify(success=True)
    return jsonify(success=True)


@app.route("/casino/bluff/start", methods=["POST"])
@limiter.limit("30/minute")
def bluff_start():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    gid = (request.get_json() or {}).get("gid")
    g = _bluff_get(gid)
    if not g:
        return jsonify(success=False, error="Table not found"), 404
    if g["host"] != user["username"]:
        return jsonify(success=False, error="Only the host can start"), 403
    if g["status"] != "waiting":
        return jsonify(success=False, error="Already started"), 400
    state = g["state"]; players = state["players"]
    if len(players) < 2:
        return jsonify(success=False, error="Need at least 2 players"), 400
    state["hands"] = _bluff_deal(players)
    state["turn"] = 0; state["claim"] = None; state["pile"] = []
    state["last"] = None; state["pending"] = None; state["winner"] = None
    state["log"] = (state.get("log", []) + ["The game begins — cards dealt."])[-10:]
    supabase.table("bluff_games").update({"status": "playing", "state": state}).eq("id", gid).execute()
    return jsonify(success=True)


@app.route("/casino/bluff/state")
def bluff_state():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    g = _bluff_get(request.args.get("gid", type=int))
    if not g:
        return jsonify(success=False, error="Table not found"), 404
    return jsonify(success=True, view=_bluff_view(g, user["username"]))


def _bluff_finish_if_won(g, gid):
    """If the state has a winner, mark the game done and pay the pot."""
    state = g["state"]
    if not state.get("winner"):
        supabase.table("bluff_games").update({"state": state}).eq("id", gid).execute()
        return
    winner = state["winner"]
    pot = g["ante"] * len(state["players"])
    cas_adjust(winner, "balance", pot, allow_negative=True)
    supabase.table("bluff_games").update({"status": "done", "winner": winner, "state": state}).eq("id", gid).execute()
    log_txn("bluff", "Bluff table", winner, pot, "cybucks", "won the pot")


@app.route("/casino/bluff/play", methods=["POST"])
@limiter.limit("120/minute")
def bluff_play_route():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    d = request.get_json() or {}
    g = _bluff_get(d.get("gid"))
    if not g or g["status"] != "playing":
        return jsonify(success=False, error="Game isn't running"), 400
    err = _bluff_play(g["state"], user["username"], d.get("cards") or [], d.get("rank"))
    if err:
        return jsonify(success=False, error=err), 400
    _bluff_finish_if_won(g, g["id"])
    return jsonify(success=True, view=_bluff_view(_bluff_get(g["id"]), user["username"]))


@app.route("/casino/bluff/call", methods=["POST"])
@limiter.limit("120/minute")
def bluff_call_route():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    g = _bluff_get((request.get_json() or {}).get("gid"))
    if not g or g["status"] != "playing":
        return jsonify(success=False, error="Game isn't running"), 400
    err = _bluff_call(g["state"], user["username"])
    if err:
        return jsonify(success=False, error=err), 400
    _bluff_finish_if_won(g, g["id"])
    return jsonify(success=True, view=_bluff_view(_bluff_get(g["id"]), user["username"]))


# ============================================================
#  CHAT  (now gated behind the single national account)
# ============================================================
#  MAIL  — Gmail-style inbox (subject + body, multiple recipients)
# ============================================================
def _mail_avatars(names):
    return _avatars_for(names)


@app.route("/mail/unread")
def mail_unread():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=True, unread=0)
    try:
        n = supabase.table("mail_recipients").select("id", count="exact") \
            .eq("username", user["username"]).eq("read", False).eq("archived", False) \
            .execute().count or 0
    except Exception:
        n = 0
    return jsonify(success=True, unread=n)


@app.route("/mail/list")
def mail_list():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    folder = (request.args.get("folder") or "inbox").lower()
    try:
        if folder == "sent":
            mails = supabase.table("mail").select("*").eq("sender", me) \
                .order("id", desc=True).limit(80).execute().data or []
            rec_map = {}
            for r in (supabase.table("mail_recipients").select("mail_id,username")
                      .in_("mail_id", [m["id"] for m in mails] or [0]).execute().data or []):
                rec_map.setdefault(r["mail_id"], []).append(r["username"])
            out = []
            for m in mails:
                out.append({**m, "recipients": rec_map.get(m["id"], []),
                            "read": True, "starred": False})
        else:
            q = supabase.table("mail_recipients").select("*").eq("username", me)
            q = q.eq("starred", True) if folder == "starred" else q.eq("archived", False)
            recs = q.order("id", desc=True).limit(80).execute().data or []
            mids = [r["mail_id"] for r in recs]
            mails = {m["id"]: m for m in (supabase.table("mail").select("*")
                     .in_("id", mids or [0]).execute().data or [])}
            out = []
            for r in recs:
                m = mails.get(r["mail_id"])
                if not m:
                    continue
                out.append({**m, "read": bool(r.get("read")), "starred": bool(r.get("starred")),
                            "rid": r["id"], "recipients": []})
    except Exception:
        return jsonify(success=True, mails=[], me=me, folder=folder)
    av = _mail_avatars([m.get("sender") for m in out])
    for m in out:
        m["avatar"] = av.get(m.get("sender"))
        b = (m.get("body") or "")
        m["preview"] = b[:120] + ("…" if len(b) > 120 else "")
    return jsonify(success=True, mails=out, me=me, folder=folder)


@app.route("/mail/<int:mid>")
def mail_get(mid):
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    m = supabase.table("mail").select("*").eq("id", mid).execute().data
    if not m:
        return jsonify(success=False, error="Mail not found"), 404
    m = m[0]
    tid = m.get("thread_id") or m["id"]

    # Pull the whole conversation (all messages sharing this thread).
    try:
        thread = supabase.table("mail").select("*") \
            .or_(f"thread_id.eq.{tid},id.eq.{tid}").order("created_at").execute().data or []
    except Exception:
        thread = [m]      # thread_id column not migrated — single message
    if not thread:
        thread = [m]
    ids = [x["id"] for x in thread]
    recs = supabase.table("mail_recipients").select("*").in_("mail_id", ids or [0]).execute().data or []
    rec_by_mail = {}
    for r in recs:
        rec_by_mail.setdefault(r["mail_id"], []).append(r)

    # Only messages I'm a party to (sender or recipient).
    def party(x):
        return x["sender"] == me or any(r["username"] == me for r in rec_by_mail.get(x["id"], []))
    visible = [x for x in thread if party(x)]
    if not visible:
        return jsonify(success=False, error="This mail isn't addressed to you"), 403

    # Mark my unread rows in this thread as read.
    for r in recs:
        if r["username"] == me and not r.get("read"):
            supabase.table("mail_recipients").update({"read": True}).eq("id", r["id"]).execute()

    everyone = [x["sender"] for x in visible]
    for x in visible:
        everyone += [r["username"] for r in rec_by_mail.get(x["id"], [])]
    av = _mail_avatars(everyone)
    messages = [{
        "id": x["id"], "sender": x["sender"], "subject": x.get("subject"),
        "body": x.get("body"), "created_at": x.get("created_at"),
        "avatar": av.get(x["sender"]),
        "recipients": [{"username": r["username"], "avatar": av.get(r["username"])}
                       for r in rec_by_mail.get(x["id"], [])],
    } for x in visible]

    opened = next((x for x in visible if x["id"] == mid), visible[-1])
    mine = next((r for r in rec_by_mail.get(opened["id"], []) if r["username"] == me), None)
    # who to reply to: everyone in the thread except me
    participants = [p for p in dict.fromkeys(everyone) if p != me]
    return jsonify(success=True, me=me, subject=messages[0]["subject"],
                   messages=messages, last_id=messages[-1]["id"],
                   participants=participants,
                   mail={"id": opened["id"],
                         "starred": bool(mine.get("starred")) if mine else False,
                         "rid": mine["id"] if mine else None})


@app.route("/mail/send", methods=["POST"])
@limiter.limit("20/minute")
def mail_send():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    d = request.get_json() or {}
    subject = (d.get("subject") or "").strip()[:200] or "(no subject)"
    body = (d.get("body") or "").strip()[:20000]
    if not body:
        return jsonify(success=False, error="Write something in the message."), 400

    broadcast = bool(d.get("everyone"))
    if broadcast:
        # Send to the whole micronation (every non-banned citizen except me).
        rows = supabase.table("cybucks").select("username,banned").execute().data or []
        to = [r["username"] for r in rows if not r.get("banned") and r["username"] != me]
    else:
        to = d.get("to") or []
        if not isinstance(to, list):
            to = [to]
        to = [str(t).strip() for t in to if str(t).strip() and str(t).strip() != me]
        to = list(dict.fromkeys(to))[:25]
        if not to:
            return jsonify(success=False, error="Pick at least one recipient."), 400
        # keep only real citizens
        valid = {r["username"] for r in (supabase.table("cybucks").select("username")
                 .in_("username", to).execute().data or [])}
        to = [t for t in to if t in valid]
    if not to:
        return jsonify(success=False, error="No valid recipients."), 400

    # Threading: a reply inherits its parent's thread so the conversation stays together.
    thread_id = None
    reply_to = d.get("reply_to")
    if reply_to:
        try:
            p = supabase.table("mail").select("id,thread_id").eq("id", reply_to).execute().data
            if p:
                thread_id = p[0].get("thread_id") or p[0]["id"]
        except Exception:
            thread_id = None

    row = {"sender": me, "subject": subject, "body": body}
    if thread_id:
        row["thread_id"] = thread_id
    try:
        m = supabase.table("mail").insert(row).execute().data[0]
    except Exception:
        row.pop("thread_id", None)      # thread_id column not migrated — send unthreaded
        try:
            m = supabase.table("mail").insert(row).execute().data[0]
        except Exception:
            return jsonify(success=False, error="Mail isn't set up yet — run the migration."), 500
    if not thread_id:      # a brand-new thread points at itself (best effort)
        try:
            supabase.table("mail").update({"thread_id": m["id"]}).eq("id", m["id"]).execute()
        except Exception:
            pass
    try:
        supabase.table("mail_recipients").insert(
            [{"mail_id": m["id"], "username": t} for t in to]).execute()
    except Exception:
        return jsonify(success=False, error="Mail isn't set up yet — run the migration."), 500
    for t in to:
        notify(t, f"📧 New mail from {me}: {subject}", "/mail")
    return jsonify(success=True, mail=m)


@app.route("/mail/<int:mid>/star", methods=["POST"])
@limiter.limit("60/minute")
def mail_star(mid):
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    r = supabase.table("mail_recipients").select("id,starred") \
        .eq("mail_id", mid).eq("username", user["username"]).execute().data
    if not r:
        return jsonify(success=False, error="Not your mail"), 404
    new = not bool(r[0].get("starred"))
    supabase.table("mail_recipients").update({"starred": new}).eq("id", r[0]["id"]).execute()
    return jsonify(success=True, starred=new)


@app.route("/mail/<int:mid>/archive", methods=["POST"])
@limiter.limit("60/minute")
def mail_archive(mid):
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    r = supabase.table("mail_recipients").select("id") \
        .eq("mail_id", mid).eq("username", user["username"]).execute().data
    if not r:
        return jsonify(success=False, error="Not your mail"), 404
    supabase.table("mail_recipients").update({"archived": True, "read": True}) \
        .eq("id", r[0]["id"]).execute()
    return jsonify(success=True)


def _insert_message(row, data):
    """Insert a message, attaching reply-to metadata when present.
    Fails open if the reply_* columns haven't been migrated yet."""
    reply_to = (data or {}).get("reply_to")
    if reply_to:
        try:
            return supabase.table("messages").insert({
                **row,
                "reply_to": reply_to,
                "reply_sender": ((data.get("reply_sender") or "")[:32]),
                "reply_text": ((data.get("reply_text") or "")[:140]),
            }).execute().data[0]
        except Exception:
            pass          # columns not present — fall through to a plain insert
    return supabase.table("messages").insert(row).execute().data[0]


@app.route("/presence", methods=["GET", "POST"])
@limiter.limit("120 per minute")
def presence():
    user = get_current_user(run_economics=False)   # this call also marks me online
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    now = time()
    online = [u for u, t in list(_presence.items()) if now - t < PRESENCE_TTL]
    for u, t in list(_presence.items()):           # prune stale entries
        if now - t > PRESENCE_TTL * 4:
            _presence.pop(u, None)
    return jsonify(success=True, online=online)


@app.route("/typing", methods=["GET", "POST"])
@limiter.limit("240 per minute")
def typing():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    now = time()
    convo = (request.args.get("convo")
             or (request.get_json(silent=True) or {}).get("convo") or "").strip()
    if not convo:
        return jsonify(success=True, typing=[])
    room = _typing.setdefault(convo, {})
    if request.method == "POST":
        room[user["username"]] = now + TYPING_TTL
    others = [u for u, exp in list(room.items()) if exp > now and u != user["username"]]
    for u, exp in list(room.items()):
        if exp <= now:
            room.pop(u, None)
    return jsonify(success=True, typing=others)


@app.route("/messages", methods=["POST"])
@limiter.limit("60 per minute")
def send_message():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401

    content = request.get_json().get("content", "").strip()
    if not content:
        return jsonify(success=False, error="Empty message"), 400
    if len(content) > 2000:
        return jsonify(success=False, error="Message too long (max 2000 characters)"), 400

    row = _insert_message({"sender": user["username"], "recipient": None, "content": content},
                          request.get_json())
    _notify_mentions(user["username"], content, "/chat")
    # @everyone in the Public Square pings all citizens — President only
    if _EVERYONE_RE.search(content) and is_treasury_admin(user):
        try:
            allc = {r["username"] for r in (supabase.table("cybucks")
                    .select("username").execute().data or [])}
            _notify_everyone(user["username"], "/chat", allc)
        except Exception:
            pass
    return jsonify(success=True, message=row)


_MENTION_RE = re.compile(r"@([A-Za-z0-9_.\-]{2,32})")
_EVERYONE_RE = re.compile(r"(?<![\w@])@everyone\b", re.I)


def _is_group_mod(user, group):
    """A chat moderator: the group's creator/owner, or the President."""
    if not user:
        return False
    if is_treasury_admin(user):
        return True
    return bool(group) and group.get("owner") == user["username"]


def _notify_everyone(sender, link, recipients):
    """Fan out an @everyone ping to a set of usernames (moderators only)."""
    for n in list(recipients)[:800]:
        if n and n != sender:
            notify(n, f"\U0001F4E2 {sender} tagged @everyone", link)


def _notify_mentions(sender, content, link, exclude=None, only=None):
    """Ping @mentioned citizens. `only` (a set) restricts to e.g. group members."""
    names = set(_MENTION_RE.findall(content or ""))
    names.discard(sender)
    if exclude:
        names.discard(exclude)
    if only is not None:
        names &= only
    for n in list(names)[:10]:
        if only is not None or supabase.table("cybucks").select("id").eq("username", n).execute().data:
            notify(n, f"\U0001F4AC {sender} mentioned you", link)


# ----- Chat attachments: device image upload + Tenor GIF search -----
_CHAT_BUCKET = "chat"
_chat_bucket_ready = False


def _ensure_chat_bucket():
    global _chat_bucket_ready
    if _chat_bucket_ready:
        return
    try:
        supabase.storage.create_bucket(_CHAT_BUCKET, options={"public": True})
    except Exception:
        pass          # already exists (or creation not permitted) — upload will tell us
    _chat_bucket_ready = True


def _store_image(f, folder):
    """Validate + upload an image to the public 'chat' bucket. Returns (url, error)."""
    data = f.read()
    if not data:
        return None, "Empty file"
    if len(data) > 5 * 1024 * 1024:
        return None, "Image too large (max 5 MB)"
    mime = (f.mimetype or "").lower()
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg",
           "image/gif": "gif", "image/webp": "webp"}.get(mime)
    if not ext:
        return None, "Images only (png, jpg, gif, webp)"
    path = f"{folder}/{secrets.token_hex(10)}.{ext}"
    try:
        _ensure_chat_bucket()
        supabase.storage.from_(_CHAT_BUCKET).upload(path, data, {"content-type": mime, "upsert": "true"})
        url = supabase.storage.from_(_CHAT_BUCKET).get_public_url(path)
    except Exception as ex:
        logging.warning("image store failed: %s", ex)
        return None, "Upload failed — create a public Storage bucket named 'chat' in Supabase."
    return url.rstrip("?"), None


@app.route("/chat/upload", methods=["POST"])
@limiter.limit("20/minute")
def chat_upload():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    f = request.files.get("file")
    if not f:
        return jsonify(success=False, error="No file"), 400
    url, err = _store_image(f, user["username"])
    if err:
        return jsonify(success=False, error=err), 400 if "Images only" in err or "Empty" in err or "too large" in err else 500
    return jsonify(success=True, url=url)


def _store_voice(f, folder):
    """Validate + upload a short voice clip to the public 'chat' bucket. Returns (url, error)."""
    data = f.read()
    if not data:
        return None, "Empty file"
    if len(data) > 6 * 1024 * 1024:
        return None, "Voice note too long (max ~6 MB)"
    mime = (f.mimetype or "").split(";")[0].lower()
    ext = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mpeg": "mp3",
           "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/wav": "wav",
           "audio/x-wav": "wav", "audio/webm;codecs=opus": "webm"}.get(mime)
    if not ext:
        fn = (f.filename or "").lower()
        for e in ("webm", "ogg", "mp3", "m4a", "wav"):
            if fn.endswith("." + e):
                ext = e
                break
    if not ext:
        return None, "Audio only (webm, ogg, mp3)"
    path = f"voice/{folder}/{secrets.token_hex(10)}.{ext}"
    try:
        _ensure_chat_bucket()
        supabase.storage.from_(_CHAT_BUCKET).upload(
            path, data, {"content-type": mime or ("audio/" + ext), "upsert": "true"})
        url = supabase.storage.from_(_CHAT_BUCKET).get_public_url(path)
    except Exception as ex:
        logging.warning("voice store failed: %s", ex)
        return None, "Upload failed — the public 'chat' Storage bucket may be missing."
    return url.rstrip("?"), None


@app.route("/voice/upload", methods=["POST"])
@limiter.limit("30/minute")
def voice_upload():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    f = request.files.get("file")
    if not f:
        return jsonify(success=False, error="No file"), 400
    url, err = _store_voice(f, user["username"])
    if err:
        soft = any(s in err for s in ("Audio only", "Empty", "too long"))
        return jsonify(success=False, error=err), 400 if soft else 500
    return jsonify(success=True, url=url)


@app.route("/avatar", methods=["POST"])
@limiter.limit("12/minute")
def set_avatar():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    f = request.files.get("file")
    if not f:
        return jsonify(success=False, error="No file"), 400
    url, err = _store_image(f, "avatars")
    if err:
        return jsonify(success=False, error=err), 400 if "Images only" in err or "Empty" in err or "too large" in err else 500
    try:
        supabase.table("cybucks").update({"avatar": url}).eq("username", user["username"]).execute()
    except Exception:
        return jsonify(success=False, error="Profile pictures aren't enabled yet — the database needs a quick update."), 503
    return jsonify(success=True, url=url)


@app.route("/avatar/remove", methods=["POST"])
@limiter.limit("12/minute")
def remove_avatar():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    try:
        supabase.table("cybucks").update({"avatar": None}).eq("username", user["username"]).execute()
    except Exception:
        pass
    return jsonify(success=True)


TENOR_KEY = os.environ.get("TENOR_API_KEY", "")   # optional; GIPHY is the no-Google-Cloud path


GIPHY_KEY = os.environ.get("GIPHY_API_KEY", "")


def _giphy_gifs(q):
    import requests
    endpoint = "search" if q else "trending"
    params = {"api_key": GIPHY_KEY, "limit": 24, "rating": "pg-13"}
    if q:
        params["q"] = q
    r = requests.get(f"https://api.giphy.com/v1/gifs/{endpoint}", params=params, timeout=6)
    gifs = []
    for it in r.json().get("data", []):
        im = it.get("images", {})
        full = (im.get("downsized_medium") or im.get("original") or {}).get("url")
        prev = (im.get("fixed_height_small") or im.get("fixed_width_small") or {}).get("url") or full
        if full:
            gifs.append({"url": full, "preview": prev})
    return gifs


def _tenor_gifs(q):
    import requests
    endpoint = "search" if q else "featured"
    params = {"key": TENOR_KEY, "client_key": "cyvathon", "limit": 24,
              "media_filter": "gif,tinygif", "contentfilter": "medium"}
    if q:
        params["q"] = q
    r = requests.get(f"https://tenor.googleapis.com/v2/{endpoint}", params=params, timeout=6)
    gifs = []
    for it in r.json().get("results", []):
        mf = it.get("media_formats", {})
        full = (mf.get("gif") or {}).get("url")
        prev = (mf.get("tinygif") or mf.get("nanogif") or {}).get("url") or full
        if full:
            gifs.append({"url": full, "preview": prev})
    return gifs


@app.route("/gif/search")
@limiter.limit("40/minute")
def gif_search():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    q = (request.args.get("q") or "").strip()
    # Prefer GIPHY (no Google Cloud needed) when its key is set; else Tenor.
    if not GIPHY_KEY and not TENOR_KEY:
        return jsonify(success=True, gifs=[], error="GIF search unavailable")
    try:
        gifs = _giphy_gifs(q) if GIPHY_KEY else _tenor_gifs(q)
        return jsonify(success=True, gifs=gifs)
    except Exception as ex:
        logging.warning("gif search failed: %s", ex)
        return jsonify(success=True, gifs=[], error="GIF search unavailable")


@app.route("/admin/purge_chat", methods=["POST"])
@limiter.limit("6/minute")
def admin_purge_chat():
    """President-only: wipe all chat messages for a clean slate (keeps groups/DMs structure)."""
    user = get_current_user(run_economics=False)
    if not user or not is_treasury_admin(user):
        return jsonify(success=False, error="President only"), 403
    supabase.table("messages").delete().gte("id", 0).execute()
    return jsonify(success=True)


@app.route("/admin/purge_bots", methods=["POST"])
@limiter.limit("4/minute")
def admin_purge_bots():
    """President-only: wipe all accounts whose username starts with `prefix`
    (default 'bot_'), plus every trace, and block the IPs they came from."""
    user = get_current_user(run_economics=False)
    if not user or not is_treasury_admin(user):
        return jsonify(success=False, error="President only"), 403
    prefix = (request.get_json().get("prefix") or "bot_").strip()
    if len(prefix) < 3:
        return jsonify(success=False, error="Prefix too short — refuse (would match real citizens)"), 400
    if any(a.startswith(prefix) for a in TREASURY_ADMINS):
        return jsonify(success=False, error="That prefix would match the President — refused"), 400

    pat = prefix + "%"
    ips = {r.get("reg_ip") for r in
           (supabase.table("cybucks").select("reg_ip").like("username", pat).execute().data or [])
           if r.get("reg_ip")}

    for tbl, col in [("messages", "sender"), ("messages", "recipient"),
                     ("records", "username"), ("notifications", "username"),
                     ("chat_group_members", "username"), ("holdings", "username"),
                     ("employment", "username"), ("market_items", "seller"),
                     ("loans", "username"), ("bonds", "username")]:
        try:
            supabase.table(tbl).delete().like(col, pat).execute()
        except Exception:
            pass
    supabase.table("cybucks").delete().like("username", pat).execute()

    for ip in ips:
        try:
            supabase.table("blocked_ips").upsert({"ip": ip, "reason": "bot registration"}).execute()
        except Exception:
            pass
    load_blocked_ips()
    return jsonify(success=True, ips_blocked=len(ips))


# ----- Citizens directory + direct messages -----
@app.route("/citizens/list")
def citizens_directory():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    try:
        rows = supabase.table("cybucks").select("username,designation,avatar,banned,account_type") \
            .order("username").execute().data or []
    except Exception:      # avatar / account_type column not migrated yet
        rows = supabase.table("cybucks").select("username,designation,banned") \
            .order("username").execute().data or []
    rows = [r for r in rows if not r.get("banned")]      # hide banned citizens
    companies = supabase.table("companies").select("name,founder").execute().data or []
    by_founder = {}
    for c in companies:
        by_founder.setdefault(c["founder"], c["name"])
    for r in rows:
        r["company"] = by_founder.get(r["username"])
        r.pop("banned", None)
    return jsonify(success=True, citizens=rows, me=user["username"])


# ---- National activity feed ------------------------------------------------
# Public-record events that make the homepage feel alive. We show human events
# (founded, hired, settled, sworn in, citizenship, elections…) and skip the
# noisy recurring money lines (VAT, salaries, taxes) so the feed reads well.
_FEED_SKIP = ("vat", "salary", "tax", "monthly", "interest", "upkeep", "rent to")

@app.route("/activity")
def activity_feed():
    try:
        rows = supabase.table("records").select("username,entry,created_at") \
            .order("id", desc=True).limit(80).execute().data or []
    except Exception:
        return jsonify(success=True, activity=[])
    out = []
    for r in rows:
        entry = (r.get("entry") or "")
        if any(s in entry.lower() for s in _FEED_SKIP):
            continue
        out.append({"username": r.get("username"), "entry": entry,
                    "created_at": r.get("created_at")})
        if len(out) >= 25:
            break
    return jsonify(success=True, activity=out)


# ---- Leaderboards ----------------------------------------------------------
@app.route("/leaderboard_data")
def leaderboard_data():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    try:
        cits = supabase.table("cybucks") \
            .select("username,balance,pufb,aquilines,cybits,banned,avatar,referred_by") \
            .execute().data or []
    except Exception:      # avatar / referred_by columns not migrated yet
        cits = supabase.table("cybucks") \
            .select("username,balance,pufb,aquilines,cybits,banned").execute().data or []
    avatar = {c["username"]: c.get("avatar") for c in cits}
    active = [c for c in cits if not c.get("banned")]

    def wealth(c):
        return round((c.get("balance") or 0)
                     + (c.get("pufb") or 0) * CYBUCK_VALUE["pufb"]
                     + (c.get("aquilines") or 0) * CYBUCK_VALUE["aquilines"]
                     + (c.get("cybits") or 0) * CYBUCK_VALUE["cybit"], 2)

    richest = sorted(active, key=lambda c: -wealth(c))[:10]
    richest = [{"username": c["username"], "avatar": avatar.get(c["username"]),
                "value": wealth(c)} for c in richest]

    # Top founders — companies started per citizen.
    try:
        comps = supabase.table("companies").select("founder").execute().data or []
    except Exception:
        comps = []
    fc = {}
    for c in comps:
        f = c.get("founder")
        if f:
            fc[f] = fc.get(f, 0) + 1
    founders = sorted(fc.items(), key=lambda x: -x[1])[:10]
    founders = [{"username": u, "avatar": avatar.get(u), "value": n} for u, n in founders]

    # Top recruiters — citizens brought in via referrals.
    rc = {}
    for c in cits:
        rb = c.get("referred_by")
        if rb:
            rc[rb] = rc.get(rb, 0) + 1
    recruiters = sorted(rc.items(), key=lambda x: -x[1])[:10]
    recruiters = [{"username": u, "avatar": avatar.get(u), "value": n} for u, n in recruiters]

    return jsonify(success=True, me=user["username"],
                   richest=richest, founders=founders, recruiters=recruiters)


# ============================================================
#  VIDEOS  — citizens share videos (by link) and comment
# ============================================================
def _avatars_for(names):
    names = list({n for n in names if n})
    if not names:
        return {}
    try:
        rows = supabase.table("cybucks").select("username,avatar") \
            .in_("username", names).execute().data or []
        return {r["username"]: r.get("avatar") for r in rows}
    except Exception:
        return {}


def _video_embed(url):
    """Turn a pasted URL into an embeddable source. Supports YouTube links and
    direct video files (.mp4/.webm/.ogg). Returns {kind, url} or None."""
    url = (url or "").strip()
    if not url or len(url) > 500:
        return None
    m = re.search(r'(?:youtube\.com/(?:watch\?v=|embed/|shorts/)|youtu\.be/)([A-Za-z0-9_-]{6,20})', url)
    if m:
        return {"kind": "youtube", "url": f"https://www.youtube.com/embed/{m.group(1)}"}
    if re.match(r'^https?://', url) and re.search(r'\.(mp4|webm|ogg)(\?|$)', url, re.I):
        return {"kind": "file", "url": url}
    return None


@app.route("/videos_list")
def videos_list():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    try:
        vids = supabase.table("videos").select("*").order("id", desc=True).limit(120).execute().data or []
    except Exception:
        return jsonify(success=True, videos=[], top=[], me=user["username"])
    # most-viewed (global) — fail-open if the 'views' column isn't migrated yet
    try:
        top = supabase.table("videos").select("*").order("views", desc=True).limit(3).execute().data or []
        top = [t for t in top if (t.get("views") or 0) > 0]
    except Exception:
        top = []
    everyone = [v.get("username") for v in vids] + [t.get("username") for t in top]
    av = _avatars_for(everyone)
    counts = {}
    try:
        vids_ids = [v.get("id") for v in vids]
        for c in (supabase.table("video_comments").select("video_id")
                  .in_("video_id", vids_ids).execute().data or []):
            counts[c["video_id"]] = counts.get(c["video_id"], 0) + 1
    except Exception:
        pass
    def _decorate(v):
        v["avatar"] = av.get(v.get("username"))
        v["comments"] = counts.get(v.get("id"), 0)
        v["views"] = v.get("views") or 0
        v["shares"] = v.get("shares") or 0
    for v in vids:
        _decorate(v)
    for t in top:
        _decorate(t)
    return jsonify(success=True, videos=vids, top=top, me=user["username"])


@app.route("/videos", methods=["POST"])
@limiter.limit("20/minute")
def videos_post():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    d = request.get_json() or {}
    title = (d.get("title") or "").strip()[:140]
    desc = (d.get("description") or "").strip()[:1000]
    emb = _video_embed(d.get("url"))
    if not title:
        return jsonify(success=False, error="Give your video a title."), 400
    if not emb:
        return jsonify(success=False, error="Paste a YouTube link or a direct .mp4/.webm video URL."), 400
    try:
        row = supabase.table("videos").insert({
            "username": user["username"], "title": title,
            "url": emb["url"], "kind": emb["kind"], "description": desc,
        }).execute().data[0]
    except Exception:
        return jsonify(success=False, error="Video board isn't set up yet — run the migration."), 500
    add_record(user["username"], f"Posted a video: {title}")
    return jsonify(success=True, video=row)


# ----- Direct video upload from the user's device -----
_VIDEO_BUCKET = "videos"
_video_bucket_ready = False
VIDEO_MAX = 48 * 1024 * 1024      # 48 MB — keep under MAX_CONTENT_LENGTH


def _ensure_video_bucket():
    global _video_bucket_ready
    if _video_bucket_ready:
        return
    try:
        supabase.storage.create_bucket(_VIDEO_BUCKET, options={"public": True})
    except Exception:
        pass          # already exists (or not permitted) — upload will report
    _video_bucket_ready = True


def _store_video(f, folder):
    """Validate + upload a short video to the public 'videos' bucket. Returns (url, error)."""
    data = f.read()
    if not data:
        return None, "Empty file"
    if len(data) > VIDEO_MAX:
        return None, "Video too large (max 48 MB). Trim the clip or paste a YouTube link instead."
    mime = (f.mimetype or "").lower()
    ext = {"video/mp4": "mp4", "video/webm": "webm",
           "video/ogg": "ogg", "application/ogg": "ogg"}.get(mime)
    if not ext:                                   # fall back to the filename extension
        fn = (f.filename or "").lower()
        for e in ("mp4", "webm", "ogg"):
            if fn.endswith("." + e):
                ext = e
                mime = mime or ("video/" + e)
                break
    if not ext:
        return None, "Videos only (mp4, webm, ogg)"
    path = f"{folder}/{secrets.token_hex(10)}.{ext}"
    try:
        _ensure_video_bucket()
        supabase.storage.from_(_VIDEO_BUCKET).upload(
            path, data, {"content-type": mime or ("video/" + ext), "upsert": "true"})
        url = supabase.storage.from_(_VIDEO_BUCKET).get_public_url(path)
    except Exception as ex:
        logging.warning("video store failed: %s", ex)
        return None, "Upload failed — create a public Storage bucket named 'videos' in Supabase."
    return url.rstrip("?"), None


@app.route("/video/upload", methods=["POST"])
@limiter.limit("8/minute")
def video_upload():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    title = (request.form.get("title") or "").strip()[:140]
    desc = (request.form.get("description") or "").strip()[:1000]
    if not title:
        return jsonify(success=False, error="Give your video a title."), 400
    f = request.files.get("file")
    if not f:
        return jsonify(success=False, error="No file"), 400
    url, err = _store_video(f, user["username"])
    if err:
        soft = any(s in err for s in ("Videos only", "Empty", "too large"))
        return jsonify(success=False, error=err), 400 if soft else 500
    try:
        row = supabase.table("videos").insert({
            "username": user["username"], "title": title,
            "url": url, "kind": "file", "description": desc}).execute().data[0]
    except Exception:
        return jsonify(success=False, error="Video board isn't set up yet — run the migration."), 500
    add_record(user["username"], f"Posted a video: {title}")
    return jsonify(success=True, video=row)


@app.route("/video/<int:vid>")
def video_get(vid):
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    v = supabase.table("videos").select("*").eq("id", vid).execute().data
    if not v:
        return jsonify(success=False, error="Video not found"), 404
    v = v[0]
    cmts = supabase.table("video_comments").select("*").eq("video_id", vid) \
        .order("id").limit(300).execute().data or []
    av = _avatars_for([v.get("username")] + [c.get("username") for c in cmts])
    v["avatar"] = av.get(v.get("username"))
    v["views"] = v.get("views") or 0
    v["shares"] = v.get("shares") or 0
    for c in cmts:
        c["avatar"] = av.get(c.get("username"))
    return jsonify(success=True, video=v, comments=cmts, me=user["username"])


@app.route("/video/<int:vid>/share", methods=["POST"])
@limiter.limit("30/minute")
def video_share(vid):
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    try:
        cur = supabase.table("videos").select("shares").eq("id", vid).execute().data
        if not cur:
            return jsonify(success=False, error="Video not found"), 404
        n = (cur[0].get("shares") or 0) + 1
        supabase.table("videos").update({"shares": n}).eq("id", vid).execute()
        return jsonify(success=True, shares=n)
    except Exception:
        return jsonify(success=True, shares=None)     # shares column not migrated — degrade quietly


@app.route("/video/<int:vid>/view", methods=["POST"])
@limiter.limit("60/minute")
def video_view(vid):
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    try:
        cur = supabase.table("videos").select("views").eq("id", vid).execute().data
        if not cur:
            return jsonify(success=False, error="Video not found"), 404
        n = (cur[0].get("views") or 0) + 1
        supabase.table("videos").update({"views": n}).eq("id", vid).execute()
        return jsonify(success=True, views=n)
    except Exception:
        return jsonify(success=True, views=None)     # views column not migrated — degrade quietly


@app.route("/video/<int:vid>/comment", methods=["POST"])
@limiter.limit("40/minute")
def video_comment(vid):
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    body = ((request.get_json() or {}).get("body") or "").strip()[:1000]
    if not body:
        return jsonify(success=False, error="Empty comment"), 400
    if not supabase.table("videos").select("id,username,title").eq("id", vid).execute().data:
        return jsonify(success=False, error="Video not found"), 404
    row = supabase.table("video_comments").insert(
        {"video_id": vid, "username": user["username"], "body": body}).execute().data[0]
    owner = supabase.table("videos").select("username,title").eq("id", vid).execute().data
    if owner and owner[0]["username"] != user["username"]:
        notify(owner[0]["username"], f"💬 {user['username']} commented on your video", f"/videos?v={vid}")
    return jsonify(success=True, comment=row)


# ============================================================
#  BLOGS  — citizens write posts, get likes & comments
# ============================================================
@app.route("/blogs_list")
def blogs_list():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    try:
        posts = supabase.table("blogs").select("*").order("id", desc=True).limit(50).execute().data or []
    except Exception:
        return jsonify(success=True, posts=[], me=user["username"])
    ids = [p["id"] for p in posts]
    likes, mine, comments = {}, set(), {}
    try:
        for l in (supabase.table("blog_likes").select("blog_id,username")
                  .in_("blog_id", ids).execute().data or []):
            likes[l["blog_id"]] = likes.get(l["blog_id"], 0) + 1
            if l["username"] == user["username"]:
                mine.add(l["blog_id"])
    except Exception:
        pass
    try:
        for c in (supabase.table("blog_comments").select("blog_id")
                  .in_("blog_id", ids).execute().data or []):
            comments[c["blog_id"]] = comments.get(c["blog_id"], 0) + 1
    except Exception:
        pass
    authors = list({p.get("username") for p in posts if p.get("username")})
    av = _avatars_for(authors)
    desig = {}
    try:
        for r in (supabase.table("cybucks").select("username,designation")
                  .in_("username", authors).execute().data or []):
            desig[r["username"]] = r.get("designation") or "Citizen"
    except Exception:
        pass
    for p in posts:
        p["avatar"] = av.get(p.get("username"))
        p["designation"] = desig.get(p.get("username"), "Citizen")
        p["likes"] = likes.get(p["id"], 0)
        p["liked"] = p["id"] in mine
        p["comments"] = comments.get(p["id"], 0)
        p["shares"] = p.get("shares") or 0
        body = p.get("body") or ""
        p["excerpt"] = body[:280] + ("…" if len(body) > 280 else "")
    return jsonify(success=True, posts=posts, me=user["username"])


@app.route("/blogs", methods=["POST"])
@limiter.limit("15/minute")
def blogs_post():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    d = request.get_json() or {}
    title = (d.get("title") or "").strip()[:160]
    body = (d.get("body") or "").strip()[:20000]
    if not title or not body:
        return jsonify(success=False, error="A blog needs a title and some body."), 400
    try:
        row = supabase.table("blogs").insert(
            {"username": user["username"], "title": title, "body": body}).execute().data[0]
    except Exception:
        return jsonify(success=False, error="Blog platform isn't set up yet — run the migration."), 500
    add_record(user["username"], f"Published a blog: {title}")
    return jsonify(success=True, post=row)


@app.route("/blog/<int:bid>")
def blog_get(bid):
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    p = supabase.table("blogs").select("*").eq("id", bid).execute().data
    if not p:
        return jsonify(success=False, error="Post not found"), 404
    p = p[0]
    cmts = supabase.table("blog_comments").select("*").eq("blog_id", bid) \
        .order("id").limit(300).execute().data or []
    likes = supabase.table("blog_likes").select("username").eq("blog_id", bid).execute().data or []
    av = _avatars_for([p.get("username")] + [c.get("username") for c in cmts])
    p["avatar"] = av.get(p.get("username"))
    p["shares"] = p.get("shares") or 0
    try:
        d = supabase.table("cybucks").select("designation").eq("username", p.get("username")).execute().data
        p["designation"] = (d[0].get("designation") if d else None) or "Citizen"
    except Exception:
        p["designation"] = "Citizen"
    for c in cmts:
        c["avatar"] = av.get(c.get("username"))
    return jsonify(success=True, post=p, comments=cmts, me=user["username"],
                   likes=len(likes), liked=any(l["username"] == user["username"] for l in likes))


@app.route("/blog/<int:bid>/share", methods=["POST"])
@limiter.limit("30/minute")
def blog_share(bid):
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    try:
        cur = supabase.table("blogs").select("shares").eq("id", bid).execute().data
        if not cur:
            return jsonify(success=False, error="Post not found"), 404
        n = (cur[0].get("shares") or 0) + 1
        supabase.table("blogs").update({"shares": n}).eq("id", bid).execute()
        return jsonify(success=True, shares=n)
    except Exception:
        return jsonify(success=True, shares=None)     # shares column not migrated — degrade quietly


@app.route("/blog/<int:bid>/like", methods=["POST"])
@limiter.limit("60/minute")
def blog_like(bid):
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    post = supabase.table("blogs").select("username,title").eq("id", bid).execute().data
    if not post:
        return jsonify(success=False, error="Post not found"), 404
    existing = supabase.table("blog_likes").select("id").eq("blog_id", bid).eq("username", me).execute().data
    if existing:
        supabase.table("blog_likes").delete().eq("blog_id", bid).eq("username", me).execute()
        liked = False
    else:
        supabase.table("blog_likes").insert({"blog_id": bid, "username": me}).execute()
        liked = True
        if post[0]["username"] != me:
            notify(post[0]["username"], f"❤️ {me} liked your blog", f"/blogs?b={bid}")
    total = supabase.table("blog_likes").select("id", count="exact").eq("blog_id", bid).execute().count or 0
    return jsonify(success=True, liked=liked, likes=total)


@app.route("/blog/<int:bid>/comment", methods=["POST"])
@limiter.limit("40/minute")
def blog_comment(bid):
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    body = ((request.get_json() or {}).get("body") or "").strip()[:2000]
    if not body:
        return jsonify(success=False, error="Empty comment"), 400
    post = supabase.table("blogs").select("username,title").eq("id", bid).execute().data
    if not post:
        return jsonify(success=False, error="Post not found"), 404
    row = supabase.table("blog_comments").insert(
        {"blog_id": bid, "username": user["username"], "body": body}).execute().data[0]
    if post[0]["username"] != user["username"]:
        notify(post[0]["username"], f"💬 {user['username']} commented on your blog", f"/blogs?b={bid}")
    return jsonify(success=True, comment=row)


@app.route("/dm/<username>", methods=["GET"])
@limiter.limit("90 per minute")
def dm_thread(username):
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    since_id = request.args.get("since_id", type=int)
    sq = supabase.table("messages").select("*").eq("sender", me).eq("recipient", username)
    rq = supabase.table("messages").select("*").eq("sender", username).eq("recipient", me)
    if since_id is not None:
        sq = sq.gt("id", since_id); rq = rq.gt("id", since_id)
    sent = sq.order("id").limit(200).execute().data or []
    recv = rq.order("id").limit(200).execute().data or []
    msgs = sorted(sent + recv, key=lambda m: m["id"])
    return jsonify(success=True, messages=msgs, me=me)


@app.route("/dm", methods=["POST"])
@limiter.limit("60 per minute")
def dm_send():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    d = request.get_json()
    to = (d.get("to") or "").strip()
    content = (d.get("content") or "").strip()
    if not content:
        return jsonify(success=False, error="Empty message"), 400
    if len(content) > 2000:
        return jsonify(success=False, error="Message too long (max 2000 characters)"), 400
    if to == user["username"]:
        return jsonify(success=False, error="You can't message yourself"), 400
    if not supabase.table("cybucks").select("id").eq("username", to).execute().data:
        return jsonify(success=False, error="No such citizen"), 404

    row = _insert_message({"sender": user["username"], "recipient": to, "content": content}, d)
    notify(to, f"💬 New message from {user['username']}", "/chat?dm=" + user["username"])
    _notify_mentions(user["username"], content, "/chat?dm=" + user["username"], exclude=to)
    return jsonify(success=True, message=row)


@app.route("/messages", methods=["GET"])
@limiter.limit("90 per minute")
def get_messages():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401

    since_id = request.args.get("since_id", type=int)
    query = supabase.table("messages").select("*").order("id", desc=False)
    if since_id is not None:
        query = query.gt("id", since_id)
    res = query.limit(50).execute()
    public = [m for m in res.data if m["recipient"] is None and not m.get("group_id")]
    return jsonify(success=True, messages=public)


# ----- Group chats -----
def _group_member(group_id, username):
    r = supabase.table("chat_group_members").select("id").eq("group_id", group_id) \
        .eq("username", username).execute().data
    return bool(r)


@app.route("/groups")
def groups_list():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    mine = supabase.table("chat_group_members").select("group_id") \
        .eq("username", user["username"]).execute().data or []
    ids = [m["group_id"] for m in mine]
    groups = []
    for gid in ids:
        g = supabase.table("chat_groups").select("*").eq("id", gid).execute().data
        if g:
            members = supabase.table("chat_group_members").select("username") \
                .eq("group_id", gid).execute().data or []
            groups.append({**g[0], "members": [m["username"] for m in members]})
    groups.sort(key=lambda x: x["id"])
    return jsonify(success=True, groups=groups, me=user["username"])


@app.route("/groups", methods=["POST"])
@limiter.limit("10/minute")
def groups_create():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    d = request.get_json()
    name = (d.get("name") or "").strip()
    members = [m.strip() for m in (d.get("members") or []) if m and m.strip()]
    if not name:
        return jsonify(success=False, error="Name the group"), 400
    g = supabase.table("chat_groups").insert({"name": name, "owner": user["username"]}).execute().data[0]
    roster = set(members) | {user["username"]}
    for m in roster:
        if supabase.table("cybucks").select("id").eq("username", m).execute().data:
            try:
                supabase.table("chat_group_members").insert({"group_id": g["id"], "username": m}).execute()
            except Exception:
                pass
            if m != user["username"]:
                notify(m, f"You were added to the group '{name}'.", "/chat")
    return jsonify(success=True, group=g)


@app.route("/groups/add", methods=["POST"])
@limiter.limit("20/minute")
def groups_add():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    d = request.get_json()
    gid, username = d.get("group_id"), (d.get("username") or "").strip()
    g = supabase.table("chat_groups").select("*").eq("id", gid).execute().data
    if not g:
        return jsonify(success=False, error="Group not found"), 404
    if not _group_member(gid, user["username"]):
        return jsonify(success=False, error="You're not in this group"), 403
    if not supabase.table("cybucks").select("id").eq("username", username).execute().data:
        return jsonify(success=False, error="No such citizen"), 404
    try:
        supabase.table("chat_group_members").insert({"group_id": gid, "username": username}).execute()
        notify(username, f"You were added to the group '{g[0]['name']}'.", "/chat")
    except Exception:
        pass
    return jsonify(success=True)


@app.route("/groups/leave", methods=["POST"])
@limiter.limit("20/minute")
def groups_leave():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    gid = request.get_json().get("group_id")
    supabase.table("chat_group_members").delete().eq("group_id", gid) \
        .eq("username", user["username"]).execute()
    return jsonify(success=True)


@app.route("/groups/<int:gid>/messages", methods=["GET"])
@limiter.limit("90 per minute")
def group_messages(gid):
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    if not _group_member(gid, user["username"]):
        return jsonify(success=False, error="You're not in this group"), 403
    since_id = request.args.get("since_id", type=int)
    q = supabase.table("messages").select("*").eq("group_id", gid).order("id")
    if since_id is not None:
        q = q.gt("id", since_id)
    res = q.limit(80).execute().data or []
    return jsonify(success=True, messages=res, me=user["username"])


@app.route("/groups/<int:gid>/messages", methods=["POST"])
@limiter.limit("60 per minute")
def group_send(gid):
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    if not _group_member(gid, user["username"]):
        return jsonify(success=False, error="You're not in this group"), 403
    content = (request.get_json().get("content") or "").strip()
    if not content:
        return jsonify(success=False, error="Empty message"), 400
    if len(content) > 2000:
        return jsonify(success=False, error="Message too long (max 2000 characters)"), 400
    row = _insert_message({"sender": user["username"], "recipient": None,
                           "group_id": gid, "content": content}, request.get_json())
    members = {m["username"] for m in (supabase.table("chat_group_members")
               .select("username").eq("group_id", gid).execute().data or [])}
    _notify_mentions(user["username"], content, f"/chat?group={gid}", only=members)
    # @everyone pings the whole group — moderators only (creator or President)
    if _EVERYONE_RE.search(content):
        grp = supabase.table("chat_groups").select("*").eq("id", gid).execute().data
        if _is_group_mod(user, grp[0] if grp else None):
            _notify_everyone(user["username"], f"/chat?group={gid}", members)
    return jsonify(success=True, message=row)


# ----- Message reactions -----
REACT_EMOJIS = {"👍", "❤️", "😂", "🔥", "🎉", "😮", "😢", "👀", "💯", "🙏"}


def _reactions_for(ids, me):
    if not ids:
        return {}
    try:
        rows = supabase.table("message_reactions").select("message_id,username,emoji") \
            .in_("message_id", ids).execute().data or []
    except Exception:
        return {}
    agg = {}
    for r in rows:
        mid, e = r["message_id"], r["emoji"]
        d = agg.setdefault(mid, {}).setdefault(e, {"emoji": e, "count": 0, "mine": False})
        d["count"] += 1
        if r["username"] == me:
            d["mine"] = True
    return {mid: list(v.values()) for mid, v in agg.items()}


@app.route("/messages/<int:mid>/react", methods=["POST"])
@limiter.limit("120/minute")
def message_react(mid):
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    emoji = (request.get_json() or {}).get("emoji", "")
    if emoji not in REACT_EMOJIS:
        return jsonify(success=False, error="Unknown reaction"), 400
    me = user["username"]
    try:
        existing = supabase.table("message_reactions").select("id") \
            .eq("message_id", mid).eq("username", me).eq("emoji", emoji).execute().data
        if existing:
            supabase.table("message_reactions").delete().eq("id", existing[0]["id"]).execute()
        else:
            supabase.table("message_reactions").insert(
                {"message_id": mid, "username": me, "emoji": emoji}).execute()
    except Exception:
        return jsonify(success=False, error="Reactions aren't enabled yet — the database needs a quick update."), 503
    return jsonify(success=True, reactions=_reactions_for([mid], me).get(mid, []))


@app.route("/reactions")
@limiter.limit("240/minute")
def reactions_get():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    idlist = [int(x) for x in (request.args.get("ids") or "").split(",")
              if x.strip().isdigit()][:100]
    return jsonify(success=True, reactions=_reactions_for(idlist, user["username"]))


# ============================================================
#  AI ASSISTANT
# ============================================================
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

CYVATHON_GUIDE = """You are Cyvathon AI, the friendly in-app guide for Cyvathon — a cyber micronation web app. Your job is to help citizens understand the website and the nation. Be warm, concise and practical, and point people to the right page (e.g. "head to the Bank at /bank"). Only discuss Cyvathon; if asked something unrelated, gently steer back. Never invent features that don't exist. Use short paragraphs or bullet points.

ABOUT CYVATHON — a digital micronation with a live economy and an elected government.

CURRENCIES: The Cybuck (CB) is the main currency. Pegs: 1 CB = 1 Pufferbuck (PUFB) = 10 Aquilines (AQ) = 50 Cybits (CBT). Cybits are the small "change" of a Cybuck — fractional Cybucks are automatically kept as Cybits so Cybuck balances stay whole. New citizens receive 100 of each currency.

MONEY — the Bank (/bank): send money to other citizens, convert between the four currencies, a Savings account (5% monthly interest), Government Bonds (+10% after 30 days), and Loans up to 5000 CB (/loans). A 10% VAT is collected monthly into the Treasury, plus a 5% Cyvazon delivery levy on top while the delivery service is running — that levy is what keeps delivery free for everyone. Borrowed money and your welcome grant can't be transferred away — only money you've earned. All these rates are set by the President and can change.

DEBIT CARD (/card): every citizen has a printable Cyvathon debit card with their own card number, a barcode and a QR code. Print it, and another citizen can scan it at /pay to send you money without typing your username. Tap the card on screen to turn it over.

BUSINESS: Found a company for 1000 CB (/company); take it public (IPO) and trade its shares on the Stock Exchange (/exchange); pay dividends to shareholders. The Jobs Board (/jobs) lets you apply to any company for a salaried role. Marketplaces: the national Import & Export hub (/marketplace) and per-state local markets.

STATES (/states): five states — Neonhaven, Cryptvale, Silica Plains, Portus Mare, and Aetheris (the national capital). Each has its own marketplace. To enter a state you pass Border Control (you need a Passport; the capital Aetheris also requires the Oath of Allegiance). "Settle" in a state to become a resident and join its chat channel. The President resides in the capital.

IDENTITY: your ID Card (/profile) shows your record and balances; set a profile picture and your interests there. Issue a Passport and swear the Oath of Allegiance (/passport) to become a sworn citizen and collect visa stamps by clearing state borders. Swearing the Oath makes citizenship binding and irrevocable — it can only be given up by a written request on paper to the President.

INTERESTS: when you sign up (and on your ID Card) you pick what you love to do. Cyvathon then shows personalized "Recommended for you" features on your dashboard and drops you into interest-based group chats with like-minded citizens.

GOVERNMENT: a President leads the nation; the Prime Minister and Judge are elected by vote (/voting), and a national presidential vote is held once every six years. The Legislature (/legislature) is where citizens table and vote on bills; the Gazette (/gazette) records laws and decrees; the National Court (/court) rules on cases; report a crime with an FIR (/fir); Ministries (/ministries) run departments with budgets; the Treasury (/treasury) holds national funds. Foreign Affairs (/foreign) tracks Cyvathon's allied and rival micronations — fellow nations can register at signup and request an alliance, which the President confirms.

COMMUNITY: Chat (/chat) is a full messenger — public square, group channels, per-state channels and DMs, with @mentions, replies, emoji reactions, typing indicators, online status, GIFs, image sharing and voice messages. Mail (/mail) — reached from the Chat/Mail toggle — is a Gmail-style inbox where you compose to recipients you pick from a list (or broadcast to everyone), with threaded replies. Share Videos (/videos) by YouTube link or uploading from your device, and write Blogs (/blogs) that others can like and comment on. Climb the Leaderboards (/leaderboard) — richest citizens, top founders, top recruiters. Track your cash and investments in the Portfolio (/portfolio). Browse the Citizens directory (/citizens) and National News (/news). You'll get notification pop-ups for DMs, mentions, mail, approvals, news and opened elections.

CASINO (/casino): bet Cybucks against the House (the Treasury) — Coin Flip, Lucky Number dice, and Slots. There is also a multiplayer Bluff card game.

CARD PACKETS (/packet): trade real Match Attax football cards with other citizens. Search any footballer in an online football database — their photo, club, position and nationality are pulled in automatically — then record the card you actually pulled: its subset (Base, Captain, 100 Club, Hall of Fame, Jersey Relic and so on) and its finish (Blue Crystal, Black Edge 1:30 packets, Gold Edge 1:35, Goldrush /100, Gold Rainbow 1/1 and more). You can photograph your own copy and that becomes the card face. Tap any card to open it full size and flip it over for the stats. Browse other citizens' packets, wishlist cards you're missing (the owner is notified), mark your own spares "for trade", then offer cards from your packet for theirs. When a trade is accepted the cards swap over and Cyvazon raises a parcel each way so the physical cards actually change hands.

CYVAZON (/cyvazon) — the national delivery service. Delivery is FREE for whoever requests it, anywhere in school: say which class it's collected from and which class it's going to, and a courier runs it. Buying on the marketplace or accepting a card trade raises a parcel automatically, so nobody can take the money or the card and quietly keep the goods. Set your usual class once on the Cyvazon page and deliveries to you are addressed there automatically. Becoming a courier: apply on the Cyvazon page, and the PRESIDENT reviews and approves every applicant before they can carry other citizens' property. Approved couriers earn 500 CB per pay period on top of their normal salary — more than most jobs in Cyvathon — and are paid from the Treasury out of the delivery levy. Only the person RECEIVING a parcel can confirm it arrived; a courier cannot close their own run. Couriers can't carry their own parcels, and can't stand down while still holding one.

JUSTICE: the Court (/court) can fine a citizen and jail them. A jailed citizen can only reach the jail page (/jail) until their sentence is served. Convictions are recorded on a criminal record and bar a citizen from standing for office. Ministry seats are filled by application (/ministries) — once enough eligible citizens apply, an election opens automatically.

GROW CYVATHON: invite friends via /invite — you earn 500 CB for every friend who joins on your link and gets approved by the President. New signups are reviewed and approved by the President before they can log in, to keep bad actors out.
"""


def _ai_context(user):
    if not user:
        return "\nThe person asking is a guest (not logged in)."
    return (f"\nYou are helping the citizen {user['username']} "
            f"(role: {user.get('designation', 'Citizen')}). Their balances: "
            f"{user.get('balance', 0)} CB, {user.get('pufb', 0)} PUFB, "
            f"{user.get('aquilines', 0)} AQ, {user.get('cybits', 0)} Cybits. "
            f"Address them by name when natural.")


def _groq_reply(messages):
    import requests
    r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                      headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                      json={"model": GROQ_MODEL, "temperature": 0.4, "max_tokens": 700,
                            "messages": messages}, timeout=30)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


@app.route("/ai_ask", methods=["POST"])
@limiter.limit("20/minute")
def ai_ask():
    try:
        data = request.get_json(force=True) or {}
        message = (data.get("message") or "").strip()
        if not message:
            return jsonify(reply="Please enter a message."), 200
        user = get_current_user(run_economics=False)
        system = CYVATHON_GUIDE + _ai_context(user)

        if GROQ_API_KEY:
            msgs = [{"role": "system", "content": system}]
            for h in (data.get("history") or [])[-6:]:      # recent conversation turns
                role = "assistant" if h.get("role") == "assistant" else "user"
                msgs.append({"role": role, "content": str(h.get("content", ""))[:1500]})
            msgs.append({"role": "user", "content": message[:2000]})
            try:
                return jsonify(reply=_groq_reply(msgs)), 200
            except Exception as e:
                logging.warning("Groq failed, falling back: %s", e)

        if genai_client is not None:
            resp = genai_client.models.generate_content(
                model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
                contents=system + "\n\nCitizen's question: " + message)
            return jsonify(reply=(resp.text or "No response generated.")), 200

        return jsonify(reply="Cyvathon AI is offline — an admin needs to set a GROQ_API_KEY."), 200
    except Exception as e:
        logging.exception("Cyvathon AI error: %s", e)
        return jsonify(reply="Cyvathon AI backend error. Check server logs."), 500


@app.route("/health")
def health():
    return "Backend secure, vro!"



# ============================================================
#  CYVATHON DEBIT CARDS
#  A printable card carrying the citizen's card number as a QR
#  (scanned to pay them) plus a Code128 stripe. The number is derived
#  from the account id the same way passport numbers are, so nothing
#  new has to be stored — and a lost card is reissued by reprinting.
#
#  SECURITY NOTE: a printed barcode is public. The card number is an
#  IDENTIFIER, never a credential: scanning it can only send money TO
#  the holder, never take money from them. Do not add a "charge this
#  card" endpoint without also requiring the holder to approve.
# ============================================================
CARD_BIN = "492100"          # Cyvathon issuer prefix, like a real card's BIN


def _luhn_check_digit(digits):
    """Standard Luhn check digit, so a mistyped card number is rejected."""
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:                 # doubled from the right of the payload
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


def _card_number(user):
    """16 digits: 6-digit issuer prefix + 9-digit account id + Luhn check."""
    body = CARD_BIN + f"{int(user.get('id') or 0):09d}"
    return body + _luhn_check_digit(body)


def _card_pretty(num):
    return " ".join(num[i:i + 4] for i in range(0, len(num), 4))


def _card_account_id(cardno):
    """Reverse a card number to an account id, or None if it isn't valid."""
    digits = re.sub(r"\D", "", cardno or "")
    if len(digits) != 16 or not digits.startswith(CARD_BIN):
        return None
    if _luhn_check_digit(digits[:15]) != digits[15]:
        return None
    try:
        return int(digits[6:15])
    except ValueError:
        return None


def _card_holder(cardno):
    """Look up the citizen a card belongs to. Returns None when unknown."""
    aid = _card_account_id(cardno)
    if aid is None:
        return None
    try:
        rows = supabase.table("cybucks").select("*").eq("id", aid).execute().data
    except Exception:
        return None
    return rows[0] if rows else None


# ---- Code128-B/C ------------------------------------------------------
# Verified bit-for-bit against the python-barcode reference implementation.
# 106 symbols of 11 modules each, concatenated.
_C128 = (
    "1101100110011001101100110011001101001001100010010001100100010011001001100100010011000100"
    "1000110010011001001000110010001001100010010010110011100100110111001001100111010111001100"
    "1001110110010011100110110011100101100101110011001001110110111001001100111010011101101110"
    "1110100110011100101100111001001101110110010011100110100111001100101101101100011011000110"
    "1100011011010100011000100010110001000100011010110001000100011010001000110001011010001000"
    "1100010100011000100010101101110001011000111010001101110101110110001011100011010001110110"
    "1110111011011010001110110001011101101110100011011100010110111011101110101100011101000110"
    "1110001011011101101000111011000101110001101011101111010110010000101111000101010100110000"
    "1010000110010010110000100100001101000010110010000100110101100100001011000010010011010000"
    "1001100001010000110100100001100101100001001011001010000111101110101100001010010001111010"
    "1010011110010010111100100100111101011110010010011110100100111100101111010010011110010100"
    "1111001001011011011110110111101101111011011010101111000101000111101000101111010111101000"
    "1011110001011110101000111101000101011101111010111101110111010111101111010111011010000100"
    "1101001000011010011100"
)
_C128_STOP = "1100011101011"


def _c128_bits(digits):
    """Code128-C bit pattern for an even-length digit string."""
    vals = [105] + [int(digits[i:i + 2]) for i in range(0, len(digits), 2)]
    chk = (vals[0] + sum(i * v for i, v in enumerate(vals[1:], 1))) % 103
    vals.append(chk)
    return "".join(_C128[v * 11:(v + 1) * 11] for v in vals) + _C128_STOP


def _barcode_svg(digits, height=70):
    """Render the Code128 stripe as an SVG of 1-module-wide bars."""
    bits = _c128_bits(digits)
    bars, run = [], 0
    for i, b in enumerate(bits):
        if b == "1":
            run += 1
        elif run:
            bars.append((i - run, run)); run = 0
    if run:
        bars.append((len(bits) - run, run))
    rects = "".join(f'<rect x="{x}" y="0" width="{w}" height="{height}"/>'
                    for x, w in bars)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {len(bits)} {height}" '
            f'preserveAspectRatio="none" shape-rendering="crispEdges">'
            f'<g fill="#0d1117">{rects}</g></svg>')


def _pay_url(cardno):
    return request.url_root.rstrip("/") + "/pay?c=" + cardno


# ---- routes -----------------------------------------------------------
@app.route("/card")
def card_page():
    return app.send_static_file("card.html")


@app.route("/pay")
def pay_page():
    return app.send_static_file("pay.html")


@app.route("/card_data")
@limiter.limit("30/minute")
def card_data():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    num = _card_number(user)
    return jsonify(success=True, card={
        "number": num,
        "pretty": _card_pretty(num),
        "holder": user["username"],
        "designation": user.get("designation") or "Citizen",
        "since": user.get("created_at"),
        "pay_url": _pay_url(num),
    })


@app.route("/card/barcode/<cardno>.svg")
@limiter.limit("60/minute")
def card_barcode(cardno):
    digits = re.sub(r"\D", "", cardno or "")
    if len(digits) != 16:
        return jsonify(success=False, error="Bad card number"), 400
    resp = app.response_class(_barcode_svg(digits), mimetype="image/svg+xml")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.route("/card/qr/<cardno>.svg")
@limiter.limit("60/minute")
def card_qr(cardno):
    digits = re.sub(r"\D", "", cardno or "")
    if len(digits) != 16:
        return jsonify(success=False, error="Bad card number"), 400
    buf = io.BytesIO()
    segno.make(_pay_url(digits), error="m").save(
        buf, kind="svg", scale=1, dark="#0d1117", light=None,
        svgclass=None, lineclass=None, omitsize=True, xmldecl=False)
    resp = app.response_class(buf.getvalue(), mimetype="image/svg+xml")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.route("/card/resolve")
@limiter.limit("40/minute")
def card_resolve():
    """Card number -> who to pay. Public on purpose: the whole point of the
    QR is that someone else can scan it and send the holder money."""
    holder = _card_holder(request.args.get("c", ""))
    if not holder:
        return jsonify(success=False, error="That card isn't recognised."), 404
    if holder.get("banned"):
        return jsonify(success=False, error="That account is banned."), 403
    return jsonify(success=True, holder={
        "username": holder["username"],
        "designation": holder.get("designation") or "Citizen",
        "avatar": holder.get("avatar"),
        "company": holder.get("company"),
    })


@app.route("/card/sheet")
def card_sheet_page():
    return app.send_static_file("cardsheet.html")


@app.route("/card/all")
@limiter.limit("6/minute")
def card_all():
    """Every citizen's card, for the President to print and hand out."""
    user = get_current_user(run_economics=False)
    if not user or not is_treasury_admin(user):
        return jsonify(success=False, error="President only"), 403
    rows = supabase.table("cybucks").select(
        "id,username,designation,created_at,banned").order("id").execute().data or []
    cards = []
    for r in rows:
        if r.get("banned"):
            continue
        num = _card_number(r)
        cards.append({"number": num, "pretty": _card_pretty(num),
                      "holder": r["username"],
                      "designation": r.get("designation") or "Citizen",
                      "since": r.get("created_at")})
    return jsonify(success=True, cards=cards, count=len(cards))


# ============================================================
#  FOOTBALL CARD TRADING  ("Packets")
# ============================================================
#  Not to be confused with the debit card above (/card): a "packet" is a
#  citizen's Match Attax collection. Cards are looked up in an online
#  football database so every entry carries a real player photo, club and
#  nationality; the citizen then picks which Match Attax edition they hold.
#
#  Flow: browse someone's packet -> wishlist the cards you want -> offer
#  cards out of your own packet for them -> they accept and the cards swap.

# A real Match Attax card is two things at once: which SUBSET it belongs to
# (a plain base card, a Captain, a 100 Club…) and which FINISH it was pulled in
# (plain, Blue Crystal, Black Edge…). Both are printed on the card, and
# collectors name a card using both — "Saka, Captain, Black Edge" — so the
# packet stores them separately instead of mashing them into one label.
#
# Names and pull rates below follow the published Match Attax 2024/25 checklist.
# There is no public Match Attax API to read this from (see _search_players),
# so it is kept here as reference data and reviewed when a new season ships.

CARD_SUBSETS = [
    {"key": "base",         "label": "Base",                        "tier": 0},
    {"key": "attax_debut",  "label": "Attax Debut",                 "tier": 1},
    {"key": "captain",      "label": "Captain",                     "tier": 2},
    {"key": "team_badge",   "label": "Team Badge",                  "tier": 2},
    {"key": "new_signing",  "label": "New Signing",                 "tier": 2},
    {"key": "star_ballers", "label": "Star Ballers",                "tier": 3},
    {"key": "scream_team",  "label": "Scream Team",                 "tier": 3},
    {"key": "snow_ballers", "label": "Snow Ballers",                "tier": 3},
    {"key": "queens",       "label": "Queens of Europe",            "tier": 3},
    {"key": "squadzone",    "label": "Festive Edition Squadzone",   "tier": 3},
    {"key": "celebration",  "label": "Classic Celebration",         "tier": 3},
    {"key": "heritage",     "label": "Topps Heritage",              "tier": 4},
    {"key": "generation",   "label": "Generation Now",              "tier": 4},
    {"key": "motm_sig",     "label": "Man of the Match Signature Style", "tier": 4},
    {"key": "vintage",      "label": "Vintage Vibes Legend",        "tier": 5},
    {"key": "trophy",       "label": "Trophy Triumph",              "tier": 5},
    {"key": "pro_elite",    "label": "Chrome Pro Elite Shield",     "tier": 6},
    {"key": "hundred",      "label": "100 Club",                    "tier": 7},
    {"key": "hall_of_fame", "label": "Match Attax Hall of Fame",    "tier": 8},
    {"key": "jersey_relic", "label": "Jersey Relic",                "tier": 9},
    {"key": "memento",      "label": "UCL Champion Memento Relic",  "tier": 9},
    {"key": "autograph",    "label": "Genuine Autograph",           "tier": 10},
    {"key": "auto_combo",   "label": "Genuine Autograph Combo",     "tier": 11},
    {"key": "ultimate",     "label": "Ultimate Talent Autograph",   "tier": 12},
]
SUBSET_BY_KEY = {s["key"]: s for s in CARD_SUBSETS}

# `rarity` is what's actually printed/quoted for the pull — shown on the card so
# a trade is negotiated on real scarcity rather than vibes.
CARD_EDITIONS = [
    {"key": "base",         "label": "Base",            "color": "#8aa0b4", "tier": 0,  "rarity": ""},
    {"key": "rainbow_foil", "label": "Rainbow Foil",    "color": "#a78bfa", "tier": 1,  "rarity": "2 per packet"},
    {"key": "blue_crystal", "label": "Blue Crystal",    "color": "#58c4ff", "tier": 2,  "rarity": "1:4 packets"},
    {"key": "black_edge",   "label": "Black Edge",      "color": "#20242e", "tier": 3,  "rarity": "1:30 packets"},
    {"key": "platinum",     "label": "Platinum Pull",   "color": "#d6dde8", "tier": 4,  "rarity": "1:30 packets"},
    {"key": "gold_edge",    "label": "Gold Edge",       "color": "#e8b400", "tier": 5,  "rarity": "1:35 packets"},
    {"key": "starburst",    "label": "Starburst",       "color": "#ff7a29", "tier": 6,  "rarity": "Match of the Day exclusive"},
    {"key": "blue_diamond", "label": "Blue Diamond",    "color": "#22d3ee", "tier": 7,  "rarity": "Bundle exclusive"},
    {"key": "goldrush",     "label": "Goldrush",        "color": "#ffd700", "tier": 8,  "rarity": "/100"},
    {"key": "refractor",    "label": "Refractor",       "color": "#7c5cff", "tier": 9,  "rarity": "/99"},
    {"key": "orange",       "label": "Orange",          "color": "#ff8a3d", "tier": 10, "rarity": "/25"},
    {"key": "mirrors",      "label": "Mirrors",         "color": "#c0c8d4", "tier": 11, "rarity": "/25"},
    {"key": "rainbow_num",  "label": "Rainbow",         "color": "#ff3b6b", "tier": 12, "rarity": "/10"},
    {"key": "gold_rainbow", "label": "Gold Rainbow",    "color": "#fff1a8", "tier": 13, "rarity": "1/1"},
]
EDITION_BY_KEY = {e["key"]: e for e in CARD_EDITIONS}

# The releases a card can come from. Free text is still accepted for anything
# older or more obscure than this list.
CARD_SERIES = [
    "Match Attax 2025/26",
    "Match Attax Extra 2024/25",
    "Match Attax 2024/25",
    "Match Attax UEFA Euro 2024",
    "Match Attax Extra 2023/24",
    "Match Attax 2023/24",
    "Match Attax 2022/23",
    "Match Attax 2021/22",
    "Match Attax 2020/21",
    "Topps Premier League 2025/26",
]

# Free, key-less football database used to look players up. Falls back to a
# clear "search unavailable" rather than an error if it's ever unreachable.
SPORTSDB_KEY  = os.environ.get("SPORTSDB_API_KEY", "3").strip() or "3"
SPORTSDB_BASE = "https://www.thesportsdb.com/api/v1/json"

MAX_PACKET_CARDS = 500          # a packet is a collection, not a firehose
MAX_TRADE_CARDS  = 12           # cards on either side of a single offer

_player_cache = {}              # lowercased query -> (expires_ts, [players])
PLAYER_CACHE_TTL = 900


def _card_url_ok(url):
    """Only allow a direct https image link — the same rule profile banners use."""
    return bool(re.match(r"^https://[A-Za-z0-9._~:/?#@!$&*+,;=%-]+$", url or ""))


def _search_players(q):
    """Look a footballer up in the online database. Raises on transport failure."""
    key = q.lower()
    hit = _player_cache.get(key)
    if hit and hit[0] > time():
        return hit[1]
    import requests
    r = requests.get(f"{SPORTSDB_BASE}/{SPORTSDB_KEY}/searchplayers.php",
                     params={"p": q}, timeout=8)
    out = []
    for p in (r.json().get("player") or []):
        # The database covers every sport; a Match Attax packet is football only.
        if (p.get("strSport") or "").lower() not in ("soccer", "football"):
            continue
        photo = p.get("strCutout") or p.get("strThumb") or p.get("strRender") or ""
        out.append({
            "player_id":   p.get("idPlayer") or "",
            "player_name": (p.get("strPlayer") or "").strip(),
            "team":        (p.get("strTeam") or "").strip(),
            "nationality": (p.get("strNationality") or "").strip(),
            "position":    (p.get("strPosition") or "").strip(),
            "image_url":   photo if _card_url_ok(photo) else "",
        })
        if len(out) >= 24:
            break
    now = time()
    _player_cache[key] = (now + PLAYER_CACHE_TTL, out)
    for k in [k for k, v in _player_cache.items() if v[0] <= now]:
        _player_cache.pop(k, None)
    return out


def _card_public(row, extra=None):
    """Shape a packet row for the frontend, resolving its subset and finish."""
    ed  = EDITION_BY_KEY.get(row.get("edition") or "base", CARD_EDITIONS[0])
    sub = SUBSET_BY_KEY.get(row.get("subset") or "base", CARD_SUBSETS[0])
    out = {
        "id":          row.get("id"),
        "owner":       row.get("owner"),
        "player_name": row.get("player_name") or "",
        "team":        row.get("team") or "",
        "nationality": row.get("nationality") or "",
        "position":    row.get("position") or "",
        "image_url":   row.get("image_url") or "",
        "card_image":  row.get("card_image") or "",
        "card_url":    row.get("card_url") or "",
        "edition":     ed["key"],
        "edition_label": ed["label"],
        "edition_color": ed["color"],
        "rarity":      ed["rarity"],
        "subset":      sub["key"],
        "subset_label": sub["label"],
        # How special the card is overall — both halves count, so a Hall of Fame
        # base sits above a plain Rainbow Foil when the packet is sorted.
        "tier":        ed["tier"] + sub["tier"],
        "card_no":     row.get("card_no") or "",
        "series":      row.get("series") or "",
        "rating":      row.get("rating"),
        "quantity":    row.get("quantity") or 1,
        "for_trade":   bool(row.get("for_trade")),
        "note":        row.get("note") or "",
        "created_at":  row.get("created_at"),
    }
    if extra:
        out.update(extra)
    return out


def _packet_missing():
    return jsonify(success=False,
                   error="Card trading isn't enabled yet — the database needs a quick update "
                         "(run migration_card_trading.sql)."), 503


def _cards_by_ids(ids):
    """Fetch packet rows for a list of ids, keyed by id. Empty list -> {}."""
    ids = [i for i in ids if i]
    if not ids:
        return {}
    rows = supabase.table("card_packet").select("*").in_("id", ids).execute().data or []
    return {r["id"]: r for r in rows}


def _parse_ids(val, cap=MAX_TRADE_CARDS):
    """Accept a list (or comma-separated string) of card ids -> [int], deduped."""
    if isinstance(val, str):
        seq = [p for p in val.split(",") if p.strip()]
    elif isinstance(val, list):
        seq = val
    else:
        return []
    out = []
    for v in seq:
        try:
            i = int(v)
        except (TypeError, ValueError):
            continue
        if i > 0 and i not in out:
            out.append(i)
    return out[:cap]


def _card_label(card):
    """"Saka, Captain, Black Edge" — how a collector would actually name it."""
    ed  = EDITION_BY_KEY.get(card.get("edition") or "base", CARD_EDITIONS[0])
    sub = SUBSET_BY_KEY.get(card.get("subset") or "base", CARD_SUBSETS[0])
    bits = [card.get("player_name") or "Unknown"]
    if sub["key"] != "base":
        bits.append(sub["label"])
    if ed["key"] != "base":
        bits.append(ed["label"])
    return bits[0] if len(bits) == 1 else f"{bits[0]} ({', '.join(bits[1:])})"


def _move_card(card, to_user):
    """Give one copy of a card to another citizen. A stack of duplicates hands
    over a single copy and keeps the rest; a lone card moves whole. Merges into
    an identical card the receiver already holds."""
    qty = card.get("quantity") or 1
    same = supabase.table("card_packet").select("id,quantity").eq("owner", to_user) \
        .eq("player_name", card.get("player_name")) \
        .eq("subset", card.get("subset") or "base") \
        .eq("edition", card.get("edition") or "base").execute().data or []
    if same:
        cas_num("card_packet", [("id", same[0]["id"])], "quantity", 1, places=0)
    else:
        supabase.table("card_packet").insert({
            "owner": to_user,
            "player_id": card.get("player_id"), "player_name": card.get("player_name"),
            "team": card.get("team"), "nationality": card.get("nationality"),
            "position": card.get("position"), "image_url": card.get("image_url"),
            "card_image": card.get("card_image"), "card_url": card.get("card_url"),
            "edition": card.get("edition") or "base", "subset": card.get("subset") or "base",
            "series": card.get("series"), "card_no": card.get("card_no"),
            "rating": card.get("rating"), "quantity": 1, "for_trade": False,
        }).execute()
    if qty > 1:
        supabase.table("card_packet").update({"quantity": qty - 1}).eq("id", card["id"]).execute()
    else:
        supabase.table("card_wishlist").delete().eq("card_id", card["id"]).execute()
        supabase.table("card_packet").delete().eq("id", card["id"]).execute()


# ---- routes -----------------------------------------------------------
@app.route("/packet")
def packet_page():
    return app.send_static_file("packet.html")


@app.route("/packet/editions")
def packet_editions():
    """The Match Attax reference data the add-a-card form is built from."""
    return jsonify(success=True, editions=CARD_EDITIONS,
                   subsets=CARD_SUBSETS, series=CARD_SERIES)


@app.route("/packet/search")
@limiter.limit("30/minute")
def packet_search():
    """Search the online football database for a player to add to a packet."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    q = (request.args.get("q") or "").strip()[:60]
    if len(q) < 2:
        return jsonify(success=True, players=[])
    try:
        return jsonify(success=True, players=_search_players(q))
    except Exception as ex:
        logging.warning("player search failed: %s", ex)
        return jsonify(success=True, players=[],
                       error="The card database is unreachable right now — "
                             "you can still add the card by hand.")


@app.route("/packet/collectors")
@limiter.limit("60/minute")
def packet_collectors():
    """Every citizen who owns cards, so you know whose packet to browse."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    try:
        rows = supabase.table("card_packet").select("owner,for_trade,quantity").execute().data or []
    except Exception:
        return _packet_missing()
    tally = {}
    for r in rows:
        t = tally.setdefault(r["owner"], {"username": r["owner"], "cards": 0, "trading": 0})
        t["cards"] += r.get("quantity") or 1
        if r.get("for_trade"):
            t["trading"] += r.get("quantity") or 1
    avatars = {}
    try:
        for c in (supabase.table("cybucks").select("username,avatar").execute().data or []):
            avatars[c["username"]] = c.get("avatar")
    except Exception:
        pass
    out = []
    for t in tally.values():
        t["avatar"] = avatars.get(t["username"])
        t["me"] = t["username"] == user["username"]
        out.append(t)
    out.sort(key=lambda t: (-t["trading"], -t["cards"], t["username"].lower()))
    return jsonify(success=True, collectors=out, me=user["username"])


@app.route("/packet/data")
@limiter.limit("60/minute")
def packet_data():
    """A packet: your own by default, or ?user=<name> for someone else's."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    owner = (request.args.get("user") or me).strip()[:32] or me
    mine = owner == me

    if not mine:
        exists = supabase.table("cybucks").select("username").eq("username", owner).execute().data
        if not exists:
            return jsonify(success=False, error="No such citizen"), 404

    try:
        rows = supabase.table("card_packet").select("*").eq("owner", owner) \
            .order("created_at", desc=True).execute().data or []
        # What I have already wished for out of this packet.
        wished = {w["card_id"] for w in (supabase.table("card_wishlist")
                  .select("card_id").eq("username", me).execute().data or [])}
        # Who wants each of my cards (only shown on my own packet).
        wanters = {}
        if mine:
            for w in (supabase.table("card_wishlist").select("card_id,username")
                      .eq("owner", me).execute().data or []):
                wanters.setdefault(w["card_id"], []).append(w["username"])
    except Exception:
        return _packet_missing()

    cards = [_card_public(r, {"wished": r["id"] in wished,
                              "wanted_by": wanters.get(r["id"], [])}) for r in rows]
    cards.sort(key=lambda c: (-c["tier"], c["player_name"].lower()))
    return jsonify(success=True, owner=owner, mine=mine, me=me, cards=cards,
                   total=sum(c["quantity"] for c in cards),
                   trading=sum(c["quantity"] for c in cards if c["for_trade"]))


@app.route("/packet/add", methods=["POST"])
@limiter.limit("30/minute")
def packet_add():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    d = request.get_json() or {}
    name = (d.get("player_name") or "").strip()[:80]
    if not name:
        return jsonify(success=False, error="Search for a player first"), 400

    edition = (d.get("edition") or "base").strip()
    if edition not in EDITION_BY_KEY:
        return jsonify(success=False, error="Unknown edition"), 400
    subset = (d.get("subset") or "base").strip()
    if subset not in SUBSET_BY_KEY:
        return jsonify(success=False, error="Unknown card type"), 400

    image_url  = (d.get("image_url") or "").strip()[:500]
    card_image = (d.get("card_image") or "").strip()[:500]
    if image_url and not _card_url_ok(image_url):
        image_url = ""
    if card_image and not _card_url_ok(card_image):
        return jsonify(success=False, error="Use a direct https:// link for the card image."), 400
    card_url = (d.get("card_url") or "").strip()[:500]
    if card_url and not _card_url_ok(card_url):
        return jsonify(success=False, error="Use an https:// link for the card's reference page."), 400

    try:
        qty = int(d.get("quantity") or 1)
    except (TypeError, ValueError):
        qty = 1
    qty = max(1, min(qty, 99))

    rating = d.get("rating")
    try:
        rating = round(float(rating), 2) if rating not in (None, "") else None
        if rating is not None and not (0 <= rating <= 200):
            rating = None
    except (TypeError, ValueError):
        rating = None

    try:
        held = supabase.table("card_packet").select("id").eq("owner", user["username"]).execute().data or []
    except Exception:
        return _packet_missing()
    if len(held) >= MAX_PACKET_CARDS:
        return jsonify(success=False, error=f"Your packet is full ({MAX_PACKET_CARDS} cards)."), 400

    row = {
        "owner": user["username"],
        "player_id":   (d.get("player_id") or "").strip()[:32] or None,
        "player_name": name,
        "team":        (d.get("team") or "").strip()[:80],
        "nationality": (d.get("nationality") or "").strip()[:60],
        "position":    (d.get("position") or "").strip()[:40],
        "image_url":   image_url,
        "card_image":  card_image,
        "card_url":    card_url,
        "edition":     edition,
        "subset":      subset,
        "series":      (d.get("series") or "").strip()[:60],
        "card_no":     (d.get("card_no") or "").strip()[:16],
        "rating":      rating,
        "quantity":    qty,
        "for_trade":   bool(d.get("for_trade")),
        "note":        (d.get("note") or "").strip()[:200],
    }
    try:
        card = supabase.table("card_packet").insert(row).execute().data[0]
    except Exception:
        return _packet_missing()
    return jsonify(success=True, card=_card_public(card))


@app.route("/packet/upload", methods=["POST"])
@limiter.limit("20/minute")
def packet_upload():
    """Photograph the card actually in your hand. Beats any stock scan — it
    shows your copy, creases and all — and it's the only card art we can
    legitimately host, since no Match Attax image database is open to us."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    f = request.files.get("file")
    if not f:
        return jsonify(success=False, error="No file"), 400
    url, err = _store_image(f, "cards")
    if err:
        code = 400 if ("Images only" in err or "Empty" in err or "too large" in err) else 500
        return jsonify(success=False, error=err), code
    return jsonify(success=True, url=url)


@app.route("/packet/update", methods=["POST"])
@limiter.limit("40/minute")
def packet_update():
    """Change your own card: offer it for trade, fix the count, add a note."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    d = request.get_json() or {}
    try:
        cid = int(d.get("card_id"))
    except (TypeError, ValueError):
        return jsonify(success=False, error="Bad card"), 400

    try:
        r = supabase.table("card_packet").select("*").eq("id", cid) \
            .eq("owner", user["username"]).execute().data
    except Exception:
        return _packet_missing()
    if not r:
        return jsonify(success=False, error="That card isn't in your packet"), 404

    patch = {}
    if "for_trade" in d:
        patch["for_trade"] = bool(d.get("for_trade"))
    if "note" in d:
        patch["note"] = (d.get("note") or "").strip()[:200]
    if "quantity" in d:
        try:
            patch["quantity"] = max(1, min(int(d.get("quantity")), 99))
        except (TypeError, ValueError):
            pass
    if not patch:
        return jsonify(success=True, card=_card_public(r[0]))

    supabase.table("card_packet").update(patch).eq("id", cid).execute()
    card = {**r[0], **patch}

    # Newly up for trade? Tell everyone who wished for it.
    if patch.get("for_trade") and not r[0].get("for_trade"):
        for w in (supabase.table("card_wishlist").select("username")
                  .eq("card_id", cid).execute().data or []):
            notify(w["username"],
                   f"\U0001F3B4 {user['username']} put {_card_label(card)} up for trade — "
                   "the card you wishlisted.",
                   "/packet?user=" + user["username"])
    return jsonify(success=True, card=_card_public(card))


@app.route("/packet/remove", methods=["POST"])
@limiter.limit("30/minute")
def packet_remove():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    try:
        cid = int((request.get_json() or {}).get("card_id"))
    except (TypeError, ValueError):
        return jsonify(success=False, error="Bad card"), 400
    try:
        r = supabase.table("card_packet").select("id").eq("id", cid) \
            .eq("owner", user["username"]).execute().data
    except Exception:
        return _packet_missing()
    if not r:
        return jsonify(success=False, error="That card isn't in your packet"), 404
    supabase.table("card_wishlist").delete().eq("card_id", cid).execute()
    supabase.table("card_packet").delete().eq("id", cid).execute()
    return jsonify(success=True)


@app.route("/packet/wishlist", methods=["POST"])
@limiter.limit("40/minute")
def packet_wishlist():
    """Add (or remove) a card from someone else's packet to your wishlist."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    d = request.get_json() or {}
    try:
        cid = int(d.get("card_id"))
    except (TypeError, ValueError):
        return jsonify(success=False, error="Bad card"), 400
    remove = bool(d.get("remove"))

    try:
        r = supabase.table("card_packet").select("*").eq("id", cid).execute().data
    except Exception:
        return _packet_missing()
    if not r:
        return jsonify(success=False, error="That card is gone"), 404
    card = r[0]
    if card["owner"] == user["username"]:
        return jsonify(success=False, error="That card is already yours"), 400

    if remove:
        supabase.table("card_wishlist").delete().eq("username", user["username"]) \
            .eq("card_id", cid).execute()
        return jsonify(success=True, wished=False)

    already = supabase.table("card_wishlist").select("id").eq("username", user["username"]) \
        .eq("card_id", cid).execute().data
    if already:
        return jsonify(success=True, wished=True)
    try:
        supabase.table("card_wishlist").insert({
            "username": user["username"], "card_id": cid, "owner": card["owner"]}).execute()
    except Exception:
        return jsonify(success=True, wished=True)      # raced another wish — same outcome
    notify(card["owner"],
           f"\U0001F3B4 {user['username']} wishlisted your {_card_label(card)}.",
           "/packet?tab=wishes")
    return jsonify(success=True, wished=True)


@app.route("/packet/wishes")
@limiter.limit("60/minute")
def packet_wishes():
    """My wishlist, plus the cards other citizens want out of my packet."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    try:
        mine_rows = supabase.table("card_wishlist").select("*").eq("username", me) \
            .order("created_at", desc=True).execute().data or []
        theirs_rows = supabase.table("card_wishlist").select("*").eq("owner", me) \
            .order("created_at", desc=True).execute().data or []
    except Exception:
        return _packet_missing()

    cards = _cards_by_ids([r["card_id"] for r in mine_rows + theirs_rows])
    wanted = [_card_public(cards[r["card_id"]], {"wished": True})
              for r in mine_rows if r["card_id"] in cards]
    wanted_from_me = []
    for r in theirs_rows:
        c = cards.get(r["card_id"])
        if c:
            wanted_from_me.append(_card_public(c, {"wanter": r["username"],
                                                   "wished_at": r.get("created_at")}))
    return jsonify(success=True, wanted=wanted, wanted_from_me=wanted_from_me)


@app.route("/packet/trade", methods=["POST"])
@limiter.limit("15/minute")
def packet_trade():
    """Offer cards out of your packet for cards out of someone else's."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    d = request.get_json() or {}
    offer_ids = _parse_ids(d.get("offer_ids"))
    want_ids  = _parse_ids(d.get("want_ids"))
    message   = (d.get("message") or "").strip()[:300]

    if not want_ids:
        return jsonify(success=False, error="Pick at least one card you want"), 400
    if not offer_ids:
        return jsonify(success=False, error="Offer at least one card from your own packet"), 400

    try:
        cards = _cards_by_ids(offer_ids + want_ids)
    except Exception:
        return _packet_missing()
    if len(cards) != len(set(offer_ids + want_ids)):
        return jsonify(success=False, error="One of those cards no longer exists"), 404

    for cid in offer_ids:
        if cards[cid]["owner"] != me:
            return jsonify(success=False, error="You can only offer cards from your own packet"), 403
    owners = {cards[cid]["owner"] for cid in want_ids}
    if len(owners) != 1:
        return jsonify(success=False, error="Trade with one citizen at a time"), 400
    to_user = owners.pop()
    if to_user == me:
        return jsonify(success=False, error="You already own those cards"), 400
    for cid in want_ids:
        if not cards[cid].get("for_trade"):
            return jsonify(success=False,
                           error=_card_label(cards[cid]) + " isn't up for trade — wishlist it instead."), 400

    try:
        supabase.table("card_trades").insert({
            "from_user": me, "to_user": to_user, "message": message,
            "offer_ids": ",".join(str(i) for i in offer_ids),
            "want_ids":  ",".join(str(i) for i in want_ids),
            "offer_label": ", ".join(_card_label(cards[i]) for i in offer_ids),
            "want_label":  ", ".join(_card_label(cards[i]) for i in want_ids),
        }).execute()
    except Exception:
        return _packet_missing()
    notify(to_user,
           f"\U0001F91D {me} offered you a card trade "
           f"({len(offer_ids)} for {len(want_ids)}).", "/packet?tab=trades")
    return jsonify(success=True)


@app.route("/packet/trades")
@limiter.limit("60/minute")
def packet_trades():
    """Trade offers waiting on me, and the ones I have sent."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    try:
        rows = supabase.table("card_trades").select("*") \
            .order("created_at", desc=True).limit(120).execute().data or []
    except Exception:
        return _packet_missing()
    rows = [r for r in rows if r["from_user"] == me or r["to_user"] == me]

    ids = []
    for r in rows:
        ids += _parse_ids(r.get("offer_ids")) + _parse_ids(r.get("want_ids"))
    cards = _cards_by_ids(ids)

    def shape(r):
        offer_ids, want_ids = _parse_ids(r.get("offer_ids")), _parse_ids(r.get("want_ids"))
        offer = [_card_public(cards[i]) for i in offer_ids if i in cards]
        want  = [_card_public(cards[i]) for i in want_ids  if i in cards]
        # A card traded or deleted elsewhere makes a pending offer unfulfillable.
        gone = r["status"] == "pending" and (len(offer) != len(offer_ids)
                                             or len(want) != len(want_ids))
        return {"id": r["id"], "from_user": r["from_user"], "to_user": r["to_user"],
                "message": r.get("message") or "", "status": "stale" if gone else r["status"],
                "created_at": r.get("created_at"), "offer": offer, "want": want,
                # Once a trade settles the cards have moved, so fall back to the
                # labels captured when the offer was made.
                "offer_label": r.get("offer_label") or "",
                "want_label": r.get("want_label") or "",
                "incoming": r["to_user"] == me}

    trades = [shape(r) for r in rows]
    return jsonify(success=True, me=me, trades=trades,
                   incoming=sum(1 for t in trades if t["incoming"] and t["status"] == "pending"),
                   outgoing=sum(1 for t in trades if not t["incoming"] and t["status"] == "pending"))


@app.route("/packet/trade/respond", methods=["POST"])
@limiter.limit("20/minute")
def packet_trade_respond():
    """Accept or decline an offer. Accepting swaps the cards over."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    d = request.get_json() or {}
    action = (d.get("action") or "").strip()
    if action not in ("accept", "decline", "cancel"):
        return jsonify(success=False, error="Unknown action"), 400
    try:
        tid = int(d.get("trade_id"))
    except (TypeError, ValueError):
        return jsonify(success=False, error="Bad trade"), 400

    try:
        r = supabase.table("card_trades").select("*").eq("id", tid).execute().data
    except Exception:
        return _packet_missing()
    if not r:
        return jsonify(success=False, error="Trade not found"), 404
    t = r[0]
    if t["status"] != "pending":
        return jsonify(success=False, error="That offer is already settled"), 400

    if action == "cancel":
        if t["from_user"] != me:
            return jsonify(success=False, error="Only the proposer can cancel"), 403
        supabase.table("card_trades").update(
            {"status": "cancelled", "resolved_at": _now().isoformat()}).eq("id", tid).execute()
        return jsonify(success=True, status="cancelled")

    if t["to_user"] != me:
        return jsonify(success=False, error="That offer isn't addressed to you"), 403

    if action == "decline":
        supabase.table("card_trades").update(
            {"status": "declined", "resolved_at": _now().isoformat()}).eq("id", tid).execute()
        notify(t["from_user"], f"❌ {me} declined your card trade.", "/packet?tab=trades")
        return jsonify(success=True, status="declined")

    # ---- accept: re-check both sides still hold what they promised ----
    offer_ids, want_ids = _parse_ids(t.get("offer_ids")), _parse_ids(t.get("want_ids"))
    cards = _cards_by_ids(offer_ids + want_ids)
    if len(cards) != len(set(offer_ids + want_ids)):
        supabase.table("card_trades").update(
            {"status": "stale", "resolved_at": _now().isoformat()}).eq("id", tid).execute()
        return jsonify(success=False, error="Some of those cards have already been traded away."), 409
    for cid in offer_ids:
        if cards[cid]["owner"] != t["from_user"]:
            return jsonify(success=False, error="The proposer no longer owns what they offered."), 409
    for cid in want_ids:
        if cards[cid]["owner"] != me:
            return jsonify(success=False, error="You no longer own one of those cards."), 409

    # Claim the trade first so a double-click can't run the swap twice.
    claimed = supabase.table("card_trades") \
        .update({"status": "accepted", "resolved_at": _now().isoformat()}) \
        .eq("id", tid).eq("status", "pending").execute().data
    if not claimed:
        return jsonify(success=False, error="That offer is already settled"), 400

    for cid in offer_ids:
        _move_card(cards[cid], me)
    for cid in want_ids:
        _move_card(cards[cid], t["from_user"])

    got  = ", ".join(_card_label(cards[i]) for i in offer_ids)
    gave = ", ".join(_card_label(cards[i]) for i in want_ids)
    add_record(me, f"Traded {gave} to {t['from_user']} for {got}.")
    add_record(t["from_user"], f"Traded {got} to {me} for {gave}.")
    notify(t["from_user"], f"✅ {me} accepted your card trade — you got {gave}.", "/packet")
    log_txn("cards", t["from_user"], me, 0, "cards", f"Card trade: {got} for {gave}")

    # The cards have swapped owner in the database; Cyvazon moves the physical
    # ones. A parcel each way, so neither side can bank the trade and then
    # quietly hang on to the cardboard.
    raise_parcel("card", tid, got, t["from_user"], me, me)
    raise_parcel("card", tid, gave, me, t["from_user"], me)

    # Anyone still wishing for a card that just changed hands is told where it went.
    for cid in offer_ids + want_ids:
        new_owner = me if cid in offer_ids else t["from_user"]
        for w in (supabase.table("card_wishlist").select("username")
                  .eq("card_id", cid).execute().data or []):
            if w["username"] not in (me, t["from_user"]):
                notify(w["username"],
                       f"\U0001F3B4 {_card_label(cards[cid])} on your wishlist moved to "
                       f"{new_owner}'s packet.", "/packet?user=" + new_owner)
    return jsonify(success=True, status="accepted")


@app.route("/packet/summary")
@limiter.limit("60/minute")
def packet_summary():
    """Small counts for the dashboard banner: offers waiting, cards wished for."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    try:
        pending = supabase.table("card_trades").select("id,from_user") \
            .eq("to_user", me).eq("status", "pending").execute().data or []
        wishes = supabase.table("card_wishlist").select("id").eq("owner", me).execute().data or []
        mine = supabase.table("card_packet").select("quantity,for_trade") \
            .eq("owner", me).execute().data or []
        market = supabase.table("card_packet").select("id").eq("for_trade", True) \
            .neq("owner", me).execute().data or []
    except Exception:
        # Not migrated yet — the banner just stays hidden rather than erroring.
        return jsonify(success=True, enabled=False, offers=0, wishes=0,
                       cards=0, trading=0, up_for_trade=0, offer_from=[])
    return jsonify(success=True, enabled=True,
                   offers=len(pending),
                   offer_from=sorted({p["from_user"] for p in pending})[:3],
                   wishes=len(wishes),
                   cards=sum(c.get("quantity") or 1 for c in mine),
                   trading=sum(c.get("quantity") or 1 for c in mine if c.get("for_trade")),
                   up_for_trade=len(market))


# ============================================================
#  CYVAZON — the national delivery service
# ============================================================
#  Citizens sign up as couriers and run parcels between classrooms.
#  Delivery is free to whoever requests it, so the nation pays for it
#  collectively: DELIVERY_LEVY rides on top of VAT while the service is
#  open, and couriers draw COURIER_WAGE a week from the Treasury.
#
#  Marketplace sales and card trades raise a parcel automatically. That is
#  the point of it — the money or the card moves the instant a deal is
#  struck, so without a tracked handover there is nothing stopping someone
#  pocketing the goods. A parcel makes the physical half of the bargain
#  visible to both sides and to the courier carrying it.

# Where in the school a parcel can be picked up or dropped off. Classes are
# free text (every school names them differently); this is the coarse
# "which part of the building" picker that goes with the class.
SCHOOL_AREAS = [
    "Classroom", "Form room", "Library", "Canteen", "Science lab",
    "Computer lab", "Art room", "Music room", "Sports hall", "Playground",
    "Field", "Assembly hall", "Reception", "Staff room", "Corridor",
    "Bus bay", "Gate",
]

DELIVERY_STATUSES = ("open", "claimed", "picked_up", "delivered", "cancelled")

MAX_OPEN_PARCELS = 20      # per citizen, so nobody floods the board


def _delivery_missing():
    return jsonify(success=False,
                   error="Cyvazon isn't enabled yet — the database needs a quick update "
                         "(run migration_delivery.sql)."), 503


def _courier_row(username):
    try:
        r = supabase.table("couriers").select("*").eq("username", username).execute().data
    except Exception:
        return None
    return r[0] if r else None


def is_courier(user):
    """Only an APPROVED, still-active courier counts. A pending applicant can
    see that they applied and nothing else — they aren't carrying anyone's
    property until the President has vetted them."""
    if not user:
        return False
    row = _courier_row(user["username"])
    return bool(row and row.get("active") and (row.get("status") or "pending") == "approved")


def _clean_area(v):
    v = (v or "").strip()[:40]
    return v if v in SCHOOL_AREAS else ""


def _delivery_public(row):
    return {
        "id":            row.get("id"),
        "kind":          row.get("kind") or "custom",
        "ref_id":        row.get("ref_id"),
        "item_label":    row.get("item_label") or "",
        "sender":        row.get("sender"),
        "recipient":     row.get("recipient"),
        "requested_by":  row.get("requested_by"),
        "pickup_class":  row.get("pickup_class") or "",
        "pickup_area":   row.get("pickup_area") or "",
        "dropoff_class": row.get("dropoff_class") or "",
        "dropoff_area":  row.get("dropoff_area") or "",
        "notes":         row.get("notes") or "",
        "status":        row.get("status") or "open",
        "courier":       row.get("courier"),
        "created_at":    row.get("created_at"),
        "claimed_at":    row.get("claimed_at"),
        "delivered_at":  row.get("delivered_at"),
    }


def _where(cls, area):
    """'8B (Science lab)' — how a stop reads on the board."""
    cls, area = (cls or "").strip(), (area or "").strip()
    if cls and area:
        return f"{cls} ({area})"
    return cls or area or "unspecified"


def raise_parcel(kind, ref_id, label, sender, recipient, requested_by,
                 pickup=None, dropoff=None, notes=""):
    """Put a parcel on the Cyvazon board. Best-effort: a delivery failing to
    record must never roll back the sale or trade that created it."""
    if not DELIVERY_OPEN or sender == recipient:
        return None
    pickup = pickup or {}
    dropoff = dropoff or {}
    # Fall back to wherever each citizen said they're normally found.
    if not pickup.get("class"):
        pickup = _home_of(sender)
    if not dropoff.get("class"):
        dropoff = _home_of(recipient)
    row = {
        "kind": kind, "ref_id": ref_id, "item_label": (label or "")[:140],
        "sender": sender, "recipient": recipient, "requested_by": requested_by,
        "pickup_class":  (pickup.get("class") or "")[:40],
        "pickup_area":   _clean_area(pickup.get("area")),
        "dropoff_class": (dropoff.get("class") or "")[:40],
        "dropoff_area":  _clean_area(dropoff.get("area")),
        "notes": (notes or "")[:200],
    }
    try:
        made = supabase.table("deliveries").insert(row).execute().data[0]
    except Exception as ex:
        logging.warning("parcel not raised (%s): %s", kind, ex)
        return None
    notify(sender, f"\U0001F4E6 Cyvazon parcel raised: hand '{row['item_label']}' to a courier "
                   f"at {_where(row['pickup_class'], row['pickup_area'])}.", "/cyvazon")
    notify(recipient, f"\U0001F4E6 '{row['item_label']}' is coming to you via Cyvazon.", "/cyvazon")
    return made


def _home_of(username):
    try:
        r = supabase.table("cybucks").select("home_class,home_area") \
            .eq("username", username).execute().data
    except Exception:
        return {}
    if not r:
        return {}
    return {"class": r[0].get("home_class") or "", "area": r[0].get("home_area") or ""}


# ---- routes -----------------------------------------------------------
@app.route("/cyvazon")
def cyvazon_page():
    return app.send_static_file("cyvazon.html")


@app.route("/cyvazon/config")
@limiter.limit("60/minute")
def cyvazon_config():
    """Everything the page needs to render its forms."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    row = _courier_row(user["username"])
    return jsonify(success=True, areas=SCHOOL_AREAS,
                   open=bool(DELIVERY_OPEN), wage=COURIER_WAGE,
                   levy=DELIVERY_LEVY, vat=VAT_RATE,
                   salary_days=SALARY_PERIOD_DAYS,
                   me=user["username"],
                   home={"class": user.get("home_class") or "",
                         "area": user.get("home_area") or ""},
                   courier=is_courier(user),
                   courier_status=((row or {}).get("status") or "") if row and row.get("active") else "",
                   is_admin=has_power(user, "couriers"),
                   deliveries_done=(row or {}).get("deliveries") or 0)


@app.route("/cyvazon/home", methods=["POST"])
@limiter.limit("20/minute")
def cyvazon_set_home():
    """Where you're normally found, so parcels can be addressed to you."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    d = request.get_json() or {}
    patch = {"home_class": (d.get("class") or "").strip()[:40],
             "home_area": _clean_area(d.get("area"))}
    try:
        supabase.table("cybucks").update(patch).eq("username", user["username"]).execute()
    except Exception:
        return _delivery_missing()
    return jsonify(success=True, home={"class": patch["home_class"], "area": patch["home_area"]})


@app.route("/cyvazon/signup", methods=["POST"])
@limiter.limit("10/minute")
def cyvazon_signup():
    """Sign on as a courier. Pays COURIER_WAGE a week from the Treasury."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    if not DELIVERY_OPEN:
        return jsonify(success=False, error="Cyvazon recruitment is closed right now."), 403
    d = request.get_json() or {}
    covers = (d.get("covers") or "").strip()[:120]
    note   = (d.get("note") or "").strip()[:200]
    me = user["username"]

    existing = _courier_row(me)
    if existing and existing.get("status") == "approved" and existing.get("active"):
        return jsonify(success=False, error="You're already a courier"), 400

    try:
        if existing:
            # Re-applying after standing down or being turned away goes back
            # into the queue — approval isn't something you keep forever.
            supabase.table("couriers").update(
                {"active": True, "status": "pending", "covers": covers, "note": note,
                 "left_at": None, "decided_by": None, "decided_at": None}) \
                .eq("username", me).execute()
        else:
            supabase.table("couriers").insert(
                {"username": me, "covers": covers, "note": note, "status": "pending"}).execute()
    except Exception:
        return _delivery_missing()
    add_record(me, "Applied to join the Cyvazon courier crew.")
    for boss in sorted(TREASURY_ADMINS):
        notify(boss, "\U0001F69A " + me + " applied to be a Cyvazon courier.",
               "/cyvazon?tab=admin")
    return jsonify(success=True, courier=False, status="pending")


@app.route("/cyvazon/resign", methods=["POST"])
@limiter.limit("10/minute")
def cyvazon_resign():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    # Don't let someone walk off holding other people's parcels.
    try:
        holding = supabase.table("deliveries").select("id").eq("courier", me) \
            .in_("status", ["claimed", "picked_up"]).execute().data or []
    except Exception:
        return _delivery_missing()
    if holding:
        return jsonify(success=False,
                       error=f"Finish or drop your {len(holding)} active parcel(s) first."), 400
    supabase.table("couriers").update({"active": False, "left_at": _now().isoformat()}) \
        .eq("username", me).execute()
    add_record(me, "Stood down as a Cyvazon courier.")
    return jsonify(success=True, courier=False)


@app.route("/cyvazon/couriers")
@limiter.limit("60/minute")
def cyvazon_couriers():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    try:
        rows = supabase.table("couriers").select("*").eq("active", True) \
            .eq("status", "approved").execute().data or []
    except Exception:
        return _delivery_missing()
    avatars = {}
    try:
        for c in (supabase.table("cybucks").select("username,avatar").execute().data or []):
            avatars[c["username"]] = c.get("avatar")
    except Exception:
        pass
    out = [{"username": r["username"], "covers": r.get("covers") or "",
            "note": r.get("note") or "", "deliveries": r.get("deliveries") or 0,
            "avatar": avatars.get(r["username"]), "me": r["username"] == user["username"]}
           for r in rows]
    out.sort(key=lambda c: (-c["deliveries"], c["username"].lower()))
    return jsonify(success=True, couriers=out, wage=COURIER_WAGE)


@app.route("/cyvazon/request", methods=["POST"])
@limiter.limit("20/minute")
def cyvazon_request():
    """Send anything to anyone, anywhere in school. Free."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    if not DELIVERY_OPEN:
        return jsonify(success=False, error="Cyvazon is closed right now."), 403
    me = user["username"]
    d = request.get_json() or {}

    recipient = (d.get("recipient") or "").strip()[:32]
    label     = (d.get("item_label") or "").strip()[:140]
    if not recipient:
        return jsonify(success=False, error="Who is it going to?"), 400
    if recipient == me:
        return jsonify(success=False, error="You can't post something to yourself"), 400
    if not label:
        return jsonify(success=False, error="Say what you're sending"), 400
    if not (d.get("pickup_class") or "").strip():
        return jsonify(success=False, error="Which class is it being collected from?"), 400
    if not (d.get("dropoff_class") or "").strip():
        return jsonify(success=False, error="Which class is it going to?"), 400

    exists = supabase.table("cybucks").select("username").eq("username", recipient).execute().data
    if not exists:
        return jsonify(success=False, error="No such citizen"), 404

    try:
        mine = supabase.table("deliveries").select("id").eq("requested_by", me) \
            .in_("status", ["open", "claimed", "picked_up"]).execute().data or []
    except Exception:
        return _delivery_missing()
    if len(mine) >= MAX_OPEN_PARCELS:
        return jsonify(success=False,
                       error=f"You already have {MAX_OPEN_PARCELS} parcels in flight."), 400

    made = raise_parcel("custom", None, label, me, recipient, me,
                        pickup={"class": d.get("pickup_class"), "area": d.get("pickup_area")},
                        dropoff={"class": d.get("dropoff_class"), "area": d.get("dropoff_area")},
                        notes=d.get("notes"))
    if not made:
        return _delivery_missing()
    return jsonify(success=True, delivery=_delivery_public(made))


@app.route("/cyvazon/board")
@limiter.limit("60/minute")
def cyvazon_board():
    """Unclaimed jobs, the runs I'm carrying, and my own parcels."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    try:
        rows = supabase.table("deliveries").select("*") \
            .order("created_at", desc=True).limit(300).execute().data or []
    except Exception:
        return _delivery_missing()

    live = [r for r in rows if r.get("status") != "cancelled"]
    # A courier shouldn't carry their own parcel — that defeats the point.
    board = [_delivery_public(r) for r in live
             if r.get("status") == "open" and me not in (r.get("sender"), r.get("recipient"))]
    carrying = [_delivery_public(r) for r in live
                if r.get("courier") == me and r.get("status") in ("claimed", "picked_up")]
    sending = [_delivery_public(r) for r in live if r.get("sender") == me]
    incoming = [_delivery_public(r) for r in live if r.get("recipient") == me]
    done = [_delivery_public(r) for r in live
            if r.get("courier") == me and r.get("status") == "delivered"][:20]
    return jsonify(success=True, me=me, courier=is_courier(user), open=bool(DELIVERY_OPEN),
                   board=board, carrying=carrying, sending=sending,
                   incoming=incoming, completed=done)


@app.route("/cyvazon/claim", methods=["POST"])
@limiter.limit("30/minute")
def cyvazon_claim():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    if not is_courier(user):
        return jsonify(success=False, error="Sign up as a Cyvazon courier first"), 403
    me = user["username"]
    try:
        did = int((request.get_json() or {}).get("delivery_id"))
    except (TypeError, ValueError):
        return jsonify(success=False, error="Bad parcel"), 400

    try:
        r = supabase.table("deliveries").select("*").eq("id", did).execute().data
    except Exception:
        return _delivery_missing()
    if not r:
        return jsonify(success=False, error="Parcel not found"), 404
    p = r[0]
    if p["status"] != "open":
        return jsonify(success=False, error="Another courier already took that one"), 409
    if me in (p["sender"], p["recipient"]):
        return jsonify(success=False, error="You can't carry your own parcel"), 400

    # Claim it conditionally, so two couriers tapping at once can't both win.
    claimed = supabase.table("deliveries") \
        .update({"status": "claimed", "courier": me, "claimed_at": _now().isoformat()}) \
        .eq("id", did).eq("status", "open").execute().data
    if not claimed:
        return jsonify(success=False, error="Another courier already took that one"), 409

    notify(p["sender"], f"\U0001F6F5 {me} is collecting '{p['item_label']}' from "
                        f"{_where(p['pickup_class'], p['pickup_area'])}.", "/cyvazon")
    notify(p["recipient"], f"\U0001F6F5 {me} is bringing you '{p['item_label']}'.", "/cyvazon")
    return jsonify(success=True, status="claimed")


@app.route("/cyvazon/status", methods=["POST"])
@limiter.limit("40/minute")
def cyvazon_status():
    """Courier marks a parcel collected; the RECIPIENT confirms it arrived.
    Only the person receiving the goods can close a run — otherwise a courier
    could mark everything delivered and the whole system means nothing."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    d = request.get_json() or {}
    action = (d.get("action") or "").strip()
    if action not in ("picked_up", "delivered", "drop"):
        return jsonify(success=False, error="Unknown action"), 400
    try:
        did = int(d.get("delivery_id"))
    except (TypeError, ValueError):
        return jsonify(success=False, error="Bad parcel"), 400

    try:
        r = supabase.table("deliveries").select("*").eq("id", did).execute().data
    except Exception:
        return _delivery_missing()
    if not r:
        return jsonify(success=False, error="Parcel not found"), 404
    p = r[0]

    if action == "picked_up":
        if p.get("courier") != me:
            return jsonify(success=False, error="That isn't your run"), 403
        if p["status"] != "claimed":
            return jsonify(success=False, error="That parcel isn't waiting for collection"), 400
        supabase.table("deliveries").update({"status": "picked_up"}).eq("id", did).execute()
        notify(p["recipient"], f"\U0001F4E6 '{p['item_label']}' is on its way to "
                               f"{_where(p['dropoff_class'], p['dropoff_area'])}.", "/cyvazon")
        return jsonify(success=True, status="picked_up")

    if action == "drop":
        if p.get("courier") != me:
            return jsonify(success=False, error="That isn't your run"), 403
        if p["status"] not in ("claimed", "picked_up"):
            return jsonify(success=False, error="Nothing to drop"), 400
        supabase.table("deliveries").update(
            {"status": "open", "courier": None, "claimed_at": None}).eq("id", did).execute()
        return jsonify(success=True, status="open")

    # ---- delivered: only the recipient signs for it ----
    if p["recipient"] != me:
        return jsonify(success=False,
                       error="Only the person receiving the parcel can confirm it arrived."), 403
    if p["status"] not in ("claimed", "picked_up"):
        return jsonify(success=False, error="That parcel hasn't been collected yet"), 400

    signed = supabase.table("deliveries") \
        .update({"status": "delivered", "delivered_at": _now().isoformat()}) \
        .eq("id", did).in_("status", ["claimed", "picked_up"]).execute().data
    if not signed:
        return jsonify(success=False, error="That parcel is already settled"), 400

    courier = p.get("courier")
    if courier:
        cas_num("couriers", [("username", courier)], "deliveries", 1, places=0)
        notify(courier, f"✅ {me} confirmed delivery of '{p['item_label']}'.", "/cyvazon")
        add_record(courier, f"Delivered '{p['item_label']}' to {me}.")
    notify(p["sender"], f"✅ '{p['item_label']}' reached {me}.", "/cyvazon")
    return jsonify(success=True, status="delivered")


@app.route("/cyvazon/cancel", methods=["POST"])
@limiter.limit("20/minute")
def cyvazon_cancel():
    """The sender can pull a parcel back, but only before a courier has it."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    try:
        did = int((request.get_json() or {}).get("delivery_id"))
    except (TypeError, ValueError):
        return jsonify(success=False, error="Bad parcel"), 400
    try:
        r = supabase.table("deliveries").select("*").eq("id", did).execute().data
    except Exception:
        return _delivery_missing()
    if not r:
        return jsonify(success=False, error="Parcel not found"), 404
    p = r[0]
    if me not in (p["sender"], p["requested_by"]) and not is_treasury_admin(user):
        return jsonify(success=False, error="That isn't your parcel"), 403
    if p["status"] not in ("open", "claimed"):
        return jsonify(success=False, error="Too late — it's already on the move"), 400
    supabase.table("deliveries").update({"status": "cancelled"}).eq("id", did).execute()
    if p.get("courier"):
        notify(p["courier"], f"❌ '{p['item_label']}' was cancelled by {me}.", "/cyvazon")
    return jsonify(success=True, status="cancelled")


@app.route("/cyvazon/admin")
@limiter.limit("60/minute")
def cyvazon_admin():
    """The delivery service at a glance: who wants to carry parcels, who
    already does, and what the service is costing."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    if not has_power(user, "couriers"):
        return jsonify(success=False, error="President or Transport Minister only"), 403
    try:
        rows = supabase.table("couriers").select("*").execute().data or []
        parcels = supabase.table("deliveries").select("status").execute().data or []
    except Exception:
        return _delivery_missing()

    def shape(r):
        return {"username": r["username"], "status": r.get("status") or "pending",
                "active": bool(r.get("active")), "covers": r.get("covers") or "",
                "note": r.get("note") or "", "deliveries": r.get("deliveries") or 0,
                "hired_at": r.get("hired_at"), "decided_by": r.get("decided_by")}

    pending  = [shape(r) for r in rows
                if r.get("active") and (r.get("status") or "pending") == "pending"]
    approved = [shape(r) for r in rows if r.get("active") and r.get("status") == "approved"]
    past     = [shape(r) for r in rows if r.get("status") == "rejected" or not r.get("active")]
    counts = {}
    for p in parcels:
        k = p.get("status") or "open"
        counts[k] = counts.get(k, 0) + 1
    return jsonify(success=True, pending=pending, approved=approved, past=past,
                   parcels=counts, wage=COURIER_WAGE, levy=DELIVERY_LEVY,
                   open=bool(DELIVERY_OPEN),
                   weekly_cost=round(COURIER_WAGE * len(approved), 2))


@app.route("/cyvazon/admin/decide", methods=["POST"])
@limiter.limit("30/minute")
def cyvazon_admin_decide():
    """Approve, turn away, or revoke a courier."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    if not has_power(user, "couriers"):
        return jsonify(success=False, error="President or Transport Minister only"), 403
    d = request.get_json() or {}
    who = (d.get("username") or "").strip()[:32]
    action = (d.get("action") or "").strip()
    if action not in ("approve", "reject", "revoke"):
        return jsonify(success=False, error="Unknown action"), 400
    row = _courier_row(who)
    if not row:
        return jsonify(success=False, error="No such applicant"), 404

    if action == "approve":
        if row.get("status") == "approved" and row.get("active"):
            return jsonify(success=True, status="approved")
        supabase.table("couriers").update(
            {"status": "approved", "active": True, "decided_by": user["username"],
             "decided_at": _now().isoformat()}).eq("username", who).execute()
        # The wage clock starts on approval — nobody is paid for time spent
        # waiting in the queue.
        try:
            supabase.table("cybucks").update({"last_courier_pay": _now().isoformat()}) \
                .eq("username", who).execute()
        except Exception:
            pass
        add_record(who, "Approved as a Cyvazon courier "
                        "(" + format(COURIER_WAGE, "g") + " CB per pay period).")
        notify(who, "✅ You're an approved Cyvazon courier — "
                    + format(COURIER_WAGE, "g") + " CB per pay period. "
                    "Open the jobs board to start.", "/cyvazon?tab=jobs")
        return jsonify(success=True, status="approved")

    # reject / revoke — don't strand parcels the courier is already holding
    try:
        holding = supabase.table("deliveries").select("id").eq("courier", who) \
            .in_("status", ["claimed", "picked_up"]).execute().data or []
    except Exception:
        holding = []
    for h in holding:
        supabase.table("deliveries").update(
            {"status": "open", "courier": None, "claimed_at": None}).eq("id", h["id"]).execute()

    supabase.table("couriers").update(
        {"status": "rejected", "active": False, "decided_by": user["username"],
         "decided_at": _now().isoformat()}).eq("username", who).execute()
    if action == "revoke":
        add_record(who, "Removed from the Cyvazon courier crew.")
        notify(who, "Your Cyvazon courier licence was withdrawn.", "/cyvazon")
    else:
        notify(who, "Your Cyvazon courier application wasn't approved.", "/cyvazon")
    return jsonify(success=True, status="rejected", released=len(holding))


@app.route("/cyvazon/summary")
@limiter.limit("60/minute")
def cyvazon_summary():
    """Counts for the dashboard banner."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    try:
        rows = supabase.table("deliveries").select("sender,recipient,courier,status") \
            .in_("status", ["open", "claimed", "picked_up"]).execute().data or []
    except Exception:
        # Tables not created yet. Still answer the questions that don't depend
        # on them — above all is_admin, or the President loses the delivery
        # admin panel precisely when they need it to see what's wrong.
        return jsonify(success=True, enabled=False, waiting=0, incoming=0,
                       carrying=0, to_hand_over=0, courier=False,
                       courier_status="", is_admin=has_power(user, "couriers"),
                       open=bool(DELIVERY_OPEN), wage=COURIER_WAGE)
    crow = _courier_row(me)
    return jsonify(success=True, enabled=True, courier=is_courier(user),
                   courier_status=((crow or {}).get("status") or "") if crow and crow.get("active") else "",
                   is_admin=has_power(user, "couriers"),
                   open=bool(DELIVERY_OPEN), wage=COURIER_WAGE,
                   waiting=sum(1 for r in rows if r["status"] == "open"
                               and me not in (r["sender"], r["recipient"])),
                   incoming=sum(1 for r in rows if r["recipient"] == me),
                   to_hand_over=sum(1 for r in rows if r["sender"] == me
                                    and r["status"] in ("open", "claimed")),
                   carrying=sum(1 for r in rows if r.get("courier") == me))

# ============================================================
#  CYVASHIELD — national insurance
# ============================================================
#  Cover is free for every citizen. The plan a citizen picks doesn't cost
#  anything; it only sets how much a single claim can be worth and how many
#  they may file in a month. Payouts come from the Treasury.
#
#  Every claim is ruled on by the President. To make that judgement quick
#  rather than a guess, a claim can cite something the site already knows
#  about — a Cyvazon parcel, a marketplace sale, a card trade — and the admin
#  panel then shows what the records actually say next to what the citizen
#  claims. A parcel the claimant personally signed for is very hard to argue
#  was never delivered.

INSURANCE_PLANS = [
    {"key": "basic",    "label": "Basic",      "color": "#58c4ff",
     "cap": 500,  "monthly": 2,
     "covers": ["parcel", "market"],
     "blurb": "Undelivered parcels and marketplace purchases that never arrived."},
    {"key": "standard", "label": "Standard",   "color": "#1fd6a6",
     "cap": 1500, "monthly": 3,
     "covers": ["parcel", "market", "theft", "cards", "lend"],
     "blurb": "Adds stolen Cybucks and Match Attax cards that never changed hands."},
    {"key": "full",     "label": "Full Cover", "color": "#ffce56",
     "cap": 5000, "monthly": 4,
     "covers": ["parcel", "market", "theft", "cards", "lend", "scam", "other"],
     "blurb": "Everything, including scams and anything else the President accepts."},
]
PLAN_BY_KEY = {p["key"]: p for p in INSURANCE_PLANS}

CLAIM_CATEGORIES = [
    {"key": "parcel", "label": "Parcel never arrived",     "icon": "fa-box-open"},
    {"key": "market", "label": "Marketplace purchase",     "icon": "fa-store"},
    {"key": "theft",  "label": "Money stolen",             "icon": "fa-sack-dollar"},
    {"key": "cards",  "label": "Card trade gone wrong",    "icon": "fa-futbol"},
    {"key": "lend",   "label": "Lent item never returned",  "icon": "fa-hand-holding"},
    {"key": "scam",   "label": "Scammed by a citizen",     "icon": "fa-user-secret"},
    {"key": "other",  "label": "Something else",           "icon": "fa-circle-question"},
]
CATEGORY_BY_KEY = {c["key"]: c for c in CLAIM_CATEGORIES}

# Switching to a richer plan doesn't take effect instantly — otherwise a
# citizen could upgrade the moment something goes wrong and claim the higher
# cap for a loss that happened while they were on Basic.
PLAN_UPGRADE_WAIT_DAYS = 3
INSURANCE_OPEN = True
INSURANCE_LEVY = 0.0        # cover is free; raise only if payouts outrun revenue


def _insurance_missing():
    return jsonify(success=False,
                   error="Cyvashield isn't enabled yet — the database needs a quick update "
                         "(run migration_insurance.sql)."), 503


def _policy_row(username):
    try:
        r = supabase.table("insurance_policies").select("*") \
            .eq("username", username).execute().data
    except Exception:
        return None
    return r[0] if r else None


def _plan_of(username):
    """The plan a citizen is actually covered by right now."""
    row = _policy_row(username)
    if not row or not row.get("active"):
        return None
    return PLAN_BY_KEY.get(row.get("plan") or "basic", INSURANCE_PLANS[0])


def _claims_this_month(username):
    since = (_now() - timedelta(days=30)).isoformat()
    try:
        rows = supabase.table("insurance_claims").select("id,created_at,status") \
            .eq("username", username).execute().data or []
    except Exception:
        return []
    # Withdrawn/rejected-as-fraud still count — the limit is on filing, not winning.
    return [r for r in rows if (r.get("created_at") or "") >= since]


def _claim_public(row, extra=None):
    plan = PLAN_BY_KEY.get(row.get("plan") or "basic", INSURANCE_PLANS[0])
    cat = CATEGORY_BY_KEY.get(row.get("category") or "other", CLAIM_CATEGORIES[-1])
    out = {
        "id": row.get("id"), "username": row.get("username"),
        "plan": plan["key"], "plan_label": plan["label"],
        "category": cat["key"], "category_label": cat["label"], "category_icon": cat["icon"],
        "ref_kind": row.get("ref_kind") or "", "ref_id": row.get("ref_id"),
        "amount": row.get("amount") or 0, "amount_paid": row.get("amount_paid") or 0,
        "description": row.get("description") or "",
        "evidence_url": row.get("evidence_url") or "",
        "accused": row.get("accused") or "",
        "status": row.get("status") or "open",
        "decided_by": row.get("decided_by"), "decided_at": row.get("decided_at"),
        "decision_note": row.get("decision_note") or "",
        "created_at": row.get("created_at"),
    }
    if extra:
        out.update(extra)
    return out


def _verify_claim(row):
    """Check the citizen's story against what the site already recorded.
    Returns a list of {tone, text} notes for the President — `bad` means the
    records contradict the claim, `good` means they support it."""
    notes = []
    kind, rid, who = row.get("ref_kind") or "", row.get("ref_id"), row.get("username")

    if kind == "delivery" and rid:
        try:
            d = supabase.table("deliveries").select("*").eq("id", rid).execute().data
        except Exception:
            d = None
        if not d:
            notes.append({"tone": "bad", "text": f"No Cyvazon parcel #{rid} exists."})
        else:
            p = d[0]
            if p.get("recipient") != who:
                notes.append({"tone": "bad",
                              "text": f"Parcel #{rid} was addressed to {p.get('recipient')}, not {who}."})
            if p.get("status") == "delivered":
                notes.append({"tone": "bad",
                              "text": f"{who} already confirmed parcel #{rid} as delivered "
                                      f"on {(p.get('delivered_at') or '')[:16]}."})
            elif p.get("status") == "cancelled":
                notes.append({"tone": "warn", "text": f"Parcel #{rid} was cancelled, not lost."})
            else:
                notes.append({"tone": "good",
                              "text": f"Parcel #{rid} is '{p.get('status')}' and never signed for"
                                      + (f", carried by {p.get('courier')}." if p.get("courier") else ".")})
            if p.get("courier") and not (row.get("accused") or ""):
                notes.append({"tone": "info", "text": f"Courier on that run: {p.get('courier')}."})

    elif kind == "market" and rid:
        try:
            m = supabase.table("market_items").select("*").eq("id", rid).execute().data
        except Exception:
            m = None
        if not m:
            notes.append({"tone": "bad", "text": f"No marketplace listing #{rid} exists."})
        else:
            it = m[0]
            if it.get("buyer") != who:
                notes.append({"tone": "bad",
                              "text": f"Listing #{rid} was bought by {it.get('buyer') or 'nobody'}, not {who}."})
            else:
                notes.append({"tone": "good",
                              "text": f"{who} did buy '{it.get('title')}' from {it.get('seller')}."})
            paid = float(it.get("price") or 0)
            if float(row.get("amount") or 0) > paid:
                notes.append({"tone": "warn",
                              "text": f"Claiming {row.get('amount')} CB but only paid {paid:g} CB."})

    elif kind == "card_trade" and rid:
        try:
            t = supabase.table("card_trades").select("*").eq("id", rid).execute().data
        except Exception:
            t = None
        if not t:
            notes.append({"tone": "bad", "text": f"No card trade #{rid} exists."})
        else:
            tr = t[0]
            if who not in (tr.get("from_user"), tr.get("to_user")):
                notes.append({"tone": "bad", "text": f"{who} wasn't part of card trade #{rid}."})
            elif tr.get("status") != "accepted":
                notes.append({"tone": "warn",
                              "text": f"Card trade #{rid} was never accepted (status: {tr.get('status')})."})
            else:
                other = tr["from_user"] if tr["to_user"] == who else tr["to_user"]
                notes.append({"tone": "good",
                              "text": f"Trade #{rid} with {other} was accepted — cards were due both ways."})

    # Cap check, whatever the category.
    plan = PLAN_BY_KEY.get(row.get("plan") or "basic", INSURANCE_PLANS[0])
    if float(row.get("amount") or 0) > plan["cap"]:
        notes.append({"tone": "warn",
                      "text": f"Claim exceeds the {plan['label']} cap of {plan['cap']} CB."})

    # A citizen who keeps claiming is worth a second look.
    try:
        prior = supabase.table("insurance_claims").select("id,status") \
            .eq("username", who).execute().data or []
    except Exception:
        prior = []
    paid_before = [p for p in prior if p.get("status") == "approved"]
    if len(paid_before) >= 3:
        notes.append({"tone": "warn",
                      "text": f"{who} has already had {len(paid_before)} claims approved."})
    if any(p.get("status") == "fraudulent" for p in prior):
        notes.append({"tone": "bad", "text": f"{who} has a claim previously ruled fraudulent."})
    return notes


# ---- routes -----------------------------------------------------------
@app.route("/shield")
def shield_page():
    return app.send_static_file("shield.html")


@app.route("/shield/config")
@limiter.limit("60/minute")
def shield_config():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    row = _policy_row(user["username"])
    plan = PLAN_BY_KEY.get((row or {}).get("plan") or "", None)
    used = len(_claims_this_month(user["username"])) if row else 0
    return jsonify(success=True, plans=INSURANCE_PLANS, categories=CLAIM_CATEGORIES,
                   open=bool(INSURANCE_OPEN), me=user["username"],
                   is_admin=has_power(user, "claims"),
                   enrolled=bool(row and row.get("active")),
                   plan=(plan or {}).get("key", ""),
                   switched_at=(row or {}).get("switched_at"),
                   upgrade_wait_days=PLAN_UPGRADE_WAIT_DAYS,
                   claims_this_month=used,
                   claims_paid=(row or {}).get("claims_paid") or 0)


@app.route("/shield/enrol", methods=["POST"])
@limiter.limit("15/minute")
def shield_enrol():
    """Join, or switch plan. Always free."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    if not INSURANCE_OPEN:
        return jsonify(success=False, error="Cyvashield is closed to new policies right now."), 403
    me = user["username"]
    plan = (request.get_json() or {}).get("plan") or "basic"
    if plan not in PLAN_BY_KEY:
        return jsonify(success=False, error="Unknown plan"), 400

    row = _policy_row(me)
    now = _now()
    try:
        if row:
            supabase.table("insurance_policies").update(
                {"plan": plan, "active": True, "switched_at": now.isoformat()}) \
                .eq("username", me).execute()
        else:
            supabase.table("insurance_policies").insert(
                {"username": me, "plan": plan}).execute()
    except Exception:
        return _insurance_missing()
    add_record(me, f"Covered by Cyvashield ({PLAN_BY_KEY[plan]['label']}).")
    return jsonify(success=True, plan=plan)


@app.route("/shield/claim", methods=["POST"])
@limiter.limit("10/minute")
def shield_claim():
    """Report a loss. The President rules on it."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    if not INSURANCE_OPEN:
        return jsonify(success=False, error="Cyvashield isn't taking claims right now."), 403
    me = user["username"]

    row = _policy_row(me)
    if not row or not row.get("active"):
        return jsonify(success=False, error="You aren't covered yet — pick a plan first."), 403
    plan = PLAN_BY_KEY.get(row.get("plan") or "basic", INSURANCE_PLANS[0])

    d = request.get_json() or {}
    category = (d.get("category") or "").strip()
    if category not in CATEGORY_BY_KEY:
        return jsonify(success=False, error="Pick what went wrong"), 400
    if category not in plan["covers"]:
        return jsonify(success=False,
                       error=f"{plan['label']} doesn't cover {CATEGORY_BY_KEY[category]['label'].lower()}. "
                             "Switch to a higher plan — it's still free."), 403

    description = (d.get("description") or "").strip()[:800]
    if len(description) < 15:
        return jsonify(success=False, error="Tell us what happened (at least 15 characters)."), 400

    try:
        amount = round(float(d.get("amount") or 0), 2)
    except (TypeError, ValueError):
        amount = 0
    if not math.isfinite(amount) or amount <= 0:
        return jsonify(success=False, error="How much did you lose?"), 400
    if amount > plan["cap"]:
        return jsonify(success=False,
                       error=f"{plan['label']} covers up to {plan['cap']} CB per claim. "
                             "A higher plan is free if you need more."), 400

    # A plan switch has to settle before its bigger cap applies.
    sw = _parse(row.get("switched_at"))
    if sw and (_now() - sw).days < PLAN_UPGRADE_WAIT_DAYS:
        base = INSURANCE_PLANS[0]
        if amount > base["cap"]:
            left = PLAN_UPGRADE_WAIT_DAYS - (_now() - sw).days
            return jsonify(success=False,
                           error=f"You changed plan recently — the higher cap applies in {left} day(s). "
                                 f"Until then you can claim up to {base['cap']} CB."), 403

    used = len(_claims_this_month(me))
    if used >= plan["monthly"]:
        return jsonify(success=False,
                       error=f"{plan['label']} allows {plan['monthly']} claims a month. "
                             "You've used them all."), 429

    ref_kind = (d.get("ref_kind") or "").strip()
    if ref_kind not in ("delivery", "market", "card_trade", ""):
        ref_kind = ""
    try:
        ref_id = int(d.get("ref_id")) if d.get("ref_id") else None
    except (TypeError, ValueError):
        ref_id = None

    evidence = (d.get("evidence_url") or "").strip()[:500]
    if evidence and not _card_url_ok(evidence):
        return jsonify(success=False, error="Evidence must be a direct https:// link."), 400

    accused = (d.get("accused") or "").strip()[:32]
    if accused == me:
        return jsonify(success=False, error="You can't report yourself."), 400

    claim = {
        "username": me, "plan": plan["key"], "category": category,
        "ref_kind": ref_kind, "ref_id": ref_id, "amount": amount,
        "description": description, "evidence_url": evidence, "accused": accused,
    }
    try:
        made = supabase.table("insurance_claims").insert(claim).execute().data[0]
    except Exception:
        return _insurance_missing()

    add_record(me, f"Filed a Cyvashield claim for {amount:g} CB ({CATEGORY_BY_KEY[category]['label']}).")
    for boss in sorted(TREASURY_ADMINS):
        notify(boss, f"\U0001F6E1️ {me} filed a {amount:g} CB Cyvashield claim.",
               "/shield?tab=admin")
    return jsonify(success=True, claim=_claim_public(made))


@app.route("/shield/claims")
@limiter.limit("60/minute")
def shield_claims():
    """My own claims."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    try:
        rows = supabase.table("insurance_claims").select("*") \
            .eq("username", user["username"]).order("created_at", desc=True) \
            .limit(60).execute().data or []
    except Exception:
        return _insurance_missing()
    return jsonify(success=True, claims=[_claim_public(r) for r in rows])


@app.route("/shield/admin")
@limiter.limit("60/minute")
def shield_admin():
    """The claim desk, with the records checked for whoever is ruling."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    if not has_power(user, "claims"):
        return jsonify(success=False, error="President or Justice Minister only"), 403
    try:
        rows = supabase.table("insurance_claims").select("*") \
            .order("created_at", desc=True).limit(200).execute().data or []
        pols = supabase.table("insurance_policies").select("*").execute().data or []
    except Exception:
        return _insurance_missing()

    openq = [_claim_public(r, {"checks": _verify_claim(r)}) for r in rows if (r.get("status") or "open") == "open"]
    settled = [_claim_public(r) for r in rows if (r.get("status") or "open") != "open"][:40]
    paid_total = sum(float(r.get("amount_paid") or 0) for r in rows if r.get("status") == "approved")
    by_plan = {}
    for p in pols:
        if p.get("active"):
            k = p.get("plan") or "basic"
            by_plan[k] = by_plan.get(k, 0) + 1
    return jsonify(success=True, open_claims=openq, settled=settled,
                   covered=sum(1 for p in pols if p.get("active")),
                   by_plan=by_plan, paid_total=round(paid_total, 2),
                   open_value=round(sum(float(c["amount"]) for c in openq), 2),
                   plans=INSURANCE_PLANS)


@app.route("/shield/admin/decide", methods=["POST"])
@limiter.limit("30/minute")
def shield_admin_decide():
    """Approve (in full or in part), reject, or rule a claim fraudulent."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    if not has_power(user, "claims"):
        return jsonify(success=False, error="President or Justice Minister only"), 403
    d = request.get_json() or {}
    action = (d.get("action") or "").strip()
    if action not in ("approve", "reject", "fraud"):
        return jsonify(success=False, error="Unknown action"), 400
    try:
        cid = int(d.get("claim_id"))
    except (TypeError, ValueError):
        return jsonify(success=False, error="Bad claim"), 400
    note = (d.get("note") or "").strip()[:400]

    try:
        r = supabase.table("insurance_claims").select("*").eq("id", cid).execute().data
    except Exception:
        return _insurance_missing()
    if not r:
        return jsonify(success=False, error="Claim not found"), 404
    c = r[0]
    if (c.get("status") or "open") != "open":
        return jsonify(success=False, error="That claim is already settled"), 400

    me = user["username"]
    now = _now()
    plan = PLAN_BY_KEY.get(c.get("plan") or "basic", INSURANCE_PLANS[0])

    if action == "approve":
        # The President may award less than was asked for.
        try:
            award = round(float(d.get("amount") if d.get("amount") not in (None, "") else c.get("amount") or 0), 2)
        except (TypeError, ValueError):
            return jsonify(success=False, error="Bad payout amount"), 400
        if not math.isfinite(award) or award <= 0:
            return jsonify(success=False, error="Payout must be more than zero"), 400
        if award > float(c.get("amount") or 0):
            return jsonify(success=False, error="You can't award more than was claimed."), 400
        if award > plan["cap"]:
            return jsonify(success=False,
                           error=f"That exceeds the {plan['label']} cap of {plan['cap']} CB."), 400

        # Claim the row first so a double-click can't pay twice.
        claimed = supabase.table("insurance_claims").update(
            {"status": "approved", "amount_paid": award, "decided_by": me,
             "decided_at": now.isoformat(), "decision_note": note}) \
            .eq("id", cid).eq("status", "open").execute().data
        if not claimed:
            return jsonify(success=False, error="That claim is already settled"), 400

        cas_adjust(c["username"], "balance", award, allow_negative=False)
        treasury_add(cybucks=-award, counterparty=c["username"], kind="insurance_payout")
        cas_num("insurance_policies", [("username", c["username"])], "claims_paid", award)
        cas_num("insurance_policies", [("username", c["username"])], "claims_count", 1, places=0)
        add_record(c["username"], f"Cyvashield paid out {award:g} CB on a claim.")
        log_txn("insurance", "Cyvashield", c["username"], award, "cybucks",
                note or "Insurance claim approved")
        notify(c["username"], f"✅ Your Cyvashield claim was approved — {award:g} CB paid."
                              + (f" {note}" if note else ""), "/shield")
        return jsonify(success=True, status="approved", paid=award)

    if action == "reject":
        supabase.table("insurance_claims").update(
            {"status": "rejected", "decided_by": me, "decided_at": now.isoformat(),
             "decision_note": note}).eq("id", cid).eq("status", "open").execute()
        notify(c["username"], "Your Cyvashield claim was not upheld."
                              + (f" {note}" if note else ""), "/shield")
        return jsonify(success=True, status="rejected")

    # ---- fraud: refused AND recorded against them ----
    supabase.table("insurance_claims").update(
        {"status": "fraudulent", "decided_by": me, "decided_at": now.isoformat(),
         "decision_note": note}).eq("id", cid).eq("status", "open").execute()
    add_criminal_record(c["username"], me,
                        note or f"Filed a fraudulent Cyvashield claim for {c.get('amount')} CB.")
    notify(c["username"], "⚠️ Your Cyvashield claim was ruled fraudulent and recorded "
                          "against you." + (f" {note}" if note else ""), "/shield")
    return jsonify(success=True, status="fraudulent")


@app.route("/shield/summary")
@limiter.limit("60/minute")
def shield_summary():
    """Counts for the dashboard banner and the tab badges."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    admin = has_power(user, "claims")
    try:
        mine = supabase.table("insurance_claims").select("id,status") \
            .eq("username", me).execute().data or []
        waiting = 0
        if admin:
            waiting = len(supabase.table("insurance_claims").select("id")
                          .eq("status", "open").execute().data or [])
        row = _policy_row(me)
    except Exception:
        # Not migrated yet — still answer what doesn't depend on those tables,
        # so the President keeps the claim desk that explains the problem.
        return jsonify(success=True, enabled=False, is_admin=admin, enrolled=False,
                       plan="", open_mine=0, waiting=0, open=bool(INSURANCE_OPEN))
    return jsonify(success=True, enabled=True, is_admin=admin,
                   enrolled=bool(row and row.get("active")),
                   plan=(row or {}).get("plan") or "",
                   open_mine=sum(1 for c in mine if (c.get("status") or "open") == "open"),
                   waiting=waiting, open=bool(INSURANCE_OPEN))

# ============================================================
#  CYVALEND — the national lending library
# ============================================================
#  You forgot your calculator; someone three rooms away has a spare. Put
#  what you'd lend on the shelf, borrow what you're short of, and get it
#  back. Cyvazon carries it if the two of you aren't in the same room, and
#  Cyvashield covers the owner if it never comes home.
#
#  Two rules do the real work. The OWNER confirms the return — the person
#  who is owed something is the one who says it came back, exactly as the
#  recipient confirms a parcel. And an owner may ask for a deposit, held
#  from the borrower and refunded on return, so lending something you care
#  about isn't just an act of faith.

LEND_CATEGORIES = [
    {"key": "stationery", "label": "Stationery",      "icon": "fa-pen"},
    {"key": "calculator", "label": "Calculator",      "icon": "fa-calculator"},
    {"key": "charger",    "label": "Charger or cable", "icon": "fa-plug"},
    {"key": "book",       "label": "Book or textbook", "icon": "fa-book"},
    {"key": "sports",     "label": "Sports gear",     "icon": "fa-futbol"},
    {"key": "art",        "label": "Art supplies",    "icon": "fa-palette"},
    {"key": "music",      "label": "Instrument",      "icon": "fa-music"},
    {"key": "tech",       "label": "Tech",            "icon": "fa-laptop"},
    {"key": "other",      "label": "Something else",  "icon": "fa-box"},
]
LEND_CAT_BY_KEY = {c["key"]: c for c in LEND_CATEGORIES}

LEND_CONDITIONS = ["new", "good", "worn"]

LEND_OPEN = True
LEND_MAX_DEPOSIT = 200        # ceiling on what an owner may ask for
MAX_LOAN_DAYS = 14
MAX_SHELF_ITEMS = 30          # per citizen
MAX_OPEN_BORROWS = 5          # things one citizen may have out at once


def _lend_missing():
    return jsonify(success=False,
                   error="Cyvalend isn't enabled yet — the database needs a quick update "
                         "(run migration_cyvalend.sql)."), 503


def _item_public(row, extra=None):
    cat = LEND_CAT_BY_KEY.get(row.get("category") or "other", LEND_CATEGORIES[-1])
    out = {
        "id": row.get("id"), "owner": row.get("owner"),
        "name": row.get("name") or "",
        "category": cat["key"], "category_label": cat["label"], "category_icon": cat["icon"],
        "description": row.get("description") or "",
        "image_url": row.get("image_url") or "",
        "condition": row.get("condition") or "good",
        "deposit": row.get("deposit") or 0,
        "max_days": row.get("max_days") or 1,
        "status": row.get("status") or "available",
        "times_lent": row.get("times_lent") or 0,
        "created_at": row.get("created_at"),
    }
    if extra:
        out.update(extra)
    return out


def _loan_public(row, item=None, extra=None):
    out = {
        "id": row.get("id"), "item_id": row.get("item_id"),
        "owner": row.get("owner"), "borrower": row.get("borrower"),
        "days": row.get("days") or 1, "reason": row.get("reason") or "",
        "status": row.get("status") or "requested",
        "deposit_held": row.get("deposit_held") or 0,
        "delivery_id": row.get("delivery_id"),
        "requested_at": row.get("requested_at"), "due_at": row.get("due_at"),
        "returned_at": row.get("returned_at"), "late": bool(row.get("late")),
        "note": row.get("note") or "",
        "overdue": _loan_overdue(row),
    }
    if item:
        out["item"] = _item_public(item)
    if extra:
        out.update(extra)
    return out


def _loan_overdue(row):
    if (row.get("status") or "") != "out":
        return False
    due = _parse(row.get("due_at"))
    return bool(due and _now() > due)


def _lend_stats(username):
    """How reliable is this citizen? Shown next to their name so people can
    decide whether to hand over something they care about."""
    try:
        rows = supabase.table("lend_loans").select("borrower,owner,status,late") \
            .execute().data or []
    except Exception:
        return {"borrowed": 0, "returned": 0, "late": 0, "lent": 0, "out_now": 0}
    mine_b = [r for r in rows if r.get("borrower") == username]
    mine_o = [r for r in rows if r.get("owner") == username]
    return {
        "borrowed": len([r for r in mine_b if r.get("status") in ("out", "returned")]),
        "returned": len([r for r in mine_b if r.get("status") == "returned"]),
        "late":     len([r for r in mine_b if r.get("late")]),
        "lent":     len([r for r in mine_o if r.get("status") in ("out", "returned")]),
        "out_now":  len([r for r in mine_b if r.get("status") == "out"]),
    }


# ---- routes -----------------------------------------------------------
@app.route("/cyvalend")
def cyvalend_page():
    return app.send_static_file("cyvalend.html")


@app.route("/cyvalend/config")
@limiter.limit("60/minute")
def cyvalend_config():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    return jsonify(success=True, categories=LEND_CATEGORIES, conditions=LEND_CONDITIONS,
                   open=bool(LEND_OPEN), me=user["username"],
                   max_deposit=LEND_MAX_DEPOSIT, max_days=MAX_LOAN_DAYS,
                   max_items=MAX_SHELF_ITEMS, max_borrows=MAX_OPEN_BORROWS,
                   home={"class": user.get("home_class") or "",
                         "area": user.get("home_area") or ""},
                   stats=_lend_stats(user["username"]))


@app.route("/cyvalend/shelf")
@limiter.limit("60/minute")
def cyvalend_shelf():
    """Everything on offer, plus my own items and how they're doing."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    try:
        items = supabase.table("lend_items").select("*") \
            .order("created_at", desc=True).limit(300).execute().data or []
        loans = supabase.table("lend_loans").select("*") \
            .in_("status", ["requested", "out"]).execute().data or []
    except Exception:
        return _lend_missing()

    # Who currently holds what, and what I've already asked for.
    holder = {l["item_id"]: l for l in loans if l["status"] == "out"}
    asked = {l["item_id"] for l in loans if l["status"] == "requested" and l["borrower"] == me}

    # Owner reliability, computed once rather than per item.
    rep = {}
    for it in items:
        if it["owner"] not in rep:
            rep[it["owner"]] = _lend_stats(it["owner"])

    shelf, mine = [], []
    for it in items:
        h = holder.get(it["id"])
        extra = {"asked": it["id"] in asked,
                 "borrower": h["borrower"] if h else None,
                 "due_at": h["due_at"] if h else None,
                 "overdue": _loan_overdue(h) if h else False,
                 "owner_lent": rep.get(it["owner"], {}).get("lent", 0)}
        pub = _item_public(it, extra)
        if it["owner"] == me:
            mine.append(pub)
        elif it.get("status") == "available":
            shelf.append(pub)
    return jsonify(success=True, me=me, shelf=shelf, mine=mine, open=bool(LEND_OPEN))


@app.route("/cyvalend/item", methods=["POST"])
@limiter.limit("20/minute")
def cyvalend_add_item():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    if not LEND_OPEN:
        return jsonify(success=False, error="Cyvalend is closed right now."), 403
    me = user["username"]
    d = request.get_json() or {}

    name = (d.get("name") or "").strip()[:80]
    if not name:
        return jsonify(success=False, error="What are you lending?"), 400
    category = (d.get("category") or "other").strip()
    if category not in LEND_CAT_BY_KEY:
        return jsonify(success=False, error="Unknown category"), 400
    condition = (d.get("condition") or "good").strip()
    if condition not in LEND_CONDITIONS:
        condition = "good"

    try:
        deposit = round(float(d.get("deposit") or 0), 2)
    except (TypeError, ValueError):
        deposit = 0
    if not math.isfinite(deposit) or deposit < 0:
        deposit = 0
    if deposit > LEND_MAX_DEPOSIT:
        return jsonify(success=False,
                       error=f"Deposits are capped at {LEND_MAX_DEPOSIT:g} CB."), 400

    try:
        max_days = int(d.get("max_days") or 1)
    except (TypeError, ValueError):
        max_days = 1
    max_days = max(1, min(max_days, MAX_LOAN_DAYS))

    image_url = (d.get("image_url") or "").strip()[:500]
    if image_url and not _card_url_ok(image_url):
        return jsonify(success=False, error="Use a direct https:// link for the photo."), 400

    try:
        held = supabase.table("lend_items").select("id").eq("owner", me) \
            .neq("status", "retired").execute().data or []
    except Exception:
        return _lend_missing()
    if len(held) >= MAX_SHELF_ITEMS:
        return jsonify(success=False,
                       error=f"You already have {MAX_SHELF_ITEMS} things on the shelf."), 400

    row = {"owner": me, "name": name, "category": category, "condition": condition,
           "description": (d.get("description") or "").strip()[:300],
           "image_url": image_url, "deposit": deposit, "max_days": max_days}
    try:
        item = supabase.table("lend_items").insert(row).execute().data[0]
    except Exception:
        return _lend_missing()
    return jsonify(success=True, item=_item_public(item))


@app.route("/cyvalend/item/update", methods=["POST"])
@limiter.limit("30/minute")
def cyvalend_update_item():
    """Take something off the shelf, or put it back."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    d = request.get_json() or {}
    try:
        iid = int(d.get("item_id"))
    except (TypeError, ValueError):
        return jsonify(success=False, error="Bad item"), 400
    try:
        r = supabase.table("lend_items").select("*").eq("id", iid) \
            .eq("owner", user["username"]).execute().data
    except Exception:
        return _lend_missing()
    if not r:
        return jsonify(success=False, error="That isn't yours"), 404
    item = r[0]
    if item.get("status") == "out":
        return jsonify(success=False, error="It's out on loan — wait until it's back."), 400

    want = (d.get("status") or "").strip()
    if want not in ("available", "retired"):
        return jsonify(success=False, error="Unknown status"), 400
    supabase.table("lend_items").update({"status": want}).eq("id", iid).execute()
    return jsonify(success=True, item=_item_public({**item, "status": want}))


@app.route("/cyvalend/item/remove", methods=["POST"])
@limiter.limit("20/minute")
def cyvalend_remove_item():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    try:
        iid = int((request.get_json() or {}).get("item_id"))
    except (TypeError, ValueError):
        return jsonify(success=False, error="Bad item"), 400
    try:
        r = supabase.table("lend_items").select("*").eq("id", iid) \
            .eq("owner", user["username"]).execute().data
    except Exception:
        return _lend_missing()
    if not r:
        return jsonify(success=False, error="That isn't yours"), 404
    if r[0].get("status") == "out":
        return jsonify(success=False, error="It's out on loan — wait until it's back."), 400
    supabase.table("lend_loans").delete().eq("item_id", iid).eq("status", "requested").execute()
    supabase.table("lend_items").delete().eq("id", iid).execute()
    return jsonify(success=True)


@app.route("/cyvalend/request", methods=["POST"])
@limiter.limit("20/minute")
def cyvalend_request():
    """Ask to borrow something. The owner decides."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    if not LEND_OPEN:
        return jsonify(success=False, error="Cyvalend is closed right now."), 403
    me = user["username"]
    d = request.get_json() or {}
    try:
        iid = int(d.get("item_id"))
    except (TypeError, ValueError):
        return jsonify(success=False, error="Bad item"), 400

    try:
        r = supabase.table("lend_items").select("*").eq("id", iid).execute().data
    except Exception:
        return _lend_missing()
    if not r:
        return jsonify(success=False, error="That item is gone"), 404
    item = r[0]
    if item["owner"] == me:
        return jsonify(success=False, error="It's already yours"), 400
    if item.get("status") != "available":
        return jsonify(success=False, error="That's not on the shelf right now"), 409

    try:
        days = int(d.get("days") or 1)
    except (TypeError, ValueError):
        days = 1
    days = max(1, min(days, int(item.get("max_days") or 1)))

    mine = supabase.table("lend_loans").select("id,status,item_id") \
        .eq("borrower", me).in_("status", ["requested", "out"]).execute().data or []
    if any(l["item_id"] == iid for l in mine):
        return jsonify(success=False, error="You've already asked for that one"), 400
    if len([l for l in mine if l["status"] == "out"]) >= MAX_OPEN_BORROWS:
        return jsonify(success=False,
                       error=f"You already have {MAX_OPEN_BORROWS} things out. "
                             "Return something first."), 400

    # A deposit is only meaningful if the borrower can actually cover it.
    deposit = float(item.get("deposit") or 0)
    if deposit > 0 and (user.get("balance") or 0) < deposit:
        return jsonify(success=False,
                       error=f"That needs a {deposit:g} CB deposit and you don't have it."), 400

    loan = {"item_id": iid, "owner": item["owner"], "borrower": me, "days": days,
            "reason": (d.get("reason") or "").strip()[:200]}
    try:
        made = supabase.table("lend_loans").insert(loan).execute().data[0]
    except Exception:
        return _lend_missing()
    notify(item["owner"], f"\U0001F91D {me} would like to borrow your {item['name']}.",
           "/cyvalend?tab=lending")
    return jsonify(success=True, loan=_loan_public(made, item))


@app.route("/cyvalend/decide", methods=["POST"])
@limiter.limit("30/minute")
def cyvalend_decide():
    """The owner says yes or no. Saying yes takes the deposit and starts the clock."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    d = request.get_json() or {}
    action = (d.get("action") or "").strip()
    if action not in ("approve", "decline", "cancel"):
        return jsonify(success=False, error="Unknown action"), 400
    try:
        lid = int(d.get("loan_id"))
    except (TypeError, ValueError):
        return jsonify(success=False, error="Bad request"), 400

    try:
        r = supabase.table("lend_loans").select("*").eq("id", lid).execute().data
    except Exception:
        return _lend_missing()
    if not r:
        return jsonify(success=False, error="Request not found"), 404
    loan = r[0]
    if loan["status"] != "requested":
        return jsonify(success=False, error="That request is already settled"), 400

    if action == "cancel":
        if loan["borrower"] != me:
            return jsonify(success=False, error="Only the borrower can withdraw"), 403
        supabase.table("lend_loans").update(
            {"status": "cancelled", "decided_at": _now().isoformat()}).eq("id", lid).execute()
        return jsonify(success=True, status="cancelled")

    if loan["owner"] != me:
        return jsonify(success=False, error="That isn't your item"), 403

    it = supabase.table("lend_items").select("*").eq("id", loan["item_id"]).execute().data
    item = it[0] if it else None
    if not item:
        return jsonify(success=False, error="That item is gone"), 404

    if action == "decline":
        supabase.table("lend_loans").update(
            {"status": "declined", "decided_at": _now().isoformat(),
             "note": (d.get("note") or "").strip()[:200]}).eq("id", lid).execute()
        notify(loan["borrower"], f"{me} can't lend you the {item['name']} right now.", "/cyvalend")
        return jsonify(success=True, status="declined")

    # ---- approve ----
    if item.get("status") != "available":
        return jsonify(success=False, error="It's already out on loan"), 409

    deposit = float(item.get("deposit") or 0)
    if deposit > 0 and not cas_adjust(loan["borrower"], "balance", -deposit):
        return jsonify(success=False,
                       error=f"{loan['borrower']} can't cover the {deposit:g} CB deposit."), 400

    now = _now()
    due = now + timedelta(days=float(loan.get("days") or 1))
    # Claim the item conditionally, so two approvals can't both win.
    took = supabase.table("lend_items").update({"status": "out"}) \
        .eq("id", item["id"]).eq("status", "available").execute().data
    if not took:
        if deposit > 0:
            cas_adjust(loan["borrower"], "balance", deposit, allow_negative=True)
        return jsonify(success=False, error="It's already out on loan"), 409

    supabase.table("lend_loans").update(
        {"status": "out", "decided_at": now.isoformat(), "due_at": due.isoformat(),
         "deposit_held": deposit}).eq("id", lid).execute()
    # Any other outstanding request for the same item is now moot.
    supabase.table("lend_loans").update({"status": "declined", "decided_at": now.isoformat()}) \
        .eq("item_id", item["id"]).eq("status", "requested").execute()

    parcel = None
    if d.get("deliver"):
        parcel = raise_parcel("lend", lid, item["name"], me, loan["borrower"], me,
                              notes="Cyvalend loan — please return it by "
                                    + due.strftime("%d %b"))
        if parcel:
            supabase.table("lend_loans").update({"delivery_id": parcel["id"]}).eq("id", lid).execute()

    add_record(loan["borrower"], f"Borrowed {me}'s {item['name']} via Cyvalend.")
    notify(loan["borrower"],
           f"✅ {me} lent you the {item['name']}. Due back "
           f"{due.strftime('%d %b')}." + (f" {deposit:g} CB deposit held." if deposit else ""),
           "/cyvalend?tab=borrowed")
    return jsonify(success=True, status="out", due_at=due.isoformat(),
                   deposit=deposit, delivery=(_delivery_public(parcel) if parcel else None))


@app.route("/cyvalend/return", methods=["POST"])
@limiter.limit("30/minute")
def cyvalend_return():
    """The OWNER confirms it came back. Only they can close a loan — otherwise
    a borrower could mark everything returned and keep the lot."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    try:
        lid = int((request.get_json() or {}).get("loan_id"))
    except (TypeError, ValueError):
        return jsonify(success=False, error="Bad loan"), 400

    try:
        r = supabase.table("lend_loans").select("*").eq("id", lid).execute().data
    except Exception:
        return _lend_missing()
    if not r:
        return jsonify(success=False, error="Loan not found"), 404
    loan = r[0]
    if loan["owner"] != me:
        return jsonify(success=False,
                       error="Only the owner can confirm something came back."), 403
    if loan["status"] != "out":
        return jsonify(success=False, error="That loan isn't open"), 400

    now = _now()
    late = _loan_overdue(loan)
    closed = supabase.table("lend_loans").update(
        {"status": "returned", "returned_at": now.isoformat(), "late": late}) \
        .eq("id", lid).eq("status", "out").execute().data
    if not closed:
        return jsonify(success=False, error="That loan is already closed"), 400

    supabase.table("lend_items").update({"status": "available"}).eq("id", loan["item_id"]).execute()
    cas_num("lend_items", [("id", loan["item_id"])], "times_lent", 1, places=0)

    # The deposit was security, not a fee — it goes back either way.
    dep = float(loan.get("deposit_held") or 0)
    if dep > 0:
        cas_adjust(loan["borrower"], "balance", dep, allow_negative=True)

    notify(loan["borrower"],
           f"✅ {me} confirmed the return."
           + (f" Your {dep:g} CB deposit is back." if dep else "")
           + (" It was late." if late else ""), "/cyvalend")
    add_record(loan["borrower"],
               f"Returned {me}'s item via Cyvalend" + (" (late)." if late else "."))
    return jsonify(success=True, status="returned", late=late, deposit_refunded=dep)


@app.route("/cyvalend/loans")
@limiter.limit("60/minute")
def cyvalend_loans():
    """What I've borrowed, and what people want to borrow from me."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    try:
        rows = supabase.table("lend_loans").select("*") \
            .order("requested_at", desc=True).limit(200).execute().data or []
    except Exception:
        return _lend_missing()
    rows = [r for r in rows if me in (r.get("owner"), r.get("borrower"))]
    ids = list({r["item_id"] for r in rows})
    items = {}
    if ids:
        for it in (supabase.table("lend_items").select("*").in_("id", ids).execute().data or []):
            items[it["id"]] = it

    borrowed = [_loan_public(r, items.get(r["item_id"])) for r in rows if r["borrower"] == me]
    lending  = [_loan_public(r, items.get(r["item_id"])) for r in rows if r["owner"] == me]
    return jsonify(success=True, me=me, borrowed=borrowed, lending=lending,
                   stats=_lend_stats(me))


@app.route("/cyvalend/summary")
@limiter.limit("60/minute")
def cyvalend_summary():
    """Counts for the dashboard banner and the tab badges."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    try:
        rows = supabase.table("lend_loans").select("owner,borrower,status,due_at") \
            .in_("status", ["requested", "out"]).execute().data or []
        shelf = supabase.table("lend_items").select("id").eq("status", "available") \
            .neq("owner", me).execute().data or []
    except Exception:
        return jsonify(success=True, enabled=False, requests=0, out=0,
                       overdue=0, due_soon=0, shelf=0, open=bool(LEND_OPEN))
    mine_out = [r for r in rows if r["borrower"] == me and r["status"] == "out"]
    soon = 0
    for r in mine_out:
        due = _parse(r.get("due_at"))
        if due and 0 <= (due - _now()).total_seconds() <= 86400:
            soon += 1
    return jsonify(success=True, enabled=True, open=bool(LEND_OPEN),
                   requests=sum(1 for r in rows if r["owner"] == me and r["status"] == "requested"),
                   out=len(mine_out),
                   overdue=sum(1 for r in mine_out if _loan_overdue(r)),
                   awaiting_return=sum(1 for r in rows
                                       if r["owner"] == me and r["status"] == "out"),
                   due_soon=soon, shelf=len(shelf))

# ============================================================
#  THE ARMOURY — ordnance for the Cyvathon Armed Forces
# ============================================================
#  Why the Republic pays 400 CB for a G2:
#
#  Cyvathon fields no conventional weapons. Its entire defensive posture
#  rests on the pen launcher, and the standard munition is the G2 — the
#  barrel is the right gauge, the clip gives it spin, and it flies true.
#  The Republic has no pen foundry and cannot import under present trade
#  terms, so every round in the armoury came out of a citizen's pencil
#  case. An unarmed Corps is the one thing the War Room cannot plan
#  around.
#
#  So the rate is a war-effort rate, not a market one. The Republic would
#  far rather overpay its own citizens than send the Corps out short of
#  ammunition. Doctrine remains "Defence, not invasion" — the armoury
#  exists to make invading Cyvathon a bad idea, nothing more.
#
#  Armoury stock counts toward GDP because materiel is national property.
#
#  Nothing is paid on a pledge. A citizen says what they're handing in,
#  the rounds go to the Quartermaster (Cyvazon will carry them), and the
#  Quartermaster logs what actually arrived — otherwise "I donated forty
#  pens" would be a money printer rather than an armoury.

# Every round goes to one person: the Quartermaster, who holds the armoury.
# Ordnance has to be physically handed to somebody, and a state account
# can't take delivery of a pen.
PEN_REGISTRAR = (os.environ.get("PEN_REGISTRAR", "Prathyay").strip() or "Prathyay")

PEN_RATE = 400          # CB per live round — the war-effort rate, set by the President
PEN_OPEN = True
# Ordnance grades. A pen that still writes is a live round; a dry one flies
# perfectly well but can't sign a field order, so it's issued for drill.
PEN_CONDITIONS = [
    {"key": "working", "label": "Live round",  "share": 1.0,
     "blurb": "Writes and flies. Full front-line rate."},
    {"key": "dry",     "label": "Drill round", "share": 0.25,
     "blurb": "Out of ink but still flies true — issued for training."},
    {"key": "broken",  "label": "Salvage",     "share": 0.1,
     "blurb": "Cracked barrel or missing clip. Stripped for spares."},
]
PEN_COND_BY_KEY = {c["key"]: c for c in PEN_CONDITIONS}

MAX_PENS_PER_PLEDGE = 20
MAX_OPEN_PLEDGES    = 3


def is_pen_registrar(user):
    """The Quartermaster holds the armoury. The Defence Minister works the desk
    as a delegated duty, and the President can always reach it, so resupply
    doesn't stall if the Quartermaster is away."""
    if not user:
        return False
    return (user["username"] == PEN_REGISTRAR
            or is_treasury_admin(user)
            or has_power(user, "armoury"))


def _pen_missing():
    return jsonify(success=False,
                   error="The Armoury isn't enabled yet — the database needs a quick "
                         "update (run migration_pen_reserve.sql)."), 503


def pen_value(count, condition, rate=None):
    """What the Armoury owes for `count` rounds of this grade."""
    c = PEN_COND_BY_KEY.get(condition or "working", PEN_CONDITIONS[0])
    return round(max(0, int(count or 0)) * float(rate if rate is not None else PEN_RATE) * c["share"], 2)


def _pen_public(row):
    cond = PEN_COND_BY_KEY.get(row.get("condition") or "working", PEN_CONDITIONS[0])
    return {
        "id": row.get("id"), "username": row.get("username"),
        "count": row.get("count") or 0,
        "condition": cond["key"], "condition_label": cond["label"], "share": cond["share"],
        "note": row.get("note") or "", "status": row.get("status") or "pledged",
        "rate": row.get("rate") or PEN_RATE,
        "counted": row.get("counted") or 0,
        "amount_paid": row.get("amount_paid") or 0,
        "expected": pen_value(row.get("count"), row.get("condition"), row.get("rate")),
        "delivery_id": row.get("delivery_id"),
        "decided_by": row.get("decided_by"), "decided_at": row.get("decided_at"),
        "decision_note": row.get("decision_note") or "",
        "created_at": row.get("created_at"),
    }


def _reserve_holdings():
    try:
        return int(get_treasury().get("pens") or 0)
    except Exception:
        return 0


# ---- routes -----------------------------------------------------------
@app.route("/pens")
def pens_page():
    return app.send_static_file("pens.html")


@app.route("/pens/config")
@limiter.limit("60/minute")
def pens_config():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    held = _reserve_holdings()
    try:
        rows = supabase.table("pen_donations").select("username,status,counted,amount_paid") \
            .execute().data or []
    except Exception:
        rows = []
    mine = [r for r in rows if r["username"] == user["username"]]
    return jsonify(success=True, rate=PEN_RATE, open=bool(PEN_OPEN),
                   conditions=PEN_CONDITIONS, me=user["username"],
                   registrar=PEN_REGISTRAR,
                   is_admin=is_pen_registrar(user),
                   max_per_pledge=MAX_PENS_PER_PLEDGE, max_open=MAX_OPEN_PLEDGES,
                   reserve_pens=held,
                   reserve_value=round(held * PEN_RATE, 2),
                   donors=len({r["username"] for r in rows if r["status"] == "received"}),
                   my_pens=sum(r.get("counted") or 0 for r in mine if r["status"] == "received"),
                   my_earned=round(sum(float(r.get("amount_paid") or 0) for r in mine), 2))


@app.route("/pens/pledge", methods=["POST"])
@limiter.limit("15/minute")
def pens_pledge():
    """Declare rounds you're handing in. Payment follows the Quartermaster's count."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    if not PEN_OPEN:
        return jsonify(success=False, error="The Armoury isn't taking handovers right now."), 403
    me = user["username"]
    d = request.get_json() or {}

    try:
        count = int(d.get("count") or 0)
    except (TypeError, ValueError):
        count = 0
    if count < 1:
        return jsonify(success=False, error="How many rounds?"), 400
    if count > MAX_PENS_PER_PLEDGE:
        return jsonify(success=False,
                       error=f"{MAX_PENS_PER_PLEDGE} rounds per handover — "
                             "split a bigger haul across several."), 400

    condition = (d.get("condition") or "working").strip()
    if condition not in PEN_COND_BY_KEY:
        return jsonify(success=False, error="Unknown condition"), 400

    try:
        openp = supabase.table("pen_donations").select("id").eq("username", me) \
            .eq("status", "pledged").execute().data or []
    except Exception:
        return _pen_missing()
    if len(openp) >= MAX_OPEN_PLEDGES:
        return jsonify(success=False,
                       error=f"You already have {MAX_OPEN_PLEDGES} handovers waiting to be "
                             "logged in. Deliver those first."), 400

    row = {"username": me, "count": count, "condition": condition, "rate": PEN_RATE,
           "note": (d.get("note") or "").strip()[:200]}
    try:
        made = supabase.table("pen_donations").insert(row).execute().data[0]
    except Exception:
        return _pen_missing()

    parcel = None
    if d.get("deliver") and me != PEN_REGISTRAR:
        # Cyvazon carries them straight to the Registrar, so a donor doesn't
        # have to go and find them.
        parcel = raise_parcel("pens", made["id"],
                              f"{count} G2 round(s) for the Armoury",
                              me, PEN_REGISTRAR, me,
                              notes="Ordnance for the Corps — to be logged in.")
        if parcel:
            supabase.table("pen_donations").update({"delivery_id": parcel["id"]}) \
                .eq("id", made["id"]).execute()

    notify(PEN_REGISTRAR,
           f"\U0001F58A️ {me} is handing in {count} G2 round(s) for the Armoury.",
           "/pens?tab=registry")
    return jsonify(success=True, donation=_pen_public(made),
                   delivery=(_delivery_public(parcel) if parcel else None))


@app.route("/pens/cancel", methods=["POST"])
@limiter.limit("20/minute")
def pens_cancel():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    try:
        did = int((request.get_json() or {}).get("donation_id"))
    except (TypeError, ValueError):
        return jsonify(success=False, error="Bad donation"), 400
    try:
        r = supabase.table("pen_donations").select("*").eq("id", did).execute().data
    except Exception:
        return _pen_missing()
    if not r:
        return jsonify(success=False, error="Donation not found"), 404
    if r[0]["username"] != user["username"]:
        return jsonify(success=False, error="That isn't your donation"), 403
    if r[0]["status"] != "pledged":
        return jsonify(success=False, error="That donation is already settled"), 400
    supabase.table("pen_donations").update(
        {"status": "cancelled", "decided_at": _now().isoformat()}).eq("id", did).execute()
    return jsonify(success=True, status="cancelled")


@app.route("/pens/mine")
@limiter.limit("60/minute")
def pens_mine():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    try:
        rows = supabase.table("pen_donations").select("*").eq("username", user["username"]) \
            .order("created_at", desc=True).limit(60).execute().data or []
    except Exception:
        return _pen_missing()
    return jsonify(success=True, donations=[_pen_public(r) for r in rows])


@app.route("/pens/board")
@limiter.limit("60/minute")
def pens_board():
    """Who has kept the Corps supplied."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    try:
        rows = supabase.table("pen_donations").select("username,counted,amount_paid,status") \
            .eq("status", "received").execute().data or []
    except Exception:
        return _pen_missing()
    tally = {}
    for r in rows:
        t = tally.setdefault(r["username"], {"username": r["username"], "pens": 0, "earned": 0})
        t["pens"] += r.get("counted") or 0
        t["earned"] += float(r.get("amount_paid") or 0)
    out = sorted(tally.values(), key=lambda t: (-t["pens"], t["username"].lower()))
    for t in out:
        t["earned"] = round(t["earned"], 2)
        t["me"] = t["username"] == user["username"]
    return jsonify(success=True, donors=out[:50], reserve_pens=_reserve_holdings())


@app.route("/pens/registry")
@limiter.limit("60/minute")
def pens_registry():
    """The Quartermaster's desk: rounds pledged but not yet logged in."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    if not is_pen_registrar(user):
        return jsonify(success=False, error="Quartermaster only"), 403
    try:
        rows = supabase.table("pen_donations").select("*") \
            .order("created_at", desc=True).limit(200).execute().data or []
    except Exception:
        return _pen_missing()
    pledged = [_pen_public(r) for r in rows if (r.get("status") or "pledged") == "pledged"]
    settled = [_pen_public(r) for r in rows if (r.get("status") or "pledged") != "pledged"][:40]
    paid = sum(float(r.get("amount_paid") or 0) for r in rows if r.get("status") == "received")
    return jsonify(success=True, pledged=pledged, settled=settled,
                   reserve_pens=_reserve_holdings(), rate=PEN_RATE,
                   paid_total=round(paid, 2),
                   owed=round(sum(p["expected"] for p in pledged), 2))


@app.route("/pens/registry/decide", methods=["POST"])
@limiter.limit("30/minute")
def pens_registry_decide():
    """Log rounds into the armoury and pay for them, or turn the handover away.
    The count is the Quartermaster's, not the donor's — that is the whole
    difference between an armoury and a rumour."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    if not is_pen_registrar(user):
        return jsonify(success=False, error="Quartermaster only"), 403
    d = request.get_json() or {}
    action = (d.get("action") or "").strip()
    if action not in ("receive", "reject"):
        return jsonify(success=False, error="Unknown action"), 400
    try:
        did = int(d.get("donation_id"))
    except (TypeError, ValueError):
        return jsonify(success=False, error="Bad donation"), 400
    note = (d.get("note") or "").strip()[:300]

    try:
        r = supabase.table("pen_donations").select("*").eq("id", did).execute().data
    except Exception:
        return _pen_missing()
    if not r:
        return jsonify(success=False, error="Donation not found"), 404
    don = r[0]
    if (don.get("status") or "pledged") != "pledged":
        return jsonify(success=False, error="That donation is already settled"), 400

    me, now = user["username"], _now()

    if action == "reject":
        supabase.table("pen_donations").update(
            {"status": "rejected", "decided_by": me, "decided_at": now.isoformat(),
             "decision_note": note}).eq("id", did).eq("status", "pledged").execute()
        notify(don["username"], "The Quartermaster didn't accept your handover."
                                + (f" {note}" if note else ""), "/pens")
        return jsonify(success=True, status="rejected")

    # ---- receive: the Quartermaster's count is what gets paid ----
    try:
        counted = int(d.get("counted") if d.get("counted") not in (None, "") else don.get("count") or 0)
    except (TypeError, ValueError):
        return jsonify(success=False, error="Bad count"), 400
    if counted < 0:
        return jsonify(success=False, error="Count can't be negative"), 400
    if counted > int(don.get("count") or 0):
        return jsonify(success=False,
                       error="You can't log in more rounds than were handed in."), 400

    condition = (d.get("condition") or don.get("condition") or "working").strip()
    if condition not in PEN_COND_BY_KEY:
        condition = don.get("condition") or "working"
    award = pen_value(counted, condition, don.get("rate"))

    claimed = supabase.table("pen_donations").update(
        {"status": "received", "counted": counted, "condition": condition,
         "amount_paid": award, "decided_by": me, "decided_at": now.isoformat(),
         "decision_note": note}).eq("id", did).eq("status", "pledged").execute().data
    if not claimed:
        return jsonify(success=False, error="That donation is already settled"), 400

    if counted:
        # Into the armoury, and out of the Treasury's cash.
        cas_num("treasury", [("id", 1)], "pens", counted, places=0)
        _gdp_cache["v"] = None          # materiel counts toward national wealth
    if award:
        cas_adjust(don["username"], "balance", award, allow_negative=False)
        treasury_add(cybucks=-award, counterparty=don["username"], kind="pen_reserve")
        log_txn("pens", "Cyvathon Armoury", don["username"], award, "cybucks",
                f"{counted} G2 round(s) logged into the Armoury")
    add_record(don["username"],
               f"Armed the Republic with {counted} G2 round(s) for {award:g} CB.")
    notify(don["username"],
           f"\U0001F58A️ The Quartermaster logged in {counted} round(s) — {award:g} CB paid. "
           "The Corps thanks you." + (f" {note}" if note else ""), "/pens")
    return jsonify(success=True, status="received", counted=counted, paid=award,
                   reserve_pens=_reserve_holdings())


@app.route("/pens/summary")
@limiter.limit("60/minute")
def pens_summary():
    """Counts for the dashboard banner and the tab badges."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    admin = is_pen_registrar(user)
    try:
        rows = supabase.table("pen_donations").select("username,status").execute().data or []
    except Exception:
        return jsonify(success=True, enabled=False, is_admin=admin, rate=PEN_RATE,
                       registrar=PEN_REGISTRAR, waiting=0, mine_open=0,
                       reserve_pens=0, open=bool(PEN_OPEN))
    return jsonify(success=True, enabled=True, is_admin=admin, rate=PEN_RATE,
                   registrar=PEN_REGISTRAR, open=bool(PEN_OPEN),
                   waiting=sum(1 for r in rows if r["status"] == "pledged") if admin else 0,
                   mine_open=sum(1 for r in rows
                                 if r["username"] == me and r["status"] == "pledged"),
                   donated=sum(1 for r in rows
                               if r["username"] == me and r["status"] == "received"),
                   reserve_pens=_reserve_holdings())

# ============================================================
#  CABINET POWERS — what a minister may actually do
# ============================================================
#  Two kinds of authority, and the distinction matters.
#
#  DUTIES are delegated outright. The Defence Minister works the Armoury
#  desk; the Transport Minister vets couriers; the Justice Minister rules
#  on insurance claims. These are jobs, not decisions — making the
#  President countersign every logged pen would just move the bottleneck
#  rather than share the load.
#
#  POLICY is proposed, never imposed. A minister who wants to move a
#  national lever — the tax rate, the GDP multiplier, what the Armoury
#  pays — raises a proposal, and nothing changes until the President
#  assents. That keeps the levers in one pair of hands while still
#  letting a minister govern their own brief.
#
#  Ministries are named freely by the President, so a portfolio is matched
#  on the name: anything called "Ministry of Defence", "War Office" or
#  "Defence & Security" picks up the defence brief.

# Every lever a minister may ask to move, with the bounds the President's
# own admin panel would enforce anyway.
LEVER_SPECS = {
    "vat_rate":        {"label": "VAT rate",              "min": 0,   "max": 0.5,  "pct": True},
    "gdp_multiplier":  {"label": "GDP multiplier",        "min": 0.1, "max": 20},
    "savings_rate":    {"label": "Savings interest",      "min": 0,   "max": 0.5,  "pct": True},
    "bond_rate":       {"label": "Bond return",           "min": 0,   "max": 1,    "pct": True},
    "bond_days":       {"label": "Bond maturity (days)",  "min": 1,   "max": 365},
    "company_fee":     {"label": "Company founding fee",  "min": 0,   "max": 100000},
    "loan_max":        {"label": "Maximum loan",          "min": 0,   "max": 100000},
    "loan_days":       {"label": "Loan term (days)",      "min": 1,   "max": 365},
    "starting_grant":  {"label": "New citizen grant",     "min": 0,   "max": 100000},
    "tax_period_days": {"label": "Tax period (days)",     "min": 1,   "max": 365},
    "salary_period_days": {"label": "Salary period (days)", "min": 1, "max": 365},
    "courier_wage":    {"label": "Courier wage",          "min": 0,   "max": 100000},
    "delivery_levy":   {"label": "Delivery levy",         "min": 0,   "max": 0.5,  "pct": True},
    "insurance_levy":  {"label": "Insurance levy",        "min": 0,   "max": 0.5,  "pct": True},
    "lend_max_deposit": {"label": "Cyvalend deposit cap", "min": 0,   "max": 100000},
    "pen_rate":        {"label": "Armoury rate per round", "min": 0,  "max": 100000},
}

# The portfolios. `duties` are carried out directly; `levers` may only be
# proposed. Order matters: the first name match wins.
PORTFOLIOS = [
    {"key": "defence", "label": "Defence", "icon": "fa-shield-halved", "color": "#d9b45c",
     "match": ["defence", "defense", "war", "military", "army", "armed forces", "ordnance"],
     "duties": [{"key": "armoury", "label": "Work the Armoury desk",
                 "blurb": "Log G2 rounds into the armoury and pay citizens for them, "
                          "or turn a handover away.", "href": "/pens?tab=registry"}],
     "levers": ["pen_rate"]},

    {"key": "finance", "label": "Finance", "icon": "fa-coins", "color": "#58c4ff",
     "match": ["finance", "treasury", "economy", "economic", "exchequer", "revenue"],
     "duties": [],
     "levers": ["vat_rate", "gdp_multiplier", "savings_rate", "bond_rate", "bond_days",
                "company_fee", "loan_max", "loan_days", "starting_grant",
                "tax_period_days", "salary_period_days"]},

    {"key": "transport", "label": "Transport & Logistics", "icon": "fa-truck-fast", "color": "#ff9900",
     "match": ["transport", "logistic", "delivery", "post", "infrastructure", "cyvazon"],
     "duties": [{"key": "couriers", "label": "Vet Cyvazon couriers",
                 "blurb": "Approve, turn away and revoke couriers on the delivery "
                          "admin panel.", "href": "/cyvazon?tab=admin"}],
     "levers": ["courier_wage", "delivery_levy"]},

    {"key": "justice", "label": "Justice & Home Affairs", "icon": "fa-scale-balanced", "color": "#1fd6a6",
     "match": ["justice", "home", "interior", "insurance", "welfare", "citizen"],
     "duties": [{"key": "claims", "label": "Rule on Cyvashield claims",
                 "blurb": "Approve, reduce, reject or rule fraudulent on national "
                          "insurance claims.", "href": "/shield?tab=admin"}],
     "levers": ["insurance_levy", "lend_max_deposit"]},
]
PORTFOLIO_BY_KEY = {p["key"]: p for p in PORTFOLIOS}


def _portfolio_for_name(name):
    n = (name or "").lower()
    for p in PORTFOLIOS:
        if any(m in n for m in p["match"]):
            return p
    return None


_portfolio_cache = {}       # username -> (portfolio_key_or_None, ministry_name, ts)


def ministry_portfolio(user):
    """The brief this citizen holds, or None. Cached briefly — this is
    checked on every gated request."""
    if not user:
        return None, None
    u = user["username"]
    hit = _portfolio_cache.get(u)
    if hit and time() - hit[2] < FLAG_TTL:
        return (PORTFOLIO_BY_KEY.get(hit[0]) if hit[0] else None), hit[1]
    port, mname = None, None
    try:
        rows = supabase.table("ministries").select("name,minister") \
            .eq("minister", u).execute().data or []
    except Exception:
        rows = []
    for r in rows:
        p = _portfolio_for_name(r.get("name"))
        if p:
            port, mname = p, r.get("name")
            break
    _portfolio_cache[u] = (port["key"] if port else None, mname, time())
    return port, mname


def has_power(user, duty):
    """Can this citizen carry out `duty`? The President always can."""
    if not user:
        return False
    if is_treasury_admin(user):
        return True
    port, _ = ministry_portfolio(user)
    return bool(port and any(d["key"] == duty for d in port["duties"]))


def _lever_now(field):
    """Current value of a lever, read from the live globals."""
    name = _CONFIG_KEYS.get(field)
    return globals().get(name) if name else None


def _proposal_public(row):
    spec = LEVER_SPECS.get(row.get("field") or "", {})
    return {
        "id": row.get("id"), "ministry": row.get("ministry") or "",
        "portfolio": row.get("portfolio") or "", "minister": row.get("minister"),
        "field": row.get("field"), "field_label": spec.get("label") or row.get("field"),
        "is_pct": bool(spec.get("pct")),
        "current_value": row.get("current_value"),
        "proposed_value": row.get("proposed_value"),
        "reason": row.get("reason") or "", "status": row.get("status") or "pending",
        "decided_by": row.get("decided_by"), "decided_at": row.get("decided_at"),
        "decision_note": row.get("decision_note") or "",
        "created_at": row.get("created_at"),
    }


def _cabinet_missing():
    return jsonify(success=False,
                   error="Cabinet powers aren't enabled yet — the database needs a quick "
                         "update (run migration_cabinet_powers.sql)."), 503


# ---- routes -----------------------------------------------------------
@app.route("/cabinet")
def cabinet_page():
    return app.send_static_file("cabinet.html")


@app.route("/cabinet/powers")
@limiter.limit("60/minute")
def cabinet_powers():
    """What this citizen may do, and which levers they may ask to move."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    port, mname = ministry_portfolio(user)
    prez = is_treasury_admin(user)

    levers = []
    for f in (port["levers"] if port else []):
        spec = LEVER_SPECS.get(f) or {}
        levers.append({"field": f, "label": spec.get("label") or f,
                       "min": spec.get("min"), "max": spec.get("max"),
                       "pct": bool(spec.get("pct")), "current": _lever_now(f)})

    return jsonify(success=True, me=user["username"], is_president=prez,
                   portfolio=(port["key"] if port else ""),
                   portfolio_label=(port["label"] if port else ""),
                   portfolio_icon=(port["icon"] if port else ""),
                   portfolio_color=(port["color"] if port else ""),
                   ministry=mname or "",
                   duties=(port["duties"] if port else []),
                   levers=levers,
                   all_portfolios=[{"key": p["key"], "label": p["label"], "icon": p["icon"],
                                    "color": p["color"],
                                    "duties": [d["label"] for d in p["duties"]],
                                    "levers": [LEVER_SPECS.get(f, {}).get("label") or f
                                               for f in p["levers"]]}
                                   for p in PORTFOLIOS])


@app.route("/cabinet/propose", methods=["POST"])
@limiter.limit("15/minute")
def cabinet_propose():
    """A minister asks to move a lever in their own brief."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    port, mname = ministry_portfolio(user)
    if not port:
        return jsonify(success=False,
                       error="You don't hold a ministry with policy powers."), 403

    d = request.get_json() or {}
    field = (d.get("field") or "").strip()
    if field not in port["levers"]:
        return jsonify(success=False,
                       error=f"The {port['label']} brief doesn't cover that lever."), 403
    spec = LEVER_SPECS[field]

    try:
        value = round(float(d.get("value")), 4)
    except (TypeError, ValueError):
        return jsonify(success=False, error="Give a number"), 400
    if not math.isfinite(value):
        return jsonify(success=False, error="Give a real number"), 400
    if value < spec["min"] or value > spec["max"]:
        return jsonify(success=False,
                       error=f"{spec['label']} must be between {spec['min']} and {spec['max']}."), 400

    reason = (d.get("reason") or "").strip()[:400]
    if len(reason) < 10:
        return jsonify(success=False,
                       error="Tell the President why (at least 10 characters)."), 400

    current = _lever_now(field)
    if current is not None and abs(float(current) - value) < 1e-9:
        return jsonify(success=False, error=f"{spec['label']} is already that."), 400

    try:
        openp = supabase.table("policy_proposals").select("id,field") \
            .eq("minister", me).eq("status", "pending").execute().data or []
    except Exception:
        return _cabinet_missing()
    if any(p["field"] == field for p in openp):
        return jsonify(success=False,
                       error=f"You already have a proposal on {spec['label']} awaiting assent."), 400
    if len(openp) >= 5:
        return jsonify(success=False,
                       error="You have five proposals awaiting assent already."), 400

    row = {"ministry": mname or "", "portfolio": port["key"], "minister": me,
           "field": field, "current_value": current, "proposed_value": value,
           "reason": reason}
    try:
        made = supabase.table("policy_proposals").insert(row).execute().data[0]
    except Exception:
        return _cabinet_missing()

    add_record(me, f"Proposed moving {spec['label']} to {value:g}.")
    for boss in sorted(TREASURY_ADMINS):
        notify(boss, f"\U0001F3DB️ {me} ({port['label']}) proposes {spec['label']} "
                     f"→ {value:g}. Your assent is needed.", "/cabinet?tab=assent")
    return jsonify(success=True, proposal=_proposal_public(made))


@app.route("/cabinet/proposals")
@limiter.limit("60/minute")
def cabinet_proposals():
    """My proposals, and — for the President — everything awaiting assent."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me, prez = user["username"], is_treasury_admin(user)
    try:
        rows = supabase.table("policy_proposals").select("*") \
            .order("created_at", desc=True).limit(150).execute().data or []
    except Exception:
        return _cabinet_missing()
    mine = [_proposal_public(r) for r in rows if r.get("minister") == me]
    pending = [_proposal_public(r) for r in rows
               if (r.get("status") or "pending") == "pending"] if prez else []
    settled = [_proposal_public(r) for r in rows
               if (r.get("status") or "pending") != "pending"][:30] if prez else []
    return jsonify(success=True, me=me, is_president=prez,
                   mine=mine, pending=pending, settled=settled)


@app.route("/cabinet/decide", methods=["POST"])
@limiter.limit("30/minute")
def cabinet_decide():
    """The President assents to a proposal, or refuses it. A minister may
    withdraw their own before it is decided."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    d = request.get_json() or {}
    action = (d.get("action") or "").strip()
    if action not in ("approve", "reject", "withdraw"):
        return jsonify(success=False, error="Unknown action"), 400
    try:
        pid = int(d.get("proposal_id"))
    except (TypeError, ValueError):
        return jsonify(success=False, error="Bad proposal"), 400
    note = (d.get("note") or "").strip()[:300]

    try:
        r = supabase.table("policy_proposals").select("*").eq("id", pid).execute().data
    except Exception:
        return _cabinet_missing()
    if not r:
        return jsonify(success=False, error="Proposal not found"), 404
    prop = r[0]
    if (prop.get("status") or "pending") != "pending":
        return jsonify(success=False, error="That proposal is already settled"), 400

    now = _now()
    spec = LEVER_SPECS.get(prop["field"], {"label": prop["field"]})

    if action == "withdraw":
        if prop["minister"] != me:
            return jsonify(success=False, error="Only the minister can withdraw it"), 403
        supabase.table("policy_proposals").update(
            {"status": "withdrawn", "decided_at": now.isoformat()}).eq("id", pid).execute()
        return jsonify(success=True, status="withdrawn")

    if not is_treasury_admin(user):
        return jsonify(success=False, error="Only the President can assent"), 403

    if action == "reject":
        supabase.table("policy_proposals").update(
            {"status": "rejected", "decided_by": me, "decided_at": now.isoformat(),
             "decision_note": note}).eq("id", pid).eq("status", "pending").execute()
        notify(prop["minister"],
               f"The President did not assent to your {spec['label']} proposal."
               + (f" {note}" if note else ""), "/cabinet")
        return jsonify(success=True, status="rejected")

    # ---- assent: this is the moment the lever actually moves ----
    claimed = supabase.table("policy_proposals").update(
        {"status": "approved", "decided_by": me, "decided_at": now.isoformat(),
         "decision_note": note}).eq("id", pid).eq("status", "pending").execute().data
    if not claimed:
        return jsonify(success=False, error="That proposal is already settled"), 400

    try:
        refresh_config()
        supabase.table("config").update(
            {prop["field"]: prop["proposed_value"]}).eq("id", 1).execute()
        refresh_config()
        _gdp_cache["v"] = None
    except Exception:
        logging.exception("policy assent failed to apply")
        return jsonify(success=False,
                       error="Assent recorded but the change didn't apply — check the config table."), 500

    add_record(prop["minister"],
               f"{spec['label']} moved to {prop['proposed_value']:g} with Presidential assent.")
    _gazette_the_assent(prop, spec, me, note)
    notify(prop["minister"],
           f"✅ The President assented — {spec['label']} is now {prop['proposed_value']:g}.",
           "/cabinet")
    return jsonify(success=True, status="approved",
                   field=prop["field"], value=prop["proposed_value"])


def _gazette_the_assent(prop, spec, president, note):
    """Record the assent in the Gazette. A change to national policy belongs in
    the public record — but a Gazette failure must never undo an assent that
    has already been applied, so this is strictly best-effort."""
    try:
        no = _next_gazette_no("decree")
        supabase.table("gazette").insert({
            "ref": f"Decree No. {no} of {_now().year}",
            "kind": "decree",
            "title": f"{spec['label']} set to {prop['proposed_value']:g}",
            "body": (f"On the proposal of {prop['minister']} "
                     f"({prop.get('ministry') or 'the Cabinet'}), {spec['label']} is moved "
                     f"from {prop.get('current_value')} to {prop['proposed_value']:g}."
                     + (f" {note}" if note else "")),
            "issued_by": president,
        }).execute()
    except Exception as ex:
        logging.warning("assent not gazetted: %s", ex)


@app.route("/cabinet/summary")
@limiter.limit("60/minute")
def cabinet_summary():
    """Counts for the dashboard banner and the tab badges."""
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me, prez = user["username"], is_treasury_admin(user)
    port, mname = ministry_portfolio(user)
    try:
        rows = supabase.table("policy_proposals").select("minister,status").execute().data or []
    except Exception:
        return jsonify(success=True, enabled=False, is_president=prez,
                       portfolio=(port["key"] if port else ""), awaiting=0, mine_open=0)
    return jsonify(success=True, enabled=True, is_president=prez,
                   portfolio=(port["key"] if port else ""),
                   portfolio_label=(port["label"] if port else ""),
                   ministry=mname or "",
                   awaiting=sum(1 for r in rows if r["status"] == "pending") if prez else 0,
                   mine_open=sum(1 for r in rows
                                 if r["minister"] == me and r["status"] == "pending"))

# ============================================================
#  JUSTICE — jail, criminal records, eligibility for office
# ============================================================
#  A conviction is a structured row in `criminal_records`, separate from
#  the free-text `records` feed. It carries a fine and/or a jail term, it
#  shows on the citizen's ID, and it bars them from standing for office.
#
#  RECORD_EXPIRY_DAYS = 0 means "bars you forever", which is the rule the
#  President asked for. Set it above 0 from the admin panel to let
#  convictions become spent after that many days.
# ============================================================

# Pages a jailed citizen may still reach. Everything else is blocked.
_JAIL_ALLOWED_PATHS = {"/jail", "/jail/status", "/logout", "/me",
                       "/favicon.ico", "/robots.txt", "/sitemap.xml"}
_JAIL_ALLOWED_PREFIXES = ("/static/",)

MAX_JAIL_DAYS = 365


def _jail_info(user):
    """Return jail details for a citizen, or None if they are free.
       Sentences that have run out are treated as served."""
    if not user:
        return None
    until = _parse(user.get("jailed_until"))
    if not until:
        return None
    now = _now()
    if until <= now:
        return None
    return {
        "until": until.isoformat(),
        "reason": user.get("jail_reason") or "",
        "by": user.get("jailed_by") or "",
        "seconds_left": int((until - now).total_seconds()),
    }


def is_jailed(user):
    return _jail_info(user) is not None


def _release_if_served(user):
    """Clear a spent sentence so the citizen isn't re-checked forever."""
    if not user or not user.get("jailed_until"):
        return user
    until = _parse(user.get("jailed_until"))
    if until and until <= _now():
        try:
            supabase.table("cybucks").update(
                {"jailed_until": None, "jail_reason": None, "jailed_by": None}
            ).eq("username", user["username"]).execute()
            notify(user["username"], "\U0001F513 You have served your sentence and are free.", "/")
        except Exception as e:
            logging.warning("jail release failed for %s: %s", user.get("username"), e)
        user["jailed_until"] = None
    return user


def _record_is_active(rec):
    """A conviction still counts against you unless it's been spent, or
       enough days have passed for it to expire (when expiry is enabled)."""
    if rec.get("spent"):
        return False
    days = int(RECORD_EXPIRY_DAYS or 0)
    if days <= 0:
        return True                       # permanent
    when = _parse(rec.get("created_at"))
    return not when or (_now() - when).days < days


def criminal_records(username, active_only=False):
    try:
        rows = supabase.table("criminal_records").select("*") \
            .eq("username", username).order("created_at", desc=True) \
            .limit(50).execute().data or []
    except Exception as e:
        logging.warning("criminal record lookup failed: %s", e)
        return []
    return [r for r in rows if _record_is_active(r)] if active_only else rows


def _has_unpaid_loan(username):
    """A loan still owed. Defaulted loans are excluded on purpose: the Treasury
    has already seized the assets and /loans/repay refuses to settle them, so
    treating one as outstanding would bar the citizen from office permanently
    with no way back."""
    try:
        rows = supabase.table("loans").select("id,repaid,defaulted") \
            .eq("username", username).eq("repaid", False).execute().data or []
    except Exception:
        return False
    # Filter here rather than in the query: on older rows `defaulted` can be
    # NULL, which an .eq(False) would miss entirely.
    return any(not r.get("defaulted") for r in rows)


def office_eligibility(user):
    """Can this citizen stand for office? Returns (ok, reason)."""
    if not user:
        return False, "Not logged in"
    if user.get("banned"):
        return False, "Banned citizens cannot hold office."
    if is_jailed(user):
        return False, "You cannot stand for office while serving a sentence."
    recs = criminal_records(user["username"], active_only=True)
    if not FINE_BARS_OFFICE:
        recs = [r for r in recs if (r.get("jail_days") or 0) > 0]
    if recs:
        n = len(recs)
        return False, (f"You have {n} criminal record{'' if n == 1 else 's'} on file. "
                       "A conviction bars you from holding office in Cyvathon.")
    if _has_unpaid_loan(user["username"]):
        return False, "Settle your outstanding loan before standing for office."
    return True, ""


def add_criminal_record(username, issued_by, reason, fine=0, jail_days=0, case_id=None):
    kind = "both" if (fine > 0 and jail_days > 0) else ("jail" if jail_days > 0 else "fine")
    try:
        supabase.table("criminal_records").insert({
            "username": username, "kind": kind, "fine": round(float(fine or 0), 2),
            "jail_days": float(jail_days or 0), "reason": reason[:500],
            "case_id": case_id, "issued_by": issued_by,
        }).execute()
    except Exception as e:
        logging.warning("criminal record insert failed: %s", e)


def send_to_jail(username, days, reason, by):
    """Start (or extend) a jail term. Returns the release time."""
    days = max(0.0, min(float(days or 0), MAX_JAIL_DAYS))
    until = _now() + timedelta(days=days)
    supabase.table("cybucks").update({
        "jailed_until": until.isoformat(),
        "jail_reason": (reason or "")[:300],
        "jailed_by": by,
    }).eq("username", username).execute()
    notify(username, f"\U0001F6A8 You have been jailed for {days:g} day(s). {reason or ''}".strip(), "/jail")
    refresh_jailed(force=True)
    return until


# The gate runs on EVERY request, so it must not hit the database. Jailing is
# rare, so the roster is cached in memory and refreshed like BLOCKED_IPS is.
# Worst-case staleness is JAIL_CACHE_TTL; jailing and releasing refresh at once.
_JAILED = {}            # username -> (release datetime, reason, jailed_by)
_JAILED_AT = 0.0
JAIL_CACHE_TTL = 30     # seconds


def refresh_jailed(force=False):
    global _JAILED, _JAILED_AT
    if not force and (time() - _JAILED_AT) < JAIL_CACHE_TTL:
        return
    _JAILED_AT = time()
    try:
        rows = supabase.table("cybucks") \
            .select("username,jailed_until,jail_reason,jailed_by") \
            .not_.is_("jailed_until", "null").execute().data or []
    except Exception as e:
        logging.warning("jail roster refresh skipped: %s", e)
        return
    now, roster = _now(), {}
    for r in rows:
        until = _parse(r.get("jailed_until"))
        if until and until > now:
            roster[r["username"]] = (until, r.get("jail_reason") or "",
                                     r.get("jailed_by") or "")
    _JAILED = roster


@app.before_request
def _jail_gate():
    """A jailed citizen sees only the jail page."""
    p = request.path
    if p in _JAIL_ALLOWED_PATHS or p.startswith(_JAIL_ALLOWED_PREFIXES):
        return None
    username = session.get("username")
    if not username:
        return None
    refresh_jailed()
    entry = _JAILED.get(username)
    if not entry:
        return None
    until, reason, by = entry
    now = _now()
    if until <= now:                       # sentence served
        _JAILED.pop(username, None)
        return None
    info = {"until": until.isoformat(), "reason": reason, "by": by,
            "seconds_left": int((until - now).total_seconds())}
    # HTML navigation gets the jail page; API calls get a clear 403.
    if request.method == "GET" and "text/html" in (request.headers.get("Accept") or ""):
        return app.send_static_file("jail.html")
    return jsonify(success=False, error="You are in jail.", jailed=info), 403


@app.route("/jail")
def jail_page():
    return app.send_static_file("jail.html")


@app.route("/jail/status")
@limiter.limit("60/minute")
def jail_status():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    info = _jail_info(user)
    if not info:
        _release_if_served(user)
    return jsonify(success=True, jailed=info,
                   username=user["username"],
                   records=criminal_records(user["username"])[:10])


@app.route("/jail/put", methods=["POST"])
@limiter.limit("20/minute")
def jail_put():
    user = get_current_user(run_economics=False)
    if not user or not is_treasury_admin(user):
        return jsonify(success=False, error="Only the President may jail a citizen"), 403
    d = request.get_json() or {}
    target = (d.get("username") or "").strip()
    reason = (d.get("reason") or "").strip()
    try:
        days = float(d.get("days") or 0)
    except (TypeError, ValueError):
        return jsonify(success=False, error="Invalid number of days"), 400
    if not target:
        return jsonify(success=False, error="Who?"), 400
    if days <= 0 or days > MAX_JAIL_DAYS:
        return jsonify(success=False, error=f"Days must be between 1 and {MAX_JAIL_DAYS}"), 400
    if target == user["username"]:
        return jsonify(success=False, error="You cannot jail yourself."), 400
    rows = supabase.table("cybucks").select("username").eq("username", target).execute().data
    if not rows:
        return jsonify(success=False, error="No such citizen"), 404

    until = send_to_jail(target, days, reason, user["username"])
    add_criminal_record(target, user["username"],
                        reason or "Detained by presidential order", jail_days=days)
    add_record(target, f"Jailed for {days:g} day(s) by presidential order. {reason}".strip())
    return jsonify(success=True, until=until.isoformat())


@app.route("/jail/release", methods=["POST"])
@limiter.limit("20/minute")
def jail_release():
    user = get_current_user(run_economics=False)
    if not user or not is_treasury_admin(user):
        return jsonify(success=False, error="Only the President may release a citizen"), 403
    target = ((request.get_json() or {}).get("username") or "").strip()
    if not target:
        return jsonify(success=False, error="Who?"), 400
    supabase.table("cybucks").update(
        {"jailed_until": None, "jail_reason": None, "jailed_by": None}
    ).eq("username", target).execute()
    refresh_jailed(force=True)
    notify(target, "\U0001F513 You have been released by presidential pardon.", "/")
    add_record(target, "Released from jail by presidential pardon.")
    return jsonify(success=True)


@app.route("/jail/pardon", methods=["POST"])
@limiter.limit("20/minute")
def jail_pardon():
    """Wipe a conviction, so it no longer bars the citizen from office."""
    user = get_current_user(run_economics=False)
    if not user or not is_treasury_admin(user):
        return jsonify(success=False, error="Only the President may pardon"), 403
    rec_id = (request.get_json() or {}).get("record_id")
    if not rec_id:
        return jsonify(success=False, error="Which record?"), 400
    rows = supabase.table("criminal_records").update({"spent": True}) \
        .eq("id", rec_id).execute().data
    if not rows:
        return jsonify(success=False, error="Record not found"), 404
    who = rows[0]["username"]
    notify(who, "\U0001F54A\uFE0F A conviction on your record has been pardoned.", "/profile")
    add_record(who, "A conviction was pardoned by the President.")
    return jsonify(success=True)


@app.route("/jail/inmates")
@limiter.limit("30/minute")
def jail_inmates():
    """Who is currently serving time — public, like a prison roll."""
    try:
        rows = supabase.table("cybucks").select(
            "username,jailed_until,jail_reason,jailed_by,avatar") \
            .not_.is_("jailed_until", "null").execute().data or []
    except Exception as e:
        logging.warning("inmate list failed: %s", e)
        rows = []
    now = _now()
    out = []
    for r in rows:
        until = _parse(r.get("jailed_until"))
        if not until or until <= now:
            continue
        out.append({"username": r["username"], "until": until.isoformat(),
                    "reason": r.get("jail_reason") or "", "by": r.get("jailed_by") or "",
                    "avatar": r.get("avatar"),
                    "seconds_left": int((until - now).total_seconds())})
    out.sort(key=lambda x: x["seconds_left"], reverse=True)
    return jsonify(success=True, inmates=out, count=len(out))


@app.route("/records/<username>")
@limiter.limit("40/minute")
def records_for(username):
    """A citizen's criminal record — public, so voters can judge candidates."""
    recs = criminal_records(unquote(username or ""))
    return jsonify(success=True, records=[{
        "id": r["id"], "kind": r.get("kind"), "fine": r.get("fine") or 0,
        "jail_days": r.get("jail_days") or 0, "reason": r.get("reason") or "",
        "issued_by": r.get("issued_by"), "spent": bool(r.get("spent")),
        "active": _record_is_active(r), "created_at": r.get("created_at"),
    } for r in recs])


# ============================================================
#  CABINET — applying for a vacant ministry
# ============================================================
def _ministry_vacancies():
    try:
        rows = supabase.table("ministries").select("*").order("rank").execute().data or []
    except Exception:
        return []
    return [m for m in rows if (m.get("minister") or "Vacant") == "Vacant"]


def _applicants(ministry_id, status="pending"):
    try:
        q = supabase.table("ministry_applications").select("*").eq("ministry_id", ministry_id)
        if status:
            q = q.eq("status", status)
        return q.order("created_at").execute().data or []
    except Exception:
        return []


def _open_ministry_election(ministry, applicants, opened_by="the Republic"):
    """Once enough citizens have applied, the vote opens by itself."""
    names = [a["username"] for a in applicants]
    existing = supabase.table("polls").select("id").eq("ministry_id", ministry["id"]) \
        .eq("open", True).execute().data
    if existing:
        return None
    poll = supabase.table("polls").insert({
        "title": f"Election — {ministry['name']}",
        "position": ministry["name"],
        "options": names,
        "open": True,
        "created_by": opened_by,
        "ministry_id": ministry["id"],
    }).execute().data[0]
    notify_all(f"\U0001F5F3\uFE0F An election has opened for {ministry['name']} — "
               f"{len(names)} candidates. Cast your vote.", "/voting")
    return poll


@app.route("/ministries/vacancies")
@limiter.limit("30/minute")
def ministry_vacancies():
    user = get_current_user(run_economics=False)
    vac = _ministry_vacancies()
    mine, out = (user or {}).get("username"), []
    for m in vac:
        apps = _applicants(m["id"])
        out.append({
            "id": m["id"], "name": m["name"], "mandate": m.get("mandate") or "",
            "icon": m.get("icon") or "fa-landmark",
            "applicants": len(apps),
            "needed": int(MINISTRY_MIN_APPLICANTS),
            "applied": any(a["username"] == mine for a in apps),
            "candidates": [a["username"] for a in apps],
        })
    ok, why = office_eligibility(user) if user else (False, "Not logged in")
    return jsonify(success=True, vacancies=out, eligible=ok, reason=why,
                   min_applicants=int(MINISTRY_MIN_APPLICANTS))


@app.route("/ministries/apply", methods=["POST"])
@limiter.limit("10/minute")
def ministry_apply():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401

    ok, why = office_eligibility(user)
    if not ok:
        return jsonify(success=False, error=why), 403

    d = request.get_json() or {}
    try:
        mid = int(d.get("ministry_id") or 0)
    except (TypeError, ValueError):
        return jsonify(success=False, error="Which ministry?"), 400
    statement = (d.get("statement") or "").strip()[:600]
    if len(statement) < 10:
        return jsonify(success=False, error="Tell the nation why you want the job (at least 10 characters)."), 400

    rows = supabase.table("ministries").select("*").eq("id", mid).execute().data
    if not rows:
        return jsonify(success=False, error="No such ministry"), 404
    ministry = rows[0]
    if (ministry.get("minister") or "Vacant") != "Vacant":
        return jsonify(success=False, error="That ministry already has a minister."), 400

    # Check for a real duplicate first, so a genuine database problem below
    # isn't misreported as "you already applied".
    try:
        already = supabase.table("ministry_applications").select("id") \
            .eq("ministry_id", mid).eq("username", user["username"]) \
            .eq("status", "pending").execute().data
    except Exception:
        return jsonify(success=False,
                       error="Ministry applications aren't enabled yet — the database needs a "
                             "quick update (run migration_justice_cabinet.sql)."), 503
    if already:
        return jsonify(success=False, error="You have already applied for this ministry."), 400

    try:
        supabase.table("ministry_applications").insert({
            "ministry_id": mid, "username": user["username"], "statement": statement,
            "status": "pending",
        }).execute()
    except Exception as ex:
        # A unique-constraint hit here means a stale non-pending row exists;
        # anything else is a real fault worth surfacing honestly.
        if "duplicate" in str(ex).lower() or "unique" in str(ex).lower():
            supabase.table("ministry_applications").update(
                {"status": "pending", "statement": statement}) \
                .eq("ministry_id", mid).eq("username", user["username"]).execute()
        else:
            logging.exception("ministry application failed")
            return jsonify(success=False,
                           error="Couldn't file your application — please try again."), 500

    add_record(user["username"], f"Applied for the post of {ministry['name']}.")

    apps = _applicants(mid)
    opened = False
    if len(apps) >= int(MINISTRY_MIN_APPLICANTS):
        opened = bool(_open_ministry_election(ministry, apps))
    return jsonify(success=True, applicants=len(apps),
                   needed=int(MINISTRY_MIN_APPLICANTS), election_opened=opened)


@app.route("/ministries/applications")
@limiter.limit("30/minute")
def ministry_applications():
    try:
        mid = int(request.args.get("ministry_id") or 0)
    except (TypeError, ValueError):
        return jsonify(success=False, error="Which ministry?"), 400
    apps = _applicants(mid, status=None)
    return jsonify(success=True, applications=[{
        "username": a["username"], "statement": a.get("statement") or "",
        "status": a.get("status"), "created_at": a.get("created_at"),
    } for a in apps])


@app.route("/ministries/withdraw", methods=["POST"])
@limiter.limit("10/minute")
def ministry_withdraw():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    try:
        mid = int((request.get_json() or {}).get("ministry_id") or 0)
    except (TypeError, ValueError):
        return jsonify(success=False, error="Which ministry?"), 400
    supabase.table("ministry_applications").update({"status": "withdrawn"}) \
        .eq("ministry_id", mid).eq("username", user["username"]) \
        .eq("status", "pending").execute()
    return jsonify(success=True)


# ============================================================
#  ANNOUNCEMENTS — tell every citizen when something changes
# ============================================================
#  Broadcasts a notification to the whole nation. Used to announce new
#  features (debit cards, the courts, elections) so citizens actually find
#  them, rather than discovering them by accident.
#
#  Each announcement is also filed in the Gazette, so there is a permanent
#  public record of what changed and when.
# ============================================================

# Ready-made announcements the President can fire from the admin panel.
ANNOUNCEMENT_PRESETS = [
    {"key": "cards",
     "label": "Debit cards",
     "message": "\U0001F4B3 Cyvathon debit cards have arrived — open the Bank to see yours, "
                "print it, and let other citizens scan it to pay you.",
     "link": "/card"},
    {"key": "justice",
     "label": "Courts & jail",
     "message": "\u2696\uFE0F The justice system is live. Cases can now end in a fine, "
                "jail time, or both — and convictions go on your permanent record.",
     "link": "/court"},
    {"key": "cabinet",
     "label": "Cabinet applications",
     "message": "\U0001F3DB\uFE0F Vacant ministries are open for applications. Apply, and once "
                "enough citizens stand, an election opens automatically.",
     "link": "/ministries"},
]


@app.route("/announce/presets")
@limiter.limit("30/minute")
def announce_presets():
    user = get_current_user(run_economics=False)
    if not user or not is_treasury_admin(user):
        return jsonify(success=False, error="President only"), 403
    return jsonify(success=True, presets=ANNOUNCEMENT_PRESETS)


@app.route("/announce", methods=["POST"])
@limiter.limit("6/minute")
def announce():
    """Notify every citizen, and file the announcement in the Gazette."""
    user = get_current_user(run_economics=False)
    if not user or not is_treasury_admin(user):
        return jsonify(success=False, error="Only the President may address the nation"), 403

    d = request.get_json() or {}
    key = (d.get("preset") or "").strip()
    if key:
        preset = next((p for p in ANNOUNCEMENT_PRESETS if p["key"] == key), None)
        if not preset:
            return jsonify(success=False, error="Unknown announcement"), 400
        message, link = preset["message"], preset["link"]
    else:
        message = (d.get("message") or "").strip()[:300]
        link = (d.get("link") or "").strip()[:120]
        if len(message) < 5:
            return jsonify(success=False, error="Say something worth announcing."), 400
        if link and not link.startswith("/"):
            return jsonify(success=False, error="Link must be a path on this site, e.g. /card"), 400

    notify_all(message, link, exclude=None)
    try:
        n = len(supabase.table("gazette").select("id").eq("kind", "announcement")
                .execute().data or []) + 1
        supabase.table("gazette").insert({
            "ref": f"Announcement No. {n} of {_now().year}",
            "kind": "announcement",
            "title": "National Announcement",
            "body": message,
            "issued_by": user["username"],
        }).execute()
    except Exception as e:
        logging.warning("gazette entry for announcement failed: %s", e)

    return jsonify(success=True, message=message, link=link)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    # Debugger is OFF unless FLASK_DEBUG is explicitly set — an exposed Werkzeug
    # debugger is remote code execution. (Production runs gunicorn, not this.)
    app.run(debug=bool(os.getenv("FLASK_DEBUG")), host="0.0.0.0", port=port)
