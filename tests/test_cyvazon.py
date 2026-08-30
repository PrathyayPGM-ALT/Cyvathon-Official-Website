"""Exercise Cyvazon — couriers, parcels, the levy and the wage."""
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
db.unique["couriers"] = [("username",)]
db.defaults["couriers"] = {"active": True, "status": "pending", "covers": "",
                           "note": "", "deliveries": 0}
db.defaults["deliveries"] = {"status": "open", "kind": "custom", "item_label": "",
                             "pickup_class": "", "pickup_area": "", "dropoff_class": "",
                             "dropoff_area": "", "notes": ""}
db.defaults["market_items"] = {"status": "available", "kind": "sale", "currency": "cybucks"}

now = main._now()
ISO = lambda dt: dt.isoformat()

db.seed("cybucks", [
    {"id": 1, "username": "Prathyay", "designation": "President", "balance": 9000, "approved": True},
    {"id": 2, "username": "Aarav", "designation": "Citizen", "balance": 500, "approved": True,
     "home_class": "8B", "home_area": "Classroom"},
    {"id": 3, "username": "Meera", "designation": "Citizen", "balance": 500, "approved": True,
     "home_class": "10A", "home_area": "Library"},
    {"id": 4, "username": "Kabir", "designation": "Citizen", "balance": 500, "approved": True},
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

def parcels():
    return db.data.get("deliveries", [])
def cyb(name):
    return [r for r in db.data["cybucks"] if r["username"] == name][0]


print("\n=== 1. signing up as a courier ===")
r = client_as().post("/cyvazon/signup", json={})
check("logged out cannot sign up", r.status_code, 401)
r = client_as("Kabir").post("/cyvazon/signup", json={"covers": "8B, 9C"})
check("citizen can apply", r.status_code, 200)
check("  it is only an application", r.get_json()["status"], "pending")
check("  not yet a courier", r.get_json()["courier"], False)
check("  recorded as pending", db.data["couriers"][0]["status"], "pending")
check("  no wage clock until approval", cyb("Kabir").get("last_courier_pay"), None)
check("  the President is told",
      any("applied to be a Cyvazon courier" in n["message"]
          for n in db.data.get("notifications", [])), True)
d = client_as("Kabir").get("/cyvazon/config").get_json()
check("  applicant is not a courier yet", d["courier"], False)
check("  but sees their application", d["courier_status"], "pending")
check("  and the advertised wage", d["wage"], 500)
d = client_as("Aarav").get("/cyvazon/config").get_json()
check("non-applicant has no status", d["courier_status"], "")
check("  and is not an admin", d["is_admin"], False)
check("President is an admin", client_as("Prathyay").get("/cyvazon/config").get_json()["is_admin"], True)


print("\n=== 1b. only the President approves couriers ===")
r = client_as("Aarav").get("/cyvazon/admin")
check("ordinary citizen cannot open the panel", r.status_code, 403)
d = client_as("Prathyay").get("/cyvazon/admin").get_json()
check("President sees the applicant", [a["username"] for a in d["pending"]], ["Kabir"])
check("  nobody approved yet", d["approved"], [])
check("  wage bill starts at zero", d["weekly_cost"], 0)

r = client_as("Aarav").post("/cyvazon/admin/decide",
                            json={"username": "Kabir", "action": "approve"})
check("citizen cannot approve couriers", r.status_code, 403)

# A pending applicant must not be able to touch parcels.
client_as("Aarav").post("/cyvazon/request", json={
    "recipient": "Meera", "item_label": "gate test",
    "pickup_class": "8B", "dropoff_class": "10A"})
gate_id = parcels()[-1]["id"]
r = client_as("Kabir").post("/cyvazon/claim", json={"delivery_id": gate_id})
check("a pending applicant cannot claim", r.status_code, 403)
client_as("Aarav").post("/cyvazon/cancel", json={"delivery_id": gate_id})

r = client_as("Prathyay").post("/cyvazon/admin/decide",
                               json={"username": "Kabir", "action": "approve"})
check("President approves", r.get_json()["status"], "approved")
check("  wage clock starts on approval", bool(cyb("Kabir").get("last_courier_pay")), True)
check("  applicant is told",
      any("approved Cyvazon courier" in n["message"] for n in db.data["notifications"]), True)
d = client_as("Kabir").get("/cyvazon/config").get_json()
check("  now a real courier", d["courier"], True)
d = client_as("Prathyay").get("/cyvazon/admin").get_json()
check("  wage bill reflects one courier", d["weekly_cost"], 500)
check("  and the queue is empty", d["pending"], [])

r = client_as("Kabir").post("/cyvazon/signup", json={})
check("an approved courier can't re-apply", r.status_code, 400)


print("\n=== 2. booking a delivery ===")
r = client_as("Aarav").post("/cyvazon/request", json={
    "recipient": "Meera", "item_label": "Match Attax binder",
    "pickup_class": "8B", "pickup_area": "Classroom",
    "dropoff_class": "10A", "dropoff_area": "Library", "notes": "break time"})
check("citizen can book a free delivery", r.status_code, 200)
p = r.get_json()["delivery"]
check("  starts unclaimed", p["status"], "open")
check("  pickup class recorded", p["pickup_class"], "8B")
check("  dropoff class recorded", p["dropoff_class"], "10A")
check("  area kept from the picker", p["dropoff_area"], "Library")

r = client_as("Aarav").post("/cyvazon/request", json={
    "recipient": "Aarav", "item_label": "x", "pickup_class": "8B", "dropoff_class": "8B"})
check("cannot post to yourself", r.status_code, 400)
r = client_as("Aarav").post("/cyvazon/request", json={
    "recipient": "Meera", "item_label": "x", "dropoff_class": "10A"})
check("pickup class is required", r.status_code, 400)
r = client_as("Aarav").post("/cyvazon/request", json={
    "recipient": "Nobody", "item_label": "x", "pickup_class": "8B", "dropoff_class": "9A"})
check("unknown recipient rejected", r.status_code, 404)
r = client_as("Aarav").post("/cyvazon/request", json={
    "recipient": "Meera", "pickup_class": "8B", "dropoff_class": "10A"})
check("must say what you're sending", r.status_code, 400)

# an unrecognised area must not be stored verbatim
r = client_as("Aarav").post("/cyvazon/request", json={
    "recipient": "Meera", "item_label": "pen", "pickup_class": "8B",
    "pickup_area": "<script>", "dropoff_class": "10A"})
check("bogus area is dropped", r.get_json()["delivery"]["pickup_area"], "")


print("\n=== 3. claiming and running it ===")
pid = [x for x in parcels() if x["item_label"] == "Match Attax binder"][0]["id"]
r = client_as("Aarav").post("/cyvazon/claim", json={"delivery_id": pid})
check("non-courier cannot claim", r.status_code, 403)
r = client_as("Kabir").post("/cyvazon/claim", json={"delivery_id": pid})
check("courier can claim", r.get_json()["status"], "claimed")
r = client_as("Kabir").post("/cyvazon/claim", json={"delivery_id": pid})
check("a claimed parcel can't be taken twice", r.status_code, 409)

r = client_as("Meera").post("/cyvazon/status", json={"delivery_id": pid, "action": "picked_up"})
check("only the courier marks collection", r.status_code, 403)
r = client_as("Kabir").post("/cyvazon/status", json={"delivery_id": pid, "action": "picked_up"})
check("courier marks it collected", r.get_json()["status"], "picked_up")

# The whole point: the courier must not be able to close their own run.
r = client_as("Kabir").post("/cyvazon/status", json={"delivery_id": pid, "action": "delivered"})
check("courier CANNOT sign for delivery", r.status_code, 403)
r = client_as("Aarav").post("/cyvazon/status", json={"delivery_id": pid, "action": "delivered"})
check("nor can the sender", r.status_code, 403)
r = client_as("Meera").post("/cyvazon/status", json={"delivery_id": pid, "action": "delivered"})
check("the recipient signs for it", r.get_json()["status"], "delivered")
check("  courier's tally went up", db.data["couriers"][0]["deliveries"], 1)
r = client_as("Meera").post("/cyvazon/status", json={"delivery_id": pid, "action": "delivered"})
check("can't be signed for twice", r.status_code, 400)


print("\n=== 4. the board keeps people honest ===")
client_as("Aarav").post("/cyvazon/request", json={
    "recipient": "Meera", "item_label": "ruler", "pickup_class": "8B", "dropoff_class": "10A"})
d = client_as("Aarav").get("/cyvazon/board").get_json()
check("sender doesn't see their own job on the board",
      [b for b in d["board"] if b["sender"] == "Aarav"], [])
check("  but does see it as outgoing", len(d["sending"]) >= 1, True)
d = client_as("Meera").get("/cyvazon/board").get_json()
check("recipient sees it as incoming", any(i["item_label"] == "ruler" for i in d["incoming"]), True)
d = client_as("Kabir").get("/cyvazon/board").get_json()
check("an uninvolved courier sees it as claimable",
      any(b["item_label"] == "ruler" for b in d["board"]), True)

open_id = [p for p in parcels() if p["item_label"] == "ruler"][0]["id"]
r = client_as("Meera").post("/cyvazon/cancel", json={"delivery_id": open_id})
check("a stranger cannot cancel someone's parcel", r.status_code, 403)
r = client_as("Aarav").post("/cyvazon/cancel", json={"delivery_id": open_id})
check("the sender can cancel while unclaimed", r.get_json()["status"], "cancelled")


print("\n=== 5. couriers can't abandon a live run ===")
client_as("Aarav").post("/cyvazon/request", json={
    "recipient": "Meera", "item_label": "book", "pickup_class": "8B", "dropoff_class": "10A"})
live = [p for p in parcels() if p["item_label"] == "book"][0]["id"]
client_as("Kabir").post("/cyvazon/claim", json={"delivery_id": live})
r = client_as("Kabir").post("/cyvazon/resign", json={})
check("cannot resign holding a parcel", r.status_code, 400)
r = client_as("Kabir").post("/cyvazon/status", json={"delivery_id": live, "action": "drop"})
check("courier can give a run back", r.get_json()["status"], "open")
r = client_as("Kabir").post("/cyvazon/resign", json={})
check("then they can stand down", r.get_json()["courier"], False)
r = client_as("Kabir").post("/cyvazon/claim", json={"delivery_id": live})
check("a former courier can't claim", r.status_code, 403)
# Re-applying goes back into the queue rather than restoring the old licence.
client_as("Kabir").post("/cyvazon/signup", json={})
check("re-application is pending again",
      client_as("Kabir").get("/cyvazon/config").get_json()["courier_status"], "pending")
client_as("Prathyay").post("/cyvazon/admin/decide",
                           json={"username": "Kabir", "action": "approve"})


print("\n=== 5b. revoking a licence releases held parcels ===")
client_as("Aarav").post("/cyvazon/request", json={
    "recipient": "Meera", "item_label": "revoke test",
    "pickup_class": "8B", "dropoff_class": "10A"})
rv = [p for p in parcels() if p["item_label"] == "revoke test"][0]["id"]
client_as("Kabir").post("/cyvazon/claim", json={"delivery_id": rv})
r = client_as("Prathyay").post("/cyvazon/admin/decide",
                               json={"username": "Kabir", "action": "revoke"})
check("President can revoke", r.get_json()["status"], "rejected")
check("  the held parcel was released", r.get_json()["released"], 1)
held = [p for p in parcels() if p["id"] == rv][0]
check("  it is back on the board", held["status"], "open")
check("  with no courier", held["courier"], None)
check("  revoked courier is barred",
      client_as("Kabir").post("/cyvazon/claim",
                              json={"delivery_id": rv}).status_code, 403)
client_as("Aarav").post("/cyvazon/cancel", json={"delivery_id": rv})
client_as("Kabir").post("/cyvazon/signup", json={})
client_as("Prathyay").post("/cyvazon/admin/decide",
                           json={"username": "Kabir", "action": "approve"})


print("\n=== 6. a marketplace sale raises a parcel ===")
db.seed("market_items", [{"id": 900, "seller": "Meera", "title": "Poster",
                          "price": 20, "currency": "cybucks", "status": "available",
                          "kind": "sale", "description": ""}])
before = len(parcels())
r = client_as("Aarav").post("/market/buy", json={
    "item_id": 900, "dropoff_class": "8B", "dropoff_area": "Classroom"})
check("purchase succeeds", r.status_code, 200)
check("  a parcel was raised", len(parcels()), before + 1)
sale = parcels()[-1]
check("  from the seller", sale["sender"], "Meera")
check("  to the buyer", sale["recipient"], "Aarav")
check("  tagged as a marketplace run", sale["kind"], "market")
check("  pickup defaulted to the seller's class", sale["pickup_class"], "10A")

db.seed("market_items", [{"id": 901, "seller": "Meera", "title": "Mug", "price": 5,
                          "currency": "cybucks", "status": "available", "kind": "sale",
                          "description": ""}])
before = len(parcels())
r = client_as("Aarav").post("/market/buy", json={"item_id": 901, "collect": True})
check("collecting in person raises no parcel", len(parcels()), before)
check("  and reports none back", r.get_json()["delivery"], None)


print("\n=== 7. the delivery levy rides on VAT ===")
main.VAT_RATE, main.DELIVERY_LEVY, main.DELIVERY_OPEN = 0.10, 0.05, True
main.TAX_PERIOD_DAYS = 30
u = cyb("Prathyay")
u["balance"] = 1000
u["last_tax"] = ISO(now - timedelta(days=40))
u["last_salary"] = ISO(now)
u["savings_updated"] = ISO(now)
main._econ_seen.clear()
main.apply_economics(dict(u))
check("15% taken, not 10%", cyb("Prathyay")["balance"], 850)
flows = db.data.get("treasury_flows", [])
check("  levy logged separately",
      any(f["kind"] == "delivery_levy" and f["amount"] == 50 for f in flows), True)
check("  the rest logged as VAT",
      any(f["kind"] == "vat" and f["amount"] == 100 for f in flows), True)

# Closing the service should stop charging for it.
main.DELIVERY_OPEN = False
u = cyb("Meera"); u["balance"] = 1000
u["last_tax"] = ISO(now - timedelta(days=40))
u["last_salary"] = ISO(now); u["savings_updated"] = ISO(now)
main._econ_seen.clear()
main.apply_economics(dict(u))
check("no levy while the service is shut", cyb("Meera")["balance"], 900)
main.DELIVERY_OPEN = True


print("\n=== 8. couriers draw their wage ===")
main.SALARY_PERIOD_DAYS = 7
main.COURIER_WAGE = 500
k = cyb("Kabir")
k["balance"] = 0
k["last_courier_pay"] = ISO(now - timedelta(days=14))    # two periods owed
k["last_tax"] = ISO(now); k["last_salary"] = ISO(now); k["savings_updated"] = ISO(now)
main._econ_seen.clear()
main.apply_economics(dict(k))
check("two weeks of courier wage paid", cyb("Kabir")["balance"], 1000)
check("  logged against the Treasury",
      any(f["kind"] == "courier_wage" for f in db.data["treasury_flows"]), True)

a = cyb("Aarav")
a["balance"] = 0
a["last_courier_pay"] = ISO(now - timedelta(days=14))
a["last_tax"] = ISO(now); a["last_salary"] = ISO(now); a["savings_updated"] = ISO(now)
main._econ_seen.clear()
main.apply_economics(dict(a))
check("non-couriers get nothing", cyb("Aarav")["balance"], 0)


print("\n=== 9. dashboard summary ===")
s = client_as("Meera").get("/cyvazon/summary").get_json()
check("summary is enabled", s["enabled"], True)
check("  counts what's coming to Meera", s["incoming"] >= 1, True)
s = client_as("Kabir").get("/cyvazon/summary").get_json()
check("  and flags Kabir as a courier", s["courier"], True)
crew = client_as("Aarav").get("/cyvazon/couriers").get_json()["couriers"]
check("crew list shows only approved couriers",
      sorted(c["username"] for c in crew), ["Kabir"])

print(f"\n{'='*46}\n  {PASS} passed, {FAIL} failed\n{'='*46}")
sys.exit(1 if FAIL else 0)
