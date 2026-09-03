"""Exercise Cabinet powers — delegated duties, and policy that needs assent."""
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
db.defaults["policy_proposals"] = {"status": "pending", "reason": "", "decision_note": "",
                                   "ministry": "", "portfolio": ""}
db.defaults["pen_donations"] = {"status": "pledged", "count": 1, "condition": "working",
                                "rate": 400, "counted": 0, "amount_paid": 0,
                                "note": "", "decision_note": ""}
db.defaults["couriers"] = {"active": True, "status": "pending", "covers": "",
                           "note": "", "deliveries": 0}
db.defaults["insurance_claims"] = {"status": "open", "amount_paid": 0, "ref_kind": "",
                                   "description": "", "evidence_url": "", "accused": "",
                                   "decision_note": "", "plan": "basic"}
db.defaults["insurance_policies"] = {"plan": "full", "active": True,
                                     "claims_paid": 0, "claims_count": 0}

now = main._now()
ISO = lambda dt: dt.isoformat()

db.seed("cybucks", [
    {"id": 1, "username": "Prathyay", "designation": "President", "balance": 9000, "approved": True},
    {"id": 2, "username": "Vikram", "designation": "Minister", "balance": 500, "approved": True},
    {"id": 3, "username": "Nisha",  "designation": "Minister", "balance": 500, "approved": True},
    {"id": 4, "username": "Rhea",   "designation": "Minister", "balance": 500, "approved": True},
    {"id": 5, "username": "Dev",    "designation": "Minister", "balance": 500, "approved": True},
    {"id": 6, "username": "Aarav",  "designation": "Citizen",  "balance": 500, "approved": True},
])
db.seed("ministries", [
    {"id": 10, "name": "Ministry of Defence",  "minister": "Vikram", "rank": 1},
    {"id": 11, "name": "Ministry of Finance",  "minister": "Nisha",  "rank": 2},
    {"id": 12, "name": "Ministry of Transport", "minister": "Rhea",  "rank": 3},
    {"id": 13, "name": "Ministry of Justice",  "minister": "Dev",    "rank": 4},
    {"id": 14, "name": "Ministry of Culture",  "minister": "Aarav",  "rank": 5},
])
db.seed("treasury", [{"id": 1, "balance": 100000, "pufb": 0, "aquilines": 0,
                      "cybits": 0, "pens": 0}])
db.seed("config", [{"id": 1, "vat_rate": 0.10, "gdp_multiplier": 1, "pen_rate": 400,
                    "courier_wage": 500, "insurance_levy": 0}])

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

def fresh():
    """Portfolios are cached briefly; clear it between role changes."""
    main._portfolio_cache.clear()

def props(): return db.data.get("policy_proposals", [])
def cyb(n): return [r for r in db.data["cybucks"] if r["username"] == n][0]


print("\n=== 1. a ministry's name decides its brief ===")
fresh()
d = client_as("Vikram").get("/cabinet/powers").get_json()
check("Defence picks up the defence brief", d["portfolio"], "defence")
check("  and names the ministry", d["ministry"], "Ministry of Defence")
check("  with the Armoury as a duty", [x["key"] for x in d["duties"]], ["armoury"])
d = client_as("Nisha").get("/cabinet/powers").get_json()
check("Finance picks up the finance brief", d["portfolio"], "finance")
check("  which is policy-only, no duties", d["duties"], [])
check("  and covers the tax rate", "vat_rate" in [l["field"] for l in d["levers"]], True)
check("  and the GDP multiplier", "gdp_multiplier" in [l["field"] for l in d["levers"]], True)
d = client_as("Rhea").get("/cabinet/powers").get_json()
check("Transport vets couriers", [x["key"] for x in d["duties"]], ["couriers"])
d = client_as("Dev").get("/cabinet/powers").get_json()
check("Justice rules on claims", [x["key"] for x in d["duties"]], ["claims"])
d = client_as("Aarav").get("/cabinet/powers").get_json()
check("a ministry with no matching name gets no brief", d["portfolio"], "")


print("\n=== 2. duties are delegated outright ===")
fresh()
check("the Defence Minister may work the Armoury",
      main.has_power(cyb("Vikram"), "armoury"), True)
check("  but not the courier desk", main.has_power(cyb("Vikram"), "couriers"), False)
check("  nor the claim desk", main.has_power(cyb("Vikram"), "claims"), False)
check("the President may do all three",
      [main.has_power(cyb("Prathyay"), k) for k in ("armoury", "couriers", "claims")],
      [True, True, True])
check("a citizen may do none",
      [main.has_power(cyb("Aarav"), k) for k in ("armoury", "couriers", "claims")],
      [False, False, False])

# and it works end to end on the real desks
client_as("Aarav").post("/pens/pledge", json={"count": 2, "condition": "working"})
pid = db.data["pen_donations"][-1]["id"]
r = client_as("Aarav").get("/pens/registry")
check("a citizen still can't open the Armoury desk", r.status_code, 403)
r = client_as("Vikram").get("/pens/registry")
check("the Defence Minister can", r.status_code, 200)
before = cyb("Aarav")["balance"]
r = client_as("Vikram").post("/pens/registry/decide",
                             json={"donation_id": pid, "action": "receive"})
