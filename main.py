from flask import Flask, request, jsonify
from supabase import create_client, Client
import os

app = Flask(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Table used: cybucks (columns: username text PK, balance int)

@app.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    username = data["username"]

    # Check if user exists
    res = supabase.table("cybucks").select("*").eq("username", username).execute()

    if len(res.data) == 0:
        # Create user with balance 0
        supabase.table("cybucks").insert({"username": username, "balance": 0}).execute()
        balance = 0
    else:
        balance = res.data[0]["balance"]

    return jsonify(success=True, balance=balance)


@app.route("/deposit", methods=["POST"])
def deposit():
    data = request.get_json()
    username = data["username"]
    amount = int(data["amount"])

    # Fetch current balance
    user = supabase.table("cybucks").select("*").eq("username", username).execute().data[0]
    new_balance = user["balance"] + amount

    supabase.table("cybucks").update({"balance": new_balance}).eq("username", username).execute()

    return jsonify(success=True, balance=new_balance)


@app.route("/withdraw", methods=["POST"])
def withdraw():
    data = request.get_json()
    username = data["username"]
    amount = int(data["amount"])

    user = supabase.table("cybucks").select("*").eq("username", username).execute().data[0]
    balance = user["balance"]

    if balance < amount:
        return jsonify(success=False, error="Insufficient funds")

    new_balance = balance - amount
    supabase.table("cybucks").update({"balance": new_balance}).eq("username", username).execute()

    return jsonify(success=True, balance=new_balance)


@app.route("/")
def home():
    return "Cybucks backend running."

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
