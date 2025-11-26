from flask import Flask, request, jsonify, session
from flask_cors import CORS
from supabase import create_client
from werkzeug.security import generate_password_hash, check_password_hash
import os
import logging
from time import time
from datetime import timedelta
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

recent_registrations = {}
REGISTRATION_LIMIT_WINDOW = 15

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-this-secret-vro")
app.config["SESSION_COOKIE_NAME"] = "cyvathon_session"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=6)

CORS(app, supports_credentials=True)
logging.basicConfig(level=logging.INFO)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"]
)

def get_current_user():
    username = session.get("username")
    if not username:
        return None
    result = supabase.table("cybucks").select("*").eq("username", username).execute()
    return result.data[0] if result.data else None

def get_chat_user():
    username = session.get("chat_username")
    if not username:
        return None
    result = supabase.table("chat_users").select("*").eq("username", username).execute()
    return result.data[0] if result.data else None

@app.route("/")
def home():
    return app.send_static_file("bank.html")

@app.route("/bank")
def bank_page():
    return app.send_static_file("bank.html")

@app.route("/chat")
def chat_page():
    return app.send_static_file("chat.html")

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
    supabase.table("cybucks").insert({"username": username, "password": hashed, "balance": 0}).execute()
    session["username"] = username

    return jsonify(success=True, username=username, balance=0)

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

    session["username"] = username
    return jsonify(success=True, username=username, balance=user["balance"])

@app.route("/logout", methods=["POST"])
def logout():
    session.pop("username", None)
    return jsonify(success=True)

@app.route("/me")
def me():
    user = get_current_user()
    if not user:
        return jsonify(success=False, error="Not logged in"), 401
    return jsonify(success=True, username=user["username"], balance=user["balance"])

# ----------------------------------------------------
# FIXED USERS ROUTE (Banking recipients)
# ----------------------------------------------------
@app.route("/users")
def users():
    user = get_current_user()   # FIX: use bank login instead of chat login
    if not user:
        return jsonify(success=False, error="Not logged in"), 401

    # Get all BANKING users except current user
    res = supabase.table("cybucks") \
        .select("username") \
        .neq("username", user["username"]) \
        .execute()

    return jsonify(success=True, users=[u["username"] for u in res.data])

# ----------------------------------------------------

@limiter.limit("5/min")
@app.route("/transfer", methods=["POST"])
def transfer():
    user = get_current_user()
    if not user:
        return jsonify(success=False, error="Not logged in"), 401

    data = request.get_json()
    to_username = data.get("to_username", "").strip()
    amount = int(data.get("amount") or 0)

    if amount <= 0:
        return jsonify(success=False, error="Invalid amount"), 400

    sender = supabase.table("cybucks").select("*").eq("username", user["username"]).execute().data[0]
    if sender["balance"] < amount:
        return jsonify(success=False, error="Insufficient funds"), 400

    receiver_res = supabase.table("cybucks").select("*").eq("username", to_username).execute()
    if not receiver_res.data:
        return jsonify(success=False, error="User not found"), 404

    receiver = receiver_res.data[0]

    supabase.table("cybucks").update({"balance": sender["balance"] - amount}).eq("username", user["username"]).execute()
    supabase.table("cybucks").update({"balance": receiver["balance"] + amount}).eq("username", to_username).execute()

    return jsonify(success=True, balance=sender["balance"] - amount)

@app.route("/chat_register", methods=["POST"])
def chat_register():
    data = request.get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    if not username or not password:
        return jsonify(success=False, error="Missing credentials"), 400

    exists = supabase.table("chat_users").select("id").eq("username", username).execute()
    if exists.data:
        return jsonify(success=False, error="Username exists"), 400

    hashed = generate_password_hash(password)
    supabase.table("chat_users").insert({"username": username, "password": hashed}).execute()

    session["chat_username"] = username
    return jsonify(success=True)

@app.route("/chat_login", methods=["POST"])
def chat_login():
    data = request.get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    res = supabase.table("chat_users").select("*").eq("username", username).execute()
    if not res.data:
        return jsonify(success=False, error="User not found"), 404

    user = res.data[0]
    if not check_password_hash(user["password"], password):
        return jsonify(success=False, error="Incorrect password"), 401

    session["chat_username"] = username
    return jsonify(success=True)

@app.route("/chat_logout", methods=["POST"])
def chat_logout():
    session.pop("chat_username", None)
    return jsonify(success=True)

@limiter.exempt
@app.route("/messages", methods=["POST"])
def send_message():
    user = get_chat_user()
    if not user:
        return jsonify(success=False, error="Not logged in"), 401

    data = request.get_json()
    content = data.get("content", "").strip()

    if not content:
        return jsonify(success=False, error="Empty message"), 400

    supabase.table("messages").insert({
        "sender": user["username"],
        "recipient": None,
        "content": content
    }).execute()

    return jsonify(success=True)

@limiter.exempt
@app.route("/messages", methods=["GET"])
def get_messages():
    user = get_chat_user()
    if not user:
        return jsonify(success=False, error="Not logged in"), 401

    since_id = request.args.get("since_id", type=int)

    query = supabase.table("messages").select("*").order("id", desc=False)

    if since_id is not None:
        query = query.gt("id", since_id)

    res = query.limit(50).execute()

    public = [m for m in res.data if m["recipient"] is None]

    return jsonify(success=True, messages=public)

# ----------------------------------------------------
# DM SYSTEM
# ----------------------------------------------------

@app.route("/dm_users", methods=["GET"])
def dm_users():
    user = get_chat_user()
    if not user:
        return jsonify(success=False, error="Not logged in"), 401

    res = supabase.table("chat_users").select("username").execute()
    all_users = [u["username"] for u in res.data if u["username"] != user["username"]]

    return jsonify(success=True, users=all_users)


@app.route("/dm_send", methods=["POST"])
def dm_send():
    user = get_chat_user()
    if not user:
        return jsonify(success=False, error="Not logged in"), 401

    data = request.get_json()
    recipient = data.get("recipient", "").strip()
    content = data.get("content", "").strip()

    if not recipient or not content:
        return jsonify(success=False, error="Missing data"), 400

    check = supabase.table("chat_users").select("id").eq("username", recipient).execute()
    if not check.data:
        return jsonify(success=False, error="Recipient not found"), 404

    supabase.table("messages").insert({
        "sender": user["username"],
        "recipient": recipient,
        "content": content
    }).execute()

    return jsonify(success=True)
@app.route("/dm_get", methods=["GET"])
def dm_get():
    user = get_chat_user()
    if not user:
        return jsonify(success=False, error="Not logged in"), 401

    other = request.args.get("user")
    if not other:
        return jsonify(success=False, error="Missing target user"), 400

    res = supabase.table("messages").select("*") \
        .or_(
            f"(sender.eq.{user['username']},recipient.eq.{other})",
            f"(sender.eq.{other},recipient.eq.{user['username']})"
        ) \
        .order("id", desc=False) \
        .limit(200) \
        .execute()

    return jsonify(success=True, messages=res.data)


@app.route("/chat_users", methods=["GET"])
def chat_users():
    user = get_chat_user()
    if not user:
        return jsonify(success=False, error="Not logged in"), 401

    res = supabase.table("chat_users").select("username").execute()
    users = [u["username"] for u in res.data if u["username"] != user["username"]]

    return jsonify(success=True, users=users)

@app.route("/health")
def health():
    return "Backend secure, vro!"

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
