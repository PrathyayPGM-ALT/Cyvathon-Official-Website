"""Exercise the Cyvathon app: what a phone needs to install it and run it."""
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

from PIL import Image
import main
from fakedb import FakeSupabase

main.supabase = FakeSupabase()
app = main.app
app.config["TESTING"] = True
main.limiter.enabled = False
c = app.test_client()

PASS = FAIL = 0


def check(label, got, want):
    global PASS, FAIL
    ok = got == want
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(f"  {'OK  ' if ok else 'FAIL'} {label}" + ("" if ok else f"\n         got={got!r} want={want!r}"))


def on_disk(src):
    return os.path.join(ROOT, src.lstrip("/"))


print("\n=== 1. the manifest ===")
r = c.get("/manifest.webmanifest")
check("is served at the root", r.status_code, 200)
check("  as a web app manifest", r.mimetype, "application/manifest+json")
m = json.loads(r.get_data(as_text=True))
check("it's called Cyvathon", (m["name"], m["short_name"]), ("Cyvathon", "Cyvathon"))
check("opens on the dashboard", m["start_url"], "/")
check("  and covers the whole site", m["scope"], "/")
check("opens full-screen, like an app", m["display"], "standalone")
sizes = {(i["sizes"], i.get("purpose", "any")) for i in m["icons"]}
check("has the 192px icon phones need", ("192x192", "any") in sizes, True)
check("has the 512px icon", ("512x512", "any") in sizes, True)
check("has an Android maskable icon", ("512x512", "maskable") in sizes, True)
for i in m["icons"]:
    path = on_disk(i["src"])
    w, h = (int(x) for x in i["sizes"].split("x"))
    check(f"  {os.path.basename(path)} exists at {i['sizes']}",
          os.path.exists(path) and Image.open(path).size, (w, h))
mask = Image.open(on_disk("/static/icons/icon-maskable-512.png")).convert("RGBA")
check("the maskable icon fills every corner (Android crops it itself)", mask.getpixel((1, 1))[3], 255)
check("long-press shortcuts point at real pages",
      all(c.get(s["url"]).status_code in (200, 302) for s in m["shortcuts"]), True)


print("\n=== 2. the service worker ===")
r = c.get("/sw.js")
check("is served from the root, so it covers the whole site", r.status_code, 200)
check("  as JavaScript", r.mimetype, "application/javascript")
check("  and never cached, so updates reach phones", "no-cache" in r.headers.get("Cache-Control", ""), True)
sw = r.get_data(as_text=True)
shell = re.findall(r'"(/static/[^"]+)"', sw)
check("everything it keeps for offline exists",
      [s for s in shell if not os.path.exists(on_disk(s))], [])
check("it only ever handles GET requests", 'req.method !== "GET"' in sw, True)
check("it leaves other websites alone", "url.origin !== self.location.origin" in sw, True)
check("it never keeps anything outside the app's own files",
      re.search(r'pathname\.startsWith\("/static/"\)', sw) is not None, True)
check("with no connection, pages show the offline screen", "caches.match(OFFLINE)" in sw, True)


print("\n=== 3. the offline screen ===")
off = open(on_disk("/static/offline.html"), encoding="utf-8").read()
check("exists", bool(off), True)
check("needs nothing from another website", re.findall(r'(?:src|href)="https?://', off), [])
check("comes back by itself when the connection does", 'addEventListener("online"' in off, True)


print("\n=== 4. every page can be installed ===")
pages = [p for p in glob.glob(os.path.join(ROOT, "static", "*.html"))
         if not p.endswith(("checkout.html", "offline.html"))]
missing = [os.path.basename(p) for p in pages
           if 'rel="manifest"' not in open(p, encoding="utf-8").read()]
check(f"all {len(pages)} pages link the manifest", missing, [])
missing = [os.path.basename(p) for p in pages
           if 'apple-mobile-web-app-capable' not in open(p, encoding="utf-8").read()]
check("  and tell iPhones they can run full-screen", missing, [])
checkout = open(os.path.join(ROOT, "static", "checkout.html"), encoding="utf-8").read()
check("the Cyvapay checkout other websites send people to is left as it is",
      'rel="manifest"' in checkout, False)


print("\n=== 5. the app shell ===")
appjs = open(on_disk("/static/app.js"), encoding="utf-8").read()
check("every page registers the service worker", 'navigator.serviceWorker.register("/sw.js")' in appjs, True)
check("the install offer is held until the app decides when to show it",
      "beforeinstallprompt" in appjs and "e.preventDefault()" in appjs, True)
check("the nav loads the shell", "loadShell(user)" in appjs, True)
shelljs = open(on_disk("/static/appshell.js"), encoding="utf-8").read()
for tab in ('"/"', '"/bank"', '"/chat"', '"/notifications"'):
    check(f"the tab bar has {tab}", f"href: {tab}" in shelljs, True)
check("the tab bar sits under every pop-up (z-index below the lowest, 80)",
      "z-index:60" in shelljs, True)
check("  and keeps the music player clear of it", "body.has-tabbar .cm{" in shelljs, True)
check("on a phone the floating music button makes way for one in the header",
      "body.has-tabbar .cm-fab" in shelljs and 'className = "m-music"' in shelljs, True)
check("  which doesn't close the player the moment it opens it", "e.stopPropagation()" in shelljs, True)
check("nothing on a page may widen a phone's screen",
      "overflow-x:clip" in shelljs, True)
check("the menu sheet is built from the real nav, so it can't drift from it",
      "header .nav-links .nav-group" in shelljs, True)


print("\n=== 6. every part of the Republic has its own colour ===")
css = open(on_disk("/static/theme.css"), encoding="utf-8").read()
moods = re.findall(r'^html\[data-mood="(\w+)"\]', css, re.M)
check("seven page colours",
      sorted(set(moods)), ["cyan", "emerald", "gold", "orange", "pink", "rose", "violet"])
check("  each with a light-mode version",
      all(f'html[data-theme="light"][data-mood="{m}"]' in css for m in moods), True)
check("Government pages glow gold", re.search(r'gold:\s*\["government"', appjs) is not None, True)
check("the header and phone tab bar keep the national blue",
      "html[data-mood] header, html[data-mood] .cyv-tabbar" in css, True)
check("buttons take the page's colour", "color:var(--btn-ink);" in css, True)
check("the dashboard keeps its own look", '"index"' not in appjs.split("PAGE_MOOD")[1].split("};")[0], True)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
