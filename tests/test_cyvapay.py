"""Exercise Cyvapay — links, the hosted checkout, and the rules that keep it safe."""
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
db.unique["cyvapay_links"] = [("code",)]
db.unique["cyvapay_payments"] = [("ref",)]
db.defaults["cyvapay_links"] = {"active": True, "reusable": True, "flexible": False,
                                "uses": 0, "total_taken": 0, "min_amount": 1,
                                "max_amount": 100000, "description": "", "redirect_url": "",
                                "currency": "cybucks", "amount": 0}
db.defaults["cyvapay_payments"] = {"note": "", "title": "", "currency": "cybucks"}

now = main._now()
ISO = lambda dt: dt.isoformat()

# Aarav has EARNED money (balance well above the grant); Kabir only has the grant.
db.seed("cybucks", [
    {"id": 1, "username": "Meera", "designation": "Citizen", "balance": 100, "approved": True},
    {"id": 2, "username": "Aarav", "designation": "Citizen", "balance": 2000, "approved": True},
    {"id": 3, "username": "Kabir", "designation": "Citizen", "balance": 100, "approved": True},
])
db.seed("companies", [{"id": 50, "name": "Saka Prints", "founder": "Meera", "balance": 0,
                       "cofounders": ""}])
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

def cyb(n): return [r for r in db.data["cybucks"] if r["username"] == n][0]
def links(): return db.data.get("cyvapay_links", [])
def pays(): return db.data.get("cyvapay_payments", [])


print("\n=== 1. making a payment link ===")
r = client_as().post("/cyvapay/link", json={"title": "Poster", "amount": 50})
check("logged out cannot make a link", r.status_code, 401)
r = client_as("Meera").post("/cyvapay/link", json={"title": "", "amount": 50})
check("a link needs a title", r.status_code, 400)
r = client_as("Meera").post("/cyvapay/link", json={"title": "Poster", "amount": 0})
check("a fixed link needs a price", r.status_code, 400)
r = client_as("Meera").post("/cyvapay/link", json={"title": "Poster", "amount": 50,
                                                    "currency": "dollars"})
check("unknown currency refused", r.status_code, 400)
r = client_as("Meera").post("/cyvapay/link", json={"title": "Poster", "amount": 50,
                                                    "redirect_url": "javascript:alert(1)"})
check("a non-https return link is refused", r.status_code, 400)
r = client_as("Meera").post("/cyvapay/link", json={"title": "Poster", "amount": 50,
                                                    "company_id": 50})
check("can't collect for a company you don't own... wait, Meera does", r.status_code, 200)
r = client_as("Aarav").post("/cyvapay/link", json={"title": "Poster", "amount": 50,
                                                    "company_id": 50})
check("...but Aarav can't collect for Meera's company", r.status_code, 403)

r = client_as("Meera").post("/cyvapay/link", json={
    "title": "A3 poster print", "amount": 50, "description": "Collected from 8B at break.",
    "redirect_url": "https://example.com/thanks"})
check("a citizen can make a link", r.status_code, 200)
L = r.get_json()["link"]
check("  it has an unguessable code", len(L["code"]) >= 10, True)
check("  a hosted checkout URL", L["url"].endswith("/cyvapay/checkout/" + L["code"]), True)
check("  it is live", L["active"], True)
check("  and reusable by default", L["reusable"], True)


print("\n=== 2. the checkout is public, paying is not ===")
r = client_as().get("/cyvapay/link/" + L["code"])
check("anyone can VIEW a link", r.status_code, 200)
d = r.get_json()
check("  it shows who is being paid", d["link"]["payee"], "Meera")
check("  and how much", d["link"]["amount"], 50)
check("  a guest is told they're not signed in", d["signed_in"], False)
check("  and given a signup link that comes back here",
      d["signup_url"], "/login?next=%2Fcyvapay%2Fcheckout%2F" + L["code"])
r = client_as().get("/cyvapay/link/NOPE")
check("an unknown code 404s", r.status_code, 404)

r = client_as().post("/cyvapay/charge", json={"code": L["code"]})
check("a guest CANNOT pay", r.status_code, 401)
r = client_as("Aarav").get("/cyvapay/charge?code=" + L["code"])
check("GET can never move money", r.status_code, 405)


print("\n=== 3. paying ===")
r = client_as("Meera").post("/cyvapay/charge", json={"code": L["code"]})
check("you can't pay your own link", r.status_code, 400)

before_a, before_m = cyb("Aarav")["balance"], cyb("Meera")["balance"]
r = client_as("Aarav").post("/cyvapay/charge", json={"code": L["code"], "note": "order 12"})
check("a signed-in citizen can pay", r.status_code, 200)
rc = r.get_json()
check("  and gets a receipt number", rc["ref"].startswith("CYP-"), True)
check("  the payer was debited", cyb("Aarav")["balance"], before_a - 50)
check("  the merchant was credited", cyb("Meera")["balance"], before_m + 50)
check("  the return link is handed back, not followed", rc["redirect_url"],
      "https://example.com/thanks")
check("  a receipt was written", len(pays()), 1)
check("  carrying the payer's note", pays()[0]["note"], "order 12")
check("  the link's tally moved",
      [(l["uses"], l["total_taken"]) for l in links() if l["code"] == L["code"]], [(1, 50)])
check("  the merchant was told",
      any("paid you 50" in n["message"] and n["username"] == "Meera"
          for n in db.data.get("notifications", [])), True)


