"""Exercise Cyvalend — the shelf, borrowing, deposits and returns."""
import logging, warnings, os, sys
logging.disable(logging.CRITICAL); warnings.filterwarnings("ignore")
os.environ.update(
    SUPABASE_URL="https://example.supabase.co",
    SUPABASE_KEY="eyJhbGciOiAiSFMyNTYiLCAidHlwIjogIkpXVCJ9.eyJpc3MiOiAic3VwYWJhc2UiLCAicm9sZSI6ICJhbm9uIiwgImV4cCI6IDk5OTk5OTk5OTl9.sig",
    SECRET_KEY="test-key")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import main
from fakedb import FakeSupabase
from datetime import timedelta

db = FakeSupabase()
main.supabase = db
db.defaults["lend_items"] = {"status": "available", "times_lent": 0, "deposit": 0,
                             "max_days": 1, "condition": "good", "description": "",
                             "image_url": "", "category": "other"}
db.defaults["lend_loans"] = {"status": "requested", "deposit_held": 0, "days": 1,
                             "reason": "", "note": "", "late": False}
db.defaults["deliveries"] = {"status": "open", "kind": "custom"}

now = main._now()
ISO = lambda dt: dt.isoformat()

db.seed("cybucks", [
    {"id": 1, "username": "Aarav", "designation": "Citizen", "balance": 500, "approved": True,
     "home_class": "8B", "home_area": "Classroom"},
    {"id": 2, "username": "Meera", "designation": "Citizen", "balance": 500, "approved": True,
     "home_class": "10A", "home_area": "Library"},
    {"id": 3, "username": "Kabir", "designation": "Citizen", "balance": 20, "approved": True},
])
db.seed("treasury", [{"id": 1, "balance": 100000, "pufb": 0, "aquilines": 0, "cybits": 0}])

PASS = FAIL = 0
def check(label, got, want):
    global PASS, FAIL
    ok = got == want
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(f"  {'OK  ' if ok else 'FAIL'} {label}" + ("" if ok else f"\n         got={got!r} want={want!r}"))

app = main.app
app.config["TESTING"] = True
main.limiter.enabled = False

def client_as(username=None):
    c = app.test_client()
    if username:
        with c.session_transaction() as sess:
            sess["username"] = username
    return c

def cyb(n): return [r for r in db.data["cybucks"] if r["username"] == n][0]
def items(): return db.data.get("lend_items", [])
def loans(): return db.data.get("lend_loans", [])


print("\n=== 1. putting something on the shelf ===")
r = client_as().post("/cyvalend/item", json={"name": "Calculator"})
check("logged out cannot lend", r.status_code, 401)

r = client_as("Aarav").post("/cyvalend/item", json={
    "name": "Casio calculator", "category": "calculator", "condition": "good",
    "max_days": 3, "deposit": 50, "description": "Case is cracked but it works."})
check("citizen can list an item", r.status_code, 200)
calc = r.get_json()["item"]
check("  it starts available", calc["status"], "available")
check("  deposit kept", calc["deposit"], 50)
check("  category resolved", calc["category_label"], "Calculator")

r = client_as("Aarav").post("/cyvalend/item", json={"name": ""})
check("nameless item refused", r.status_code, 400)
r = client_as("Aarav").post("/cyvalend/item", json={"name": "x", "deposit": 99999})
check("deposit above the cap refused", r.status_code, 400)
r = client_as("Aarav").post("/cyvalend/item", json={"name": "x", "image_url": "javascript:1"})
check("non-https photo refused", r.status_code, 400)
r = client_as("Aarav").post("/cyvalend/item", json={"name": "Spare pen", "category": "stationery"})
pen = r.get_json()["item"]
check("a second item is fine", pen["status"], "available")

d = client_as("Meera").get("/cyvalend/shelf").get_json()
check("others see it on the shelf", sorted(i["name"] for i in d["shelf"]),
      ["Casio calculator", "Spare pen"])
check("  and none of it is theirs", d["mine"], [])
d = client_as("Aarav").get("/cyvalend/shelf").get_json()
check("the owner sees it under their own things", len(d["mine"]), 2)
check("  and not on the borrowable shelf", d["shelf"], [])


print("\n=== 2. asking to borrow ===")
r = client_as("Aarav").post("/cyvalend/request", json={"item_id": calc["id"]})
check("cannot borrow your own thing", r.status_code, 400)
r = client_as("Kabir").post("/cyvalend/request", json={"item_id": calc["id"], "days": 2})
check("a citizen who can't cover the deposit is stopped", r.status_code, 400)
check("  and told why", "deposit" in r.get_json()["error"], True)

