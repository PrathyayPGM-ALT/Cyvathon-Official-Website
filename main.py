from flask import Flask, request, jsonify, session
from flask_cors import CORS
from supabase import create_client
from werkzeug.security import generate_password_hash, check_password_hash
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


# ============================================================
#  CONFIG
# ============================================================
recent_registrations = {}
REGISTRATION_LIMIT_WINDOW = 90   # min seconds between registrations from one IP

# --- Economy constants -------------------------------------
PUFB_PER_CYBUCK      = 1        # Treaty peg: 1 Pufferbuck = 1 Cybuck
AQUILINES_PER_PUFB   = 10       # 10 Aquilines   = 1 Pufferbuck
# Value of one unit expressed in Cybucks:
CYBUCK_VALUE = {
    "cybucks":   1.0,
    "pufb":      1.0 / PUFB_PER_CYBUCK,                       # 0.2
    "aquilines": 1.0 / (PUFB_PER_CYBUCK * AQUILINES_PER_PUFB) # 0.02
}

# Maps a currency code -> the actual DB column. Cybucks live in "balance".
CURRENCY_COLUMN = {
    "cybucks":   "balance",
    "pufb":      "pufb",
    "aquilines": "aquilines",
}

STARTING_GRANT   = 100          # new citizens get 100 of EACH currency
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
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024   # reject request bodies > 256 KB

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

@app.errorhandler(Exception)
def handle_any_error(e):
    if isinstance(e, HTTPException):
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
        logging.warning("FIREWALL blocked %s from %s (UA=%s)", target[:160], ip, ua[:100])
        return jsonify(success=False, error="Request blocked by the Cyvathon firewall."), 403


@app.after_request
def _security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
        "font-src 'self' https://cdnjs.cloudflare.com https://fonts.gstatic.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; object-src 'none'")
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


def cia_rank(user):
    """Return the user's Athena rank, or None if not an agent."""
    if not user:
        return None
    try:
        r = supabase.table("cia_agents").select("rank").eq("username", user["username"]).execute().data
        if r:
            return r[0]["rank"]
    except Exception:
        return "Director" if is_treasury_admin(user) else None
    return "Director" if is_treasury_admin(user) else None


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
        vp, va = CYBUCK_VALUE["pufb"], CYBUCK_VALUE["aquilines"]
        total = 0.0
        for c in (supabase.table("cybucks").select("balance,pufb,aquilines,savings").execute().data or []):
            total += (c.get("balance") or 0) + (c.get("savings") or 0) \
                   + (c.get("pufb") or 0) * vp + (c.get("aquilines") or 0) * va
        t = get_treasury()
        total += (t.get("balance") or 0) + (t.get("pufb") or 0) * vp + (t.get("aquilines") or 0) * va
        for c in (supabase.table("companies")
                  .select("balance,pufb,aquilines,shares,last_price,ipo_price,is_public")
                  .execute().data or []):
            total += (c.get("balance") or 0) + (c.get("pufb") or 0) * vp + (c.get("aquilines") or 0) * va
            if c.get("is_public"):
                total += (c.get("last_price") or c.get("ipo_price") or 0) * (c.get("shares") or 0)
        gdp = round(total * (GDP_MULTIPLIER or 1), 2)
        _gdp_cache["v"], _gdp_cache["t"] = gdp, time()
        return gdp
    except Exception as e:
        logging.warning("GDP auto-calc failed, using fallback: %s", e)
        return GDP


