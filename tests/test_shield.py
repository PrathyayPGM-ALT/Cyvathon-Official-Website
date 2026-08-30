"""Exercise Cyvashield — free cover, claims, and the President's rulings."""
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
db.unique["insurance_policies"] = [("username",)]
db.defaults["insurance_policies"] = {"plan": "basic", "active": True,
                                     "claims_paid": 0, "claims_count": 0}
db.defaults["insurance_claims"] = {"status": "open", "amount_paid": 0, "ref_kind": "",
                                   "description": "", "evidence_url": "", "accused": "",
                                   "decision_note": "", "plan": "basic"}
db.defaults["deliveries"] = {"status": "open", "kind": "custom"}

now = main._now()
ISO = lambda dt: dt.isoformat()

db.seed("cybucks", [
    {"id": 1, "username": "Prathyay", "designation": "President", "balance": 9000, "approved": True},
    {"id": 2, "username": "Aarav", "designation": "Citizen", "balance": 500, "approved": True},
    {"id": 3, "username": "Meera", "designation": "Citizen", "balance": 500, "approved": True},
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
def claims(): return db.data.get("insurance_claims", [])

GOOD = "A courier took my parcel and it never turned up anywhere."


print("\n=== 1. cover is free and chosen ===")
r = client_as().post("/shield/enrol", json={"plan": "basic"})
check("logged out cannot enrol", r.status_code, 401)
d = client_as("Aarav").get("/shield/config").get_json()
check("starts uncovered", d["enrolled"], False)
check("  three plans offered", len(d["plans"]), 3)
check("  every plan is free", all("cap" in p for p in d["plans"]), True)

r = client_as("Aarav").post("/shield/enrol", json={"plan": "full"})
check("citizen can pick any plan", r.get_json()["plan"], "full")
check("  nothing was charged", cyb("Aarav")["balance"], 500)
d = client_as("Aarav").get("/shield/config").get_json()
check("  now covered", d["enrolled"], True)
r = client_as("Aarav").post("/shield/enrol", json={"plan": "nonsense"})
check("unknown plan rejected", r.status_code, 400)


print("\n=== 2. filing a claim ===")
r = client_as("Meera").post("/shield/claim", json={
    "category": "theft", "amount": 100, "description": GOOD})
check("uncovered citizen cannot claim", r.status_code, 403)

client_as("Meera").post("/shield/enrol", json={"plan": "basic"})
r = client_as("Meera").post("/shield/claim", json={
    "category": "scam", "amount": 100, "description": GOOD})
check("plan must cover the category", r.status_code, 403)
check("  and says so plainly", "doesn't cover" in r.get_json()["error"], True)

r = client_as("Meera").post("/shield/claim", json={
    "category": "parcel", "amount": 900, "description": GOOD})
check("claim above the plan cap refused", r.status_code, 400)
r = client_as("Meera").post("/shield/claim", json={
    "category": "parcel", "amount": 100, "description": "lost"})
check("a one-word story is refused", r.status_code, 400)
r = client_as("Meera").post("/shield/claim", json={
    "category": "parcel", "amount": 0, "description": GOOD})
check("must say what was lost", r.status_code, 400)
r = client_as("Meera").post("/shield/claim", json={
    "category": "parcel", "amount": 100, "description": GOOD,
    "evidence_url": "javascript:alert(1)"})
check("non-https evidence refused", r.status_code, 400)
r = client_as("Meera").post("/shield/claim", json={
    "category": "parcel", "amount": 100, "description": GOOD, "accused": "Meera"})
check("cannot report yourself", r.status_code, 400)

before = len(db.data.get("notifications", []))
r = client_as("Meera").post("/shield/claim", json={
    "category": "parcel", "amount": 300, "description": GOOD, "accused": "Aarav"})
check("a valid claim is filed", r.status_code, 200)
check("  it starts open", r.get_json()["claim"]["status"], "open")
check("  the President is told", len(db.data["notifications"]) > before, True)


print("\n=== 3. the monthly limit holds ===")
client_as("Meera").post("/shield/claim", json={
    "category": "market", "amount": 50, "description": GOOD})
r = client_as("Meera").post("/shield/claim", json={
    "category": "parcel", "amount": 50, "description": GOOD})
check("Basic allows only two a month", r.status_code, 429)


print("\n=== 4. an upgrade can't be used to inflate a fresh claim ===")
client_as("Meera").post("/shield/enrol", json={"plan": "full"})   # switched_at = now
r = client_as("Meera").post("/shield/claim", json={
    "category": "scam", "amount": 3000, "description": GOOD})
check("the bigger cap waits", r.status_code, 403)
check("  and explains why", "applies in" in r.get_json()["error"], True)
# Backdate the switch and the higher cap applies.
pol = [p for p in db.data["insurance_policies"] if p["username"] == "Meera"][0]
pol["switched_at"] = ISO(now - timedelta(days=5))
r = client_as("Meera").post("/shield/claim", json={
    "category": "scam", "amount": 3000, "description": GOOD})
check("once settled, the cap applies", r.status_code, 200)


print("\n=== 5. only the President rules ===")
cid = claims()[0]["id"]
r = client_as("Aarav").get("/shield/admin")
check("citizen cannot open the claim desk", r.status_code, 403)
r = client_as("Aarav").post("/shield/admin/decide",
                            json={"claim_id": cid, "action": "approve"})
check("citizen cannot approve claims", r.status_code, 403)
d = client_as("Prathyay").get("/shield/admin").get_json()
check("President sees the queue", len(d["open_claims"]) >= 1, True)
check("  and nothing paid yet", d["paid_total"], 0)


print("\n=== 6. the records are checked for the President ===")
db.seed("deliveries", [
    {"id": 500, "recipient": "Meera", "sender": "Aarav", "status": "delivered",
     "courier": "Kabir", "delivered_at": ISO(now), "item_label": "book"},
    {"id": 501, "recipient": "Meera", "sender": "Aarav", "status": "picked_up",
     "courier": "Kabir", "item_label": "pen"},
])
pol["switched_at"] = ISO(now - timedelta(days=9))
for c in claims():
    c["created_at"] = ISO(now - timedelta(days=40))      # clear the monthly window

client_as("Meera").post("/shield/claim", json={
    "category": "parcel", "amount": 200, "description": GOOD,
    "ref_kind": "delivery", "ref_id": 500})
d = client_as("Prathyay").get("/shield/admin").get_json()
lie = [c for c in d["open_claims"] if c["ref_id"] == 500][0]
texts = " ".join(k["text"] for k in lie["checks"])
check("a parcel the claimant signed for is flagged",
      "already confirmed" in texts, True)
check("  and flagged as contradicting", any(k["tone"] == "bad" for k in lie["checks"]), True)

client_as("Meera").post("/shield/claim", json={
    "category": "parcel", "amount": 200, "description": GOOD,
    "ref_kind": "delivery", "ref_id": 501})
d = client_as("Prathyay").get("/shield/admin").get_json()
real = [c for c in d["open_claims"] if c["ref_id"] == 501][0]
check("an undelivered parcel supports the claim",
      any(k["tone"] == "good" for k in real["checks"]), True)
check("  and names the courier",
      "Kabir" in " ".join(k["text"] for k in real["checks"]), True)

client_as("Meera").post("/shield/claim", json={
    "category": "parcel", "amount": 200, "description": GOOD,
    "ref_kind": "delivery", "ref_id": 9999})
d = client_as("Prathyay").get("/shield/admin").get_json()
ghost = [c for c in d["open_claims"] if c["ref_id"] == 9999][0]
check("a parcel that doesn't exist is caught",
      "No Cyvazon parcel" in " ".join(k["text"] for k in ghost["checks"]), True)


print("\n=== 7. approving pays out ===")
target = [c for c in claims() if c["ref_id"] == 501][0]["id"]
bal = cyb("Meera")["balance"]
r = client_as("Prathyay").post("/shield/admin/decide",
    json={"claim_id": target, "action": "approve", "amount": 500, "note": "Parcel confirmed lost."})
check("cannot award more than claimed", r.status_code, 400)
r = client_as("Prathyay").post("/shield/admin/decide",
    json={"claim_id": target, "action": "approve", "amount": 150, "note": "Partly upheld."})
check("President can award less than asked", r.get_json()["paid"], 150)
check("  the citizen was paid", cyb("Meera")["balance"], bal + 150)
check("  from the Treasury",
      any(f["kind"] == "insurance_payout" for f in db.data.get("treasury_flows", [])), True)
check("  and told", any("claim was approved" in n["message"] for n in db.data["notifications"]), True)
pol2 = [p for p in db.data["insurance_policies"] if p["username"] == "Meera"][0]
check("  the policy records the payout", pol2["claims_paid"], 150)
r = client_as("Prathyay").post("/shield/admin/decide",
    json={"claim_id": target, "action": "approve", "amount": 100})
check("a settled claim can't be paid twice", r.status_code, 400)


print("\n=== 8. rejecting and fraud ===")
rej = [c for c in claims() if c["ref_id"] == 9999][0]["id"]
r = client_as("Prathyay").post("/shield/admin/decide",
    json={"claim_id": rej, "action": "reject", "note": "No such parcel."})
check("President can reject", r.get_json()["status"], "rejected")
check("  nothing was paid", [c for c in claims() if c["id"] == rej][0]["amount_paid"], 0)

fraud = [c for c in claims() if c["ref_id"] == 500][0]["id"]
recs_before = len(db.data.get("criminal_records", []))
r = client_as("Prathyay").post("/shield/admin/decide",
    json={"claim_id": fraud, "action": "fraud", "note": "You signed for it yourself."})
check("President can rule a claim fraudulent", r.get_json()["status"], "fraudulent")
check("  it goes on their criminal record",
      len(db.data.get("criminal_records", [])) > recs_before, True)
check("  which bars them from office",
      main.office_eligibility(cyb("Meera"))[0], False)


print("\n=== 9. dashboard summary ===")
s = client_as("Prathyay").get("/shield/summary").get_json()
check("President sees the pending count", s["waiting"] >= 1, True)
check("  and is flagged as admin", s["is_admin"], True)
s = client_as("Aarav").get("/shield/summary").get_json()
check("a citizen is not", s["is_admin"], False)
check("  and sees their own cover", s["enrolled"], True)


print("\n=== 10. the claim desk survives an un-migrated database ===")
_real = db.table
class _Missing:
    def __getattr__(self, n): raise Exception("relation does not exist")
db.table = lambda n: _Missing() if n.startswith("insurance_") else _real(n)
s = client_as("Prathyay").get("/shield/summary").get_json()
check("summary still succeeds", s["success"], True)
check("  reports the scheme as not set up", s["enabled"], False)
check("  but keeps the President's admin flag", s["is_admin"], True)
check("the claim desk explains the real problem",
      client_as("Prathyay").get("/shield/admin").status_code, 503)
db.table = _real

print(f"\n{'='*46}\n  {PASS} passed, {FAIL} failed\n{'='*46}")
sys.exit(1 if FAIL else 0)
