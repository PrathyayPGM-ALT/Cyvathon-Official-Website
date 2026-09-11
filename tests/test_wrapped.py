"""Exercise Cyvathon Wrapped — the season, the year it covers, and the numbers."""
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

from datetime import datetime, timezone
import main
from fakedb import FakeSupabase

db = FakeSupabase()
main.supabase = db
app = main.app
app.config["TESTING"] = True
main.limiter.enabled = False

UTC = timezone.utc
IST = main.WRAPPED_TZ


def at(y, mo, d, h=12, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=UTC)


def T(y, mo, d, h=12):
    return at(y, mo, d, h).isoformat()


IN = T(2026, 8, 10)            # inside Wrapped 2026's year
LATE = T(2026, 9, 5)           # September 2026: next year's Wrapped
EARLY = T(2025, 5, 1)          # before the founding

PASS = FAIL = 0


def check(label, got, want):
    global PASS, FAIL
    ok = got == want
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(f"  {'OK  ' if ok else 'FAIL'} {label}" + ("" if ok else f"\n         got={got!r} want={want!r}"))


def set_now(dt):
    main._now = lambda: dt
    main._wrapped_cache.clear()


def client_as(username=None):
    c = app.test_client()
    if username:
        with c.session_transaction() as sess:
            sess["username"] = username
    return c


db.seed("cybucks", [
    {"id": 1, "username": "Prathyay", "designation": "President", "balance": 9000, "approved": True,
     "created_at": T(2025, 5, 20)},
    {"id": 2, "username": "Aarav", "designation": "Citizen", "balance": 500, "approved": True,
     "created_at": "2025-06-01T06:00:00+00:00"},
    {"id": 3, "username": "Priya", "designation": "Citizen", "balance": 500, "approved": True,
     "created_at": T(2025, 7, 1)},
    {"id": 4, "username": "Rohan", "designation": "Citizen", "balance": 500, "approved": True,
     "created_at": T(2025, 8, 1)},
    {"id": 5, "username": "Crystonia", "account_type": "nation", "approved": True, "created_at": T(2025, 8, 1)},
    {"id": 6, "username": "Troll", "banned": True, "approved": True, "created_at": T(2025, 8, 1)},
    {"id": 7, "username": "Newbie", "designation": "Citizen", "approved": True, "created_at": LATE},
])

db.seed("transactions", [
    {"id": 1, "kind": "transfer", "from_party": "Aarav", "to_party": "Priya", "amount": 50, "currency": "cybucks", "created_at": IN},
    {"id": 2, "kind": "transfer", "from_party": "Priya", "to_party": "Aarav", "amount": 20, "currency": "pufb", "created_at": IN},
    {"id": 3, "kind": "market", "from_party": "Aarav", "to_party": "Priya", "amount": 30, "currency": "cybucks", "created_at": IN},
    {"id": 4, "kind": "cyvapay", "from_party": "Priya", "to_party": "Aarav", "amount": 100, "currency": "aquilines", "created_at": IN},
    # written to both ledgers by the app; must be counted from treasury_flows only
    {"id": 5, "kind": "casino", "from_party": "The House", "to_party": "Aarav", "amount": 999, "currency": "cybucks", "created_at": IN},
    {"id": 6, "kind": "insurance", "from_party": "Cyvashield", "to_party": "Aarav", "amount": 999, "currency": "cybucks", "created_at": IN},
    {"id": 7, "kind": "pens", "from_party": "Cyvathon Armoury", "to_party": "Aarav", "amount": 999, "currency": "cybucks", "created_at": IN},
    {"id": 8, "kind": "transfer", "from_party": "Aarav", "to_party": "Rohan", "amount": 7, "currency": "cybucks", "created_at": LATE},
    {"id": 9, "kind": "convert", "from_party": "Aarav", "to_party": "Aarav", "amount": 500, "currency": "cybucks", "created_at": IN},
    {"id": 10, "kind": "transfer", "from_party": "Aarav", "to_party": "Priya", "amount": 250, "currency": "cybit", "created_at": IN},
])

