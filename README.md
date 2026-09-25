
# Cyvathon – Official Website & Banking System

> **Code. Conquer. Cause Creativity.**

Cyvathon is a playful micronation built around coding, creativity, and community.  
This repository contains the code for the **Cyvathon Official Website**, the **Cyvathon Chat** and the **Cybucks Banking System**, the virtual economy inside Cyvathon.

<img src="https://skillicons.dev/icons?i=python" width="55" /> &nbsp;&nbsp;
<img src="https://skillicons.dev/icons?i=javascript" width="55" /> &nbsp;&nbsp;
<img src="https://skillicons.dev/icons?i=css" width="55" /> &nbsp;&nbsp;
<img src="https://skillicons.dev/icons?i=html" width="55" /> &nbsp;&nbsp;
<img src="https://skillicons.dev/icons?i=PostgreSQL" width="55" /> &nbsp;&nbsp;

---

## 🌍 Live Websites

- **Main Cyvathon Website:**  
  https://cyvathon.onrender.com/ 

- **Cybucks Banking System (from this repo):**  
  https://cyvathon.onrender.com/bank
- **Cyvathon Chat (from this repo):**  
  https://cyvathon.onrender.com/chat  
- **Card Packets — Match Attax trading (from this repo):**  
  https://cyvathon.onrender.com/packet  

---

## 🧱 Project Overview

### 1. Cyvathon Official Website
A simple HTML-based site introducing:
- What Cyvathon is  
- Citizen information  
- Links to alliances, YouTube, and the banking system  

### 2. Cyvathon Banking System
A fun in-world currency manager built for:
- User registration  
- Login  
- Account balance tracking  
- Sending Cybucks to other users  

> **Note:** This is a fun/learning project, not a real banking system.

### 3. Card Packets
Citizen-to-citizen **Match Attax** trading, under Community:
- Search any footballer in an online football database — the player's photo,
  club, position and nationality come straight from it
- Record the card you actually pulled: its **subset** (Base, Captain, 100 Club,
  Hall of Fame…) and its **finish** (Blue Crystal, Black Edge, Gold Edge,
  Goldrush /100, Gold Rainbow 1/1…), with the real pull rates shown
- Photograph your own copy and it becomes the card face
- Tap any card to open it and turn it over — stats on the back, same tilt-and-
  flip feel as the Cyvathon debit card
- Browse other citizens' packets and **wishlist** what you're missing — the
  owner is notified
- Offer cards out of your own packet for theirs; accepting swaps them over

Needs `migration_card_trading.sql` run once in Supabase, plus the public `chat`
Storage bucket that avatars already use (card photos go in there too).

### 4. Cyvazon — national delivery
Free delivery anywhere in school, run by citizen couriers (`/cyvazon`):
- Send anything to anyone — say which class it leaves from and which class
  it goes to, and a courier runs it
- Marketplace purchases and accepted card trades raise a parcel automatically,
  so the goods can't be quietly kept after the deal settles
- Couriers **apply**, and the President approves each one from the delivery
  admin panel (visible only to the President) before they can carry anything
- Approved couriers earn 500 CB per pay period on top of their salary
- Only the **recipient** can confirm a parcel arrived — a courier can't close
  their own run
- Delivery is free, so the nation funds it: a 5% delivery levy rides on top of
  VAT while the service is open, logged separately in the Treasury

Needs `migration_delivery.sql`. Wage and levy are tunable from the Presidential
Admin Panel; school areas live in `SCHOOL_AREAS` in `main.py` — edit that list
to match your school. Class names are free text.

---

### 5. Cyvashield — national insurance
Free cover for every citizen (`/shield`):
- Three plans — Basic, Standard, Full Cover — all free. The plan only sets the
  per-claim cap (500 / 1500 / 5000 CB) and how many claims a month
- Covers undelivered parcels, marketplace purchases that never arrived, stolen
  Cybucks, card trades that went bad, and scams
- A claim can cite a Cyvazon parcel, a marketplace listing or a card trade, and
  the President's claim desk then **checks the story against the records** —
  it will say plainly if the claimant already signed for the parcel they say
  never arrived
- The President approves (in full or in part), rejects, or rules a claim
  fraudulent — which files a criminal record and bars them from office
