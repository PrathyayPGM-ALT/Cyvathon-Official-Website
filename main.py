from flask import Flask, request, jsonify
from supabase import create_client
from flask_cors import CORS
import os
import logging

# -------------------------------------------------
# Flask app – also serves static files (bank.html)
# -------------------------------------------------
app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

logging.basicConfig(level=logging.INFO)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    logging.error("Supabase URL or Key not set in environment variables!")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# -------------------------------------------------
# Routes for pages
# -------------------------------------------------

@app.route("/")
def index():
    # Serve the bank page directly at root
    return app.send_static_file("bank.html")


@app.route("/bank")
def bank_page():
    # Also serve it at /bank, so both URLs work
    return app.send_static_file("bank.html")

# -------------------------------------------------
# Auth / basic bank
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

        # New user → create with 0 balance
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


# -------------------------------------------------
# List all users (for dropdown)
# -------------------------------------------------
@app.route("/users", methods=["GET"])
def list_users():
    try:
        result = supabase.table("cybucks").select("username").execute()
        usernames = [row["username"] for row in result.data]
        return jsonify(success=True, users=usernames)
    except Exception as e:
        logging.exception("Exception in /users")
        return jsonify(success=False, error=str(e)), 500


# -------------------------------------------------
# Send Cybucks from one user to another
# -------------------------------------------------
@app.route("/send", methods=["POST"])
def send_cybucks():
    try:
        data = request.get_json()
        if not data:
            return jsonify(success=False, error="Missing JSON body"), 400

        from_user = data.get("from_username")
        to_user = data.get("to_username")
        amount = data.get("amount")

        if not from_user or not to_user or amount is None:
            return jsonify(success=False, error="from_username, to_username and amount required"), 400

        if from_user == to_user:
            return jsonify(success=False, error="You cannot send Cybucks to yourself"), 400

        try:
            amount = int(amount)
        except ValueError:
            return jsonify(success=False, error="Amount must be a number"), 400

        if amount <= 0:
            return jsonify(success=False, error="Amount must be positive"), 400

        # Get sender
        sender_result = supabase.table("cybucks").select("*").eq("username", from_user).execute()
        if not sender_result.data:
            return jsonify(success=False, error="Sender not found"), 404
        sender = sender_result.data[0]

        # Get receiver
        receiver_result = supabase.table("cybucks").select("*").eq("username", to_user).execute()
        if not receiver_result.data:
            return jsonify(success=False, error="Receiver not found"), 404
        receiver = receiver_result.data[0]

        sender_balance = sender["balance"]
        receiver_balance = receiver["balance"]

        if sender_balance < amount:
            return jsonify(success=False, error="Insufficient funds"), 400

        new_sender_balance = sender_balance - amount
        new_receiver_balance = receiver_balance + amount

        # Update balances
        supabase.table("cybucks").update(
            {"balance": new_sender_balance}
        ).eq("username", from_user).execute()

        supabase.table("cybucks").update(
            {"balance": new_receiver_balance}
        ).eq("username", to_user).execute()

        logging.info(
            f"{amount} Cybucks sent from {from_user} to {to_user}. "
            f"Sender balance: {new_sender_balance}, Receiver balance: {new_receiver_balance}"
        )

        return jsonify(
            success=True,
            sender_balance=new_sender_balance,
            receiver_balance=new_receiver_balance
        )

    except Exception as e:
        logging.exception("Exception in /send")
        return jsonify(success=False, error=str(e)), 500


# -------------------------------------------------
# Run locally (Render will use gunicorn / start command)
# -------------------------------------------------
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
