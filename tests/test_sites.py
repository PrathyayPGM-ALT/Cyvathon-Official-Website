"""Exercise Citizen Sites — the directory of websites citizens have built."""
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

db = FakeSupabase()                     # rows are stamped 2026-08-27 12:00 UTC
main.supabase = db
main._now = lambda: datetime(2026, 8, 28, tzinfo=timezone.utc)   # so today's stars are this week's
db.unique["citizen_sites"] = [("url_key",)]
db.unique["site_stars"] = [("site_id", "username")]
db.unique["site_reports"] = [("site_id", "username")]
db.defaults["citizen_sites"] = {"title": "", "description": "", "category": "other", "cyvapay": False,
                                "stars": 0, "clicks": 0, "reports": 0, "hidden": False}
db.defaults["site_reports"] = {"reason": "broken"}

db.seed("cybucks", [
    {"id": 1, "username": "Prathyay", "designation": "President", "balance": 9000, "approved": True},
    {"id": 2, "username": "Amartya", "designation": "Citizen", "balance": 500, "approved": True, "avatar": "https://img.example.com/a.png"},
    {"id": 3, "username": "Aarav", "designation": "Citizen", "balance": 500, "approved": True},
    {"id": 4, "username": "Priya", "designation": "Citizen", "balance": 500, "approved": True},
    {"id": 5, "username": "Rohan", "designation": "Citizen", "balance": 500, "approved": True},
    {"id": 6, "username": "Zoe", "designation": "Citizen", "balance": 500, "approved": True},
])
db.seed("cyvapay_links", [{"id": 1, "owner": "Amartya", "active": True, "code": "abc"}])

app = main.app
app.config["TESTING"] = True
main.limiter.enabled = False

PASS = FAIL = 0


def check(label, got, want):
    global PASS, FAIL
    ok = got == want
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(f"  {'OK  ' if ok else 'FAIL'} {label}" + ("" if ok else f"\n         got={got!r} want={want!r}"))


def client_as(username=None):
    c = app.test_client()
    if username:
        with c.session_transaction() as sess:
            sess["username"] = username
    return c


def post(username, path, body):
    return client_as(username).post(path, json=body)


def listing(username=None, **args):
    q = "&".join(f"{k}={v}" for k, v in args.items())
    return client_as(username).get("/sites/list" + (f"?{q}" if q else "")).get_json()


def site(sid):
    return next((s for s in db.data.get("citizen_sites", []) if s["id"] == sid), None)


def notes(username):
    return [n["message"] for n in db.data.get("notifications", []) if n["username"] == username]


print("\n=== 1. listing a site ===")
check("the page itself loads", client_as().get("/sites").status_code, 200)
r = post("Amartya", "/sites/add", {"url": "aquacast.onrender.com", "category": "project"})
check("a bare address is accepted", r.status_code, 200)
aquacast = r.get_json()["site"]
check("  and becomes an https link", aquacast["url"], "https://aquacast.onrender.com")
check("  titled by its host when no name is given", aquacast["title"], "aquacast.onrender.com")
check("  filed under its category", aquacast["category"], "project")
check("  and it goes on the owner's record",
      any("aquacast" in r["entry"] for r in db.data.get("records", []) if r["username"] == "Amartya"), True)

for bad, why in [("http://plain.example.com", "plain http"), ("javascript:alert(1)", "a script"),
                 ("https://localhost:3000", "localhost"), ("https://192.168.1.4/x", "an IP address"),
                 ("https://user:pw@evil.example.com", "a link with a password in it"),
                 ("https://nodot", "an address with no domain"),
                 ("https://ex.example.com/" + "a" * 400, "a link that's too long"), ("", "nothing at all")]:
    check(f"refuses {why}", post("Amartya", "/sites/add", {"url": bad}).status_code, 400)

r = post("Priya", "/sites/add", {"url": "https://WWW.aquacast.onrender.com/"})
check("the same site can't be listed twice — www. and a slash don't make it new", r.status_code, 400)
check("  and it says who listed it", "Amartya" in r.get_json()["error"], True)
check("an unknown category is filed under Other",
      post("Priya", "/sites/add", {"url": "priya.art", "category": "nonsense",
                                   "title": "Priya's Studio", "description": "Paintings and sketches"})
      .get_json()["site"]["category"], "other")
priya = site(max(s["id"] for s in db.data["citizen_sites"]))
post("Priya", "/sites/edit", {"id": priya["id"], "category": "art"})
aquadash = post("Amartya", "/sites/add", {"url": "https://aquadash-e3ll.onrender.com", "category": "tool",
                                          "cyvapay": True}).get_json()["site"]
