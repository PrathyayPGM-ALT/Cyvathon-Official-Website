from flask import Flask, request, jsonify, session
from flask_cors import CORS
from supabase import create_client
from werkzeug.security import generate_password_hash, check_password_hash
import os
import logging
from time import time
from datetime import timedelta, datetime, timezone
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from google import genai


# ============================================================
#  CONFIG
# ============================================================
recent_registrations = {}
REGISTRATION_LIMIT_WINDOW = 15

# --- Economy constants -------------------------------------
PUFB_PER_CYBUCK      = 5        # 5 Pufferbucks  = 1 Cybuck
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
GDP              = 500000       # national GDP figure

# These economic levers are stored in the `config` table and can be changed
# live by the President from the admin panel. Maps config column -> global.
_CONFIG_KEYS = {
    "vat_rate": "VAT_RATE", "tax_period_days": "TAX_PERIOD_DAYS",
    "salary_period_days": "SALARY_PERIOD_DAYS", "savings_rate": "SAVINGS_RATE",
    "bond_rate": "BOND_RATE", "bond_days": "BOND_DAYS", "company_fee": "COMPANY_FEE",
    "loan_max": "LOAN_MAX", "loan_days": "LOAN_DAYS", "starting_grant": "STARTING_GRANT",
    "gdp": "GDP",
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
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-this-secret-vro")
app.config["SESSION_COOKIE_NAME"] = "cyvathon_session"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=6)

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
    # Generous global ceiling (per IP) — an interactive page fires several
    # reads per view, so the old 400/day was far too low. Sensitive write
    # routes keep their own tight per-route limits below.
    default_limits=["12000 per hour"]
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
    logging.exception("Unhandled server error on %s", request.path)
    return jsonify(success=False,
                   error="Server error: " + str(e) +
                         " — did you run schema.sql in Supabase?"), 500


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


refresh_config()   # load policy at startup


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


def get_treasury():
    res = supabase.table("treasury").select("*").eq("id", 1).execute()
    if res.data:
        return res.data[0]
    supabase.table("treasury").insert({"id": 1}).execute()
    return supabase.table("treasury").select("*").eq("id", 1).execute().data[0]


def treasury_add(cybucks=0, pufb=0, aquilines=0, counterparty=None, kind="manual"):
    """Move money in/out of the Treasury and record each currency as a ledger flow."""
    t = get_treasury()
    supabase.table("treasury").update({
        "balance":   max(0, (t["balance"]   or 0) + cybucks),
        "pufb":      max(0, (t["pufb"]       or 0) + pufb),
        "aquilines": max(0, (t["aquilines"] or 0) + aquilines),
    }).eq("id", 1).execute()
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


def apply_economics(user):
    """Run economics, but never let a failure (e.g. a missing table/column
    before schema.sql has been applied) break login or page loads."""
    try:
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


# ============================================================
#  AUTH  (single account gates bank + chat + everything)
# ============================================================
@limiter.limit("5/min")
@app.route("/register", methods=["POST"])
def register():
    client_ip = request.remote_addr
    now = time()
    if now - recent_registrations.get(client_ip, 0) < REGISTRATION_LIMIT_WINDOW:
        return jsonify(success=False, error="Too many accounts"), 429
    recent_registrations[client_ip] = now

    data = request.get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    if not username or not password:
        return jsonify(success=False, error="Missing credentials"), 400

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
    }).execute()
    add_record(username, "Granted citizenship of Cyvathon with 100 CB / 100 PUFB / 100 AQ.")
    session.permanent = True
    session["username"] = username

    new_user = {
        "username": username, "balance": STARTING_GRANT, "pufb": STARTING_GRANT,
        "aquilines": STARTING_GRANT, "designation": designation, "company_id": None
    }
    return jsonify(success=True, user=public_user(new_user), admin=is_treasury_admin(new_user))


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
    if not check_password_hash(user["password"], password):
        return jsonify(success=False, error="Incorrect password"), 401

    session.permanent = True
    session["username"] = username
    user = apply_economics(user)
    return jsonify(success=True, user=public_user(user), admin=is_treasury_admin(user))


@app.route("/logout", methods=["POST"])
def logout():
    session.pop("username", None)
    return jsonify(success=True)