def treasury_add(cybucks=0, pufb=0, aquilines=0, counterparty=None, kind="manual"):
    """Move money in/out of the Treasury (atomically) and log each currency as a flow.
    The Treasury may run a deficit (negative) — that records true national debt
    instead of silently 'minting' the shortfall by flooring at zero."""
    get_treasury()   # ensure the row exists
    for col, delta in (("balance", cybucks), ("pufb", pufb), ("aquilines", aquilines)):
        if delta:
            cas_num("treasury", [("id", 1)], col, delta, allow_negative=True)
    for cur, delta in (("cybucks", cybucks), ("pufb", pufb), ("aquilines", aquilines)):
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
        if tax_cb: updates["balance"]   = round((user.get("balance")   or 0) - tax_cb, 2)
        if tax_pf: updates["pufb"]      = round((user.get("pufb")      or 0) - tax_pf, 2)
        if tax_aq: updates["aquilines"] = round((user.get("aquilines") or 0) - tax_aq, 2)
        if tax_cb or tax_pf or tax_aq:
            treasury_add(cybucks=tax_cb, pufb=tax_pf, aquilines=tax_aq,
                         counterparty=username, kind="vat")
            add_record(username, f"Paid monthly VAT: {tax_cb} CB / {tax_pf} PUFB / {tax_aq} AQ to the Treasury.")
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
    if run_economics:
        user = apply_economics(user)
    return user


def public_user(user):
    return {
        "username":    user["username"],
        "balance":     user.get("balance") or 0,
        "pufb":        user.get("pufb") or 0,
        "aquilines":   user.get("aquilines") or 0,
        "designation": user.get("designation") or "Citizen",
        "company_id":  user.get("company_id"),
    }


def company_founders(c):
    """All citizens with founder privileges: the founder + any co-founders."""
    cf = c.get("cofounders") or []
    if not isinstance(cf, list):
        cf = []
    return [c["founder"]] + [x for x in cf if x != c["founder"]]


def user_net_worth(username):
    u = supabase.table("cybucks").select("balance,pufb,aquilines").eq("username", username).execute().data
    if not u:
        return 0
    u = u[0]
    nw = (u.get("balance") or 0) + (u.get("pufb") or 0) * CYBUCK_VALUE["pufb"] \
        + (u.get("aquilines") or 0) * CYBUCK_VALUE["aquilines"]
    hs = supabase.table("holdings").select("shares,company_id").eq("username", username).execute().data or []
    for h in hs:
        if (h.get("shares") or 0) > 0:
            c = supabase.table("companies").select("last_price,ipo_price").eq("id", h["company_id"]).execute().data
            if c:
                lp = c[0].get("last_price") or c[0].get("ipo_price") or 0
                nw += lp * h["shares"]
    return round(nw, 2)


# ============================================================
#  PAGES
# ============================================================
@app.route("/")
def home():
    return app.send_static_file("index.html")

@app.route("/bank")
def bank_page():
    return app.send_static_file("bank.html")

@app.route("/chat")
def chat_page():
    return app.send_static_file("chat.html")

@app.route("/ai")
def ai_page():
    return app.send_static_file("ai.html")

@app.route("/company")
def company_page():
    return app.send_static_file("company.html")

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

@app.route("/exchange")
def exchange_page():
    return app.send_static_file("exchange.html")

@app.route("/marketplace")
def marketplace_page():
    return app.send_static_file("marketplace.html")

@app.route("/news")
def news_page():
    return app.send_static_file("news.html")

@app.route("/notifications")
def notifications_page():
    return app.send_static_file("notifications.html")

@app.route("/citizens")
def citizens_page():
    return app.send_static_file("citizens.html")

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


# ============================================================
#  AUTH  (single account gates bank + chat + everything)
# ============================================================
_captchas = {}   # token -> (answer:int, expires:float)

@app.route("/captcha")
def captcha():
    a, b = random.randint(1, 9), random.randint(1, 9)
    token = secrets.token_hex(8)
    now = time()
    _captchas[token] = (a + b, now + 600)
    for t in list(_captchas):            # drop expired
        if _captchas[t][1] < now:
            _captchas.pop(t, None)
    return jsonify(success=True, token=token, question=f"What is {a} + {b}?")


