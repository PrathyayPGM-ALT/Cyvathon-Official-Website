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
}

COMPANY_CATEGORIES = ["Finance", "Selling", "Service", "Technology", "Other"]

# Weekly salary (Cybucks) by designation
SALARY_TABLE = {
    "President":         1000,
    "Prime Minister":    800,
    "Judge":             700,
    "Minister":          650,
    "Security Minister": 600,
    "Head of Coding":    600,
    "Head of Hacking":   600,
    "Founder":           500,
    "Employee":          300,
    "Citizen":           100,
}

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
                g[name] = int(v) if name.endswith(("_DAYS", "GRANT")) or name in ("LOAN_MAX",) else v
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
        tax_cb = round((user.get("balance")   or 0) * VAT_RATE, 2)
        tax_pf = round((user.get("pufb")      or 0) * VAT_RATE, 2)
        tax_aq = round((user.get("aquilines") or 0) * VAT_RATE, 2)
        tax_cy = round((user.get("cybits")    or 0) * VAT_RATE, 2)
        if tax_cb: updates["balance"]   = round((user.get("balance")   or 0) - tax_cb, 2)
        if tax_pf: updates["pufb"]      = round((user.get("pufb")      or 0) - tax_pf, 2)
        if tax_aq: updates["aquilines"] = round((user.get("aquilines") or 0) - tax_aq, 2)
        if tax_cy: updates["cybits"]    = round((user.get("cybits")    or 0) - tax_cy, 2)
        if tax_cb or tax_pf or tax_aq or tax_cy:
            treasury_add(cybucks=tax_cb, pufb=tax_pf, aquilines=tax_aq, cybits=tax_cy,
                         counterparty=username, kind="vat")
            add_record(username, f"Paid monthly VAT: {tax_cb} CB / {tax_pf} PUFB / {tax_aq} AQ / {tax_cy} CBT to the Treasury.")
        updates["last_tax"] = now.isoformat()

    # ---- 2. Weekly salary (from Treasury) -------------------
    last_salary = _parse(user.get("last_salary"))
    if last_salary is None:
        updates["last_salary"] = now.isoformat()
    else:
        weeks = (now - last_salary).days // SALARY_PERIOD_DAYS
        if weeks >= 1:
            pay = SALARY_TABLE.get(user.get("designation", "Citizen"), 100) * weeks
            base = updates.get("balance", user.get("balance") or 0)
            updates["balance"] = round(base + pay, 2)
            treasury_add(cybucks=-pay, counterparty=username, kind="salary")
            add_record(username, f"Received {pay} CB salary ({user.get('designation','Citizen')}).")
            updates["last_salary"] = now.isoformat()

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
             "/flightsim", "/passport", "/login"]
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
    return jsonify(success=True)


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
            "judge": user["username"], "ruled_at": _now().isoformat()
        }).eq("id", case["id"]).execute()
        add_record(case["defendant"], f"Found GUILTY in '{case['title']}'. Fined {paid} CB. {note}")
        notify(case["defendant"], f"⚖️ You were found guilty in '{case['title']}' — fined {paid} CB.", "/court")
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
        supabase.table("government").update({"holder": winner}) \
            .eq("position", poll["position"]).execute()
        # update winner's designation if the position maps to one
        if poll["position"] in SALARY_TABLE:
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
                 "starting_grant", "gdp", "gdp_multiplier"]


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

MONEY — the Bank (/bank): send money to other citizens, convert between the four currencies, a Savings account (5% monthly interest), Government Bonds (+10% after 30 days), and Loans up to 5000 CB (/loans). A 10% VAT is collected monthly into the Treasury. Borrowed money and your welcome grant can't be transferred away — only money you've earned.

BUSINESS: Found a company for 1000 CB (/company); take it public (IPO) and trade its shares on the Stock Exchange (/exchange); pay dividends to shareholders. The Jobs Board (/jobs) lets you apply to any company for a salaried role. Marketplaces: the national Import & Export hub (/marketplace) and per-state local markets.

STATES (/states): five states — Neonhaven, Cryptvale, Silica Plains, Portus Mare, and Aetheris (the national capital). Each has its own marketplace. To enter a state you pass Border Control (you need a Passport; the capital Aetheris also requires the Oath of Allegiance). "Settle" in a state to become a resident and join its chat channel. The President resides in the capital.

IDENTITY: your ID Card (/profile) shows your record and balances; set a profile picture and your interests there. Issue a Passport and swear the Oath of Allegiance (/passport) to become a sworn citizen and collect visa stamps by clearing state borders. Swearing the Oath makes citizenship binding and irrevocable — it can only be given up by a written request on paper to the President.

INTERESTS: when you sign up (and on your ID Card) you pick what you love to do. Cyvathon then shows personalized "Recommended for you" features on your dashboard and drops you into interest-based group chats with like-minded citizens.

GOVERNMENT: a President leads the nation; the Prime Minister and Judge are elected by vote (/voting), and a national presidential vote is held once every six years. The Legislature (/legislature) is where citizens table and vote on bills; the Gazette (/gazette) records laws and decrees; the National Court (/court) rules on cases; report a crime with an FIR (/fir); Ministries (/ministries) run departments with budgets; the Treasury (/treasury) holds national funds. Foreign Affairs (/foreign) tracks Cyvathon's allied and rival micronations — fellow nations can register at signup and request an alliance, which the President confirms.

COMMUNITY: Chat (/chat) is a full messenger — public square, group channels, per-state channels and DMs, with @mentions, replies, emoji reactions, typing indicators, online status, GIFs, image sharing and voice messages. Mail (/mail) — reached from the Chat/Mail toggle — is a Gmail-style inbox where you compose to recipients you pick from a list (or broadcast to everyone), with threaded replies. Share Videos (/videos) by YouTube link or uploading from your device, and write Blogs (/blogs) that others can like and comment on. Climb the Leaderboards (/leaderboard) — richest citizens, top founders, top recruiters. Track your cash and investments in the Portfolio (/portfolio). Browse the Citizens directory (/citizens) and National News (/news). You'll get notification pop-ups for DMs, mentions, mail, approvals, news and opened elections.

CASINO (/casino): bet Cybucks against the House (the Treasury) — Coin Flip, Lucky Number dice, and Slots.

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

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    # Debugger is OFF unless FLASK_DEBUG is explicitly set — an exposed Werkzeug
    # debugger is remote code execution. (Production runs gunicorn, not this.)
    app.run(debug=bool(os.getenv("FLASK_DEBUG")), host="0.0.0.0", port=port)
