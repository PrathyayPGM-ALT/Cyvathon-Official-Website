"""Exercise the football-card packet/trading system against an in-memory database."""
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
db.unique["card_wishlist"] = [("username", "card_id")]
db.defaults["card_packet"] = {"quantity": 1, "for_trade": False, "note": "",
                              "edition": "base", "subset": "base", "series": "",
                              "team": "", "card_no": "", "card_url": ""}
db.defaults["card_trades"] = {"status": "pending", "message": "",
                              "offer_ids": "", "want_ids": ""}

db.seed("cybucks", [
    {"id": 1, "username": "Aarav", "designation": "Citizen", "balance": 500, "approved": True},
    {"id": 2, "username": "Meera", "designation": "Citizen", "balance": 500, "approved": True},
    {"id": 3, "username": "Kabir", "designation": "Citizen", "balance": 500, "approved": True},
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

def card_of(owner):
    return [c for c in db.data["card_packet"] if c["owner"] == owner]


print("\n=== 1. adding cards to a packet ===")
r = client_as().post("/packet/add", json={"player_name": "Saka"})
check("logged out cannot add", r.status_code, 401)

r = client_as("Aarav").post("/packet/add", json={
    "player_name": "Bukayo Saka", "team": "Arsenal", "position": "Right Winger",
    "nationality": "England", "edition": "black_edge", "subset": "captain", "rating": 99,
    "card_no": "142",
    "image_url": "https://r2.thesportsdb.com/images/media/player/cutout/x.png",
    "series": "Match Attax 2024/25"})
check("citizen can add a card", r.status_code, 200)
check("  edition kept", r.get_json()["card"]["edition"], "black_edge")
check("  edition label resolved", r.get_json()["card"]["edition_label"], "Black Edge")
check("  subset kept", r.get_json()["card"]["subset_label"], "Captain")
check("  real pull rate surfaced", r.get_json()["card"]["rarity"], "1:30 packets")
check("  card number kept", r.get_json()["card"]["card_no"], "142")
check("  player photo kept", bool(r.get_json()["card"]["image_url"]), True)

r = client_as("Aarav").post("/packet/add", json={"player_name": "X", "edition": "holographic-unicorn"})
check("unknown edition rejected", r.status_code, 400)
r = client_as("Aarav").post("/packet/add", json={"player_name": "X", "subset": "not-a-subset"})
check("unknown card type rejected", r.status_code, 400)
r = client_as("Aarav").post("/packet/add", json={"player_name": "X", "card_url": "javascript:x"})
check("non-https reference link rejected", r.status_code, 400)
r = client_as("Aarav").post("/packet/add", json={"player_name": ""})
check("nameless card rejected", r.status_code, 400)

# A javascript: url must never survive into an <img src>.
r = client_as("Aarav").post("/packet/add", json={
    "player_name": "Hack", "card_image": 'javascript:alert(1)'})
check("non-https card scan rejected", r.status_code, 400)
r = client_as("Aarav").post("/packet/add", json={
    "player_name": "Ollie Watkins", "team": "Aston Villa", "edition": "rainbow_foil",
    "image_url": "http://insecure.example.com/x.png"})
check("non-https player photo dropped, card still added", r.status_code, 200)
check("  photo blanked", r.get_json()["card"]["image_url"], "")

client_as("Meera").post("/packet/add", json={
    "player_name": "Erling Haaland", "team": "Man City", "edition": "gold_rainbow", "rating": 105})
client_as("Meera").post("/packet/add", json={
    "player_name": "Cole Palmer", "team": "Chelsea", "edition": "gold_edge",
    "subset": "hundred"})
check("Meera holds two cards", len(card_of("Meera")), 2)


print("\n=== 2. offering a card for trade ===")
haaland = [c for c in card_of("Meera") if c["player_name"] == "Erling Haaland"][0]
saka    = [c for c in card_of("Aarav") if c["player_name"] == "Bukayo Saka"][0]

r = client_as("Aarav").post("/packet/update", json={"card_id": haaland["id"], "for_trade": True})
check("cannot flip someone else's card", r.status_code, 404)
r = client_as("Meera").post("/packet/update", json={"card_id": haaland["id"], "for_trade": True})
check("owner can put a card up for trade", r.get_json()["card"]["for_trade"], True)
client_as("Aarav").post("/packet/update", json={"card_id": saka["id"], "for_trade": True})


print("\n=== 3. wishlists ===")
r = client_as("Meera").post("/packet/wishlist", json={"card_id": haaland["id"]})
check("cannot wishlist your own card", r.status_code, 400)

before = len(db.data.get("notifications", []))
r = client_as("Aarav").post("/packet/wishlist", json={"card_id": haaland["id"]})
check("wishlisting works", r.get_json()["wished"], True)
check("  owner is notified", len(db.data["notifications"]) > before, True)
r = client_as("Aarav").post("/packet/wishlist", json={"card_id": haaland["id"]})
check("wishlisting twice is a no-op", r.get_json()["wished"], True)
check("  only one wishlist row", len(db.data["card_wishlist"]), 1)

d = client_as("Aarav").get("/packet/wishes").get_json()
check("wishlist lists the card I want", d["wanted"][0]["player_name"], "Erling Haaland")
d = client_as("Meera").get("/packet/wishes").get_json()
check("owner sees who wants their card", d["wanted_from_me"][0]["wanter"], "Aarav")

d = client_as("Aarav").get("/packet/data?user=Meera").get_json()
wished = [c for c in d["cards"] if c["id"] == haaland["id"]][0]
check("their packet marks what I already wished for", wished["wished"], True)
d = client_as("Meera").get("/packet/data").get_json()
mine = [c for c in d["cards"] if c["id"] == haaland["id"]][0]
check("my own packet shows who wants each card", mine["wanted_by"], ["Aarav"])


print("\n=== 4. proposing a trade ===")
palmer = [c for c in card_of("Meera") if c["player_name"] == "Cole Palmer"][0]
watkins = [c for c in card_of("Aarav") if c["player_name"] == "Ollie Watkins"][0]

r = client_as("Aarav").post("/packet/trade", json={
    "want_ids": [haaland["id"]], "offer_ids": []})
check("must offer something", r.status_code, 400)
r = client_as("Aarav").post("/packet/trade", json={
    "want_ids": [], "offer_ids": [saka["id"]]})
check("must want something", r.status_code, 400)
r = client_as("Aarav").post("/packet/trade", json={
    "want_ids": [haaland["id"]], "offer_ids": [haaland["id"]]})
check("cannot offer a card you don't own", r.status_code, 403)
r = client_as("Aarav").post("/packet/trade", json={
    "want_ids": [palmer["id"]], "offer_ids": [saka["id"]]})
check("cannot demand a card that isn't up for trade", r.status_code, 400)

before = len(db.data.get("notifications", []))
r = client_as("Aarav").post("/packet/trade", json={
    "want_ids": [haaland["id"]], "offer_ids": [saka["id"], watkins["id"]],
    "message": "two for your Haaland?"})
check("valid offer accepted", r.status_code, 200)
check("  receiver notified", len(db.data["notifications"]) > before, True)

d = client_as("Meera").get("/packet/trades").get_json()
check("shows up as incoming for Meera", d["incoming"], 1)
check("  'you get' side is the proposer's cards", len(d["trades"][0]["offer"]), 2)
d = client_as("Aarav").get("/packet/trades").get_json()
check("shows up as outgoing for Aarav", d["outgoing"], 1)
check("  and not as incoming", d["incoming"], 0)

s = client_as("Meera").get("/packet/summary").get_json()
check("summary counts the waiting offer", s["offers"], 1)
check("  and names who sent it", s["offer_from"], ["Aarav"])


print("\n=== 5. accepting a trade ===")
tid = db.data["card_trades"][0]["id"]
r = client_as("Kabir").post("/packet/trade/respond", json={"trade_id": tid, "action": "accept"})
check("a bystander cannot accept", r.status_code, 403)
r = client_as("Aarav").post("/packet/trade/respond", json={"trade_id": tid, "action": "accept"})
check("the proposer cannot accept their own offer", r.status_code, 403)

r = client_as("Meera").post("/packet/trade/respond", json={"trade_id": tid, "action": "accept"})
check("receiver can accept", r.get_json()["status"], "accepted")
names = lambda u: sorted(c["player_name"] for c in card_of(u))
check("  Meera received both offered cards",
      names("Meera"), ["Bukayo Saka", "Cole Palmer", "Ollie Watkins"])
check("  Aarav received the Haaland", names("Aarav"), ["Erling Haaland"])
check("  the wishlist entry was cleared", db.data["card_wishlist"], [])
check("  received cards are not auto-listed for trade",
      any(c["for_trade"] for c in card_of("Meera") if c["player_name"] == "Bukayo Saka"), False)

r = client_as("Meera").post("/packet/trade/respond", json={"trade_id": tid, "action": "accept"})
check("a settled trade cannot be accepted twice", r.status_code, 400)

# The cards have moved, so the ids no longer resolve — the history has to
# fall back to the labels captured when the offer was made.
d = client_as("Meera").get("/packet/trades").get_json()
settled = [t for t in d["trades"] if t["id"] == tid][0]
# The label names both halves of the card, the way a collector would.
check("settled trade still says what was offered",
      "Bukayo Saka (Captain, Black Edge)" in settled["offer_label"], True)
check("  and what was wanted", settled["want_label"], "Erling Haaland (Gold Rainbow)")


print("\n=== 6. declining and cancelling ===")
haaland2 = card_of("Aarav")[0]
client_as("Aarav").post("/packet/update", json={"card_id": haaland2["id"], "for_trade": True})
palmer2 = [c for c in card_of("Meera") if c["player_name"] == "Cole Palmer"][0]
client_as("Meera").post("/packet/update", json={"card_id": palmer2["id"], "for_trade": True})

client_as("Meera").post("/packet/trade", json={
    "want_ids": [haaland2["id"]], "offer_ids": [palmer2["id"]]})
t2 = db.data["card_trades"][-1]["id"]
r = client_as("Meera").post("/packet/trade/respond", json={"trade_id": t2, "action": "cancel"})
check("proposer can cancel", r.get_json()["status"], "cancelled")

client_as("Meera").post("/packet/trade", json={
    "want_ids": [haaland2["id"]], "offer_ids": [palmer2["id"]]})
t3 = db.data["card_trades"][-1]["id"]
r = client_as("Aarav").post("/packet/trade/respond", json={"trade_id": t3, "action": "cancel"})
check("receiver cannot cancel", r.status_code, 403)
r = client_as("Aarav").post("/packet/trade/respond", json={"trade_id": t3, "action": "decline"})
check("receiver can decline", r.get_json()["status"], "declined")
check("  cards stayed put", names("Aarav"), ["Erling Haaland"])


print("\n=== 7. duplicates split instead of moving whole ===")
client_as("Kabir").post("/packet/add", json={
    "player_name": "Phil Foden", "edition": "blue_crystal", "subset": "star_ballers",
    "quantity": 3, "for_trade": True})
foden = card_of("Kabir")[0]
client_as("Aarav").post("/packet/trade", json={
    "want_ids": [foden["id"]], "offer_ids": [haaland2["id"]]})
t4 = db.data["card_trades"][-1]["id"]
client_as("Kabir").post("/packet/trade/respond", json={"trade_id": t4, "action": "accept"})
check("one copy moved", [c["quantity"] for c in card_of("Kabir") if c["player_name"] == "Phil Foden"], [2])
check("  receiver got exactly one",
      [c["quantity"] for c in card_of("Aarav") if c["player_name"] == "Phil Foden"], [1])


print("\n=== 8. removing cards & browsing ===")
d = client_as("Kabir").get("/packet/collectors").get_json()
check("collectors lists everyone with cards",
      sorted(c["username"] for c in d["collectors"]), ["Aarav", "Kabir", "Meera"])
check("  and flags which one is me",
      [c["me"] for c in d["collectors"] if c["username"] == "Kabir"], [True])

r = client_as("Aarav").get("/packet/data?user=Nobody")
check("unknown citizen's packet 404s", r.status_code, 404)

foden_mine = [c for c in card_of("Aarav") if c["player_name"] == "Phil Foden"][0]
r = client_as("Meera").post("/packet/remove", json={"card_id": foden_mine["id"]})
check("cannot remove someone else's card", r.status_code, 404)
r = client_as("Aarav").post("/packet/remove", json={"card_id": foden_mine["id"]})
check("owner can remove their card", r.status_code, 200)
check("  it's gone", [c for c in card_of("Aarav") if c["player_name"] == "Phil Foden"], [])


print("\n=== 9. edition catalog ===")
d = client_as("Aarav").get("/packet/editions").get_json()
keys = [e["key"] for e in d["editions"]]
check("catalog exposes the real finishes",
      "black_edge" in keys and "gold_edge" in keys and "blue_crystal" in keys, True)
check("  every finish has a colour", all(e.get("color") for e in d["editions"]), True)
subs = [x["key"] for x in d["subsets"]]
check("  and the real subsets", "captain" in subs and "hundred" in subs, True)
check("  and the release list", "Match Attax 2024/25" in d["series"], True)


print(f"\n{'='*46}\n  {PASS} passed, {FAIL} failed\n{'='*46}")
sys.exit(1 if FAIL else 0)
