from flask import Flask, request, jsonify, session
from flask_cors import CORS
from supabase import create_client
from werkzeug.security import generate_password_hash, check_password_hash
import os
import logging
from time import time

recent_registrations = {}
REGISTRATION_LIMIT_WINDOW = 15


# ----------------------------------------------------
# APP SETUP
# ----------------------------------------------------
app = Flask(__name__, static_folder="static", static_url_path="/static")

# IMPORTANT: change this in Render env later
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-this-secret-vro")

app.config["SESSION_COOKIE_NAME"] = "cyvathon_session"

# Make cookies safer
app.config["SESSION_COOKIE_HTTPONLY"] = True   
app.config["SESSION_COOKIE_SECURE"] = True    
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"  
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=6)


CORS(app, supports_credentials=True)

logging.basicConfig(level=logging.INFO)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    logging.error("Supabase URL or Key not set in environment variables!")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


# ----------------------------------------------------
# HELPERS
# ----------------------------------------------------
def get_current_user():
    username = session.get("username")
    if not username:
        return None

    result = supabase.table("cybucks").select("*").eq("username", username).execute()
    if not result.data:
        return None
    return result.data[0]


# ----------------------------------------------------
# STATIC PAGE (BANK UI)
# ----------------------------------------------------
@app.route("/")
def root():
    # send the bank UI directly
    return app.send_static_file("bank.html")


@app.route("/bank")
def bank_page():
    return app.send_static_file("bank.html")


# ----------------------------------------------------
# AUTH ROUTES
# ----------------------------------------------------
@app.route("/register", methods=["POST"])
def register():
    client_ip = request.remote_addr

    now = time()
    last_time = recent_registrations.get(client_ip, 0)

    if now - last_time < REGISTRATION_LIMIT_WINDOW:
        return jsonify(success=False, error="Too many accounts from this IP"), 429

   
    recent_registrations[client_ip] = now
    try:
        data = request.get_json()
        if not data:
            return jsonify(success=False, error="Missing JSON body"), 400

        username = data.get("username", "").strip()
        password = data.get("password", "").strip()

        if not username or not password:
            return jsonify(success=False, error="Username and password required"), 400

        # Check if user already exists
        result = supabase.table("cybucks").select("id").eq("username", username).execute()
        if result.data:
            return jsonify(success=False, error="Username already taken"), 400

        hashed = generate_password_hash(password)

        supabase.table("cybucks").insert({
            "username": username,
            "password": hashed,
            "balance": 0
        }).execute()

        logging.info(f"New user registered: {username}")
        # Auto-login after register
        session["username"] = username

        return jsonify(success=True, balance=0, username=username)

    except Exception as e:
        logging.exception("Exception in /register")
        return jsonify(success=False, error=str(e)), 500


@app.route("/login", methods=["POST"])
def login():
    try:
        data = request.get_json()
        if not data:
            return jsonify(success=False, error="Missing JSON body"), 400

        username = data.get("username", "").strip()
        password = data.get("password", "").strip()

        if not username or not password:
            return jsonify(success=False, error="Username and password required"), 400

        result = supabase.table("cybucks").select("*").eq("username", username).execute()
        if not result.data:
            return jsonify(success=False, error="User not found"), 404

        user = result.data[0]

        if not check_password_hash(user["password"], password):
            return jsonify(success=False, error="Incorrect password"), 401

        # store in session
        session["username"] = username

        logging.info(f"User logged in: {username}")
        return jsonify(success=True, balance=user["balance"], username=username)

    except Exception as e:
        logging.exception("Exception in /login")
        return jsonify(success=False, error=str(e)), 500


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify(success=True)


# ----------------------------------------------------
# USER / BALANCE INFO
# ----------------------------------------------------
@app.route("/me", methods=["GET"])
def me():
    user = get_current_user()
    if not user:
        return jsonify(success=False, error="Not logged in"), 401

    return jsonify(
        success=True,
        username=user["username"],
        balance=user["balance"]
    )


@app.route("/users", methods=["GET"])
def list_users():
    user = get_current_user()
    if not user:
        return jsonify(success=False, error="Not logged in"), 401

    # get all other users
    result = supabase.table("cybucks").select("username").neq("username", user["username"]).execute()
    usernames = [row["username"] for row in result.data]

    return jsonify(success=True, users=usernames)


# ----------------------------------------------------
# TRANSFER CYBUCKS (NO MORE DIRECT DEPOSIT/WITHDRAW)
# ----------------------------------------------------
@app.route("/transfer", methods=["POST"])
def transfer():
    try:
        user = get_current_user()
        if not user:
            return jsonify(success=False, error="Not logged in"), 401

        data = request.get_json()
        if not data:
            return jsonify(success=False, error="Missing JSON body"), 400

        to_username = data.get("to_username", "").strip()
        amount = data.get("amount")

        if not to_username or amount is None:
            return jsonify(success=False, error="Recipient and amount required"), 400

        amount = int(amount)
        if amount <= 0:
            return jsonify(success=False, error="Amount must be positive"), 400

        if to_username == user["username"]:
            return jsonify(success=False, error="Cannot send cybucks to yourself"), 400

        # refresh sender from DB
        sender_res = supabase.table("cybucks").select("*").eq("username", user["username"]).execute()
        if not sender_res.data:
            return jsonify(success=False, error="Sender not found"), 404

        sender = sender_res.data[0]

        if sender["balance"] < amount:
            return jsonify(success=False, error="Insufficient funds"), 400

        # lookup receiver
        receiver_res = supabase.table("cybucks").select("*").eq("username", to_username).execute()
        if not receiver_res.data:
            return jsonify(success=False, error="Recipient not found"), 404

        receiver = receiver_res.data[0]

        new_sender_balance = sender["balance"] - amount
        new_receiver_balance = receiver["balance"] + amount

        # Update sender
        supabase.table("cybucks").update({"balance": new_sender_balance}).eq("username", sender["username"]).execute()
        # Update receiver
        supabase.table("cybucks").update({"balance": new_receiver_balance}).eq("username", to_username).execute()

        logging.info(f"{amount} transferred from {sender['username']} to {to_username}")

        return jsonify(
            success=True,
            balance=new_sender_balance,
            to_username=to_username,
            sent=amount
        )

    except Exception as e:
        logging.exception("Exception in /transfer")
        return jsonify(success=False, error=str(e)), 500


# ----------------------------------------------------
# HEALTH CHECK
# ----------------------------------------------------
@app.route("/health")
def health():
    return "Cybucks backend running securely, vro!"


# ----------------------------------------------------
# MAIN
# ----------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