db.seed("treasury_flows", [
    {"id": 1, "direction": "OUT", "counterparty": "Aarav", "currency": "cybucks", "amount": 100, "kind": "salary", "created_at": IN},
    {"id": 2, "direction": "IN", "counterparty": "Aarav", "currency": "cybucks", "amount": 12, "kind": "vat", "created_at": IN},
    {"id": 3, "direction": "IN", "counterparty": "Aarav", "currency": "cybucks", "amount": 3, "kind": "delivery_levy", "created_at": IN},
    {"id": 4, "direction": "IN", "counterparty": "Aarav", "currency": "cybucks", "amount": 40, "kind": "casino", "created_at": IN},
    {"id": 5, "direction": "OUT", "counterparty": "Aarav", "currency": "cybucks", "amount": 15, "kind": "casino", "created_at": IN},
    {"id": 6, "direction": "OUT", "counterparty": "Aarav", "currency": "cybucks", "amount": 60, "kind": "insurance_payout", "created_at": IN},
    {"id": 7, "direction": "OUT", "counterparty": "Aarav", "currency": "cybucks", "amount": 400, "kind": "pen_reserve", "created_at": IN},
    {"id": 8, "direction": "OUT", "counterparty": "Aarav", "currency": "cybucks", "amount": 500, "kind": "courier_wage", "created_at": IN},
    {"id": 9, "direction": "OUT", "counterparty": "Aarav", "currency": "cybucks", "amount": 100, "kind": "salary", "created_at": LATE},
    {"id": 10, "direction": "OUT", "counterparty": "Priya", "currency": "cybucks", "amount": 100, "kind": "salary", "created_at": IN},
])

