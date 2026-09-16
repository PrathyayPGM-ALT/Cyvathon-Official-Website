"""Exercise account security — password rules, lockout, login alerts,
the payment PIN, and the President's security desk."""
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

from datetime import timedelta
import main
from fakedb import FakeSupabase

db = FakeSupabase()
main.supabase = db
app = main.app
app.config["TESTING"] = True
main.limiter.enabled = False

PW = {"Aarav": "battery-staple-42", "Priya": "brush-and-canvas-7", "Rohan": "quiet-river-stone",
      "Zoe": "seventeen-blue-kites", "Troll": "under-the-bridge-9", "Prathyay": "the-chair-abides-1"}
db.seed("cybucks", [
    {"id": 1, "username": "Prathyay", "designation": "President", "balance": 9000, "approved": True,
     "password": main.generate_password_hash(PW["Prathyay"])},
    {"id": 2, "username": "Aarav", "designation": "Citizen", "balance": 900, "approved": True,
     "password": main.generate_password_hash(PW["Aarav"])},
    {"id": 3, "username": "Priya", "designation": "Citizen", "balance": 900, "approved": True,
     "password": main.generate_password_hash(PW["Priya"])},
    {"id": 4, "username": "Rohan", "designation": "Citizen", "balance": 900, "approved": True,
     "password": main.generate_password_hash(PW["Rohan"])},
    {"id": 5, "username": "Zoe", "designation": "Citizen", "balance": 10000, "approved": True,
     "password": main.generate_password_hash(PW["Zoe"])},
    {"id": 6, "username": "Troll", "designation": "Citizen", "balance": 900, "approved": True,
     "password": main.generate_password_hash(PW["Troll"])},
])

PASS = FAIL = 0


def check(label, got, want):
    global PASS, FAIL
    ok = got == want
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(f"  {'OK  ' if ok else 'FAIL'} {label}" + ("" if ok else f"\n         got={got!r} want={want!r}"))


def row(username):
    return next(r for r in db.data["cybucks"] if r["username"] == username)


def notes(username):
    return [n["message"] for n in db.data.get("notifications", []) if n["username"] == username]


def events(username, kind=None):
    return [e for e in db.data.get("login_events", [])
            if e["username"] == username and (kind is None or e["kind"] == kind)]


def login(username, password, ip="203.0.113.7", client=None):
    c = client or app.test_client()
    r = c.post("/login", json={"username": username, "password": password},
               headers={"CF-Connecting-IP": ip})
    return c, r


def client_as(username):
    """A signed-in client, the way the browser gets one."""
    c, r = login(username, PW[username])
    assert r.status_code == 200, r.get_json()
    return c


print("\n=== 1. what counts as a password ===")
check("too short is refused", bool(main._password_problem("Ab3!x")), True)
check("a commonly guessed one is refused", bool(main._password_problem("password123")), True)
check("your own username is refused", bool(main._password_problem("Aarav", "Aarav")), True)
check("  and so is a password built around it", bool(main._password_problem("myAaravpw", "Aarav")), True)
check("too few different characters is refused", bool(main._password_problem("aaaaaaaaaa")), True)
check("a real password is accepted", main._password_problem("battery-staple-42", "Aarav"), None)
check("a PIN of repeated digits is refused", bool(main._pin_problem("0000")), True)
check("a counting PIN is refused", bool(main._pin_problem("1234")), True)
check("a three-digit PIN is refused", bool(main._pin_problem("839")), True)
check("letters aren't a PIN", bool(main._pin_problem("83a1")), True)
check("a real PIN is accepted", main._pin_problem("8391"), None)


print("\n=== 2. guessing a password ===")
# Each guess comes from a different address, the way a determined attacker would.
for i in range(4):
    _, r = login("Aarav", "wrong-guess", ip=f"10.0.0.{i + 1}")
    check(f"guess {i + 1} is refused", r.status_code, 401)
check("  and the tries are counted", row("Aarav")["login_fails"], 4)
_, r = login("Aarav", "wrong-guess", ip="10.0.0.5")
check("the fifth guess locks the account", r.status_code, 401)
check("  the lock is on the account, not the network", bool(row("Aarav")["login_locked_until"]), True)
check("  the counter resets for next time", row("Aarav")["login_fails"], 0)
check("  Aarav is told", any("locked for" in m for m in notes("Aarav")), True)
check("  and it's on the record", len(events("Aarav", "lockout")), 1)
_, r = login("Aarav", PW["Aarav"], ip="10.0.0.9")
check("even the right password waits out the lock", r.status_code, 429)
check("  and says so", r.get_json()["locked"], True)

row("Aarav")["login_locked_until"] = (main._now() - timedelta(minutes=1)).isoformat()
c_aarav, r = login("Aarav", PW["Aarav"], ip="203.0.113.7")
check("once the lock expires, the right password works", r.status_code, 200)
check("  and nothing is held against them", row("Aarav")["login_fails"], 0)