- Payouts come from the Treasury. Switching to a bigger plan takes 3 days to
  take effect, so cover can't be upgraded the moment something goes wrong

Needs `migration_insurance.sql`. The insurance levy defaults to 0 — cover is
genuinely free, funded from general revenue — and is tunable from the
Presidential Admin Panel if payouts ever outrun the Treasury.

---

### 6. Cyvalend — the lending library
Borrow what you forgot; lend what you're not using (`/cyvalend`):
- Put spare things on the shelf — calculator, charger, pen, textbook, sports
  kit. Set how many days you'll lend for and, optionally, a refundable deposit
- Ask to borrow; the **owner** approves each request themselves
- Approving holds the deposit, starts the clock, and can hand the item to a
  **Cyvazon** courier to carry to the borrower's class
- The **owner** confirms the return — a borrower can't close their own loan.
  The deposit comes back either way; a late return is recorded against them
- Reliability is visible: how many times you've lent, returned, and returned late
- If something never comes home, **Cyvashield** covers it (Standard plan and up)

Needs `migration_cyvalend.sql`. Deposit cap and opening the library are tunable
from the Presidential Admin Panel.

---

### 7. The Armoury — support the Corps
The Republic buys G2 pens as **ammunition**, at 400 CB a round (`/pens`):
- Cyvathon fields no conventional weapons. Its defence rests on the **pen
  launcher**, and the G2 is the standard munition — right gauge, the clip gives
  it spin, and it flies true
- There is no pen foundry and no import route, so the only supply line is the
  citizens themselves
- Citizens **hand in** rounds; **Cyvazon carries them straight to the
  Quartermaster** (Prathyay); the Quartermaster logs what actually arrived and
  only then is anything paid
- The Quartermaster's count is what gets paid, not the citizen's claim — and
  the grade can be corrected on arrival: **live round** 100%, **drill round**
  (out of ink) 25%, **salvage** 10%
- Armoury stock counts toward national GDP, because materiel is national
  property. Doctrine is unchanged: defence, not invasion

Needs `migration_pen_reserve.sql`. The rate is tunable from the Presidential
Admin Panel; set `PEN_REGISTRAR` in the environment to change who holds the
armoury.

---

### 8. Cabinet Powers
Ministers get real authority, split two ways (`/cabinet`):

**Duties** are delegated outright — no approval needed, because making the
President countersign every logged pen would only move the bottleneck:
| Brief | Carries out |
|---|---|
| Defence | Works the Armoury desk — logs G2 rounds in and pays for them |
| Transport & Logistics | Vets Cyvazon couriers |
| Justice & Home Affairs | Rules on Cyvashield claims |

**Policy is proposed, never imposed.** A minister who wants to move a national
lever raises a proposal and *nothing changes until the President assents*.
Finance covers VAT, the GDP multiplier, savings and bond rates, loans, the
company fee and the citizen grant; Defence the Armoury rate; Transport the
courier wage and delivery levy; Justice the insurance levy and deposit cap.
Assent applies the change immediately and records it in the Gazette.

A ministry picks up a brief **from its name** — call one "Ministry of Defence"
and whoever holds it works the Armoury. Needs `migration_cabinet_powers.sql`.

### Weekly salary
| Designation | CB / week |
|---|---|
| Vice President · Chancellor | 1000 |
| Minister · Judge · Security Minister | 900 |
| Founder · Head of Coding · Head of Hacking | 800 |
| Employee | 500 |
| Citizen | 100 |
| **President** | **0** — holds the Treasury and spends it on the nation |

Couriers draw 500 CB on their own clock, on top of the above.

---

### 9. National Timeline
The Republic's own record, at `/timeline` — public, so visitors can read it too.

| | |
|---|---|
| **26 May 2025** | Cyvathon is founded — a nation of citizens and ideas, no territory |
| **31 May 2025** | The website goes live, with the Cybucks banking system |
| **14 June 2026** | Treaty with Crystonia |
| **2 September 2026** | **The Treaty of Anti-Anarchism** — class 8E at TISB placed under Cyvathonian rule, agreed unanimously by everyone in the class. Cyvathon's first true territory |

The Treaty of Anti-Anarchism is published as a readable PDF at
`/static/cyvathon-treaty-of-anti-anarchism.pdf`, linked from its timeline entry.
Rebuild it with `python build_treaty_anti_anarchism.py` (needs `reportlab`,
which is build-time only and deliberately not in `requirements.txt`).

