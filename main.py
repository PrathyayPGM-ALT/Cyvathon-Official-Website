from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client
import os
import logging

# -------------------------------------------------------------------
# Flask app setup
# static_folder='.' lets us serve bank.html directly from the repo root
# -------------------------------------------------------------------
app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)  # you technically do not need this once same-origin, but it is fine

logging.basicConfig(level=logging.INFO)

# -------------------------------------------------------------------
# Supabase client
# -------------------------------------------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    logging.error("Supabase URL or Key not set in environment variables!")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


# -------------------------------------------------------------------
# API ROUTES
# -------------------------------------------------------------------
@app.route("/login", methods=["POST"])
def login():
    try:
        data = request.get_json()
        if not data:
            return jsonify(success=False, error="Missing JSON body"), 400

        username = data.get("username")
        password = data.get("password")

        if not username or not password:
            return jsonify(success=False, error="Username and password required"), 400

        result = supabase.table("cybucks").select("*").eq("username", username).execute()

        # New user → create with balance 0
        if not result.data:
            supabase.table("cybucks").insert({
                "username": username,
                "password": password,
                "balance": 0
            }).execute()

            logging.info(f"New user created: {username}")
            return jsonify(success=True, balance=0)

        user = result.data[0]

        if user["password"] != password:
            return jsonify(success=False, error="Incorrect password"), 401

        return jsonify(success=True, balance=user["balance"])

    except Exception as e:
        logging.exception("Exception in /login")
        return jsonify(success=False, error=str(e)), 500


@app.route("/deposit", methods=["POST"])
def deposit():
    try:
        data = request.get_json()
        if not data:
            return jsonify(success=False, error="Missing JSON body"), 400

        username = data.get("username")
        amount = data.get("amount")

        if not username or amount is None:
            return jsonify(success=False, error="Username and amount required"), 400

        amount = int(amount)
        if amount <= 0:
            return jsonify(success=False, error="Amount must be positive"), 400

        result = supabase.table("cybucks").select("*").eq("username", username).execute()
        if not result.data:
            return jsonify(success=False, error="User not found"), 404

        user = result.data[0]
        new_balance = user["balance"] + amount

        supabase.table("cybucks").update({"balance": new_balance}).eq("username", username).execute()

        logging.info(f"{amount} deposited for user {username}. New balance: {new_balance}")
        return jsonify(success=True, balance=new_balance)

    except Exception as e:
        logging.exception("Exception in /deposit")
        return jsonify(success=False, error=str(e)), 500


@app.route("/withdraw", methods=["POST"])
def withdraw():
    try:
        data = request.get_json()
        if not data:
            return jsonify(success=False, error="Missing JSON body"), 400

        username = data.get("username")
        amount = data.get("amount")

        if not username or amount is None:
            return jsonify(success=False, error="Username and amount required"), 400

        amount = int(amount)
        if amount <= 0:
            return jsonify(success=False, error="Amount must be positive"), 400

        result = supabase.table("cybucks").select("*").eq("username", username).execute()
        if not result.data:
            return jsonify(success=False, error="User not found"), 404

        user = result.data[0]
        balance = user["balance"]

        if balance < amount:
            return jsonify(success=False, error="Insufficient funds"), 400

        new_balance = balance - amount

        supabase.table("cybucks").update({"balance": new_balance}).eq("username", username).execute()

        logging.info(f"{amount} withdrawn for user {username}. New balance: {new_balance}")
        return jsonify(success=True, balance=new_balance)

    except Exception as e:
        logging.exception("Exception in /withdraw")
        return jsonify(success=False, error=str(e)), 500


# -------------------------------------------------------------------
# FRONTEND ROUTES (serving bank.html from Flask)
# -------------------------------------------------------------------
@app.route("/bank")
def bank_page():
    """Serve the Cybucks Banking System UI."""
    return app.send_static_file("bank.html")


@app.route("/")
def home():
    return "Cybucks backend running! Go to /bank for the banking UI."


# -------------------------------------------------------------------
# Local dev entrypoint
# -------------------------------------------------------------------
if __name__ == "__main__":
    # Render will override PORT env variable. Locally it will default to 5000.
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
