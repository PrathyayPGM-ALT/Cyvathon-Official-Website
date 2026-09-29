"""The leaderboards: many boards, each ranking the right thing, banned hidden."""
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
main._resp_cache.clear()
db.seed("cybucks", [
    {"id": 1, "username": "Arjun",  "designation": "President", "balance": 5000, "crystallines": 0, "cybits": 0, "approved": True},
    {"id": 2, "username": "Meera",  "designation": "Chancellor", "balance": 1000, "crystallines": 100, "cybits": 0, "approved": True, "referred_by": None},
    {"id": 3, "username": "Aarav",  "designation": "Citizen", "balance": 200, "crystallines": 0, "cybits": 0, "approved": True, "referred_by": "Meera"},
    {"id": 4, "username": "Kabir",  "designation": "Citizen", "balance": 50, "crystallines": 0, "cybits": 0, "approved": True, "referred_by": "Meera"},
    {"id": 5, "username": "Villain","designation": "Citizen", "balance": 999999, "crystallines": 0, "cybits": 0, "approved": True, "banned": True},
])
db.seed("companies", [{"id": 1, "founder": "Meera"}, {"id": 2, "founder": "Meera"}, {"id": 3, "founder": "Aarav"}])
db.seed("bills", [{"id": 1, "sponsor": "Arjun", "status": "enacted"},
                  {"id": 2, "sponsor": "Arjun", "status": "enacted"},
                  {"id": 3, "sponsor": "Meera", "status": "voting"}])
db.seed("ballots", [{"id": i, "poll_id": i, "voter": "Aarav"} for i in range(1, 5)]
                 + [{"id": 9, "poll_id": 9, "voter": "Meera"}])
db.seed("holdings", [{"id": 1, "username": "Arjun", "company_id": 1, "shares": 300},
                     {"id": 2, "username": "Meera", "company_id": 1, "shares": 50}])
db.seed("market_items", [{"id": 1, "seller": "Kabir", "status": "sold"},
                         {"id": 2, "seller": "Kabir", "status": "sold"},
                         {"id": 3, "seller": "Aarav", "status": "available"}])
db.seed("blogs", [{"id": 1, "username": "Meera"}, {"id": 2, "username": "Meera"}, {"id": 3, "username": "Aarav"}])
db.seed("deliveries", [{"id": 1, "courier": "Kabir", "status": "delivered", "sender": "a", "recipient": "b", "requested_by": "a"},
                       {"id": 2, "courier": "Kabir", "status": "open", "sender": "a", "recipient": "b", "requested_by": "a"}])
db.seed("card_packet", [{"id": 1, "owner": "Aarav", "quantity": 7, "player_name": "x"},
                        {"id": 2, "owner": "Meera", "quantity": 2, "player_name": "y"}])
db.seed("pen_donations", [{"id": 1, "username": "Kabir", "counted": 12, "status": "received"},
                          {"id": 2, "username": "Aarav", "counted": 3, "status": "pledged"}])

PASS = FAIL = 0
def check(label, got, want):
    global PASS, FAIL
    ok = got == want
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(f"  {'OK  ' if ok else 'FAIL'} {label}" + ("" if ok else f"\n         got={got!r} want={want!r}"))

app = main.app
app.config["TESTING"] = True
main.limiter.enabled = False
c = app.test_client()
with c.session_transaction() as s:
    s["username"] = "Aarav"

d = c.get("/leaderboard_data").get_json()
B = d["boards"]
def top(board): return B[board][0]["username"] if B.get(board) else None


print("\n=== 1. the boards exist ===")
keys = {b["key"] for b in d["meta"]}
for k in ("richest", "lawmakers", "civic", "industrialists", "shareholders",
          "traders", "armourers", "couriers", "collectors", "writers", "recruiters"):
    check(f"  has a {k} board", k in keys and k in B, True)


print("\n=== 2. each board ranks the right thing ===")
check("wealthiest is the richest citizen", top("richest"), "Arjun")
check("  and a banned account is hidden even though it's richer",
      any(r["username"] == "Villain" for r in B["richest"]), False)
check("lawmakers counts only enacted bills", top("lawmakers"), "Arjun")
check("  Arjun has 2 enacted", B["lawmakers"][0]["value"], 2)
check("most civic is the busiest voter", top("civic"), "Aarav")
check("industrialists is the top founder", top("industrialists"), "Meera")
check("  with 2 companies", B["industrialists"][0]["value"], 2)
check("shareholders is who holds most shares", top("shareholders"), "Arjun")
check("  holding 300", B["shareholders"][0]["value"], 300)
check("traders counts sold listings", (top("traders"), B["traders"][0]["value"]), ("Kabir", 2))
check("couriers counts delivered parcels", (top("couriers"), B["couriers"][0]["value"]), ("Kabir", 1))
check("collectors sums cards owned", (top("collectors"), B["collectors"][0]["value"]), ("Aarav", 7))
check("armourers counts only received ordnance", (top("armourers"), B["armourers"][0]["value"]), ("Kabir", 12))
check("writers counts posts", (top("writers"), B["writers"][0]["value"]), ("Meera", 2))
check("recruiters counts referrals", (top("recruiters"), B["recruiters"][0]["value"]), ("Meera", 2))


print("\n=== 3. rows carry what the page needs ===")
row = B["richest"][0]
check("a row has username, value, role and an avatar key",
      all(k in row for k in ("username", "value", "role", "avatar")), True)
check("  the role is the citizen's designation", B["lawmakers"][0]["role"], "President")
check("every board is capped at 10", all(len(v) <= 10 for v in B.values()), True)


print("\n=== 4. an un-migrated feature just yields an empty board ===")
main._resp_cache.clear()
_real = db.table
class _Missing:
    def __getattr__(self, n): raise Exception("relation does not exist")
db.table = lambda n: _Missing() if n == "card_packet" else _real(n)
d2 = c.get("/leaderboard_data").get_json()
check("the page still loads", d2["success"], True)
check("  the collectors board is just empty", d2["boards"]["collectors"], [])
check("  and the others still work", top2 := d2["boards"]["richest"][0]["username"], "Arjun")
db.table = _real

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