The founding events live in `main.py` as the canonical record: they are the same
for every deployment and cannot be deleted. Everything after is written by the
President and stored in `timeline_events` — needs `migration_timeline.sql`,
though the founding record still renders without it.

---

### 10. Cyvapay — take payments on any website
A payment gateway for Cybucks (`/cyvapay`, under Money):
- Make a **payment link** — fixed price, or "payer chooses" within your bounds,
  reusable or single-use, collected as yourself or as your company
- Paste the ready-made **button** (plain HTML, no script) or the bare link into
  any website, blog or page builder
- Anyone who clicks lands on a **hosted checkout** at `/cyvapay/checkout/<code>`.
  No account? They're offered a free one and return to the same checkout
- Every payment gets a receipt number both sides can see

The rules that make it safe, none of them switchable:
- **The amount comes from the link, never the request** — a fixed link charges
  what the merchant set, whatever the payer sends
- **Paying is a POST by a signed-in payer who pressed Pay** — loading a URL
  can never move money
- **Return links are shown as a button, never followed automatically** — no
  open redirect
- **Only earned Cybucks can be paid out**, same as a bank transfer, so a link
  can't be used to farm the welcome grant
- Deleting a link never deletes its receipts

Needs `migration_cyvapay.sql`. The Treasury fee defaults to 0 (free to use) and
is tunable from the Presidential Admin Panel. Login now honours a `?next=`
path (same-site only) so signups return to where they started.

---

**On the card data.** The player lookup uses TheSportsDB's free API — set
`SPORTSDB_API_KEY` to use your own key instead of the shared test key. There is
no open Match Attax card API: Topps publishes none, and card databases like
TCDB sit behind bot protection that blocks server-to-server use. So the subset,
finish and pull-rate lists in `main.py` are reference data transcribed from the
published Match Attax checklist, and the card art is either the player photo
from TheSportsDB or the owner's own photo of the card in their hand. Update
`CARD_SUBSETS` / `CARD_EDITIONS` / `CARD_SERIES` when a new season ships.

---

### 18. Page colours
Every page keeps the same navy design, but each part of the Republic has its own
colour, the one its dashboard tile wears. It tints the page's glow, buttons,
headings and panel edges: Government gold, Business emerald, markets and tools
cyan, writing and diplomacy violet, justice and defence rose, fun pink, Cyvazon
orange. Money pages keep the national blue.

| | |
|---|---|
| **Where** | `PAGE_MOOD` at the top of `static/app.js` sets `<html data-mood="…">` from the page's address; the colours are the **PAGE MOODS** block at the end of `static/theme.css`, with a light-mode version of each. |
| **What stays blue** | The header, the logo, the phone tab bar and the menu sheet, on every page. |
| **Left alone** | The dashboard, Wrapped, Cyvalend and the Registry, which have designs of their own. |
| **Adding a page** | Put its name in the right list in `PAGE_MOOD`. In a page's own CSS, write `rgba(var(--m),.3)` rather than a fixed blue, so it follows the page's colour. |

---

### 17. Three currencies: Cybucks, Cybits and Crystallines
Because of the war, **Pufferbucks and Aquilines were withdrawn in September 2026**.
Cyvathon now has exactly three currencies:

| Currency | Code | Worth |
|---|---|---|
| Cybuck (CB) | `cybucks` | 1 CB |
| Crystalline (CRY) | `crys` | 1 CB, from our ally Crystonia |
| Cybit (CBT) | `cybit` | 0.02 CB (50 = 1 CB) |

**Run `migration_crystallines.sql` in Supabase *before* deploying this code.** The
code reads a `crystallines` column that only the migration creates. The migration:
- turns every Pufferbuck and Aquiline into Crystallines at full value
  (1 PUFB = 1 CRY, 10 AQ = 1 CRY), for citizens, companies and the Treasury;
- gives every citizen who isn't banned 100 Crystallines from the Treasury, logged as a grant;
- re-prices market listings and Cyvapay links that were priced in the old money;
- writes what each holder had and got to `currency_conversion`, adds a line to every
  citizen's record and sends them a notification.