before = len(db.data.get("notifications", []))
r = client_as("Meera").post("/cyvalend/request", json={
    "item_id": calc["id"], "days": 9, "reason": "Maths test on Friday."})
check("request accepted", r.status_code, 200)
check("  days clamped to the owner's limit", r.get_json()["loan"]["days"], 3)
check("  the owner is told", len(db.data["notifications"]) > before, True)
r = client_as("Meera").post("/cyvalend/request", json={"item_id": calc["id"]})
check("cannot ask twice for the same thing", r.status_code, 400)

d = client_as("Meera").get("/cyvalend/shelf").get_json()
check("the shelf shows you've already asked",
      [i["asked"] for i in d["shelf"] if i["id"] == calc["id"]], [True])


print("\n=== 3. the owner decides ===")
lid = loans()[0]["id"]
r = client_as("Kabir").post("/cyvalend/decide", json={"loan_id": lid, "action": "approve"})
check("a stranger cannot approve", r.status_code, 403)
r = client_as("Meera").post("/cyvalend/decide", json={"loan_id": lid, "action": "approve"})
check("the borrower cannot approve their own request", r.status_code, 403)

bal = cyb("Meera")["balance"]
r = client_as("Aarav").post("/cyvalend/decide", json={"loan_id": lid, "action": "approve"})
check("the owner can lend it", r.get_json()["status"], "out")
check("  the deposit was held", cyb("Meera")["balance"], bal - 50)
check("  the item is marked out",
      [i["status"] for i in items() if i["id"] == calc["id"]], ["out"])
check("  a due date was set", bool(loans()[0]["due_at"]), True)
check("  the borrower was told",
      any("lent you" in n["message"] for n in db.data["notifications"]), True)

r = client_as("Kabir").post("/cyvalend/request", json={"item_id": calc["id"]})
check("an item that's out can't be requested", r.status_code, 409)


print("\n=== 4. only the owner closes the loan ===")
r = client_as("Meera").post("/cyvalend/return", json={"loan_id": lid})
check("the borrower CANNOT mark it returned", r.status_code, 403)
check("  and is told who can", "owner" in r.get_json()["error"], True)

bal = cyb("Meera")["balance"]
r = client_as("Aarav").post("/cyvalend/return", json={"loan_id": lid})
check("the owner confirms the return", r.get_json()["status"], "returned")
check("  the deposit came back", cyb("Meera")["balance"], bal + 50)
check("  the item is back on the shelf",
      [i["status"] for i in items() if i["id"] == calc["id"]], ["available"])
check("  and the tally went up",
      [i["times_lent"] for i in items() if i["id"] == calc["id"]], [1])
check("  it wasn't late", loans()[0]["late"], False)
r = client_as("Aarav").post("/cyvalend/return", json={"loan_id": lid})
check("a closed loan can't be closed twice", r.status_code, 400)


print("\n=== 5. late returns are recorded ===")
client_as("Meera").post("/cyvalend/request", json={"item_id": pen["id"], "days": 1})
lid2 = [l for l in loans() if l["item_id"] == pen["id"]][0]["id"]
client_as("Aarav").post("/cyvalend/decide", json={"loan_id": lid2, "action": "approve"})
# Wind the clock back so the loan is overdue.
[l for l in loans() if l["id"] == lid2][0]["due_at"] = ISO(now - timedelta(days=2))
s = client_as("Meera").get("/cyvalend/summary").get_json()
check("the borrower's summary flags it overdue", s["overdue"], 1)
d = client_as("Meera").get("/cyvalend/loans").get_json()
check("  and so does the loan itself",
      [l["overdue"] for l in d["borrowed"] if l["id"] == lid2], [True])
r = client_as("Aarav").post("/cyvalend/return", json={"loan_id": lid2})
check("returning late is allowed but marked", r.get_json()["late"], True)
d = client_as("Meera").get("/cyvalend/loans").get_json()
check("  and counted against the borrower", d["stats"]["late"], 1)
check("  who is still credited with the return", d["stats"]["returned"], 2)


print("\n=== 6. delivery by Cyvazon ===")
client_as("Meera").post("/cyvalend/request", json={"item_id": pen["id"], "days": 1})
lid3 = [l for l in loans() if l["item_id"] == pen["id"] and l["status"] == "requested"][0]["id"]
before = len(db.data.get("deliveries", []))
r = client_as("Aarav").post("/cyvalend/decide",
                            json={"loan_id": lid3, "action": "approve", "deliver": True})