check("  and can log rounds in without asking", r.get_json()["status"], "received")
check("  paying the citizen", cyb("Aarav")["balance"], before + 800)
r = client_as("Nisha").get("/pens/registry")
check("the Finance Minister cannot open the Armoury", r.status_code, 403)

client_as("Aarav").post("/cyvazon/signup", json={"covers": "8B"})
r = client_as("Rhea").get("/cyvazon/admin")
check("the Transport Minister can open the courier desk", r.status_code, 200)
r = client_as("Rhea").post("/cyvazon/admin/decide",
                           json={"username": "Aarav", "action": "approve"})
check("  and approve a courier", r.get_json()["status"], "approved")
r = client_as("Vikram").get("/cyvazon/admin")
check("the Defence Minister cannot", r.status_code, 403)

db.seed("insurance_policies", [{"id": 1, "username": "Aarav", "plan": "full", "active": True,
                                "claims_paid": 0, "claims_count": 0}])
client_as("Aarav").post("/shield/claim", json={
    "category": "theft", "amount": 100,
    "description": "Someone lifted Cybucks off my account."})
cid = db.data["insurance_claims"][-1]["id"]
r = client_as("Dev").get("/shield/admin")
check("the Justice Minister can open the claim desk", r.status_code, 200)
r = client_as("Dev").post("/shield/admin/decide",
                          json={"claim_id": cid, "action": "approve", "amount": 100})
check("  and rule on a claim", r.get_json()["status"], "approved")
r = client_as("Rhea").get("/shield/admin")
check("the Transport Minister cannot", r.status_code, 403)


print("\n=== 3. policy is proposed, never imposed ===")
fresh()
r = client_as("Aarav").post("/cabinet/propose",
                            json={"field": "vat_rate", "value": 0.2, "reason": "we need revenue"})
check("a citizen without a brief cannot propose", r.status_code, 403)
r = client_as("Vikram").post("/cabinet/propose",
                             json={"field": "vat_rate", "value": 0.2, "reason": "we need revenue"})
check("Defence cannot touch the tax rate", r.status_code, 403)
check("  and is told whose brief it is", "brief doesn't cover" in r.get_json()["error"], True)

r = client_as("Nisha").post("/cabinet/propose",
                            json={"field": "vat_rate", "value": 0.99, "reason": "maximum revenue"})
check("a lever can't be moved out of range", r.status_code, 400)
r = client_as("Nisha").post("/cabinet/propose",
                            json={"field": "vat_rate", "value": 0.15, "reason": "short"})
check("a proposal needs a real reason", r.status_code, 400)
r = client_as("Nisha").post("/cabinet/propose",
                            json={"field": "vat_rate", "value": 0.10, "reason": "no change at all"})
check("proposing the current value is refused", r.status_code, 400)

before_vat = main.VAT_RATE
r = client_as("Nisha").post("/cabinet/propose",
    json={"field": "vat_rate", "value": 0.15, "reason": "The Treasury is running a deficit."})
check("Finance can propose the tax rate", r.status_code, 200)
prop = r.get_json()["proposal"]
check("  it records where it started", prop["current_value"], 0.10)
check("  and where it should go", prop["proposed_value"], 0.15)
check("  NOTHING changed yet", main.VAT_RATE, before_vat)
check("  the President was told",
      any("Your assent is needed" in n["message"] and n["username"] == "Prathyay"
          for n in db.data.get("notifications", [])), True)
r = client_as("Nisha").post("/cabinet/propose",
    json={"field": "vat_rate", "value": 0.16, "reason": "Changed my mind about the figure."})
check("can't stack two proposals on one lever", r.status_code, 400)


print("\n=== 4. only the President assents ===")
pr = props()[0]["id"]
r = client_as("Nisha").post("/cabinet/decide", json={"proposal_id": pr, "action": "approve"})
check("a minister cannot assent to their own", r.status_code, 403)
r = client_as("Rhea").post("/cabinet/decide", json={"proposal_id": pr, "action": "approve"})
check("nor can another minister", r.status_code, 403)
d = client_as("Nisha").get("/cabinet/proposals").get_json()
check("a minister sees only their own", [p["minister"] for p in d["mine"]], ["Nisha"])
check("  and no assent queue", d["pending"], [])
d = client_as("Prathyay").get("/cabinet/proposals").get_json()
check("the President sees the queue", len(d["pending"]), 1)

r = client_as("Prathyay").post("/cabinet/decide",
    json={"proposal_id": pr, "action": "approve", "note": "Agreed, but review it next month."})
check("the President assents", r.get_json()["status"], "approved")
check("  and THAT is when the lever moves", main.VAT_RATE, 0.15)
check("  the config row was written",
      [c["vat_rate"] for c in db.data["config"]], [0.15])