print("\n=== 3. being told about sign-ins ===")
check("a first-ever sign-in isn't an alert", [m for m in notes("Priya") if "new place" in m], [])
c_priya, _ = login("Priya", PW["Priya"], ip="198.51.100.4")
check("  nor is one from the same place twice", [m for m in notes("Priya") if "new place" in m], [])
login("Priya", PW["Priya"], ip="198.51.100.4")
check("  still nothing", len([m for m in notes("Priya") if "new place" in m]), 0)
login("Priya", PW["Priya"], ip="192.0.2.99")
alerts = [m for m in notes("Priya") if "new place" in m]
check("a sign-in from somewhere new is an alert", len(alerts), 1)
check("  naming the address", "192.0.2.99" in alerts[0], True)
check("every sign-in is on the record", len(events("Priya", "login")), 3)
hist = client_as("Priya").get("/account/security").get_json()
check("a citizen can see their own sign-ins", len(hist["logins"]) >= 3, True)
check("  and whether they have a PIN", hist["has_pin"], False)
check("  and the threshold it applies above", hist["pin_threshold"], main.PIN_THRESHOLD)


print("\n=== 3b. being told about the PIN, once ===")
nudges = lambda who: [m for m in notes(who) if "Set a payment PIN" in m]
check("a citizen with no PIN is told at sign-in", len(nudges("Priya")), 1)
login("Priya", PW["Priya"], ip="198.51.100.4")
login("Priya", PW["Priya"], ip="198.51.100.4")
check("  and never again", len(nudges("Priya")), 1)
check("  the log remembers having said it", len(events("Priya", "pin-nudge")), 1)
main._set_user("Rohan", {"pin_hash": main.generate_password_hash("4821")})
login("Rohan", PW["Rohan"])
check("a citizen who already has a PIN isn't nudged", len(nudges("Rohan")), 0)
main._set_user("Rohan", {"pin_hash": None})

check("the President can announce it to everyone",
      [p["key"] for p in main.ANNOUNCEMENT_PRESETS if p["key"] in ("security", "password_reset")],
      ["security", "password_reset"])


print("\n=== 4. changing a password ===")
c = client_as("Rohan")
check("the wrong current password is refused",
      c.post("/account/password", json={"current": "nope", "new": "a-fresh-long-one"}).status_code, 400)
check("reusing the same password is refused",
      c.post("/account/password", json={"current": PW["Rohan"], "new": PW["Rohan"]}).status_code, 400)
check("a weak new password is refused",
      c.post("/account/password", json={"current": PW["Rohan"], "new": "password123"}).status_code, 400)
before = int(row("Rohan").get("session_version") or 0)
r = c.post("/account/password", json={"current": PW["Rohan"], "new": "wind-over-water-12"})
check("a good one is accepted", r.status_code, 200)
check("  every other device is signed out", int(row("Rohan")["session_version"]), before + 1)
check("  but this one stays signed in", c.get("/me").status_code, 200)
check("  Rohan is told", any("password was changed" in m for m in notes("Rohan")), True)
check("  and it's on the record", len([e for e in events("Rohan", "password-change") if e["ok"]]), 1)
check("  as is the attempt that got the current password wrong",
      len([e for e in events("Rohan", "password-change") if not e["ok"]]), 1)
check("the old password no longer works", login("Rohan", PW["Rohan"])[1].status_code, 401)
check("the new one does", login("Rohan", "wind-over-water-12")[1].status_code, 200)
PW["Rohan"] = "wind-over-water-12"


print("\n=== 5. a session that has been signed out ===")
c_zoe = client_as("Zoe")
check("Zoe is signed in", c_zoe.get("/me").status_code, 200)
main._set_user("Zoe", {"session_version": int(row("Zoe").get("session_version") or 0) + 1})
check("raising the stamp ends that session", c_zoe.get("/me").status_code, 401)
c_zoe = client_as("Zoe")
check("  signing in again works", c_zoe.get("/me").status_code, 200)


print("\n=== 6. the payment PIN ===")
check("setting a PIN needs the password",
      c_zoe.post("/account/pin", json={"pin": "8391"}).status_code, 400)
check("  the right one", c_zoe.post("/account/pin", json={"password": "guess", "pin": "8391"}).status_code, 400)
check("an easy PIN is refused",
      c_zoe.post("/account/pin", json={"password": PW["Zoe"], "pin": "1234"}).status_code, 400)
r = c_zoe.post("/account/pin", json={"password": PW["Zoe"], "pin": "8391"})
check("a real PIN is set", (r.status_code, r.get_json()["has_pin"]), (200, True))
check("  Zoe is told what it's for", any("payment PIN is set" in m for m in notes("Zoe")), True)

r = c_zoe.post("/transfer", json={"to_username": "Aarav", "currency": "cybucks", "amount": 100})
check("small payments don't ask for it", r.status_code, 200)
r = c_zoe.post("/transfer", json={"to_username": "Aarav", "currency": "cybucks", "amount": 600})
check("a large one does", (r.status_code, r.get_json().get("pin_required")), (400, True))
r = c_zoe.post("/transfer", json={"to_username": "Aarav", "currency": "cybucks", "amount": 600, "pin": "1111"})
check("the wrong PIN is refused", (r.status_code, r.get_json().get("pin_required")), (400, True))
check("  and counts down what's left", "tries left" in r.get_json()["error"], True)
for _ in range(main.PIN_FAIL_LIMIT - 2):
    c_zoe.post("/transfer", json={"to_username": "Aarav", "currency": "cybucks", "amount": 600, "pin": "1111"})
