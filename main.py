from flask import Flask, request, jsonify
from supabase import create_client
from flask_cors import CORS
import os
import logging

# Serve static files (like bank.html) from the repo root
app = Flask(__name__, static_folder=".", static_url_path="")
CORS(app)

logging.basicConfig(level=logging.INFO)  # Enable info level logging

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    logging.error("Supabase URL or Key not set in environment variables!")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


# ---------- PAGES ----------

@app.route("/bank")
def bank_page():
    # This serves bank.html from the repo root
    return app.send_static_file("bank.html")


@app.route("/")
def home():
    return "Cybucks backend running!"


# ---------- API: AUTH & BALANCE ----------

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

        # New user: create with 0 balance
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


# ---------- API: OPTIONAL DEPOSIT/WITHDRAW (not used by UI, but kept) ----------

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


# ---------- API: USERS & TRANSFERS ----------

@app.route("/users", methods=["GET"])
def get_users():
    """Return list of all usernames."""
    try:
        result = supabase.table("cybucks").select("username, balance").execute()
        users = [row["username"] for row in (result.data or [])]
        return jsonify(success=True, users=users)
    except Exception as e:
        logging.exception("Exception in /users")
        return jsonify(success=False, error=str(e)), 500


@app.route("/transfer", methods=["POST"])
def transfer():
    """
    Transfer Cybucks from one user to another.

    Body: { "from_user": "...", "to_user": "...", "amount": 10 }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify(success=False, error="Missing JSON body"), 400

        from_user = data.get("from_user")
        to_user = data.get("to_user")
        amount = data.get("amount")

        if not from_user or not to_user or amount is None:
            return jsonify(success=False, error="from_user, to_user, and amount are required"), 400

        if from_user == to_user:
            return jsonify(success=False, error="Cannot send Cybucks to yourself"), 400

        amount = int(amount)
        if amount <= 0:
            return jsonify(success=False, error="Amount must be positive"), 400

        # Get sender
        sender_result = supabase.table("cybucks").select("*").eq("username", from_user).execute()
        if not sender_result.data:
            return jsonify(success=False, error="Sender not found"), 404
        sender = sender_result.data[0]

        # Get recipient
        recipient_result = supabase.table("cybucks").select("*").eq("username", to_user).execute()
        if not recipient_result.data:
            return jsonify(success=False, error="Recipient not found"), 404
        recipient = recipient_result.data[0]

        if sender["balance"] < amount:
            return jsonify(success=False, error="Insufficient funds"), 400

        new_sender_balance = sender["balance"] - amount
        new_recipient_balance = recipient["balance"] + amount

        # Update both balances
        supabase.table("cybucks").update(
            {"balance": new_sender_balance}
        ).eq("username", from_user).execute()

        supabase.table("cybucks").update(
            {"balance": new_recipient_balance}
        ).eq("username", to_user).execute()

        logging.info(
            f"Transfer: {from_user} sent {amount} to {to_user}. "
            f"New sender balance: {new_sender_balance}, "
            f"recipient balance: {new_recipient_balance}"
        )

        return jsonify(success=True, balance=new_sender_balance)

    except Exception as e:
        logging.exception("Exception in /transfer")
        return jsonify(success=False, error=str(e)), 500


if __name__ == "__main__":
    # Render sets PORT env var
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
