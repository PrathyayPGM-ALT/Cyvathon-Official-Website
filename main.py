from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client
import os
import logging

# -------------------------------------------------
# FLASK APP SETUP
# -------------------------------------------------

# static_folder="." lets Flask serve files from the repo root
# static_url_path="" means /bank.html etc map directly
app = Flask(__name__, static_folder=".", static_url_path="")
CORS(app)  # you technically do not need CORS now, but it is okay to keep

logging.basicConfig(level=logging.INFO)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Supabase URL or Key not set in environment variables!")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# -------------------------------------------------
# ROUTES FOR BANK FRONTEND
# -------------------------------------------------

@app.route("/")
def home():
    # You can change this to send the bank directly if you want:
    # return app.send_static_file("bank.html")
    return "Cybucks backend running!"

@app.route("/bank")
def bank_page():
    # Serve bank.html from the repo root
    return app.send_static_file("bank.html")


# -------------------------------------------------
# API ENDPOINTS
# -------------------------------------------------

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

        # New user -> create with 0 balance
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


# -------------------------------------------------
# MAIN
# -------------------------------------------------

if __name__ == "__main__":
    # Render will override PORT in production; this is fine for local testing.
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