@limiter.limit("5/min")
@app.route("/register", methods=["POST"])
def register():
    ip = client_ip()
    now = time()
    if now - recent_registrations.get(ip, 0) < REGISTRATION_LIMIT_WINDOW:
        return jsonify(success=False, error="Too many accounts"), 429

    data = request.get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    # Human-check (anti-bot CAPTCHA)
    entry = _captchas.pop(data.get("captcha_token", ""), None)
    if (not entry or entry[1] < now
            or str(data.get("captcha_answer", "")).strip() != str(entry[0])):
        return jsonify(success=False, error="Incorrect human-check answer — try again"), 400

    recent_registrations[ip] = now

    if not username or not password:
        return jsonify(success=False, error="Missing credentials"), 400
    if len(username) > 32 or len(password) > 200:
        return jsonify(success=False, error="Username (max 32) or password too long"), 400

    exists = supabase.table("cybucks").select("id").eq("username", username).execute()
    if exists.data:
        return jsonify(success=False, error="Username exists"), 400

    hashed = generate_password_hash(password)
    designation = "President" if username in TREASURY_ADMINS else "Citizen"
    supabase.table("cybucks").insert({
        "username":    username,
        "password":    hashed,
        "balance":     STARTING_GRANT,
        "pufb":        STARTING_GRANT,
        "aquilines":   STARTING_GRANT,
        "designation": designation,
        "last_tax":    _now().isoformat(),
        "last_salary": _now().isoformat(),
        "reg_ip":      ip,
    }).execute()
    add_record(username, "Granted citizenship of Cyvathon with 100 CB / 100 PUFB / 100 AQ.")
    session.permanent = True
    session["username"] = username

    new_user = {
        "username": username, "balance": STARTING_GRANT, "pufb": STARTING_GRANT,
        "aquilines": STARTING_GRANT, "designation": designation, "company_id": None
    }
    return jsonify(success=True, user=public_user(new_user), admin=is_treasury_admin(new_user), cia=is_cia(new_user))


@limiter.limit("10/min")
@app.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    res = supabase.table("cybucks").select("*").eq("username", username).execute()
    if not res.data:
        return jsonify(success=False, error="User not found"), 404

    user = res.data[0]
    if user.get("banned"):
        return jsonify(success=False, error="This account has been banned from Cyvathon."), 403
    if not check_password_hash(user["password"], password):
        _strike(client_ip())          # brute-force protection: too many → auto-ban
        return jsonify(success=False, error="Incorrect password"), 401

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
    return jsonify(success=True, user=public_user(user), admin=is_treasury_admin(user), cia=is_cia(user))


@app.route("/users")
def users():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    res = supabase.table("cybucks").select("username") \
        .neq("username", user["username"]).execute()
    return jsonify(success=True, users=[u["username"] for u in res.data])


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

    return jsonify(success=True, profile={
        "username": u["username"],
        "designation": u.get("designation") or "Citizen",
        "member_since": u.get("created_at"),
        "net_worth": user_net_worth(username),
        "companies": founded, "jobs": job_list, "records": records,
        "is_self": viewer["username"] == username,
    })


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
              + (sender.get("aquilines") or 0) * CYBUCK_VALUE["aquilines"])
    grant_locked = STARTING_GRANT * (1 + CYBUCK_VALUE["pufb"] + CYBUCK_VALUE["aquilines"])
    return wealth - grant_locked - _outstanding_loan(sender["username"])


@limiter.limit("20/min")
@app.route("/transfer", methods=["POST"])
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


@limiter.limit("30/min")
@app.route("/convert", methods=["POST"])
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
    reserves = {"cybucks": t["balance"] or 0, "pufb": t["pufb"] or 0, "aquilines": t["aquilines"] or 0}

    # --- Money supply: what citizens hold + what the Treasury holds ---
    citizens = supabase.table("cybucks").select("balance,pufb,aquilines").execute().data or []
    held = {"cybucks": 0.0, "pufb": 0.0, "aquilines": 0.0}
    holders = 0
    for c in citizens:
        cb, pf, aq = (c.get("balance") or 0), (c.get("pufb") or 0), (c.get("aquilines") or 0)
        held["cybucks"] += cb; held["pufb"] += pf; held["aquilines"] += aq
        if cb or pf or aq:
            holders += 1
    supply = {k: round(held[k] + reserves[k], 2) for k in reserves}

    # --- VAT collected & salary paid, this calendar year, per currency ---
    year_start = datetime(_now().year, 1, 1, tzinfo=timezone.utc).isoformat()
    def totals(kind):
        rows = supabase.table("treasury_flows").select("currency,amount") \
            .eq("kind", kind).gte("created_at", year_start).execute().data or []
        out = {"cybucks": 0.0, "pufb": 0.0, "aquilines": 0.0}
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


