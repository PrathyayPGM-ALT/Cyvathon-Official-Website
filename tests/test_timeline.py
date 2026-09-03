"""Exercise the National Timeline — the founding record, and what's added since."""
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
db.defaults["timeline_events"] = {"kind": "event", "body": ""}

db.seed("cybucks", [
    {"id": 1, "username": "Prathyay", "designation": "President", "balance": 9000, "approved": True},
    {"id": 2, "username": "Aarav", "designation": "Citizen", "balance": 500, "approved": True},
])

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

def events(): return db.data.get("timeline_events", [])


print("\n=== 1. the founding record ===")
d = client_as().get("/timeline/data").get_json()
check("the timeline is public", d["success"], True)
by_date = {e["on"]: e for e in d["events"]}
check("Cyvathon was founded 26 May 2025", d["founded"], "2025-05-26")
check("  and that's the first entry", d["events"][0]["on"], "2025-05-26")
check("  marked as the founding", d["events"][0]["kind"], "founding")
check("the website went live 31 May 2025", by_date["2025-05-31"]["kind"], "website")
check("Crystonia was signed 14 June 2026", by_date["2026-06-14"]["kind"], "treaty")
check("  and it names Crystonia", "Crystonia" in by_date["2026-06-14"]["title"], True)

land = by_date["2026-09-02"]
check("the Treaty of Anti-Anarchism is recorded", "Anti-Anarchism" in land["title"], True)
check("  as a territory event", land["kind"], "territory")
check("  it names the class", "8E" in land["body"], True)
check("  and TISB", "TISB" in land["body"], True)
check("  notes it was unanimous", "unanimously" in land["body"], True)
check("  and it's the highlighted moment", land["highlight"], True)

check("the record is in date order",
      [e["on"] for e in d["events"]] == sorted(e["on"] for e in d["events"]), True)
check("every entry has a colour and icon",
      all(e["color"] and e["icon"] for e in d["events"]), True)
check("the founding entries are flagged as fixed",
      all(e["founding"] for e in d["events"]), True)
check("the nation's age is counted", d["days_old"] > 400, True)


print("\n=== 2. only the President writes the record ===")
d = client_as("Aarav").get("/timeline/data").get_json()
check("a citizen isn't the President", d["is_president"], False)
check("the President is", client_as("Prathyay").get("/timeline/data").get_json()["is_president"], True)

r = client_as().post("/timeline/add", json={"on": "2026-09-05", "title": "x"})
check("logged out cannot add", r.status_code, 401)
r = client_as("Aarav").post("/timeline/add",
                            json={"on": "2026-09-05", "title": "I declare myself king"})
check("a citizen cannot add", r.status_code, 403)
check("  and is told whose job it is", "President" in r.get_json()["error"], True)


print("\n=== 3. adding to the record ===")
r = client_as("Prathyay").post("/timeline/add", json={"on": "2026-09-05", "title": ""})
check("an entry needs a title", r.status_code, 400)
r = client_as("Prathyay").post("/timeline/add",
                               json={"on": "5th Sept", "title": "Bad date"})
check("the date must be YYYY-MM-DD", r.status_code, 400)
r = client_as("Prathyay").post("/timeline/add",
                               json={"on": "2024-01-01", "title": "Before the founding"})
check("nothing predates the founding", r.status_code, 400)
check("  and it says when that was",
      "2025-05-26" in r.get_json()["error"], True)

r = client_as("Prathyay").post("/timeline/add", json={
    "on": "2026-09-10", "kind": "treaty", "title": "Treaty with Aquilithia",
    "body": "Recognition and free movement between the two republics."})
check("the President can write a line", r.status_code, 200)
ev = r.get_json()["event"]
check("  it records who wrote it", ev["added_by"], "Prathyay")
check("  it is NOT a founding entry", ev["founding"], False)
check("  and takes the treaty styling", ev["icon"], "fa-file-signature")

r = client_as("Prathyay").post("/timeline/add", json={
    "on": "2026-09-11", "kind": "not-a-kind", "title": "Odd kind"})
check("an unknown kind falls back to 'event'", r.get_json()["event"]["kind"], "event")

d = client_as().get("/timeline/data").get_json()
check("added entries join the record", len(d["events"]), 6)
check("  still in date order",
      [e["on"] for e in d["events"]] == sorted(e["on"] for e in d["events"]), True)
check("  with the founding entries still first", d["events"][0]["on"], "2025-05-26")


print("\n=== 4. striking a line ===")
eid = ev["id"]
r = client_as("Aarav").post("/timeline/remove", json={"event_id": eid})
check("a citizen cannot strike an entry", r.status_code, 403)
r = client_as("Prathyay").post("/timeline/remove", json={"event_id": eid})
check("the President can", r.status_code, 200)
d = client_as().get("/timeline/data").get_json()
check("  it's gone", [e for e in d["events"] if e.get("id") == eid], [])
check("  but the founding record survives",
      len([e for e in d["events"] if e["founding"]]), 4)


print("\n=== 5. the founding record survives an un-migrated database ===")
_real = db.table
class _Missing:
    def __getattr__(self, n): raise Exception("relation does not exist")
db.table = lambda n: _Missing() if n == "timeline_events" else _real(n)
d = client_as().get("/timeline/data").get_json()
check("the page still works", d["success"], True)
check("  and the four founding events still stand", len(d["events"]), 4)
check("  including the territory", any("Anti-Anarchism" in e["title"] for e in d["events"]), True)
r = client_as("Prathyay").post("/timeline/add",
                               json={"on": "2026-09-20", "title": "Testing the missing table"})
check("adding explains the real problem", r.status_code, 503)
check("  naming the migration", "migration_timeline.sql" in r.get_json()["error"], True)
db.table = _real


print("\n=== 6. the AI knows the nation has land ===")
g = main.CYVATHON_GUIDE
check("the guide gives the founding date", "26 May 2025" in g, True)
check("  the website launch", "31 May 2025" in g, True)
check("  the Crystonia treaty", "Crystonia" in g, True)
check("  and the real territory", "8E at TISB" in g, True)
check("  described as real land", "REAL territory" in g, True)
check("  it points at the timeline", "/timeline" in g, True)

print(f"\n{'='*46}\n  {PASS} passed, {FAIL} failed\n{'='*46}")
sys.exit(1 if FAIL else 0)