@app.route("/me")
def me():
    user = get_current_user()
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    return jsonify(success=True, user=public_user(user), admin=is_treasury_admin(user))


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
    if amount <= 0:
        return jsonify(success=False, error="Invalid amount"), 400

    col = CURRENCY_COLUMN[currency]
    sender = supabase.table("cybucks").select("*").eq("username", user["username"]).execute().data[0]
    if (sender.get(col) or 0) < amount:
        return jsonify(success=False, error="Insufficient funds"), 400

    receiver_res = supabase.table("cybucks").select("*").eq("username", to_username).execute()
    if not receiver_res.data:
        return jsonify(success=False, error="User not found"), 404
    receiver = receiver_res.data[0]

    supabase.table("cybucks").update({col: round((sender.get(col) or 0) - amount, 2)}) \
        .eq("username", user["username"]).execute()
    supabase.table("cybucks").update({col: round((receiver.get(col) or 0) + amount, 2)}) \
        .eq("username", to_username).execute()

    log_txn("transfer", user["username"], to_username, amount, currency, "Bank transfer")
    notify(to_username, f"{user['username']} sent you {amount} {currency}.", "/bank")
    fresh = supabase.table("cybucks").select("*").eq("username", user["username"]).execute().data[0]
    return jsonify(success=True, user=public_user(fresh))


@limiter.limit("30/min")
@app.route("/convert", methods=["POST"])
def convert():
    """Convert between cybucks / pufb / aquilines.
       1 Cybuck = 5 Pufferbucks = 50 Aquilines."""
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
    if amount <= 0:
        return jsonify(success=False, error="Invalid amount"), 400

    scol, dcol = CURRENCY_COLUMN[src], CURRENCY_COLUMN[dst]
    if (user.get(scol) or 0) < amount:
        return jsonify(success=False, error="Insufficient funds"), 400

    # Convert through cybuck value
    converted = round(amount * CYBUCK_VALUE[src] / CYBUCK_VALUE[dst], 2)
    supabase.table("cybucks").update({
        scol: round((user.get(scol) or 0) - amount, 2),
        dcol: round((user.get(dcol) or 0) + converted, 2),
    }).eq("username", user["username"]).execute()

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
        gdp=GDP,
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
    if (user.get("balance") or 0) < COMPANY_FEE:
        return jsonify(success=False, error=f"You need {COMPANY_FEE} CB to found a company"), 400

    exists = supabase.table("companies").select("id").eq("name", name).execute()
    if exists.data:
        return jsonify(success=False, error="A company with that name already exists"), 400

    # Charge the founding fee -> Treasury
    supabase.table("cybucks").update({
        "balance": round((user.get("balance") or 0) - COMPANY_FEE, 2),
        "designation": "Founder"
    }).eq("username", user["username"]).execute()
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


def _add_shares(username, company_id, delta):
    r = supabase.table("holdings").select("*").eq("username", username) \
        .eq("company_id", company_id).execute().data
    if r:
        supabase.table("holdings").update({"shares": round((r[0]["shares"] or 0) + delta, 4)}) \
            .eq("id", r[0]["id"]).execute()
    else:
        supabase.table("holdings").insert({
            "username": username, "company_id": company_id, "shares": round(delta, 4)
        }).execute()


