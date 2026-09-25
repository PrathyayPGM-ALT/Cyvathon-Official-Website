"""Exercise the three currencies: Cybucks, Cybits and Crystallines.
Pufferbucks and Aquilines were withdrawn in September 2026."""
import logging, warnings, os, sys, glob, re
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
    {"id": 1, "username": "Aarav", "designation": "Citizen", "balance": 2000,
     "crystallines": 400, "cybits": 100, "approved": True},
    {"id": 2, "username": "Kabir", "designation": "Citizen", "balance": 100,
     "crystallines": 100, "cybits": 100, "approved": True},
])
db.seed("treasury", [{"id": 1, "balance": 100000, "crystallines": 5000, "cybits": 0}])

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


print("\n=== 1. exactly three currencies ===")
check("the codes are Cybucks, Crystallines and Cybits",
      sorted(main.CURRENCY_COLUMN), ["crys", "cybit", "cybucks"])
check("a Crystalline is worth a Cybuck", main.CYBUCK_VALUE["crys"], 1.0)
check("Crystallines live in their own column", main.CURRENCY_COLUMN["crys"], "crystallines")
check("public profiles show Crystallines",
      main.public_user(cyb("Aarav")).get("crystallines"), 400)
check("  and not the old money",
      [k for k in main.public_user(cyb("Aarav")) if k in ("pufb", "aquilines")], [])


print("\n=== 2. the old money is refused ===")
for old in ("pufb", "aquilines"):
    r = client_as("Aarav").post("/convert", json={"from": old, "to": "cybucks", "amount": 1})
    check(f"converting {old} is refused", r.status_code, 400)
    r = client_as("Aarav").post("/transfer", json={"to_username": "Kabir", "currency": old, "amount": 1})
    check(f"  and so is sending it", r.status_code, 400)


print("\n=== 3. Crystallines work like money ===")
r = client_as("Aarav").post("/convert", json={"from": "crys", "to": "cybucks", "amount": 50})
check("50 Crystallines convert", r.status_code, 200)
check("  into 50 Cybucks", r.get_json().get("converted"), 50.0)
check("  leaving 350 Crystallines", cyb("Aarav")["crystallines"], 350)
r = client_as("Aarav").post("/convert", json={"from": "crys", "to": "cybit", "amount": 2})
check("2 Crystallines are 100 Cybits", r.get_json().get("converted"), 100.0)
r = client_as("Aarav").post("/transfer", json={"to_username": "Kabir", "currency": "crys", "amount": 25})
check("Crystallines can be sent", r.status_code, 200)
check("  and arrive", cyb("Kabir")["crystallines"], 125)
r = client_as("Kabir").post("/transfer", json={"to_username": "Aarav", "currency": "crys", "amount": 110})
check("the welcome Crystallines can't be passed on", r.status_code, 400)


print("\n=== 4. the Treasury counts Crystallines ===")
r = client_as("Aarav").get("/treasury_data")
d = r.get_json()
check("its reserves are in the three currencies", sorted(d["reserves"]), ["crys", "cybit", "cybucks"])
check("  holding 5000 Crystallines", d["reserves"]["crys"], 5000)
db.seed("cybucks", [{"id": 3, "username": "Old", "designation": "Citizen", "balance": 0,
                     "crystallines": 0, "cybits": 0, "approved": True}])
check("a citizen's wealth counts Crystallines at a Cybuck each",
      main.user_net_worth("Aarav") >= 1990 + 350, True)


print("\n=== 5. no page offers the old money ===")
offers = []
for p in glob.glob(os.path.join(ROOT, "static", "*.html")):
    s = open(p, encoding="utf-8").read()
    if re.search(r'value="(pufb|aquilines)"', s) or re.search(r"\bpufb\s*:", s):
        offers.append(os.path.basename(p))
check("no currency picker or label mentions Pufferbucks or Aquilines", offers, [])
dash = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
check("the dashboard shows Crystallines", '"crystallines","Crystallines"' in dash, True)
check("the colour for Crystallines exists in both themes",
      open(os.path.join(ROOT, "static", "theme.css"), encoding="utf-8").read().count("--c-crys:"), 2)


print("\n=== 6. the migration keeps everyone's value ===")
sql = open(os.path.join(ROOT, "migration_crystallines.sql"), encoding="utf-8").read()
check("1 Pufferbuck = 1 Crystalline, 10 Aquilines = 1 Crystalline",
      "r.pf + r.aq / 10.0" in sql, True)
check("every citizen not banned gets 100 from the Treasury",
      "case when r.banned then 0 else 100 end" in sql and "'grant'" in sql, True)
check("nobody is converted twice",
      "not in (select holder from currency_conversion)" in sql, True)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
