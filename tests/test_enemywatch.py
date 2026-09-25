"""Athena's Enemy Watch: a rival's public homepage going up or down is filed
to the Registry as a CONFIDENTIAL entry only cleared officers may read."""
import logging, warnings, os, sys, types
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
db.seed("cybucks", [
    {"id": 1, "username": "Prathyay", "designation": "President", "balance": 0, "approved": True},
    {"id": 2, "username": "Riya", "designation": "Citizen", "balance": 0, "approved": True},
    {"id": 3, "username": "Officer", "designation": "Citizen", "balance": 0, "approved": True},
])
db.seed("registry_officers", [{"id": 1, "username": "Officer", "grade": "C-3", "role": "Analyst"}])
main.TREASURY_ADMINS = {"Prathyay"}
main._reg_cache["officers"] = None      # let it re-read our seeded officer

PASS = FAIL = 0
def check(label, got, want):
    global PASS, FAIL
    ok = got == want
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(f"  {'OK  ' if ok else 'FAIL'} {label}" + ("" if ok else f"\n         got={got!r} want={want!r}"))


def fake_requests(status_code):
    """A stand-in requests module whose GET returns a fixed status — no network."""
    mod = types.SimpleNamespace()
    def get(url, timeout=None, headers=None):
        return types.SimpleNamespace(
            status_code=status_code,
            text="<title>Aqualithia | Republic</title>" if status_code < 400 else "",
            headers={})
    mod.get = get
    return mod


def sweep(status):
    """Run one surveillance check with the network mocked to this status code."""
    sys.modules["requests"] = fake_requests(status)
    return main._do_surveil("aquilithia")


def reg_files():
    return db.data.get("registry_files", [])


print("\n=== 1. the name is shown correctly ===")
check("the watch reports the nation as Aqualithia", main._surveil_name("aquilithia"), "Aqualithia")


print("\n=== 2. a status change is filed to the Registry ===")
sweep(200)                                   # first check: establishes ONLINE, no prior state
check("the first check files nothing (no change yet)", len(reg_files()), 0)
sweep(503)                                   # ONLINE -> OFFLINE
check("going offline files one Registry entry", len(reg_files()), 1)
sweep(503)                                   # still offline
check("no change files nothing", len(reg_files()), 1)
sweep(200)                                   # OFFLINE -> ONLINE
check("coming back online files another", len(reg_files()), 2)

f = reg_files()[-1]
check("  it is CONFIDENTIAL", f["classification"], "CONFIDENTIAL")
check("  readable only by cleared officers", f["visibility"], "cleared")
check("  filed by Athena", f["author"], "Athena")
check("  under Foreign Affairs", f["directorate"], "Foreign Affairs")
check("  and names Aqualithia, not the old spelling",
      "Aqualithia" in f["title"] and "Aquilithia" not in f["title"], True)
check("  it says ONLINE", "ONLINE" in f["title"], True)


print("\n=== 3. only cleared eyes may read it ===")
def can_read(username):
    u = [r for r in db.data["cybucks"] if r["username"] == username][0]
    return main._reg_can_read(u, f)
check("the President can read it", can_read("Prathyay"), True)
check("a cleared officer can read it", can_read("Officer"), True)
check("an ordinary citizen cannot", can_read("Riya"), False)


print("\n=== 4. it shows up on the War Room and Athena pages ===")
wr = open(os.path.join(ROOT, "static", "warroom.html"), encoding="utf-8").read()
at = open(os.path.join(ROOT, "static", "athena.html"), encoding="utf-8").read()
check("the War Room has an Enemy Watch with ONLINE/OFFLINE",
      "Enemy Watch" in wr and "ONLINE" in wr and "OFFLINE" in wr, True)
check("Athena shows the same surveillance", "Foreign Surveillance" in at and "renderSurveil" in at, True)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
