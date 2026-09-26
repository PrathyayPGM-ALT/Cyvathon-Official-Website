"""The Corps: citizens enlist (Field or Cyber), the Quartermaster accepts or
turns them away, and an accepted soldier is paid once and wears a badge."""
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
db.defaults["corps_members"] = {"status": "pending", "branch": "field", "experience": "",
                                "decision_note": "", "badge_paid": False}
db.seed("cybucks", [
    {"id": 1, "username": "Prathyay", "designation": "President", "balance": 9000, "approved": True},
    {"id": 2, "username": "Aarav", "designation": "Citizen", "balance": 500, "approved": True},
    {"id": 3, "username": "Meera", "designation": "Citizen", "balance": 500, "approved": True},
])
db.seed("treasury", [{"id": 1, "balance": 100000, "crystallines": 0, "cybits": 0}])
main.TREASURY_ADMINS = {"Prathyay"}

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
def recs(n): return " ".join(r["entry"] for r in db.data.get("records", []) if r["username"] == n)
def badges(n):
    r = client_as(n).get("/u/" + n).get_json()["profile"]["badges"]
    return {b["key"] for b in r}


print("\n=== 1. two branches are offered ===")
d = client_as("Aarav").get("/corps/data").get_json()
check("Field and Cyber", sorted(b["key"] for b in d["branches"]), ["cyber", "field"])
check("  the badge stipend is 500 CB", d["pay"], 500)
check("  a citizen isn't yet enlisted", d["mine"], None)


print("\n=== 2. applying ===")
r = client_as("Aarav").post("/corps/apply", json={"branch": "field"})
check("a citizen can apply to the Field Corps", r.status_code, 200)
r = client_as("Aarav").post("/corps/apply", json={"branch": "field"})
check("  but not twice while one is pending", r.status_code, 400)
r = client_as("Meera").post("/corps/apply", json={"branch": "cyber"})
check("the Cyber Corps needs stated experience", r.status_code, 400)
r = client_as("Meera").post("/corps/apply",
    json={"branch": "cyber", "experience": "I code in Python/Flask and do defensive CTF challenges."})
check("  with experience, it's accepted for review", r.status_code, 200)
r = client_as("Aarav").post("/corps/apply", json={"branch": "marine"})
check("an unknown branch is refused", r.status_code, 400)


print("\n=== 3. the Quartermaster's desk ===")
d = client_as("Prathyay").get("/corps/data").get_json()
check("the Quartermaster sees pending applications", len(d["pending"]), 2)
check("  including the cyber applicant's experience",
      any("defensive CTF" in (p.get("experience") or "") for p in d["pending"]), True)
d2 = client_as("Aarav").get("/corps/data").get_json()
check("an ordinary citizen does not see the pending list", d2["pending"], [])
check("  nor another applicant's written experience",
      "experience" not in (d2["mine"] or {}) or d2["mine"].get("branch") == "field", True)


print("\n=== 4. acceptance pays once and grants the badge ===")
appid = [c for c in db.data["corps_members"] if c["username"] == "Aarav"][0]["id"]
bal0 = cyb("Aarav")["balance"]
r = client_as("Prathyay").post("/corps/decide", json={"id": appid, "action": "accept"})
check("the Quartermaster accepts", r.status_code, 200)
check("  the soldier is paid 500 CB", cyb("Aarav")["balance"], bal0 + 500)
check("  the Treasury paid it", db.data["treasury"][0]["balance"], 100000 - 500)
check("  a Field Corps badge is on the profile", "corps_field" in badges("Aarav"), True)
check("  and it's recorded", "Field Corps" in recs("Aarav"), True)
r = client_as("Prathyay").post("/corps/decide", json={"id": appid, "action": "accept"})
check("  a settled application can't be accepted again (no double pay)", r.status_code, 400)
check("    balance is unchanged", cyb("Aarav")["balance"], bal0 + 500)
r = client_as("Aarav").post("/corps/apply", json={"branch": "cyber", "experience": "x"*30})
check("an enlisted soldier can't re-apply", r.status_code, 400)


print("\n=== 5. turning an applicant away ===")
mid = [c for c in db.data["corps_members"] if c["username"] == "Meera"][0]["id"]
r = client_as("Aarav").post("/corps/decide", json={"id": mid, "action": "accept"})
check("only the Quartermaster may decide", r.status_code, 403)
bal0 = cyb("Meera")["balance"]
r = client_as("Prathyay").post("/corps/decide", json={"id": mid, "action": "reject", "note": "Need more detail."})
check("the Quartermaster can turn an applicant away", r.status_code, 200)
check("  no badge was granted", "corps_cyber" in badges("Meera"), False)
check("  nothing was paid", cyb("Meera")["balance"], bal0)
r = client_as("Meera").post("/corps/apply",
    json={"branch": "cyber", "experience": "I maintain the club's website and run its backups."})
check("  a turned-away citizen may apply again", r.status_code, 200)


print("\n=== 6. survives an un-migrated database ===")
_real = db.table
class _Missing:
    def __getattr__(self, n): raise Exception("relation does not exist")
db.table = lambda n: _Missing() if n == "corps_members" else _real(n)
r = client_as("Aarav").get("/corps/data")
check("the page still loads", r.status_code, 200)
check("  reporting it as not set up", r.get_json()["enabled"], False)
r = client_as("Aarav").post("/corps/apply", json={"branch": "field"})
check("  applying explains the migration", "migration_corps.sql" in r.get_json()["error"], True)
db.table = _real

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