check("  it's recorded in the Gazette",
      any("VAT rate" in g["title"] for g in db.data.get("gazette", [])), True)
check("  and the minister was told",
      any("assented" in n["message"] for n in db.data["notifications"]), True)
r = client_as("Prathyay").post("/cabinet/decide", json={"proposal_id": pr, "action": "approve"})
check("a settled proposal can't be assented twice", r.status_code, 400)


print("\n=== 5. refusing and withdrawing ===")
r = client_as("Nisha").post("/cabinet/propose",
    json={"field": "gdp_multiplier", "value": 5, "reason": "Make the nation look richer."})
p2 = r.get_json()["proposal"]["id"]
before = main.GDP_MULTIPLIER
r = client_as("Prathyay").post("/cabinet/decide",
    json={"proposal_id": p2, "action": "reject", "note": "That's just inflation."})
check("the President can refuse", r.get_json()["status"], "rejected")
check("  and the lever stays put", main.GDP_MULTIPLIER, before)

r = client_as("Nisha").post("/cabinet/propose",
    json={"field": "gdp_multiplier", "value": 2, "reason": "A more modest revaluation."})
p3 = r.get_json()["proposal"]["id"]
r = client_as("Rhea").post("/cabinet/decide", json={"proposal_id": p3, "action": "withdraw"})
check("only the author may withdraw", r.status_code, 403)
r = client_as("Nisha").post("/cabinet/decide", json={"proposal_id": p3, "action": "withdraw"})
check("the minister can withdraw", r.get_json()["status"], "withdrawn")
check("  and the lever is untouched", main.GDP_MULTIPLIER, before)


print("\n=== 6. each brief keeps to its own levers ===")
fresh()
r = client_as("Rhea").post("/cabinet/propose",
    json={"field": "courier_wage", "value": 600, "reason": "Couriers are walking further now."})
check("Transport may move the courier wage", r.status_code, 200)
r = client_as("Rhea").post("/cabinet/propose",
    json={"field": "pen_rate", "value": 900, "reason": "Trying to grab Defence's brief."})
check("  but not the Armoury rate", r.status_code, 403)
r = client_as("Vikram").post("/cabinet/propose",
    json={"field": "pen_rate", "value": 500, "reason": "Ordnance is getting scarce."})
check("Defence may move the Armoury rate", r.status_code, 200)
r = client_as("Dev").post("/cabinet/propose",
    json={"field": "insurance_levy", "value": 0.02, "reason": "Claims are outrunning revenue."})
check("Justice may move the insurance levy", r.status_code, 200)


print("\n=== 7. the new salary scale ===")
T = main.SALARY_TABLE
check("the President draws nothing", T["President"], 0)
check("a Minister draws 900", T["Minister"], 900)
check("a Founder draws 800", T["Founder"], 800)
check("an Employee draws 500", T["Employee"], 500)
check("a courier draws 500 on top", main.COURIER_WAGE, 500)
check("a Minister out-earns a Founder", T["Minister"] > T["Founder"], True)

# A zero salary must still advance the clock, and must not file a record.
main.SALARY_PERIOD_DAYS = 7
p = cyb("Prathyay")
p["balance"] = 9000
p["last_salary"] = ISO(now - timedelta(days=14))
p["last_tax"] = ISO(now); p["savings_updated"] = ISO(now)
recs_before = len(db.data.get("records", []))
main._econ_seen.clear()
main.apply_economics(dict(p))
check("the President is paid nothing", cyb("Prathyay")["balance"], 9000)
check("  no salary record is filed",
      len([r for r in db.data.get("records", [])[recs_before:]
           if "salary" in (r.get("entry") or "")]), 0)
check("  but the clock still moved",
      cyb("Prathyay")["last_salary"] != ISO(now - timedelta(days=14)), True)

m = cyb("Vikram")
m["balance"] = 0
m["last_salary"] = ISO(now - timedelta(days=14))
m["last_tax"] = ISO(now); m["savings_updated"] = ISO(now)
main._econ_seen.clear()
main.apply_economics(dict(m))
check("a Minister is paid for two weeks", cyb("Vikram")["balance"], 1800)


print("\n=== 8. the page survives an un-migrated database ===")
_real = db.table
class _Missing:
    def __getattr__(self, n): raise Exception("relation does not exist")
db.table = lambda n: _Missing() if n == "policy_proposals" else _real(n)
fresh()
s = client_as("Prathyay").get("/cabinet/summary").get_json()
check("summary still succeeds", s["success"], True)
check("  reports it as not set up", s["enabled"], False)
check("  and keeps the President's flag", s["is_president"], True)
r = client_as("Nisha").post("/cabinet/propose",
    json={"field": "bond_rate", "value": 0.2, "reason": "Testing the missing table path."})
check("proposing explains the real problem", r.status_code, 503)
check("  naming the migration",
      "migration_cabinet_powers.sql" in r.get_json()["error"], True)
db.table = _real

print(f"\n{'='*46}\n  {PASS} passed, {FAIL} failed\n{'='*46}")
sys.exit(1 if FAIL else 0)
