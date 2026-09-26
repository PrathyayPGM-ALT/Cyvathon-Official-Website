"""Speed guards: the account row is cached across a page's burst of calls,
writes bust that cache at once, /me stays off the heavy economics path, and
the browser is allowed to keep static assets between page loads."""
import logging, warnings, os, sys, collections
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

db = FakeSupabase()
main.supabase = db
db.seed("cybucks", [
    {"id": 1, "username": "Aarav", "designation": "Citizen", "balance": 500,
     "crystallines": 100, "cybits": 100, "approved": True},
])
db.seed("treasury", [{"id": 1, "balance": 1000, "crystallines": 0, "cybits": 0}])

# Count DB round-trips, and note which tables were read.
seen = collections.Counter()
_orig = fakedb._Query.execute
def counting(self):
    seen[self.table_name + ":" + self.kind] += 1
    return _orig(self)
fakedb._Query.execute = counting

PASS = FAIL = 0
def check(label, got, want):
    global PASS, FAIL
    ok = got == want
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(f"  {'OK  ' if ok else 'FAIL'} {label}" + ("" if ok else f"\n         got={got!r} want={want!r}"))

app = main.app
app.config["TESTING"] = True
main.limiter.enabled = False


def as_aarav():
    def _ctx():
        with app.test_request_context("/"):
            from flask import session
            session["username"] = "Aarav"
            session["sv"] = 0
            return main.get_current_user(run_economics=False)
    return _ctx


print("\n=== 1. the account row is cached across a burst ===")
main._user_cache.clear()
seen.clear()
with app.test_request_context("/"):
    from flask import session
    session["username"] = "Aarav"; session["sv"] = 0
    for _ in range(5):
        main.get_current_user(run_economics=False)   # a page's burst of calls
check("five look-ups in a row hit the database once", seen["cybucks:select"], 1)


print("\n=== 2. a write busts the cache immediately ===")
main._user_cache.clear()
with app.test_request_context("/"):
    from flask import session
    session["username"] = "Aarav"; session["sv"] = 0
    main.get_current_user(run_economics=False)       # warms the cache
    main.cas_adjust("Aarav", "balance", 250)         # a transfer-style write
    seen.clear()
    u = main.get_current_user(run_economics=False)
check("the next read goes back to the database", seen["cybucks:select"], 1)
check("  and shows the new balance", u["balance"], 750)

main._user_cache.clear()
with app.test_request_context("/"):
    from flask import session
    session["username"] = "Aarav"; session["sv"] = 0
    main.get_current_user(run_economics=False)
    seen.clear()
    main._set_user("Aarav", {"bio": "hi"})
    main.get_current_user(run_economics=False)
check("_set_user also busts it", seen["cybucks:select"], 1)


print("\n=== 3. /me stays off the heavy economics path ===")
check("economics only runs about once an hour", main.ECON_MIN_INTERVAL >= 3600, True)
c = app.test_client()
with c.session_transaction() as s:
    s["username"] = "Aarav"; s["sv"] = 0
main._econ_seen["Aarav"] = main.time()               # economics already ran this hour
main._user_cache.clear()
seen.clear()
r = c.get("/me")
check("/me answers", r.status_code, 200)
check("  without touching companies/loans/employment (the economics tables)",
      seen["companies:select"] + seen["loans:select"] + seen["employment:select"], 0)


print("\n=== 4. the browser may keep static assets ===")
r = c.get("/static/app.js")
check("app.js is cacheable", "max-age" in r.headers.get("Cache-Control", ""), True)
r = c.get("/static/theme.css")
check("theme.css is cacheable", "max-age" in r.headers.get("Cache-Control", ""), True)
r = c.get("/static/icons/icon-192.png")
check("icons are cached for a day", "max-age=86400" in r.headers.get("Cache-Control", ""), True)
r = c.get("/chat")
check("but pages are never long-cached (updates must show at once)",
      "max-age=600" not in r.headers.get("Cache-Control", "")
      and "max-age=86400" not in r.headers.get("Cache-Control", ""), True)
r = c.get("/me")
check("  and API replies are never cached", "max-age=600" not in r.headers.get("Cache-Control", ""), True)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