It's safe to run twice: nobody is converted or granted twice. Old Pufferbuck and
Aquiline entries stay in the ledgers as history, and Wrapped still values them.

---

### 16. The Cyvathon app
Cyvathon installs as an app on phones and computers. It's a Progressive Web App:
the app *is* the website, so it has every feature the moment it ships, with no
separate codebase and no store review.

| | |
|---|---|
| **Installing** | Android/Chrome: an "Install" offer appears a few seconds after signing in (dismissed offers stay quiet for 21 days), or use the browser menu. iPhone/iPad: Safari → Share → **Add to Home Screen**; the offer says exactly that. Computers: the round **Get the app** button in the bottom-right corner, opposite the music button. |
| **Icon & shortcuts** | `build_app_icons.py` redraws the site's mark at 192px and 512px, plus a full-bleed maskable version for Android. Long-pressing the icon offers Bank, Chat, Cyvazon and ID Card. |
| **On a phone** | The stacked menu becomes a bottom **tab bar**: Home, Bank, Chat, Alerts (with the unread count) and **More**. More opens a sheet with every page grouped in the dashboard's colours, plus light/dark mode, install and log out. The sheet is built from the real menu each time it opens, so it can't fall out of step. Theme music moves from the floating button to a music note in the top bar, so nothing floats over the chat box. Desktop is unchanged. |
| **Offline** | `sw.js` keeps only the app's own files (styles, scripts, icons) and an offline screen that reconnects by itself. It never stores balances, chat, votes or any other server data: those always go to the network, so money is always live. |

`/manifest.webmanifest` and `/sw.js` are served from the site root (a service
worker only covers pages at or below its own path). The manifest link and
iPhone tags are in every page's `<head>`, except the Cyvapay checkout, which
other websites send people to. The tab bar sits below every page pop-up, lifts
notification toasts above itself, and hides during Wrapped. As a safety net,
each top-level section of a page clips its own sideways overflow on phones
(`overflow-x: clip`), so nothing can widen the screen and push **More** off the
edge. Mobile Chrome ignores `overflow-x: hidden` on the page root for this, and
`clip`, unlike `hidden`, keeps the sticky header working. No migration is needed. Tests: `python tests/test_mobile_app.py`.

