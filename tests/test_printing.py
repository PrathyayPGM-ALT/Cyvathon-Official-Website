"""Cyvaprint: pay by the page to the Treasury, a courier prints & delivers,
and it's free if it misses the one-working-day promise."""
import logging, warnings, os, sys, io
from datetime import datetime, timedelta, timezone
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
db.defaults["deliveries"] = {"status": "open", "kind": "custom"}
db.defaults["print_jobs"] = {"status": "ordered", "pages": 1, "copies": 1, "color": False,
                             "cost": 0, "refunded": False, "note": ""}
db.seed("cybucks", [
    {"id": 1, "username": "Aarav", "designation": "Citizen", "balance": 1000, "approved": True,
     "home_class": "8B", "home_area": "Classroom"},
    {"id": 2, "username": "Meera", "designation": "Citizen", "balance": 3, "approved": True},
])
db.seed("treasury", [{"id": 1, "balance": 100000, "crystallines": 0, "cybits": 0}])

# Don't hit real Storage in tests.
main._store_print = lambda f, folder: ("https://example/prints/x.pdf", "essay.pdf", None)

PASS = FAIL = 0
def check(label, got, want):
    global PASS, FAIL
    ok = got == want
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(f"  {'OK  ' if ok else 'FAIL'} {label}" + ("" if ok else f"\n         got={got!r} want={want!r}"))

app = main.app
app.config["TESTING"] = True
main.limiter.enabled = False
def client_as(u):
    c = app.test_client()
    with c.session_transaction() as s:
        s["username"] = u
    return c
def cyb(n): return [r for r in db.data["cybucks"] if r["username"] == n][0]
def jobs(): return db.data.get("print_jobs", [])

def order(user, pages=1, copies=1, color=False, note=""):
    return client_as(user).post("/print/order", data={
        "file": (io.BytesIO(b"%PDF-1.4 test"), "essay.pdf"),
        "pages": str(pages), "copies": str(copies),
        "color": "true" if color else "false", "note": note},
        content_type="multipart/form-data")


print("\n=== 1. rates and the working-day clock ===")
d = client_as("Aarav").get("/print/config").get_json()
check("black & white is 5 CB a page", d["rate_bw"], 5)
check("colour is 10 CB a page", d["rate_color"], 10)
fri = datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc)      # a Friday
check("a Friday order is due Monday (weekend skipped)",
      main._next_business_day_end(fri).weekday(), 0)
tue = datetime(2026, 10, 6, 9, 0, tzinfo=timezone.utc)       # a Tuesday
check("  a Tuesday order is due Wednesday", main._next_business_day_end(tue).weekday(), 2)


print("\n=== 2. ordering pays the Treasury up front ===")
bal0, tre0 = cyb("Aarav")["balance"], db.data["treasury"][0]["balance"]
r = order("Aarav", pages=3, copies=2, color=False)
check("the order is accepted", r.status_code, 200)
check("  3 pages x 2 copies x 5 CB = 30 CB", r.get_json()["cost"], 30)
check("  the citizen is charged", cyb("Aarav")["balance"], bal0 - 30)
check("  and the Treasury receives it", db.data["treasury"][0]["balance"], tre0 + 30)
check("  a courier parcel was raised", any(x["kind"] == "print" for x in db.data["deliveries"]), True)
r = order("Aarav", pages=2, color=True)
check("colour costs double (2 x 10)", r.get_json()["cost"], 20)


print("\n=== 3. validation ===")
r = client_as("Aarav").post("/print/order", data={"pages": "1"}, content_type="multipart/form-data")
check("a file is required", r.status_code, 400)
r = order("Aarav", pages=0)
check("pages must be at least 1", r.status_code, 400)
r = order("Aarav", pages=999)
check("pages are capped", r.status_code, 400)
r = order("Meera", pages=5)      # 25 CB, Meera has 3
check("you can't order what you can't afford", r.status_code, 400)
check("  and nothing was charged", cyb("Meera")["balance"], 3)


print("\n=== 4. cancel before printing refunds ===")
bal = cyb("Aarav")["balance"]
jid = order("Aarav", pages=1).get_json()["job"]["id"]
check("  charged for the new job", cyb("Aarav")["balance"], bal - 5)
r = client_as("Meera").post("/print/cancel", json={"job_id": jid})
check("only the owner can cancel", r.status_code, 403)
r = client_as("Aarav").post("/print/cancel", json={"job_id": jid})
check("the owner cancels", r.status_code, 200)
check("  and is refunded", cyb("Aarav")["balance"], bal)


print("\n=== 5. free if it misses the working-day promise ===")
bal = cyb("Aarav")["balance"]
jid = order("Aarav", pages=4).get_json()["job"]["id"]      # 20 CB
check("  charged 20 CB", cyb("Aarav")["balance"], bal - 20)
# Force the deadline into the past, still undelivered.
job = [j for j in jobs() if j["id"] == jid][0]
job["due_at"] = (main._now() - timedelta(hours=1)).isoformat()
d = client_as("Aarav").get("/print/config").get_json()      # viewing settles overdue jobs
check("an overdue, undelivered job is auto-refunded", cyb("Aarav")["balance"], bal)
settled = [j for j in d["jobs"] if j["id"] == jid][0]
check("  and shown as refunded", settled["refunded"], True)
# A job delivered LATE is also free.
bal = cyb("Aarav")["balance"]
jid2 = order("Aarav", pages=2).get_json()["job"]["id"]      # 10 CB
job2 = [j for j in jobs() if j["id"] == jid2][0]
job2["due_at"] = (main._now() - timedelta(hours=2)).isoformat()
job2["delivered_at"] = main._now().isoformat()
job2["status"] = "delivered"
main._print_settle(job2)
check("a job delivered after the deadline is refunded too", cyb("Aarav")["balance"], bal)
# A job delivered ON TIME is NOT refunded.
bal = cyb("Aarav")["balance"]
jid3 = order("Aarav", pages=1).get_json()["job"]["id"]      # 5 CB
job3 = [j for j in jobs() if j["id"] == jid3][0]
job3["delivered_at"] = main._now().isoformat()              # due_at is ~tomorrow, so on time
main._print_settle(job3)
check("an on-time delivery keeps the charge", cyb("Aarav")["balance"], bal - 5)


print("\n=== 6. survives an un-migrated database ===")
_real = db.table
class _Missing:
    def __getattr__(self, n): raise Exception("relation does not exist")
db.table = lambda n: _Missing() if n == "print_jobs" else _real(n)
r = client_as("Aarav").get("/print/config")
check("the page still loads", r.get_json()["enabled"], False)
r = order("Aarav", pages=1)
check("  ordering explains the migration", "migration_printing.sql" in r.get_json()["error"], True)
db.table = _real

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
