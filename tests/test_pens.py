"""Exercise the Armoury — handovers, the Quartermaster's count, payouts."""
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

db = FakeSupabase()
main.supabase = db
db.defaults["pen_donations"] = {"status": "pledged", "count": 1, "condition": "working",
                                "rate": 400, "counted": 0, "amount_paid": 0,
                                "note": "", "decision_note": ""}
db.defaults["deliveries"] = {"status": "open", "kind": "custom"}

db.seed("cybucks", [
    {"id": 1, "username": "Prathyay", "designation": "President", "balance": 9000, "approved": True},
    {"id": 2, "username": "Aarav", "designation": "Citizen", "balance": 500, "approved": True},
    {"id": 3, "username": "Meera", "designation": "Citizen", "balance": 500, "approved": True},
])
db.seed("treasury", [{"id": 1, "balance": 100000, "pufb": 0, "aquilines": 0,
                      "cybits": 0, "pens": 0}])

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
def vault(): return db.data["treasury"][0].get("pens") or 0
def dons(): return db.data.get("pen_donations", [])


print("\n=== 1. the rate and the Quartermaster ===")
check("the Armoury pays 400 CB a round", main.PEN_RATE, 400)
check("and the Quartermaster is Prathyay", main.PEN_REGISTRAR, "Prathyay")
d = client_as("Aarav").get("/pens/config").get_json()
check("config advertises the rate", d["rate"], 400)
check("  names the Quartermaster", d["registrar"], "Prathyay")
check("  an ordinary citizen doesn't hold the desk", d["is_admin"], False)
check("  the armoury starts empty", d["reserve_pens"], 0)
check("Prathyay holds the desk",
      client_as("Prathyay").get("/pens/config").get_json()["is_admin"], True)
check("a live round is worth full rate", main.pen_value(3, "working"), 1200)
check("  a drill round is worth a quarter", main.pen_value(4, "dry"), 400)
check("  salvage is token", main.pen_value(10, "broken"), 400)
grades = [c["label"] for c in main.PEN_CONDITIONS]
check("  and the grades read as ordnance", grades,
      ["Live round", "Drill round", "Salvage"])


print("\n=== 2. pledging ===")
r = client_as().post("/pens/pledge", json={"count": 1})
check("logged out cannot pledge", r.status_code, 401)
r = client_as("Aarav").post("/pens/pledge", json={"count": 0})
check("must pledge at least one", r.status_code, 400)
r = client_as("Aarav").post("/pens/pledge", json={"count": 999})
check("a silly haul is capped", r.status_code, 400)
r = client_as("Aarav").post("/pens/pledge", json={"count": 2, "condition": "melted"})
check("unknown condition rejected", r.status_code, 400)

before = cyb("Aarav")["balance"]
r = client_as("Aarav").post("/pens/pledge", json={
    "count": 3, "condition": "working", "note": "Two black, one blue.", "deliver": True})
check("a citizen can pledge", r.status_code, 200)
don = r.get_json()["donation"]
check("  it starts as a pledge", don["status"], "pledged")
check("  it quotes what's expected", don["expected"], 1200)
check("  NOTHING is paid on a pledge", cyb("Aarav")["balance"], before)
check("  and nothing enters the armoury", vault(), 0)
check("  the Quartermaster is told",
      any("handing in 3 G2 round" in n["message"] and n["username"] == "Prathyay"
          for n in db.data.get("notifications", [])), True)


print("\n=== 3. Cyvazon carries them to Prathyay ===")
p = db.data.get("deliveries", [])[-1]
check("a parcel was raised", p["kind"], "pens")
check("  from the donor", p["sender"], "Aarav")
check("  straight to the Registrar", p["recipient"], "Prathyay")
check("  and linked to the donation",
      [x["delivery_id"] for x in dons() if x["id"] == don["id"]], [p["id"]])


print("\n=== 4. only the Quartermaster logs rounds in ===")
did = don["id"]
r = client_as("Meera").get("/pens/registry")
check("a citizen cannot open the desk", r.status_code, 403)
r = client_as("Meera").post("/pens/registry/decide",
                            json={"donation_id": did, "action": "receive"})
check("a citizen cannot log rounds in", r.status_code, 403)
r = client_as("Aarav").post("/pens/registry/decide",
                            json={"donation_id": did, "action": "receive"})
check("nor can the donor themselves", r.status_code, 403)

d = client_as("Prathyay").get("/pens/registry").get_json()
check("the Quartermaster sees the handover", len(d["pledged"]), 1)
check("  and what it will cost", d["owed"], 1200)
check("  with nothing paid yet", d["paid_total"], 0)


print("\n=== 5. the Quartermaster's count is what gets paid ===")
r = client_as("Prathyay").post("/pens/registry/decide",
    json={"donation_id": did, "action": "receive", "counted": 9})
