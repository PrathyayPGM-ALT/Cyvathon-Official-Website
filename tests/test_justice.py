"""Exercise the justice/cabinet system against an in-memory database."""
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
main.supabase = db            # swap the client everywhere it's referenced

now = main._now()
ISO = lambda dt: dt.isoformat()

db.seed("cybucks", [
    {"id": 1, "username": "Prathyay", "designation": "President", "balance": 9000,
     "approved": True, "created_at": ISO(now - timedelta(days=200))},
    {"id": 2, "username": "Aarav",  "designation": "Citizen", "balance": 500, "approved": True},
    {"id": 3, "username": "Meera",  "designation": "Citizen", "balance": 500, "approved": True},
    {"id": 4, "username": "Kabir",  "designation": "Citizen", "balance": 500, "approved": True},
    {"id": 5, "username": "Diya",   "designation": "Citizen", "balance": 500, "approved": True},
    {"id": 6, "username": "Rohan",  "designation": "Citizen", "balance": 500, "approved": True},
])
db.seed("ministries", [
    {"id": 10, "name": "Ministry of Finance", "minister": "Vacant", "rank": 1, "mandate": "Money."},
    {"id": 11, "name": "Ministry of Defence", "minister": "Vivaan", "rank": 2, "mandate": "War."},
])

PASS = FAIL = 0
def check(label, got, want):
    global PASS, FAIL
    ok = got == want
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(f"  {'OK  ' if ok else 'FAIL'} {label}" + ("" if ok else f"\n         got={got!r} want={want!r}"))

app = main.app
app.config["TESTING"] = True

def client_as(username=None):
    c = app.test_client()
    if username:
        with c.session_transaction() as sess:
            sess["username"] = username
    return c


print("\n=== 1. eligibility ===")
clean = db.data["cybucks"][1]
check("clean citizen is eligible", main.office_eligibility(clean)[0], True)

main.add_criminal_record("Meera", "Prathyay", "Tax fraud", fine=50)
meera = [r for r in db.data["cybucks"] if r["username"] == "Meera"][0]
ok, why = main.office_eligibility(meera)
check("citizen with a conviction is barred", ok, False)
check("  reason mentions record", "criminal record" in why, True)

db.seed("loans", [{"id": 1, "username": "Kabir", "amount": 500, "repaid": False}])
kabir = [r for r in db.data["cybucks"] if r["username"] == "Kabir"][0]
ok, why = main.office_eligibility(kabir)
check("citizen with an unpaid loan is barred", ok, False)
check("  reason mentions loan", "loan" in why.lower(), True)


print("\n=== 2. permanent vs expiring records ===")
check("permanent by default (RECORD_EXPIRY_DAYS=0)",
      main.office_eligibility(meera)[0], False)
main.RECORD_EXPIRY_DAYS = 30
rec = db.data["criminal_records"][0]
rec["created_at"] = ISO(now - timedelta(days=45))          # older than the window
check("expires once the window passes", main.office_eligibility(meera)[0], True)
rec["created_at"] = ISO(now - timedelta(days=5))
check("still counts inside the window", main.office_eligibility(meera)[0], False)
main.RECORD_EXPIRY_DAYS = 0                                 # back to the President's rule


print("\n=== 3. jail gate ===")
main.send_to_jail("Aarav", 2, "Rioting", "Prathyay")
main.refresh_jailed(force=True)

jailed = client_as("Aarav")
free = client_as("Meera")
anon = client_as()

r = jailed.get("/bank", headers={"Accept": "text/html"})
check("jailed: HTML page is replaced by the jail page", b"You are in jail" in r.data, True)
r = jailed.get("/card_data")
check("jailed: API call is refused", r.status_code, 403)
check("  refusal names the jail", r.get_json().get("jailed") is not None, True)
r = jailed.get("/jail", headers={"Accept": "text/html"})
check("jailed: /jail itself is reachable", r.status_code, 200)
r = jailed.get("/static/theme.css")
check("jailed: static assets still load", r.status_code, 200)
r = jailed.post("/logout")
check("jailed: can still log out", r.status_code, 200)

r = free.get("/bank", headers={"Accept": "text/html"})
check("free citizen passes the gate", b"You are in jail" not in r.data, True)
r = anon.get("/login", headers={"Accept": "text/html"})
check("logged-out visitor passes the gate", r.status_code, 200)


print("\n=== 4. sentence expiry ===")
row = [r for r in db.data["cybucks"] if r["username"] == "Aarav"][0]
row["jailed_until"] = ISO(now - timedelta(minutes=1))       # served
main.refresh_jailed(force=True)
r = client_as("Aarav").get("/bank", headers={"Accept": "text/html"})
check("served sentence releases automatically", b"You are in jail" not in r.data, True)


print("\n=== 5. ministry applications open an election ===")
main.MINISTRY_MIN_APPLICANTS = 4
main.RECORD_EXPIRY_DAYS = 0
for r in db.data["cybucks"]:                                # clear jail state
    r["jailed_until"] = None
main.refresh_jailed(force=True)

def apply_as(user):
    return client_as(user).post("/ministries/apply",
        json={"ministry_id": 10, "statement": f"{user} would serve the nation well."})