@limiter.limit("10/min")
@app.route("/companies", methods=["POST"])
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


@limiter.limit("10/min")
@app.route("/exchange/ipo", methods=["POST"])
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
    if (shares <= 0 or shares > 10_000_000 or not math.isfinite(price)
            or price <= 0 or price > 1_000_000):
        return jsonify(success=False, error="Shares 1–10,000,000 and IPO price 0–1,000,000 CB"), 400

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


@limiter.limit("40/min")
@app.route("/exchange/order", methods=["POST"])
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
    if qty <= 0 or not math.isfinite(price) or price <= 0:
        return jsonify(success=False, error="Enter a positive price and quantity"), 400

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


@limiter.limit("30/min")
@app.route("/exchange/cancel", methods=["POST"])
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


@limiter.limit("10/min")
@app.route("/exchange/dividend", methods=["POST"])
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


@app.route("/market", methods=["GET"])
def market_list():
    user = get_current_user(run_economics=False)
    items = supabase.table("market_items").select("*").eq("status", "available") \
        .order("created_at", desc=True).execute().data or []
    for it in items:
        it["seller_label"] = _seller_label(it)
    # companies this citizen can sell on behalf of
    my_companies = []
    if user:
        my_companies = supabase.table("companies").select("id,name") \
            .eq("founder", user["username"]).execute().data or []
    return jsonify(success=True, items=items, me=user["username"] if user else None,
                   my_companies=my_companies)


@limiter.limit("15/min")
@app.route("/market", methods=["POST"])
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

    if company_id:
        c = supabase.table("companies").select("*").eq("id", company_id).execute().data
        if not c or user["username"] not in company_founders(c[0]):
            return jsonify(success=False, error="You can only sell for your own company"), 403

    item = supabase.table("market_items").insert({
        "seller": user["username"], "company_id": company_id,
        "title": title, "description": description, "image_url": image_url,
        "price": price, "currency": currency, "kind": kind
    }).execute().data[0]
    return jsonify(success=True, item=item)


@limiter.limit("30/min")
@app.route("/market/buy", methods=["POST"])
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


@limiter.limit("20/min")
@app.route("/market/delete", methods=["POST"])
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


@limiter.limit("10/min")
@app.route("/company/cofounder", methods=["POST"])
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


@limiter.limit("10/min")
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


@limiter.limit("20/min")
@app.route("/jobs/decide", methods=["POST"])
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


@limiter.limit("30/min")
@app.route("/jobs/pay", methods=["POST"])
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


@limiter.limit("20/min")
@app.route("/jobs/fire", methods=["POST"])
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


@limiter.limit("20/min")
@app.route("/savings/deposit", methods=["POST"])
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


@limiter.limit("20/min")
@app.route("/savings/withdraw", methods=["POST"])
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


@limiter.limit("15/min")
@app.route("/bonds/buy", methods=["POST"])
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


@limiter.limit("15/min")
@app.route("/bonds/redeem", methods=["POST"])
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


@limiter.limit("30/min")
@app.route("/court/debate", methods=["POST"])
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


@limiter.limit("10/min")
@app.route("/court/file", methods=["POST"])
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


@limiter.limit("20/min")
@app.route("/court/rule", methods=["POST"])
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


@limiter.limit("10/min")
@app.route("/loans", methods=["POST"])
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


@limiter.limit("10/min")
@app.route("/loans/repay", methods=["POST"])
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

    return jsonify(
        success=True,
        profile=public_user(user),
        salary=SALARY_TABLE.get(user.get("designation", "Citizen"), 100),
        company=company,
        records=records.data or [],
        loans=loans.data or [],
        member_since=user.get("created_at"),
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
        "net_worth": user_net_worth(user["username"]),
        "mrz": _mrz(user["username"], pno),
        "stamps": _passport_stamps(user, records),
    })