for u in ("amartya.dev", "amartya.blog", "amartya.games"):
    post("Amartya", "/sites/add", {"url": u})
r = post("Amartya", "/sites/add", {"url": "one-too-many.example.com"})
check("a sixth site is refused", r.status_code, 400)
check("  with the limit in the message", "5" in r.get_json()["error"], True)
store = post("Aarav", "/sites/add", {"url": "aarav.store", "category": "business", "cyvapay": True,
                                     "title": "<script>alert(1)</script>"}).get_json()["site"]
check("titles are kept as typed — the page escapes them", store["title"], "<script>alert(1)</script>")


print("\n=== 2. the directory ===")
d = listing()
check("it's public — you needn't be logged in", d["success"], True)
check("  all seven sites are listed", len(d["sites"]), 7)
check("  with the limit", d["limit"], 5)
check("  and every category, with counts",
      {c["key"]: c["count"] for c in d["categories"]}["tool"], 1)
by_id = {s["id"]: s for s in d["sites"]}
check("a site that takes Cyvapay shows the badge when its owner has a live link",
      by_id[aquadash["id"]]["cyvapay"], True)
check("  but not when the owner has no link", by_id[store["id"]]["cyvapay"], False)
check("owners come with their avatar", by_id[aquacast["id"]]["avatar"], "https://img.example.com/a.png")
check("new sites are marked new", by_id[aquacast["id"]]["new"], True)
check("a logged-in citizen sees how many they've listed", listing("Amartya")["mine"], 5)
check("  and which are theirs", [s["mine"] for s in listing("Amartya")["sites"]
                                 if s["id"] == aquacast["id"]], [True])
check("search reaches descriptions", [s["title"] for s in listing(q="paint")["sites"]], ["Priya's Studio"])
check("search reaches owners", len(listing(q="amartya")["sites"]), 5)
check("filter by category", [s["id"] for s in listing(cat="art")["sites"]], [priya["id"]])
check("one citizen's shelf, for their ID card", len(listing(owner="Amartya")["sites"]), 5)


print("\n=== 3. stars ===")
check("you can't star your own site", post("Amartya", "/sites/star", {"id": aquacast["id"]}).status_code, 400)
check("a stranger can't star at all", client_as().post("/sites/star", json={"id": aquacast["id"]}).status_code, 401)
r = post("Priya", "/sites/star", {"id": aquacast["id"]}).get_json()
check("Priya stars aquacast", (r["starred"], r["stars"]), (True, 1))
check("  and Amartya hears about the first star", any("first star" in m for m in notes("Amartya")), True)
post("Aarav", "/sites/star", {"id": aquacast["id"]})
post("Priya", "/sites/star", {"id": aquadash["id"]})
r = post("Aarav", "/sites/star", {"id": aquacast["id"]}).get_json()
check("starring again takes the star back", (r["starred"], r["stars"]), (False, 1))
r = post("Aarav", "/sites/star", {"id": aquacast["id"]}).get_json()
check("  and again gives it back", (r["starred"], r["stars"]), (True, 2))
check("the count is stored", site(aquacast["id"])["stars"], 2)
check("Priya's view shows what she starred",
      [s["starred"] for s in listing("Priya")["sites"] if s["id"] == aquacast["id"]], [True])
check("top sort puts the most-starred first", [s["id"] for s in listing(sort="top")["sites"]][:2],
      [aquacast["id"], aquadash["id"]])
check("new sort puts the newest first", listing(sort="new")["sites"][0]["id"], store["id"])
db.seed("site_stars", [{"id": 9001, "site_id": aquadash["id"], "username": "Zoe",
                        "created_at": "2026-08-01T12:00:00+00:00"},
                       {"id": 9002, "site_id": aquadash["id"], "username": "Rohan",
                        "created_at": "2026-08-02T12:00:00+00:00"}])
f = listing()["featured"]
check("Site of the Week counts this week's stars, not last month's", f["id"], aquacast["id"])
check("  and says how many", f["week_stars"], 2)


print("\n=== 4. visits ===")
r = client_as("Priya").get(f"/sites/go/{aquadash['id']}")
check("visiting goes through to the site", (r.status_code, r.headers["Location"]),
      (302, "https://aquadash-e3ll.onrender.com"))