db.seed("ballots", [
    {"id": 1, "poll_id": 1, "voter": "Aarav", "choice": "A", "created_at": IN},
    {"id": 2, "poll_id": 2, "voter": "Aarav", "choice": "B", "created_at": IN},
    {"id": 3, "poll_id": 3, "voter": "Aarav", "choice": "A", "created_at": LATE},
    {"id": 4, "poll_id": 1, "voter": "Priya", "choice": "A", "created_at": IN},
])
db.seed("bills", [
    {"id": 1, "title": "Snack Act", "sponsor": "Aarav", "status": "enacted", "created_at": IN, "enacted_at": IN},
    {"id": 2, "title": "Old Act", "sponsor": "Priya", "status": "voting", "created_at": EARLY},
])
db.seed("bill_votes", [
    {"id": 1, "bill_id": 1, "voter": "Aarav", "vote": "aye"},
    {"id": 2, "bill_id": 2, "voter": "Aarav", "vote": "nay"},
])
db.seed("card_trades", [
    {"id": 1, "from_user": "Aarav", "to_user": "Priya", "status": "accepted", "created_at": IN, "resolved_at": IN},
    {"id": 2, "from_user": "Priya", "to_user": "Aarav", "status": "accepted", "created_at": IN, "resolved_at": None},
    {"id": 3, "from_user": "Aarav", "to_user": "Rohan", "status": "pending", "created_at": IN},
    {"id": 4, "from_user": "Aarav", "to_user": "Rohan", "status": "accepted", "created_at": LATE, "resolved_at": LATE},
])
db.seed("deliveries", [
    {"id": 1, "sender": "Priya", "recipient": "Aarav", "courier": "Rohan", "status": "delivered", "created_at": IN, "delivered_at": IN},
    {"id": 2, "sender": "Aarav", "recipient": "Priya", "courier": "Rohan", "status": "delivered", "created_at": IN, "delivered_at": IN},
    {"id": 3, "sender": "Aarav", "recipient": "Priya", "courier": None, "status": "open", "created_at": IN},
    *[{"id": 10 + i, "sender": "Priya", "recipient": "Prathyay", "courier": "Rohan", "status": "delivered",
       "created_at": IN, "delivered_at": IN} for i in range(4)],
    {"id": 20, "sender": "Priya", "recipient": "Rohan", "courier": "Aarav", "status": "delivered", "created_at": IN, "delivered_at": IN},
    {"id": 21, "sender": "Priya", "recipient": "Rohan", "courier": "Aarav", "status": "delivered", "created_at": IN, "delivered_at": IN},
])
db.seed("lend_loans", [
    {"id": 1, "item_id": 1, "owner": "Aarav", "borrower": "Priya", "status": "returned", "requested_at": IN},
    {"id": 2, "item_id": 2, "owner": "Priya", "borrower": "Aarav", "status": "out", "requested_at": IN},
    {"id": 3, "item_id": 3, "owner": "Aarav", "borrower": "Rohan", "status": "declined", "requested_at": IN},
])
db.seed("pen_donations", [
    {"id": 1, "username": "Aarav", "status": "received", "counted": 3, "created_at": IN},
    {"id": 2, "username": "Aarav", "status": "pledged", "counted": 0, "created_at": IN},
])
db.seed("messages", [
    *[{"id": 1 + i, "sender": "Aarav", "recipient": "Priya", "content": "hi", "created_at": IN} for i in range(3)],
    {"id": 4, "sender": "Aarav", "recipient": "Rohan", "content": "yo", "created_at": IN},
    {"id": 5, "sender": "Aarav", "recipient": None, "group_id": 1, "content": "all", "created_at": IN},
    {"id": 6, "sender": "Aarav", "recipient": None, "group_id": 1, "content": "all", "created_at": IN},
    {"id": 7, "sender": "Aarav", "recipient": "Priya", "content": "late", "created_at": LATE},
])
db.seed("blogs", [
    {"id": 1, "username": "Aarav", "title": "Hello", "body": "x", "created_at": IN},
    {"id": 2, "username": "Aarav", "title": "Older", "body": "x", "created_at": EARLY},
])
db.seed("blog_likes", [
    {"id": 1, "blog_id": 1, "username": "Priya", "created_at": IN},
    {"id": 2, "blog_id": 2, "username": "Rohan", "created_at": LATE},
])
db.seed("videos", [{"id": 1, "username": "Aarav", "title": "v", "url": "u", "created_at": IN}])
db.seed("mail", [{"id": 1, "sender": "Aarav", "body": "m", "created_at": IN}])
db.seed("trades", [{"id": 1, "company_id": 1, "buyer": "Aarav", "seller": "Priya", "price": 10, "quantity": 2, "created_at": IN}])
db.seed("companies", [{"id": 1, "name": "Snacks Ltd", "founder": "Aarav", "category": "Selling", "created_at": IN}])


print("\n=== 1. the season ===")
w = main._wrapped_window(at(2026, 8, 31, 18, 29, 59))          # 23:59:59 on 31 Aug, India time
check("sealed the second before September begins in India", w["open"], False)
check("  and the edition waiting is 2026", w["edition"], 2026)
check("open the moment 1 September begins in India", main._wrapped_window(at(2026, 8, 31, 18, 30))["open"], True)
check("still open in the last second of September", main._wrapped_window(at(2026, 9, 30, 18, 29, 59))["open"], True)
w = main._wrapped_window(at(2026, 9, 30, 18, 30))
check("sealed again as October begins", w["open"], False)
check("  and the next edition is 2027", w["edition"], 2027)
check("the first edition reaches back to the founding",
      main._wrapped_window(at(2026, 9, 10))["start"], datetime(2025, 5, 26, tzinfo=IST))
check("later editions cover September to August",
      main._wrapped_window(at(2027, 9, 3))["start"], datetime(2026, 9, 1, tzinfo=IST))