**Putting it in the Play Store:** paste `https://cyvathon.onrender.com` into
[PWABuilder](https://www.pwabuilder.com), download the Android package, and
upload it with a Google Play developer account (a one-time $25). PWABuilder
gives you an `assetlinks.json` file, which has to be served at
`/.well-known/assetlinks.json` so the app opens without a browser bar. The App
Store needs a paid Apple developer account ($99 a year) and a Mac.

### 15. Theme music
A floating music button in the bottom-left corner of every page with the nav
opens a player, so citizens can have soothing music on while they use the site.

- **Four built-in themes:** *Still Water*, *Night Sky*, *Rainfall* and *Ocean*.
  They're composed live in the browser with the Web Audio API, from chords,
  bells, filtered noise and a generated reverb, so there are no audio files to
  host and nothing to license. Each one plays indefinitely without repeating
  exactly.
- **Upload your own songs.** MP3, M4A, OGG, WAV, FLAC or WebM; up to 12 songs,
  40 MB each. They're stored in the browser's IndexedDB, tagged with the citizen
  who added them, so on a shared computer each citizen only sees their own.
  **They never leave the device.** Nothing is uploaded to Cyvathon, so there's
  no storage cost and no copyrighted music being redistributed. The trade-off is
  that a song added on one device isn't on another.
- **Plays throughout.** The site is separate pages, so sound can't literally
  continue through a page change. The player remembers the track, where a song
  had got to and the volume, and picks it back up on the next page with a short
  fade. Browsers only allow sound to start after a tap or key press. When a page
  isn't allowed yet, the button turns gold and the first tap anywhere resumes it.
- **Off by default.** Nothing plays until a citizen presses play.
- With Cyvathon open in two tabs, the music follows the tab being used. It also
  shows in the operating system's media controls where supported.

The player lives in `static/music.js` and is loaded by `renderNav` for signed-in
citizens, so it never appears on the login page, in jail, or on the Cyvapay
checkout that other websites use. The one server change is `blob:` in the CSP's
`media-src`, which is how a browser plays a file from its own storage. No
migration is needed.

### 14. Account security
What guards an account, beyond the hashed passwords, hardened session cookie,
CSP/HSTS headers and IP firewall the site already had.

| | |
|---|---|
| **Password rules** | At least 8 characters, not your username, not one of the commonly guessed ones. Enforced at signup and on every change. |
| **Change your password** | On your ID card under **Security**. Asks for the current one, and signs out every other device by raising the account's session stamp. |
| **Lockout** | Five wrong passwords lock the account for 15 minutes — on the **account**, not the network, so moving connection buys nothing. The citizen is notified. A correct password during a lock still waits. |
| **Login alerts** | A sign-in from an address the citizen hasn't used before sends a notification with the time and address. The last ten sign-ins, good and bad, are listed on their ID card. |
| **Payment PIN** | 4–6 digits, hashed like a password, asked for on bank transfers worth more than `pin_threshold` (500 CB, tunable in the admin panel). Five wrong PINs pause large payments for 15 minutes. Setting, changing or removing it always needs the password. |
| **Security desk** | `/admin` → Security Desk. Look up any citizen: sign-in history, sign out everywhere, issue a one-time password (they must then set their own before they can act), lock/unlock the account. |

The session stamp is what makes "sign out everywhere" instant: every signed-in
device carries the number it logged in with, and raising it makes every other
cookie worthless. A citizen owing a password change can read the site but every
write is refused until they set one.

Needs `migration_account_security.sql`. Until it's run, the new columns are
missing, those writes fail quietly and the site behaves exactly as before.
Tests: `python tests/test_security.py`.

### 13. Citizen Sites — the web, as built by citizens
A directory at `/sites` of the websites Cyvathon's citizens have made:
portfolios, blogs, projects, games, businesses, tools, art and music.

- **List up to five sites each.** Type the address with or without `https://`.
  Only secure https links are accepted, never plain http, localhost, IP addresses
  or links with a password in them. A site can only be listed once, however it's
  typed (`www.` and a trailing slash don't make it new). A live preview shows the
  card as you fill it in.
- **Find them.** Search names, descriptions, addresses and owners. Filter by
  category, and sort by **Top**, **New** or **Most visited**.
- **Star them.** One star per citizen per site, and never your own. Owners hear
  about their first star and every milestone after. The most-starred site of the
  last seven days is **Site of the Week**.
- **Visit them.** Links go through `/sites/go/<id>`, which counts the visit (not
  the owner's own) and only ever redirects to the stored https address.
- **Cyvapay badge.** An owner can say their site takes Cyvapay. The badge only
  shows if they also have a live Cyvapay link.
- **Keep it honest.** Any citizen can report a site as broken, unsuitable, a
  scam or something else. Three reports from different citizens take it down,
  and the owner and the President are told. The President's review queue on the
  same page shows the reasons, with **Restore** and **Remove**.
- **On your ID card.** Your sites appear on your profile, and on other
  citizens' when they have some.

Nothing is fetched from a listed site on the server, so the directory can't be
used to poke at anything on the inside of the network. Needs
`migration_citizen_sites.sql`; until it's run, the page says so rather than
erroring. Tests: `python tests/test_sites.py`.

### 12. Cyvathon Wrapped — every September
A Spotify-Wrapped-style story of each citizen's year, at `/wrapped`. Tap through
full-screen slides: money earned and spent (and where it came from), a ranking
among earners, the citizens you paid and messaged most, parcels and card trades,
votes and bills, the rest of what you did, a slot-machine reveal of your
**Cyvathon personality**, the Republic's year as a whole, and a summary card you
can save as a PNG or share straight from your phone.

| | |
|---|---|
| **Season** | All of September, India time. The dashboard shows a banner while it's open; the rest of the year `/wrapped` shows a countdown. The President can preview the year in progress at any time, marked as a preview. |
| **The year covered** | 1 September to 31 August, so everyone's numbers hold still while they're being shared. The first edition, **2026**, reaches back to the founding (26 May 2025). |
| **Where the numbers come from** | Only what the ledgers already record: `treasury_flows` for salary, courier wages, tax and the Treasury's payouts; `transactions` for money between citizens; the service tables for everything else. Casino, Cyvashield and Armoury payouts are written to both ledgers and are counted from `treasury_flows` only. Every currency is valued in Cybucks at the pegs. |
| **The ranking** | Shown only when it flatters: something earned, and in the top half. Foreign nations and banned accounts aren't ranked. |
| **Personalities** | The Tycoon, The Courier, The Collector, The Lawmaker, The Patriot, The Socialite, The Creator, The Good Neighbour — or The Quiet Citizen. Whichever activity dominates the year. |

No migration is needed: it reads existing tables and writes nothing. Reads are
paged by id, so Supabase's 1000-row limit can't quietly cut a year short, and
each citizen's deck is cached for ten minutes (the Republic-wide pass for thirty).
Tests: `python tests/test_wrapped.py`.

### 11. The Constitution & the Lawbook
Two documents, two pages, two PDFs — and a clear split between them:

| | Page | PDF | Says |
|---|---|---|---|
| **The Constitution** | `/constitution` | `/static/cyvathon-constitution.pdf` | How the Republic is governed: citizenship, the rights every citizen holds, what the President may and may not do, how ministers are elected, how a bill becomes an Act, the Courts, the Treasury, the public services, and how the Constitution is amended — by an Act of the Legislature, never by decree. |
| **The Lawbook** | `/rules` | `/static/cyvathon-lawbook.pdf` | What a citizen keeps to day to day: conduct, money, trade, debt, the services, elections and justice. The Rules page carries a twelve-line quick reference of it. |

Every clause describes something the site actually does, with the numbers taken
from `main.py` (the pegs, the grant, the fees, the salary table, the 365-day jail
ceiling, the four-applicant ministry election). Where the code gives a power to
the President the Constitution says so and then says what bounds it; Chairism is
the Republic's valued culture and is never required.

The Constitution's text lives in **one place** — `build_constitution.py` writes both
the PDF and `static/constitution.json`, and `/constitution` renders the JSON — so
the page and the PDF cannot drift apart. Rebuild after editing either document:

```bash
python build_constitution.py
python build_lawbook.py
```

Both need `reportlab`, which is deliberately not in `requirements.txt`: the running
site only serves the files.

## 🛠 Tech Stack

**Frontend:**  
- HTML  
- CSS  

**Backend:**  
- Python (Flask or similar micro-framework)

**Database:**  
- SQLite (local DB file)

**Deployment:**  
- Render.com

---

## 📁 Directory Structure

```
Cyvathon-Official-Website/
├── static         
  ├── chat.html         # chat website page
  ├── bank.html         # Banking system UI
├──index.html           # main website    
├── main.py             # Python backend
├── schema.sql          # base database schema
├── migration_*.sql     # incremental schema updates (run once each)
├── tests/              # in-memory test suites (python tests/test_cards.py)
├── cyvathon.db         # SQLite database (auto-created if missing)
├── requirements.txt    # Python dependencies
├── Procfile            # Render startup command
└── README.md           # Documentation
```

---

## 🚀 Run the Project Locally

### 1. Clone the repo
```bash
git clone https://github.com/PrathyayPGM-ALT/Cyvathon-Official-Website.git
cd Cyvathon-Official-Website
```

### 2. Create a virtual environment (optional)
```bash
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Set your secret key
```bash
# Windows (PowerShell)
$env:SECRET_KEY="your-secret-key"
# macOS/Linux
export SECRET_KEY="your-secret-key"
```

### 5. Run the app
```bash
python main.py
```

Visit:  
**http://localhost:5000**

---

## 🧩 Customizing the Project

### Website (`index.html`)
- Change text, layout, images  
- Add new pages like quests, ranks, badges  

### Bank UI (`bank.html`)
- Update UI  
- Add transaction logs, leaderboards, achievements  

### Backend (`main.py`)
- Add APIs (earn, missions, admin panel)  
- Improve security  
- Add anti-fake-user protection  

---

## 🌐 Deploying to Render

This repo is configured for Render using:
- `requirements.txt`
- `Procfile`

Steps:
1. Create a **Web Service**
2. Connect your GitHub repo
3. Auto-build & deploy  
4. Done — Render gives you a public URL

---

## 🧡 Credits

Cyvathon is a creative universe built around:

- Coding  
- World-building  
- Imagination  

This repo powers the official website + Cyvathon Chat + Cybucks economy.

Welcome to Cyvathon, citizen 👾  