client_as().get(f"/sites/go/{aquadash['id']}")
client_as("Amartya").get(f"/sites/go/{aquadash['id']}")
check("visits are counted, the owner's own excepted", site(aquadash["id"])["clicks"], 2)
check("most-visited sort", listing(sort="visited")["sites"][0]["id"], aquadash["id"])
check("an unknown site sends you back to the directory",
      client_as().get("/sites/go/424242").headers["Location"].endswith("/sites"), True)


print("\n=== 5. reports ===")
check("you can't report your own site", post("Aarav", "/sites/report", {"id": store["id"], "reason": "scam"}).status_code, 400)
check("reasons are checked", post("Priya", "/sites/report", {"id": store["id"], "reason": "vibes"}).status_code, 400)
r = post("Priya", "/sites/report", {"id": store["id"], "reason": "scam"}).get_json()
check("one report doesn't take a site down", r["hidden"], False)
check("the same citizen can't report twice", post("Priya", "/sites/report", {"id": store["id"], "reason": "scam"}).status_code, 400)
post("Rohan", "/sites/report", {"id": store["id"], "reason": "scam"})
r = post("Zoe", "/sites/report", {"id": store["id"], "reason": "broken"}).get_json()
check("three reports pull it for review", r["hidden"], True)
check("  the owner is told", any("taken down for review" in m for m in notes("Aarav")), True)
check("  and so is the President", any("pulled" in m for m in notes("Prathyay")), True)
check("a pulled site leaves the public directory", store["id"] in [s["id"] for s in listing()["sites"]], False)
check("  but its owner still sees it, marked", [s["hidden"] for s in listing("Aarav")["sites"]
                                                  if s["id"] == store["id"]], [True])
check("  and visiting it goes nowhere",
      client_as("Priya").get(f"/sites/go/{store['id']}").headers["Location"].endswith("/sites"), True)
rv = listing("Prathyay")["review"]
check("the President's review queue has it", [x["id"] for x in rv], [store["id"]])
check("  with the reasons tallied", rv[0]["reasons"], {"scam": 2, "broken": 1})
check("citizens have no review queue", listing("Priya").get("review"), None)


print("\n=== 6. moderation, edits and removal ===")
check("a citizen can't moderate", post("Priya", "/sites/moderate", {"id": store["id"], "action": "restore"}).status_code, 403)
check("the President restores it", post("Prathyay", "/sites/moderate", {"id": store["id"], "action": "restore"}).status_code, 200)
check("  it's back, with its reports cleared", (site(store["id"])["hidden"], site(store["id"])["reports"]), (False, 0))
check("  the report rows are gone too", [x for x in db.data.get("site_reports", []) if x["site_id"] == store["id"]], [])
check("  and Aarav is told", any("back in the directory" in m for m in notes("Aarav")), True)
check("someone else can't edit your site", post("Priya", "/sites/edit", {"id": aquacast["id"], "title": "Mine now"}).status_code, 403)
r = post("Amartya", "/sites/edit", {"id": aquacast["id"], "title": "AquaCast", "description": "Podcasts"})
check("the owner can", (r.status_code, r.get_json()["site"]["title"]), (200, "AquaCast"))
check("an edit can't copy another site's address",
      post("Amartya", "/sites/edit", {"id": aquacast["id"], "url": "priya.art"}).status_code, 400)
check("the President can edit any site", post("Prathyay", "/sites/edit", {"id": store["id"], "title": "Aarav's Store"}).status_code, 200)
check("someone else can't delete your site", post("Priya", "/sites/delete", {"id": aquacast["id"]}).status_code, 403)
check("the owner can", post("Amartya", "/sites/delete", {"id": aquacast["id"]}).status_code, 200)
check("  and its stars go with it", [x for x in db.data.get("site_stars", []) if x["site_id"] == aquacast["id"]], [])
check("  which frees a slot", post("Amartya", "/sites/add", {"url": "amartya.news"}).status_code, 200)
check("the President can remove a site outright",
      post("Prathyay", "/sites/moderate", {"id": priya["id"], "action": "remove"}).status_code, 200)
check("  and the owner is told", any("removed from the directory" in m for m in notes("Priya")), True)
check("  it's gone", site(priya["id"]), None)


print("\n=== 7. before the migration ===")


class _Broken:
    def table(self, name):
        raise RuntimeError("relation does not exist")


main.supabase = _Broken()
r = client_as().get("/sites/list")
check("a missing table is a 503, not a crash", r.status_code, 503)
check("  naming the migration to run", "migration_citizen_sites.sql" in r.get_json()["error"], True)
main.supabase = db

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