r = apply_as("Aarav");  check("1st applicant accepted", r.status_code, 200)
check("  election not open yet", r.get_json()["election_opened"], False)
r = apply_as("Aarav");  check("duplicate application rejected", r.status_code, 400)
r = apply_as("Meera");  check("barred citizen cannot apply", r.status_code, 403)
r = apply_as("Kabir");  check("unpaid-loan citizen cannot apply", r.status_code, 403)
r = apply_as("Diya");   check("2nd applicant accepted", r.status_code, 200)
r = apply_as("Rohan");  check("3rd applicant accepted", r.status_code, 200)
r = apply_as("Prathyay"); check("4th applicant accepted", r.status_code, 200)
check("  4th applicant opens the election", r.get_json()["election_opened"], True)

polls = db.data.get("polls", [])
check("a poll now exists", len(polls), 1)
check("  poll is tied to the ministry", polls[0]["ministry_id"], 10)
check("  candidates are the applicants", sorted(polls[0]["options"]),
      ["Aarav", "Diya", "Prathyay", "Rohan"])
check("  every citizen was notified",
      len(db.data.get("notifications", [])) >= 5, True)

r = apply_as("Aarav")
check("applying to a filled ministry is refused", r.status_code, 400)


print("\n=== 6. closing the election installs the minister ===")
db.seed("ballots", [
    {"poll_id": polls[0]["id"], "voter": "Meera", "choice": "Diya"},
    {"poll_id": polls[0]["id"], "voter": "Kabir", "choice": "Diya"},
    {"poll_id": polls[0]["id"], "voter": "Rohan", "choice": "Aarav"},
])
r = client_as("Prathyay").post("/polls/close", json={"poll_id": polls[0]["id"]})
check("President can close the vote", r.status_code, 200)
fin = [m for m in db.data["ministries"] if m["id"] == 10][0]
check("  winner installed as minister", fin["minister"], "Diya")
diya = [u for u in db.data["cybucks"] if u["username"] == "Diya"][0]
check("  winner's designation updated", diya["designation"], "Minister")
apps = db.data["ministry_applications"]
check("  winner's application marked elected",
      [a["status"] for a in apps if a["username"] == "Diya"], ["elected"])
check("  losing applications closed",
      all(a["status"] == "closed" for a in apps
          if a["username"] in ("Aarav", "Rohan", "Prathyay")), True)


print("\n=== 7. court sentencing ===")
db.seed("government", [{"id": 90, "position": "Judge", "holder": "Prathyay"}])
db.seed("court_cases", [{"id": 50, "title": "Theft of a Cybit", "plaintiff": "Rohan",
                         "defendant": "Kabir", "status": "open"}])
kab = [u for u in db.data["cybucks"] if u["username"] == "Kabir"][0]
kab["balance"] = 300

r = client_as("Prathyay").post("/court/rule", json={
    "case_id": 50, "verdict": "guilty", "note": "Caught red-handed.",
    "fine": 100, "jail_days": 3})
check("judge can pass a sentence", r.status_code, 200)

case = [c for c in db.data["court_cases"] if c["id"] == 50][0]
check("  case marked guilty", case["status"], "guilty")
check("  fine recorded on the case", case["fine"], 100)
check("  jail term recorded on the case", case["jail_days"], 3)
check("  fine actually deducted", kab["balance"], 200)

crim = [c for c in db.data["criminal_records"] if c["username"] == "Kabir"]
check("  conviction filed", len(crim), 1)
check("  conviction kind is 'both'", crim[0]["kind"] if crim else None, "both")
check("  conviction links to the case", crim[0]["case_id"] if crim else None, 50)

main.refresh_jailed(force=True)
check("  defendant is now in jail", main.is_jailed(kab), True)
r = client_as("Kabir").get("/bank", headers={"Accept": "text/html"})
check("  and is locked out of the site", b"You are in jail" in r.data, True)

db.seed("court_cases", [{"id": 51, "title": "Frivolous suit", "plaintiff": "Rohan",
                         "defendant": "Diya", "status": "open"}])
r = client_as("Prathyay").post("/court/rule", json={
    "case_id": 51, "verdict": "dismissed", "note": "No evidence."})
check("dismissal succeeds", r.status_code, 200)
check("  no conviction filed for a dismissal",
      [c for c in db.data["criminal_records"] if c["username"] == "Diya"], [])
check("  dismissed defendant stays free",
      main.is_jailed([u for u in db.data["cybucks"] if u["username"] == "Diya"][0]), False)


print("\n=== 8. pardon clears the bar ===")
check("Kabir barred while convicted", main.office_eligibility(kab)[0], False)
r = client_as("Prathyay").post("/jail/pardon", json={"record_id": crim[0]["id"]})
check("President can pardon", r.status_code, 200)
check("  conviction marked spent", crim[0]["spent"], True)
kab["jailed_until"] = None
db.data["loans"] = []
main.refresh_jailed(force=True)
check("  pardoned citizen is eligible again", main.office_eligibility(kab)[0], True)


print("\n=== 9. announcements ===")
before = len(db.data.get("notifications", []))
r = client_as("Prathyay").post("/announce", json={"preset": "cards"})
check("President can address the nation", r.status_code, 200)
check("  every citizen notified", len(db.data["notifications"]) > before, True)
check("  filed in the Gazette", len(db.data.get("gazette", [])), 1)
r = client_as("Rohan").post("/announce", json={"message": "vote for me"})
check("ordinary citizen cannot broadcast", r.status_code, 403)


print(f"\n{'='*46}\n  {PASS} passed, {FAIL} failed\n{'='*46}")
sys.exit(1 if FAIL else 0)
