from flask import Flask, request, jsonify
from supabase import create_client
from flask_cors import CORS
import os
import logging

# ----------------- FLASK + CORS SETUP -----------------

app = Flask(__name__)
CORS(app)

logging.basicConfig(level=logging.INFO)  # Enable info level logging

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    logging.error("Supabase URL or Key not set in environment variables!")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ----------------- AUTH / LOGIN -----------------------


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

        # Register new user if not exists
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


# ----------------- LIST USERS (FOR DROPDOWN) ----------


@app.route("/users", methods=["GET"])
def get_users():
    """Return list of all usernames so frontend can build dropdown."""
    try:
        result = supabase.table("cybucks").select("username").execute()
        usernames = [row["username"] for row in result.data]
        return jsonify(success=True, users=usernames)
    except Exception as e:
        logging.exception("Exception in /users")
        return jsonify(success=False, error=str(e)), 500


# ----------------- TRANSFER CYBUCKS -------------------


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
            return jsonify(success=False, error="from_username, to_username and amount are required"), 400

        if from_username == to_username:
            return jsonify(success=False, error="You cannot send Cybucks to yourself"), 400

        amount = int(amount)
        if amount <= 0:
            return jsonify(success=False, error="Amount must be positive"), 400

        # Fetch both users
        from_res = supabase.table("cybucks").select("*").eq("username", from_username).execute()
        to_res = supabase.table("cybucks").select("*").eq("username", to_username).execute()

        if not from_res.data:
            return jsonify(success=False, error="Sender not found"), 404
        if not to_res.data:
            return jsonify(success=False, error="Recipient not found"), 404

        from_user = from_res.data[0]
        to_user = to_res.data[0]

        if from_user["balance"] < amount:
            return jsonify(success=False, error="Insufficient funds"), 400

        new_from_balance = from_user["balance"] - amount
        new_to_balance = to_user["balance"] + amount

        # Update balances (simple two-step update – fine for your use case)
        supabase.table("cybucks").update({"balance": new_from_balance}).eq(
            "username", from_username
        ).execute()

        supabase.table("cybucks").update({"balance": new_to_balance}).eq(
            "username", to_username
        ).execute()

        logging.info(
            f"Transfer {amount} from {from_username} to {to_username}. "
            f"New balances: {from_username}={new_from_balance}, {to_username}={new_to_balance}"
        )

        return jsonify(success=True, balance=new_from_balance)

    except Exception as e:
        logging.exception("Exception in /transfer")
        return jsonify(success=False, error=str(e)), 500


# ----------------- OPTIONAL: KEEP OLD ENDPOINTS -------

# You can keep /deposit and /withdraw if you still want them for future,
# or delete them if you are sure you will not use them. For now I am leaving
# them as they are; the frontend just will not call them.


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


# ----------------- ROOT -------------------------------


@app.route("/")
def home():
    return "Cybucks backend running!"


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