check("approving with delivery raises a parcel", len(db.data["deliveries"]), before + 1)
p = db.data["deliveries"][-1]
check("  from the owner", p["sender"], "Aarav")
check("  to the borrower", p["recipient"], "Meera")
check("  tagged as a loan", p["kind"], "lend")
check("  and linked on the loan",
      [l["delivery_id"] for l in loans() if l["id"] == lid3], [p["id"]])
client_as("Aarav").post("/cyvalend/return", json={"loan_id": lid3})


print("\n=== 7. taking things off the shelf ===")
r = client_as("Meera").post("/cyvalend/item/update",
                            json={"item_id": calc["id"], "status": "retired"})
check("cannot retire someone else's item", r.status_code, 404)
r = client_as("Aarav").post("/cyvalend/item/update",
                            json={"item_id": calc["id"], "status": "retired"})
check("the owner can take it off the shelf", r.get_json()["item"]["status"], "retired")
d = client_as("Meera").get("/cyvalend/shelf").get_json()
check("  it disappears from the shelf",
      [i for i in d["shelf"] if i["id"] == calc["id"]], [])
client_as("Aarav").post("/cyvalend/item/update",
                        json={"item_id": calc["id"], "status": "available"})

client_as("Meera").post("/cyvalend/request", json={"item_id": calc["id"]})
lid4 = [l for l in loans() if l["status"] == "requested"][0]["id"]
client_as("Aarav").post("/cyvalend/decide", json={"loan_id": lid4, "action": "approve"})
r = client_as("Aarav").post("/cyvalend/item/remove", json={"item_id": calc["id"]})
check("an item out on loan can't be deleted", r.status_code, 400)
client_as("Aarav").post("/cyvalend/return", json={"loan_id": lid4})
r = client_as("Aarav").post("/cyvalend/item/remove", json={"item_id": calc["id"]})
check("once back, it can be deleted", r.status_code, 200)


print("\n=== 8. declining and withdrawing ===")
client_as("Meera").post("/cyvalend/request", json={"item_id": pen["id"]})
lid5 = [l for l in loans() if l["status"] == "requested"][0]["id"]
r = client_as("Aarav").post("/cyvalend/decide",
                            json={"loan_id": lid5, "action": "decline", "note": "Need it myself."})
check("the owner can decline", r.get_json()["status"], "declined")
check("  the item stays available",
      [i["status"] for i in items() if i["id"] == pen["id"]], ["available"])

client_as("Meera").post("/cyvalend/request", json={"item_id": pen["id"]})
lid6 = [l for l in loans() if l["status"] == "requested"][0]["id"]
r = client_as("Aarav").post("/cyvalend/decide", json={"loan_id": lid6, "action": "cancel"})
check("the owner cannot withdraw the borrower's request", r.status_code, 403)
r = client_as("Meera").post("/cyvalend/decide", json={"loan_id": lid6, "action": "cancel"})
check("the borrower can withdraw", r.get_json()["status"], "cancelled")


print("\n=== 9. insurance covers a lent item ===")
cats = [c["key"] for c in main.CLAIM_CATEGORIES]
check("there's a claim category for it", "lend" in cats, True)
std = [p for p in main.INSURANCE_PLANS if p["key"] == "standard"][0]
check("  covered from Standard up", "lend" in std["covers"], True)
basic = [p for p in main.INSURANCE_PLANS if p["key"] == "basic"][0]
check("  but not on Basic", "lend" in basic["covers"], False)


print("\n=== 10. the page survives an un-migrated database ===")
_real = db.table
class _Missing:
    def __getattr__(self, n): raise Exception("relation does not exist")
db.table = lambda n: _Missing() if n.startswith("lend_") else _real(n)
s = client_as("Aarav").get("/cyvalend/summary").get_json()
check("summary still succeeds", s["success"], True)
check("  and reports it as not set up", s["enabled"], False)
r = client_as("Aarav").get("/cyvalend/shelf")
check("the shelf explains the real problem", r.status_code, 503)
check("  naming the migration", "migration_cyvalend.sql" in r.get_json()["error"], True)
db.table = _real

print(f"\n{'='*46}\n  {PASS} passed, {FAIL} failed\n{'='*46}")
sys.exit(1 if FAIL else 0)