def _add_cash(username, delta):
    r = supabase.table("cybucks").select("balance").eq("username", username).execute().data[0]
    supabase.table("cybucks").update({"balance": round((r["balance"] or 0) + delta, 2)}) \
        .eq("username", username).execute()


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

        _add_shares(buyer, cid, fill)                 # shares -> buyer
        _add_cash(seller, round(exec_price * fill, 2))# cash -> seller
        if side == "buy":                             # refund taker's price improvement
            refund = round((taker["price"] - exec_price) * fill, 2)
            if refund > 0:
                _add_cash(taker["username"], refund)

        nf = m["filled"] + fill
        supabase.table("orders").update({
            "filled": nf, "status": "filled" if nf >= m["quantity"] else "open"
        }).eq("id", m["id"]).execute()
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
    if shares <= 0 or price <= 0:
        return jsonify(success=False, error="Enter a share count and IPO price"), 400

    supabase.table("companies").update({
        "is_public": True, "shares": shares, "ipo_price": price,
        "last_price": price, "balance": round(shares * price, 2)
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
    if qty <= 0 or price <= 0:
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
        _add_cash(user["username"], -cost)
    else:
        if _holding_shares(user["username"], cid) < qty:
            return jsonify(success=False, error="You don't own that many shares"), 400
        _add_shares(user["username"], cid, -qty)

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

    rem = (o["quantity"] or 0) - (o["filled"] or 0)
    if o["side"] == "buy":
        _add_cash(user["username"], round(o["price"] * rem, 2))   # refund escrow
    else:
        _add_shares(user["username"], o["company_id"], rem)       # return shares
    supabase.table("orders").update({"status": "cancelled"}).eq("id", oid).execute()
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
    if per_share <= 0:
        return jsonify(success=False, error="Enter a positive amount per share"), 400

    holders = supabase.table("holdings").select("*").eq("company_id", cid).execute().data or []
    total = round(sum((h["shares"] or 0) for h in holders) * per_share, 2)
    if (c.get("balance") or 0) < total:
        return jsonify(success=False, error=f"Company needs {total} CB capital to pay this"), 400

    for h in holders:
        if (h["shares"] or 0) > 0:
            _add_cash(h["username"], round(h["shares"] * per_share, 2))
    supabase.table("companies").update({"balance": round((c["balance"] or 0) - total, 2)}) \
        .eq("id", cid).execute()
    add_record(user["username"], f"Paid a {per_share} CB/share dividend for '{c['name']}' ({total} CB).")
    return jsonify(success=True, total=total)


# ============================================================
#  MARKETPLACE  (citizens & companies sell / donate goods)
# ============================================================
def _add_company_currency(company_id, currency, delta):
    col = CURRENCY_COLUMN[currency]
    r = supabase.table("companies").select(col).eq("id", company_id).execute().data
    if r:
        supabase.table("companies").update({col: round((r[0].get(col) or 0) + delta, 2)}) \
            .eq("id", company_id).execute()


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
    if kind == "sale" and price <= 0:
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
        # pay
        supabase.table("cybucks").update({col: round((buyer.get(col) or 0) - item["price"], 2)}) \
            .eq("username", user["username"]).execute()
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
    col = CURRENCY_COLUMN[currency]
    r = supabase.table("cybucks").select(col).eq("username", username).execute().data
    if r:
        supabase.table("cybucks").update({col: round((r[0].get(col) or 0) + delta, 2)}) \
            .eq("username", username).execute()


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

    supabase.table("companies").update({"balance": round((cb.get("balance") or 0) - salary, 2)}) \
        .eq("id", emp["company_id"]).execute()
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
    if amount <= 0:
        return jsonify(success=False, error="Enter a positive amount"), 400
    if (user.get("balance") or 0) < amount:
        return jsonify(success=False, error="Not enough CB"), 400
    upd = {"balance": round((user.get("balance") or 0) - amount, 2),
           "savings": round((user.get("savings") or 0) + amount, 2)}
    if not user.get("savings_updated"):
        upd["savings_updated"] = _now().isoformat()
    supabase.table("cybucks").update(upd).eq("username", user["username"]).execute()
    return jsonify(success=True)


@limiter.limit("20/min")
@app.route("/savings/withdraw", methods=["POST"])
def savings_withdraw():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    amount = float(request.get_json().get("amount") or 0)
    if amount <= 0:
        return jsonify(success=False, error="Enter a positive amount"), 400
    if (user.get("savings") or 0) < amount:
        return jsonify(success=False, error="Not enough in savings"), 400
    supabase.table("cybucks").update({
        "balance": round((user.get("balance") or 0) + amount, 2),
        "savings": round((user.get("savings") or 0) - amount, 2)
    }).eq("username", user["username"]).execute()
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
    if amount <= 0:
        return jsonify(success=False, error="Enter a positive amount"), 400
    if (user.get("balance") or 0) < amount:
        return jsonify(success=False, error="Not enough CB"), 400

    matures = _now() + timedelta(days=BOND_DAYS)
    supabase.table("cybucks").update({"balance": round((user.get("balance") or 0) - amount, 2)}) \
        .eq("username", user["username"]).execute()
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

    payout = round(b["principal"] * (1 + (b.get("rate") or BOND_RATE)), 2)
    supabase.table("cybucks").update({"balance": round((user.get("balance") or 0) + payout, 2)}) \
        .eq("username", user["username"]).execute()
    treasury_add(cybucks=-payout, counterparty=user["username"], kind="bond_redeem")
    supabase.table("bonds").update({"redeemed": True}).eq("id", bid).execute()
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

    verdict = d.get("verdict")          # 'guilty' | 'dismissed'
    note = (d.get("note") or "").strip()
    try:
        fine = float(d.get("fine") or 0)
    except (TypeError, ValueError):
        fine = 0

    if verdict == "guilty":
        paid = 0
        if fine > 0:
            defu = supabase.table("cybucks").select("balance").eq("username", case["defendant"]).execute().data[0]
            paid = min(fine, defu.get("balance") or 0)   # take what they have
            supabase.table("cybucks").update({"balance": round((defu.get("balance") or 0) - paid, 2)}) \
                .eq("username", case["defendant"]).execute()
            _add_cash(case["plaintiff"], paid)            # damages to the plaintiff
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
    if amount <= 0 or amount > LOAN_MAX:
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

    supabase.table("cybucks").update({"balance": round((user.get("balance") or 0) - loan["amount"], 2)}) \
        .eq("username", user["username"]).execute()
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
    if amount <= 0:
        return jsonify(success=False, error="Enter a positive amount"), 400
    m = supabase.table("ministries").select("*").eq("id", mid).execute().data
    if not m:
        return jsonify(success=False, error="Ministry not found"), 404
    t = get_treasury()
    if (t["balance"] or 0) < amount:
        return jsonify(success=False, error="The Treasury lacks the funds"), 400
    treasury_add(cybucks=-amount, counterparty=m[0]["name"], kind="budget")
    supabase.table("ministries").update({"budget": round((m[0].get("budget") or 0) + amount, 2)}) \
        .eq("id", mid).execute()
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
    if amount <= 0:
        return jsonify(success=False, error="Enter a positive amount"), 400
    if (m.get("budget") or 0) < amount:
        return jsonify(success=False, error="The ministry budget is insufficient"), 400
    if not supabase.table("cybucks").select("id").eq("username", to).execute().data:
        return jsonify(success=False, error="No such recipient"), 404

    supabase.table("ministries").update({"budget": round((m.get("budget") or 0) - amount, 2)}) \
        .eq("id", mid).execute()
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
#  GOVERNMENT
# ============================================================
@app.route("/gov")
def gov():
    res = supabase.table("government").select("*").order("rank").execute()
    return jsonify(success=True, government=res.data or [])


# ============================================================
#  ADMIN PANEL  (President-only economic policy)
# ============================================================
CONFIG_FIELDS = ["vat_rate", "tax_period_days", "salary_period_days", "savings_rate",
                 "bond_rate", "bond_days", "company_fee", "loan_max", "loan_days",
                 "starting_grant", "gdp"]


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
    r = supabase.table("config").select("*").eq("id", 1).execute().data
    return jsonify(success=True, config=(r[0] if r else {}))


# ============================================================
#  ECONOMY STATS  (public)
# ============================================================
@app.route("/economy")
def economy():
    t = get_treasury()
    citizens = supabase.table("cybucks").select("id", count="exact").execute()
    companies = supabase.table("companies").select("id", count="exact").execute()
    return jsonify(
        success=True,
        gdp=GDP,
        treasury=t["balance"],
        citizens=citizens.count or 0,
        companies=companies.count or 0,
        rates={"pufb_per_cybuck": PUFB_PER_CYBUCK, "aquilines_per_pufb": AQUILINES_PER_PUFB}
    )


# ============================================================
#  CHAT  (now gated behind the single national account)
# ============================================================
@limiter.exempt
@app.route("/messages", methods=["POST"])
def send_message():
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401

    content = request.get_json().get("content", "").strip()
    if not content:
        return jsonify(success=False, error="Empty message"), 400

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


@limiter.exempt
@app.route("/dm/<username>", methods=["GET"])
def dm_thread(username):
    user = get_current_user(run_economics=False)
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    me = user["username"]
    sent = supabase.table("messages").select("*").eq("sender", me) \
        .eq("recipient", username).order("id").limit(200).execute().data or []
    recv = supabase.table("messages").select("*").eq("sender", username) \
        .eq("recipient", me).order("id").limit(200).execute().data or []
    msgs = sorted(sent + recv, key=lambda m: m["id"])
    return jsonify(success=True, messages=msgs, me=me)


@limiter.exempt
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
    if to == user["username"]:
        return jsonify(success=False, error="You can't message yourself"), 400
    if not supabase.table("cybucks").select("id").eq("username", to).execute().data:
        return jsonify(success=False, error="No such citizen"), 404

    supabase.table("messages").insert({
        "sender": user["username"], "recipient": to, "content": content
    }).execute()
    notify(to, f"💬 New message from {user['username']}", "/citizens?dm=" + user["username"])
    return jsonify(success=True)


@limiter.exempt
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
    public = [m for m in res.data if m["recipient"] is None]
    return jsonify(success=True, messages=public)


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