@limiter.limit("10/min")
@app.route("/passport/issue", methods=["POST"])
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


@limiter.limit("10/min")
@app.route("/citizenship/oath", methods=["POST"])
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


@limiter.limit("10/min")
@app.route("/polls", methods=["POST"])
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
    return jsonify(success=True, poll=poll)


@limiter.limit("30/min")
@app.route("/polls/vote", methods=["POST"])
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


@limiter.limit("10/min")
@app.route("/polls/close", methods=["POST"])
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


@limiter.limit("20/min")
@app.route("/news", methods=["POST"])
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
    return jsonify(success=True, item=item)


@limiter.limit("20/min")
@app.route("/news/delete", methods=["POST"])
def news_delete():
    user = get_current_user(run_economics=False)
    if not user or not is_treasury_admin(user):
        return jsonify(success=False, error="Only the President may delete news"), 403
    supabase.table("news").delete().eq("id", request.get_json().get("id")).execute()
    return jsonify(success=True)


# ============================================================
#  NOTIFICATIONS
# ============================================================
@app.route("/notifications/list", methods=["GET"])
def notifications_list():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    rows = supabase.table("notifications").select("*").eq("username", user["username"]) \
        .order("created_at", desc=True).limit(40).execute().data or []
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


@limiter.limit("10/min")
@app.route("/legislature/table", methods=["POST"])
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


@limiter.limit("40/min")
@app.route("/legislature/vote", methods=["POST"])
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


@limiter.limit("30/min")
@app.route("/legislature/debate", methods=["POST"])
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


@limiter.limit("10/min")
@app.route("/legislature/withdraw", methods=["POST"])
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


@limiter.limit("15/min")
@app.route("/legislature/assent", methods=["POST"])
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


@limiter.limit("15/min")
@app.route("/gazette/decree", methods=["POST"])
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


@limiter.limit("15/min")
@app.route("/ministries/create", methods=["POST"])
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


@limiter.limit("20/min")
@app.route("/ministries/appoint", methods=["POST"])
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


@limiter.limit("20/min")
@app.route("/ministries/dismiss", methods=["POST"])
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


@limiter.limit("20/min")
@app.route("/ministries/fund", methods=["POST"])
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


@limiter.limit("20/min")
@app.route("/ministries/mandate", methods=["POST"])
def ministries_mandate():
    user = get_current_user(run_economics=False)
    if not user or not is_treasury_admin(user):
        return jsonify(success=False, error="President only"), 403
    d = request.get_json()
    supabase.table("ministries").update({"mandate": (d.get("mandate") or "").strip()}) \
        .eq("id", d.get("ministry_id")).execute()
    return jsonify(success=True)


@limiter.limit("30/min")
@app.route("/ministries/spend", methods=["POST"])
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


@limiter.limit("15/min")
@app.route("/ministries/delete", methods=["POST"])
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


@limiter.limit("10/min")
@app.route("/fir/file", methods=["POST"])
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


@limiter.limit("20/min")
@app.route("/fir/evidence", methods=["POST"])
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
    return jsonify(success=True, roster=roster, firs=firs,
                   is_director=is_cia_director(user), my_rank=cia_rank(user),
                   me=user["username"], ranks=CIA_RANKS)


@limiter.limit("15/min")
@app.route("/athena/recruit", methods=["POST"])
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
    return jsonify(success=True)


@limiter.limit("15/min")
@app.route("/athena/dismiss", methods=["POST"])
def athena_dismiss():
    user = get_current_user(run_economics=False)
    if not user or not is_cia_director(user):
        return jsonify(success=False, error="Director clearance required"), 403
    username = (request.get_json().get("username") or "").strip()
    if username in TREASURY_ADMINS:
        return jsonify(success=False, error="The Director cannot be removed"), 400
    supabase.table("cia_agents").delete().eq("username", username).execute()
    return jsonify(success=True)


@limiter.limit("30/min")
@app.route("/athena/case", methods=["POST"])
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


@limiter.limit("15/min")
@app.route("/athena/escalate", methods=["POST"])
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