check("cannot log in more than was handed in", r.status_code, 400)

before = cyb("Aarav")["balance"]
r = client_as("Prathyay").post("/pens/registry/decide",
    json={"donation_id": did, "action": "receive", "counted": 2,
          "note": "Only two turned up."})
check("the Quartermaster can log in fewer", r.get_json()["counted"], 2)
check("  and pays for what arrived", r.get_json()["paid"], 800)
check("  the donor was paid", cyb("Aarav")["balance"], before + 800)
check("  the rounds entered the armoury", vault(), 2)
check("  from the Treasury",
      any(f["kind"] == "pen_reserve" for f in db.data.get("treasury_flows", [])), True)
check("  and the citizen was told",
      any("logged in 2 round" in n["message"] for n in db.data["notifications"]), True)
r = client_as("Prathyay").post("/pens/registry/decide",
    json={"donation_id": did, "action": "receive"})
check("a settled handover can't be paid twice", r.status_code, 400)


print("\n=== 6. grade can be corrected on arrival ===")
r = client_as("Meera").post("/pens/pledge", json={"count": 4, "condition": "working"})
d2 = r.get_json()["donation"]["id"]
before = cyb("Meera")["balance"]
r = client_as("Prathyay").post("/pens/registry/decide",
    json={"donation_id": d2, "action": "receive", "condition": "dry",
          "note": "All four were dry."})
check("downgrading the grade cuts the payout", r.get_json()["paid"], 400)
check("  the citizen got the lower amount", cyb("Meera")["balance"], before + 400)
check("  but all four rounds still went in", vault(), 6)


print("\n=== 7. turning a handover away ===")
r = client_as("Aarav").post("/pens/pledge", json={"count": 1})
d3 = r.get_json()["donation"]["id"]
before, penned = cyb("Aarav")["balance"], vault()
r = client_as("Prathyay").post("/pens/registry/decide",
    json={"donation_id": d3, "action": "reject", "note": "That's a biro."})
check("the Quartermaster can turn one away", r.get_json()["status"], "rejected")
check("  nothing was paid", cyb("Aarav")["balance"], before)
check("  and nothing entered the armoury", vault(), penned)


print("\n=== 8. withdrawing your own handover ===")
r = client_as("Aarav").post("/pens/pledge", json={"count": 2})
d4 = r.get_json()["donation"]["id"]
r = client_as("Meera").post("/pens/cancel", json={"donation_id": d4})
check("cannot withdraw someone else's handover", r.status_code, 403)
r = client_as("Aarav").post("/pens/cancel", json={"donation_id": d4})
check("the citizen can withdraw", r.get_json()["status"], "cancelled")
r = client_as("Aarav").post("/pens/cancel", json={"donation_id": d4})
check("  but only once", r.status_code, 400)

for _ in range(3):
    client_as("Aarav").post("/pens/pledge", json={"count": 1})
r = client_as("Aarav").post("/pens/pledge", json={"count": 1})
check("open handovers are capped", r.status_code, 400)


print("\n=== 9. the armoury is national materiel ===")
d = client_as("Aarav").get("/pens/config").get_json()
check("holdings are reported", d["reserve_pens"], 6)
check("  valued at the Armoury rate", d["reserve_value"], 2400)
check("  and the citizen's own tally is shown", d["my_pens"], 2)
main._gdp_cache["v"] = None
gdp_with = main.compute_gdp()
db.data["treasury"][0]["pens"] = 0
main._gdp_cache["v"] = None
gdp_without = main.compute_gdp()
check("rounds in the armoury raise GDP", round(gdp_with - gdp_without, 2), 2400)
db.data["treasury"][0]["pens"] = 6
main._gdp_cache["v"] = None

d = client_as("Aarav").get("/pens/board").get_json()
check("the roll of honour ranks by rounds",
      [(x["username"], x["pens"]) for x in d["donors"]], [("Meera", 4), ("Aarav", 2)])


print("\n=== 10. the desk survives an un-migrated database ===")
_real = db.table
class _Missing:
    def __getattr__(self, n): raise Exception("relation does not exist")
db.table = lambda n: _Missing() if n == "pen_donations" else _real(n)
s = client_as("Prathyay").get("/pens/summary").get_json()
check("summary still succeeds", s["success"], True)
check("  reports it as not set up", s["enabled"], False)
check("  but keeps the Quartermaster's flag", s["is_admin"], True)
r = client_as("Prathyay").get("/pens/registry")
check("the desk explains the real problem", r.status_code, 503)
check("  naming the migration",
      "migration_pen_reserve.sql" in r.get_json()["error"], True)
db.table = _real

print(f"\n{'='*46}\n  {PASS} passed, {FAIL} failed\n{'='*46}")
sys.exit(1 if FAIL else 0)