check("  ending as September begins",
      main._wrapped_window(at(2027, 9, 3))["end"], datetime(2027, 9, 1, tzinfo=IST))


print("\n=== 2. who can see it, and when ===")
set_now(at(2026, 9, 10))
s = client_as().get("/wrapped/status").get_json()
check("the status is public", s["success"], True)
check("  says it's open in September", s["open"], True)
check("  names the edition", s["edition"], 2026)
check("  and the year it covers starts at the founding", s["period"]["start"], "26 May 2025")
check("  and ends on 31 August", s["period"]["end"], "31 August 2026")
check("  and closes at the end of September", s["closes_label"], "30 September 2026")
check("  and knows nobody's logged in", s["logged_in"], False)
check("a stranger can't read anyone's Wrapped", client_as().get("/wrapped/data").status_code, 401)
r = client_as("Aarav").get("/wrapped/data")
check("a citizen can open theirs in September", r.status_code, 200)
check("  and it isn't a preview", r.get_json()["preview"], False)

set_now(at(2026, 10, 5))
r = client_as("Aarav").get("/wrapped/data")
check("in October it's sealed for citizens", r.status_code, 403)
check("  marked sealed", r.get_json().get("sealed"), True)
check("  and says when it opens next", r.get_json()["opens_label"], "1 September 2027")
r = client_as("Prathyay").get("/wrapped/data")
check("the President can preview the year in progress", r.status_code, 200)
check("  marked as a preview", r.get_json()["preview"], True)
check("  covering September onwards", r.get_json()["period"]["start"], "1 September 2026")
check("  and the status offers the preview only to him",
      (client_as("Prathyay").get("/wrapped/status").get_json()["can_preview"],
       client_as("Aarav").get("/wrapped/status").get_json()["can_preview"]), (True, False))


print("\n=== 3. money ===")
set_now(at(2026, 9, 10))
d = client_as("Aarav").get("/wrapped/data").get_json()
m = d["money"]
check("salary and courier wage are salary", m["earn"]["salary"], 600.0)
check("20 Pufferbucks from Priya are worth 20 CB", m["earn"]["citizens"], 20.0)
check("100 Aquilines by Cyvapay are worth 10 CB", m["earn"]["sales"], 10.0)
check("insurance and the Armoury are counted once, from the Treasury", m["earn"]["rewards"], 460.0)
check("a casino loss is not a winning", m["earn"]["winnings"], 0.0)
check("so Aarav earned 1,090 CB", m["earned"], 1090.0)
check("250 Cybits sent are worth 5 CB", m["spend"]["citizens"], 55.0)
check("market purchases are shopping", m["spend"]["shopping"], 30.0)
check("VAT and the delivery levy are tax", m["spend"]["tax"], 15.0)
check("the casino took 25 CB net", m["spend"]["casino"], 25.0)
check("  so he spent 125 CB", m["spent"], 125.0)
check("converting money is neither earning nor spending", m["earned"] + m["spent"], 1215.0)
check("September's transfer waits for next year", m["top_paid"], {"name": "Priya", "amount": 55.0})
check("his biggest payer was Priya", m["top_payer"], {"name": "Priya", "amount": 20.0})
check("courier wages are reported on their own", m["courier_pay"], 500.0)