print("\n=== 4. the amount comes from the link, never the request ===")
before = cyb("Aarav")["balance"]
r = client_as("Aarav").post("/cyvapay/charge", json={"code": L["code"], "amount": 1})
check("a fixed link ignores a smuggled amount", r.status_code, 200)
check("  and charges the real price", cyb("Aarav")["balance"], before - 50)
r = client_as("Aarav").post("/cyvapay/charge", json={"code": L["code"], "amount": 99999})
check("  in either direction", cyb("Aarav")["balance"], before - 100)


print("\n=== 5. only earned money can be paid out ===")
# Kabir holds nothing but the welcome grant.
r = client_as("Kabir").post("/cyvapay/charge", json={"code": L["code"]})
check("the welcome grant can't be sent through a link", r.status_code, 400)
check("  and it says so", "earned" in r.get_json()["error"], True)
check("  Kabir kept his money", cyb("Kabir")["balance"], 100)

db.seed("loans", [{"id": 1, "username": "Aarav", "amount": 1900, "repaid": False,
                   "defaulted": False}])
r = client_as("Aarav").post("/cyvapay/charge", json={"code": L["code"]})
check("borrowed Cybucks can't be spent through a link", r.status_code, 400)
db.data["loans"] = []


print("\n=== 6. flexible links ===")
r = client_as("Meera").post("/cyvapay/link", json={
    "title": "Tip jar", "flexible": True, "min_amount": 5, "max_amount": 200})
F = r.get_json()["link"]
check("a flexible link is made", F["flexible"], True)
r = client_as("Aarav").post("/cyvapay/charge", json={"code": F["code"], "amount": 2})
check("below the merchant's minimum is refused", r.status_code, 400)
r = client_as("Aarav").post("/cyvapay/charge", json={"code": F["code"], "amount": 500})
check("above the maximum is refused", r.status_code, 400)
r = client_as("Aarav").post("/cyvapay/charge", json={"code": F["code"]})
check("no amount at all is refused", r.status_code, 400)
before = cyb("Aarav")["balance"]
r = client_as("Aarav").post("/cyvapay/charge", json={"code": F["code"], "amount": 25})
check("inside the bounds works", r.get_json()["amount"], 25)
check("  and charges exactly that", cyb("Aarav")["balance"], before - 25)


print("\n=== 7. single-use, switched off, and gone ===")
r = client_as("Meera").post("/cyvapay/link", json={"title": "One ticket", "amount": 10,
                                                    "reusable": False})
S = r.get_json()["link"]
client_as("Aarav").post("/cyvapay/charge", json={"code": S["code"]})
r = client_as("Aarav").post("/cyvapay/charge", json={"code": S["code"]})
check("a single-use link can't be paid twice", r.status_code, 409)
check("  and the checkout explains why",
      "already been used" in client_as().get("/cyvapay/link/" + S["code"]).get_json()["blocked"], True)

r = client_as("Aarav").post("/cyvapay/link/toggle", json={"code": L["code"]})
check("only the owner can switch a link off", r.status_code, 404)
r = client_as("Meera").post("/cyvapay/link/toggle", json={"code": L["code"]})
check("the owner can", r.get_json()["active"], False)
r = client_as("Aarav").post("/cyvapay/charge", json={"code": L["code"]})
check("  a switched-off link can't be paid", r.status_code, 409)
client_as("Meera").post("/cyvapay/link/toggle", json={"code": L["code"]})

n_receipts = len(pays())
r = client_as("Meera").post("/cyvapay/link/delete", json={"code": S["code"]})
check("the owner can delete a link", r.status_code, 200)
check("  but its receipts survive", len(pays()), n_receipts)


print("\n=== 8. collecting as a company ===")
r = client_as("Meera").post("/cyvapay/link", json={"title": "Print run", "amount": 30,
                                                    "company_id": 50})
C = r.get_json()["link"]
check("the checkout names the company", C["payee"], "Saka Prints")
before = db.data["companies"][0]["balance"]
client_as("Aarav").post("/cyvapay/charge", json={"code": C["code"]})
check("payment lands in the company, not the founder", db.data["companies"][0]["balance"], before + 30)


print("\n=== 9. receipts ===")
d = client_as("Meera").get("/cyvapay/payments").get_json()
check("the merchant sees what came in", len(d["received"]) >= 3, True)
check("  every one has a receipt number", all(p["ref"].startswith("CYP-") for p in d["received"]), True)
d = client_as("Aarav").get("/cyvapay/payments").get_json()
check("the payer sees what went out", len(d["sent"]) >= 5, True)
check("  and nothing received", d["received"], [])
d = client_as("Meera").get("/cyvapay/links").get_json()
check("the merchant's link list is theirs only", all(l["owner"] == "Meera" for l in d["links"]), True)


print("\n=== 10. the gateway survives an un-migrated database ===")
_real = db.table
class _Missing:
    def __getattr__(self, n): raise Exception("relation does not exist")
db.table = lambda n: _Missing() if n.startswith("cyvapay_") else _real(n)
s = client_as("Meera").get("/cyvapay/summary").get_json()
check("summary still succeeds", s["success"], True)
check("  and reports the gateway as not set up", s["enabled"], False)
r = client_as().get("/cyvapay/link/" + L["code"])
check("the checkout explains the real problem", r.status_code, 503)
check("  naming the migration", "migration_cyvapay.sql" in r.get_json()["error"], True)
db.table = _real

print(f"\n{'='*46}\n  {PASS} passed, {FAIL} failed\n{'='*46}")
sys.exit(1 if FAIL else 0)
