from flask import Flask, request, jsonify
from supabase import create_client
from flask_cors import CORS
import os

app = Flask(__name__)
CORS(app)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


@app.route("/login", methods=["POST"])
def login():
    try:
        data = request.get_json()
        username = data["username"]
        password = data["password"]

        result = supabase.table("cybucks").select("*").eq("username", username).execute()

        if not result.data:
            supabase.table("cybucks").insert({
                "username": username,
                "password": password,
                "balance": 0
            }).execute()

            return jsonify(success=True, balance=0)

        user = result.data[0]

        if user["password"] != password:
            return jsonify(success=False, error="Incorrect password")

        return jsonify(success=True, balance=user["balance"])

    except Exception as e:
        return jsonify(success=False, error=str(e))


@app.route("/deposit", methods=["POST"])
def deposit():
    data = request.get_json()
    username = data["username"]
    amount = int(data["amount"])

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
    return "Cybucks backend running!"