@limiter.limit("20/min")
@app.route("/admin/config", methods=["POST"])
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
    return jsonify(success=True, blocked=blocked, signups=signups, banned=banned)


@limiter.limit("30/min")
@app.route("/admin/block_user", methods=["POST"])
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


@limiter.limit("30/min")
@app.route("/admin/unban", methods=["POST"])
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


@limiter.limit("30/min")
@app.route("/admin/block", methods=["POST"])
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


@limiter.limit("30/min")
@app.route("/admin/unblock", methods=["POST"])
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
        citizens = supabase.table("cybucks").select("id", count="exact").execute()
        companies = supabase.table("companies").select("id", count="exact").execute()
        return {
            "success": True, "gdp": compute_gdp(), "treasury": t["balance"],
            "citizens": citizens.count or 0, "companies": companies.count or 0,
            "rates": {"pufb_per_cybuck": PUFB_PER_CYBUCK, "aquilines_per_pufb": AQUILINES_PER_PUFB},
        }
    return jsonify(cached_json("economy", 45, build))


# ============================================================
#  CHAT  (now gated behind the single national account)
# ============================================================
@limiter.limit("60 per minute")
@app.route("/messages", methods=["POST"])
def send_message():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401

    content = request.get_json().get("content", "").strip()
    if not content:
        return jsonify(success=False, error="Empty message"), 400
    if len(content) > 2000:
        return jsonify(success=False, error="Message too long (max 2000 characters)"), 400

    supabase.table("messages").insert({
        "sender": user["username"], "recipient": None, "content": content
    }).execute()
    return jsonify(success=True)


# ----- Citizens directory + direct messages -----
@app.route("/citizens/list")
def citizens_directory():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    rows = supabase.table("cybucks").select("username,designation") \
        .order("username").execute().data or []
    return jsonify(success=True, citizens=rows, me=user["username"])


@limiter.limit("90 per minute")
@app.route("/dm/<username>", methods=["GET"])
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


@limiter.limit("60 per minute")
@app.route("/dm", methods=["POST"])
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

    supabase.table("messages").insert({
        "sender": user["username"], "recipient": to, "content": content
    }).execute()
    notify(to, f"💬 New message from {user['username']}", "/citizens?dm=" + user["username"])
    return jsonify(success=True)


@limiter.limit("90 per minute")
@app.route("/messages", methods=["GET"])
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


@limiter.limit("10/min")
@app.route("/groups", methods=["POST"])
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


@limiter.limit("20/min")
@app.route("/groups/add", methods=["POST"])
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


@limiter.limit("20/min")
@app.route("/groups/leave", methods=["POST"])
def groups_leave():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    gid = request.get_json().get("group_id")
    supabase.table("chat_group_members").delete().eq("group_id", gid) \
        .eq("username", user["username"]).execute()
    return jsonify(success=True)


@limiter.limit("90 per minute")
@app.route("/groups/<int:gid>/messages", methods=["GET"])
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


@limiter.limit("60 per minute")
@app.route("/groups/<int:gid>/messages", methods=["POST"])
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
    supabase.table("messages").insert({
        "sender": user["username"], "recipient": None, "group_id": gid, "content": content
    }).execute()
    return jsonify(success=True)


# ============================================================
#  AI ASSISTANT
# ============================================================
@limiter.limit("10/min")
@app.route("/ai_ask", methods=["POST"])
def ai_ask():
    try:
        if genai_client is None:
            return jsonify(reply="Cyvathon AI is offline (no API key configured)."), 200
        data = request.get_json(force=True)
        message = data.get("message", "").strip()
        if not message:
            return jsonify(reply="Please enter a message."), 200
        response = genai_client.models.generate_content(
            model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"), contents=message
        )
        reply = response.text if response.text else "No response generated."
        return jsonify(reply=reply), 200
    except Exception as e:
        logging.exception("Cyvathon AI error: %s", e)
        return jsonify(reply="Cyvathon AI backend error. Check server logs."), 500


@app.route("/health")
def health():
    return "Backend secure, vro!"


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