r = c_zoe.post("/transfer", json={"to_username": "Aarav", "currency": "cybucks", "amount": 600, "pin": "1111"})
check("enough wrong PINs pause large payments", bool(row("Zoe")["pin_locked_until"]), True)
r = c_zoe.post("/transfer", json={"to_username": "Aarav", "currency": "cybucks", "amount": 600, "pin": "8391"})
check("  even the right PIN waits", (r.status_code, r.get_json().get("pin_locked")), (429, True))
check("  Zoe is warned", any("wrong payment PIN" in m for m in notes("Zoe")), True)
row("Zoe")["pin_locked_until"] = (main._now() - timedelta(minutes=1)).isoformat()
before = row("Aarav")["balance"]
r = c_zoe.post("/transfer", json={"to_username": "Aarav", "currency": "cybucks", "amount": 600, "pin": "8391"})
check("the right PIN sends the money", r.status_code, 200)
check("  and it arrives", row("Aarav")["balance"], before + 600)
check("  with the slate wiped clean", int(row("Zoe")["pin_fails"] or 0), 0)

c_ph = client_as("Prathyay")
r = c_ph.post("/transfer", json={"to_username": "Aarav", "currency": "cybucks", "amount": 600})
check("a citizen with no PIN is told to set one", (r.status_code, r.get_json().get("need_pin")), (400, True))


print("\n=== 7. the President's security desk ===")
c_troll = client_as("Troll")
for path, body in [("/admin/security/logout", {"username": "Aarav"}),
                   ("/admin/security/reset", {"username": "Aarav"}),
                   ("/admin/security/lock", {"username": "Aarav"})]:
    check(f"a citizen can't use {path.split('/')[-1]}", c_troll.post(path, json=body).status_code, 403)
check("nor read another's history",
      c_troll.get("/admin/security/user?username=Aarav").status_code, 403)
d = c_ph.get("/admin/security/user?username=Aarav").get_json()
check("the President can", d["success"], True)
check("  with the account's state", d["citizen"]["username"], "Aarav")
check("  and its sign-ins", len(d["logins"]) > 0, True)
check("an unknown citizen is a 404",
      c_ph.get("/admin/security/user?username=Nobody").status_code, 404)

check("signing a citizen out everywhere works",
      c_ph.post("/admin/security/logout", json={"username": "Aarav"}).status_code, 200)
check("  their session is dead", c_aarav.get("/me").status_code, 401)
check("  and they're told", any("signed your account out" in m for m in notes("Aarav")), True)

r = c_ph.post("/admin/security/reset", json={"username": "Aarav"})
temp = r.get_json()["temporary_password"]
check("a reset returns a one-time password", bool(temp), True)
check("  the old password is dead", login("Aarav", PW["Aarav"])[1].status_code, 401)
c_new, r = login("Aarav", temp)
check("  the one-time one works", r.status_code, 200)
check("  and says a new password is owed", r.get_json()["must_change"], True)
check("until they set one, they can't act",
      c_new.post("/transfer", json={"to_username": "Priya", "currency": "cybucks", "amount": 5}).status_code, 403)
check("  though they can still read", c_new.get("/me").status_code, 200)
r = c_new.post("/account/password", json={"current": temp, "new": "new-leaf-turning-3"})
check("  and they can set the new password", r.status_code, 200)
check("  which clears the debt", bool(row("Aarav")["must_change_pw"]), False)
check("  so they can act again",
      c_new.post("/transfer", json={"to_username": "Priya", "currency": "cybucks", "amount": 5}).status_code, 200)

check("locking an account works",
      c_ph.post("/admin/security/lock", json={"username": "Troll", "reason": "Under review"}).status_code, 200)
check("  the locked citizen is signed out", c_troll.get("/me").status_code, 401)
_, r = login("Troll", PW["Troll"])
check("  and can't get back in", r.status_code, 403)
check("  the reason is kept", row("Troll")["lock_reason"], "Under review")
check("unlocking works",
      c_ph.post("/admin/security/lock", json={"username": "Troll", "locked": False}).status_code, 200)
check("  and they're back", login("Troll", PW["Troll"])[1].status_code, 200)


print("\n=== 8. before the migration ===")


class _Broken:
    def table(self, name):
        raise RuntimeError("column does not exist")


main.supabase = _Broken()
check("a failed write is reported, not raised", main._set_user("Aarav", {"login_fails": 1}), False)
check("history reads as empty", main._login_history("Aarav"), [])
with app.test_request_context("/", headers={"CF-Connecting-IP": "1.1.1.1"}):
    main._log_login("Aarav", True, "login")      # must not raise
check("  and logging a sign-in stays quiet", True, True)
main.supabase = db

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
