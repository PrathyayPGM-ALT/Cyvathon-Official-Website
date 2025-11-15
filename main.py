from flask import Flask, request, jsonify, send_from_directory
from supabase import create_client
from flask_cors import CORS
import os
import logging

# Serve static files (like bank.html) from the repository root
app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

logging.basicConfig(level=logging.INFO)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    logging.error("Supabase URL or Key not set in environment variables!")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


# ---------- AUTH / LOGIN ----------

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


# ---------- LIST ALL USERS ----------

@app.route("/users", methods=["GET"])
def get_users():
    """
    Return list of all users and their balances.
    Frontend will filter out the current user from the dropdown.
    """
    try:
        result = supabase.table("cybucks").select("username,balance").execute()
        users = [
            {"username": row["username"], "balance": row["balance"]}
            for row in (result.data or [])
        ]
        return jsonify(success=True, users=users)
    except Exception as e:
        logging.exception("Exception in /users")
        return jsonify(success=False, error=str(e)), 500


# ---------- TRANSFER CYBUCKS BETWEEN USERS ----------

@app.route("/transfer", methods=["POST"])
def transfer():
    """
    Body: { "from_username": "...", "to_username": "...", "amount": 10 }
    Moves Cybucks from one user to another.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify(success=False, error="Missing JSON body"), 400

        from_username = data.get("from_username")
        to_username = data.get("to_username")
        amount = data.get("amount")

        if not from_username or not to_username or amount is None:
            return jsonify(success=False, error="from, to, and amount required"), 400

        if from_username == to_username:
            return jsonify(success=False, error="You cannot send Cybucks to yourself"), 400

        amount = int(amount)
        if amount <= 0:
            return jsonify(success=False, error="Amount must be positive"), 400

        # Fetch sender
        sender_res = supabase.table("cybucks").select("*").eq("username", from_username).execute()
        if not sender_res.data:
            return jsonify(success=False, error="Sender not found"), 404
        sender = sender_res.data[0]

        # Fetch receiver
        receiver_res = supabase.table("cybucks").select("*").eq("username", to_username).execute()
        if not receiver_res.data:
            return jsonify(success=False, error="Recipient not found"), 404
        receiver = receiver_res.data[0]

        sender_balance = sender["balance"]
        if sender_balance < amount:
            return jsonify(success=False, error="Insufficient funds"), 400

        new_sender_balance = sender_balance - amount
        new_receiver_balance = receiver["balance"] + amount

        # Update both balances (not a strict transaction, but fine for this project)
        supabase.table("cybucks").update({"balance": new_sender_balance}).eq(
            "username", from_username
        ).execute()

        supabase.table("cybucks").update({"balance": new_receiver_balance}).eq(
            "username", to_username
        ).execute()

        logging.info(
            f"Transfer: {amount} from {from_username} "
            f"(bal {sender_balance}->{new_sender_balance}) "
            f"to {to_username} (bal {receiver['balance']}->{new_receiver_balance})"
        )

        # Return new balance for the sender (current user)
        return jsonify(success=True, balance=new_sender_balance)

    except Exception as e:
        logging.exception("Exception in /transfer")
        return jsonify(success=False, error=str(e)), 500


# ---------- SIMPLE PAGES ----------

@app.route("/")
def home():
    return "Cybucks backend running!"


@app.route("/bank")
def bank_page():
    # Serve bank.html from the repo root
    return send_from_directory(app.static_folder, "bank.html")


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