print("\n=== 4. the rest of the year ===")
a = d["activity"]
check("two votes in the year, not the one in September", a["votes"], 2)
check("a vote on a bill from before the year doesn't count", a["bill_votes"], 1)
check("one bill tabled", a["bills"], 1)
check("  and it became law", a["bills_enacted"], 1)
check("two accepted card trades — not the pending one, not September's", a["card_trades"], 2)
check("  mostly with Priya", a["trade_partner"], {"name": "Priya", "count": 2})
check("one parcel delivered to him", a["parcels_received"], 1)
check("one he sent — the open one doesn't count", a["parcels_sent"], 1)
check("two runs as a courier", a["runs"], 2)
check("lent one thing, the declined request aside", a["lent"], 1)
check("borrowed one", a["borrowed"], 1)
check("three pens counted into the Armoury", a["pens"], 3)
check("six messages in the year", a["messages"], 6)
check("  most to Priya", a["dm_partner"], {"name": "Priya", "count": 3})
check("one blog written in the year", a["blogs"], 1)
check("one like in the year", a["likes"], 1)
check("one video, one letter", (a["videos"], a["mail"]), (1, 1))
check("one stock trade worth 20 CB", (a["stock_trades"], a["stock_volume"]), (1, 20.0))
check("one company founded", a["companies"], 1)
check("456 days a citizen by the end of the year", d["me"]["days"], 456)
check("  since 1 June 2025", d["me"]["joined"], "1 June 2025")


print("\n=== 5. the ranking and the personality ===")
check("Aarav out-earned everyone", d["rank"], {"top_pct": 25, "place": 1, "citizens": 4})
check("he tabled a law and voted on another: the Lawmaker", d["persona"]["key"], "lawmaker")
check("  with a name and a line", bool(d["persona"]["name"] and d["persona"]["line"]), True)
check("the reel knows every personality", len(d["personas"]), len(main.WRAPPED_PERSONAS))
p = client_as("Priya").get("/wrapped/data").get_json()
check("Priya earned 185 CB", p["money"]["earned"], 185.0)
check("  which is the top half", p["rank"], {"top_pct": 50, "place": 2, "citizens": 4})
rh = client_as("Rohan").get("/wrapped/data").get_json()
check("Rohan earned nothing, so no ranking is shown", rh["rank"], None)
check("  six runs make him the Courier", rh["persona"]["key"], "courier")
nb = client_as("Newbie").get("/wrapped/data").get_json()
check("a citizen who joined in September is told it's not their year yet", nb["me"]["fresh"], True)
check("  with no days counted", nb["me"]["days"], None)


print("\n=== 6. the Republic's year ===")
n = d["nation"]
check("four citizens — not the foreign nation, the banned or September's", n["citizens"], 4)
check("three joined during the year", n["new_citizens"], 3)
check("75 CB sent between citizens", n["moved"], 75.0)
check("eight parcels delivered", n["deliveries"], 8)
check("two card trades", n["card_trades"], 2)
check("three votes cast", n["ballots"], 3)
check("six messages", n["messages"], 6)
check("one Act passed", n["acts"], 1)
check("one company founded", n["companies"], 1)
check("the busiest month was August", n["busiest_month"], "August")


print("\n=== 7. the plumbing ===")
db.seed("transactions", [{"id": 99, "kind": "transfer", "from_party": "Priya", "to_party": "Aarav",
                          "amount": 1000, "currency": "cybucks", "created_at": IN}])
check("a deck is cached, so it holds still while it's shared",
      client_as("Aarav").get("/wrapped/data").get_json()["money"]["earned"], 1090.0)
main._wrapped_cache.clear()
check("  and rebuilds when the cache clears",
      client_as("Aarav").get("/wrapped/data").get_json()["money"]["earned"], 2090.0)
db.data["transactions"] = [t for t in db.data["transactions"] if t["id"] != 99]
main._wrapped_cache.clear()

db.seed("messages", [{"id": 5000 + i, "sender": "Bulk", "recipient": "Priya", "content": ".", "created_at": IN}
                     for i in range(2500)])
check("reads page past Supabase's 1000-row cap",
      len(main._wr_rows("messages", "id", [("eq", "sender", "Bulk")])), 2500)
check("counts do too", main._wr_count("messages", [("eq", "sender", "Bulk")]), 2500)


class _Broken:
    def table(self, name):
        raise RuntimeError("relation does not exist")


main.supabase = _Broken()
check("a missing table reads as empty, not as an error", main._wr_rows("ballots", "id"), [])
check("  and counts as zero", main._wr_count("ballots"), 0)
main.supabase = db

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
