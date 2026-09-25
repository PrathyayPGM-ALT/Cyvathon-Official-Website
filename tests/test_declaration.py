"""The Declaration on the Pufferbuck Debt, and a login that answers quickly."""
import logging, warnings, os, sys, json, glob, re
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
import fakedb
from fakedb import FakeSupabase
from werkzeug.security import generate_password_hash

db = FakeSupabase()
main.supabase = db
db.seed("cybucks", [
    {"id": 1, "username": "Aarav", "designation": "Citizen", "balance": 500, "approved": True,
     "password": generate_password_hash("old-pass-123", method="pbkdf2:sha256")},
    {"id": 2, "username": "Meera", "designation": "Citizen", "balance": 500, "approved": True,
     "password": generate_password_hash("new-pass-123")},
])
db.seed("treasury", [{"id": 1, "balance": 1000, "crystallines": 0, "cybits": 0}])

PASS = FAIL = 0
def check(label, got, want):
    global PASS, FAIL
    ok = got == want
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(f"  {'OK  ' if ok else 'FAIL'} {label}" + ("" if ok else f"\n         got={got!r} want={want!r}"))

app = main.app
app.config["TESTING"] = True
main.limiter.enabled = False
def cyb(n): return [r for r in db.data["cybucks"] if r["username"] == n][0]
def read(rel): return open(os.path.join(ROOT, rel), encoding="utf-8").read()


print("\n=== 1. the Declaration ===")
d = json.loads(read("static/declaration.json"))
check("it has a title and six articles", (d["title"], len(d["articles"])),
      ("Declaration on the Pufferbuck Debt", 6))
text = " ".join(a["text"] for a in d["articles"])
check("it admits the 5,000 Pufferbucks", "5,000 Pufferbucks" in text, True)
check("  records the offer of 7,500 (50% interest)", "7,500 Pufferbucks" in text and "fifty percent" in text, True)
check("  banishes Aqualithia", "hereby banished" in text, True)
check("  and keeps 7,500 Crystallines on offer with no expiry",
      "7,500 Crystallines" in text and "no expiry" in text, True)
check("the thieves are not named", "not named" in text, True)
check("the PDF exists", os.path.exists(os.path.join(ROOT, d["pdf"].lstrip("/"))), True)

r = app.test_client().get("/gazette/list")
top = r.get_json()["entries"][0]
check("the Gazette pins it first", (top["kind"], top["pinned"]), ("declaration", True))
check("  with a link to the PDF", top["doc"], d["pdf"])
g = read("static/gazette.html")
check("the Gazette only links PDFs from /static/", r"/^\/static\/[\w.-]+\.pdf$/" in g, True)

f = read("static/foreign.html")
check("Foreign Affairs shows the Declaration", "openDeclaration()" in f and d["pdf"] in f, True)
check("  and Aqualithia is marked hostile, with its status banished", 'rel:"Hostile"' in f and '["Status","Banished"]' in f, True)
check("the AI knows the story", "THE PUFFERBUCK DEBT" in read("main.py"), True)


print("\n=== 2. the name is spelt Aqualithia ===")
shown = []
for p in glob.glob(os.path.join(ROOT, "static", "*.html")) + [os.path.join(ROOT, "static", "card.js")]:
    if re.search(r"Aquilithia", read(p)):
        shown.append(os.path.basename(p))
check("no page shows the old spelling", shown, [])
check("the debit card no longer promises Pufferbucks",
      "Honoured in <b>Crystonia</b> at par, 1 CRY = 1 CB." in read("static/card.js"), True)


print("\n=== 3. signing in answers quickly ===")
calls = {"n": 0}
_orig = fakedb._Query.execute
def counting(self):
    calls["n"] += 1
    return _orig(self)
fakedb._Query.execute = counting
app.config["TESTING"] = False          # let the bookkeeping go to the background, as in production
main._bg_pool.shutdown(wait=True)      # ...but hold it until we've counted the response's own calls
import concurrent.futures
held = []
main._bg_pool = type("Held", (), {"submit": lambda self, fn, *a: held.append((fn, a)) or concurrent.futures.Future()})()
r = app.test_client().post("/login", json={"username": "Meera", "password": "new-pass-123"})
check("the right password lets you in", r.status_code, 200)
check("  after at most 3 trips to the database", calls["n"] <= 3, True)
check("  with the sign-in log, alert and PIN reminder left for afterwards",
      [fn.__name__ for fn, _ in held], ["_after_login"])
for fn, a in held:
    fn(*a)
check("the sign-in is still logged", any(e["username"] == "Meera" for e in db.data.get("login_events", [])), True)
app.config["TESTING"] = True

r = app.test_client().post("/login", json={"username": "Aarav", "password": "wrong-pass-9"})
check("a wrong password is still refused", r.status_code, 401)
r = app.test_client().post("/login", json={"username": "Aarav", "password": "old-pass-123"})
check("an old PBKDF2 password still works", r.status_code, 200)
check("  and is upgraded to scrypt, which is quicker to check",
      cyb("Aarav")["password"].startswith("scrypt:"), True)
r = app.test_client().post("/login", json={"username": "Aarav", "password": "old-pass-123"})
check("  and still works afterwards", r.status_code, 200)
login = read("static/login.html")
check("the login button says it's working the moment it's pressed", "Signing you in…" in login, True)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
