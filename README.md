
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
| Prime Minister | 1000 |
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

**On the card data.** The player lookup uses TheSportsDB's free API — set
`SPORTSDB_API_KEY` to use your own key instead of the shared test key. There is
no open Match Attax card API: Topps publishes none, and card databases like
TCDB sit behind bot protection that blocks server-to-server use. So the subset,
finish and pull-rate lists in `main.py` are reference data transcribed from the
published Match Attax checklist, and the card art is either the player photo
from TheSportsDB or the owner's own photo of the card in their hand. Update
`CARD_SUBSETS` / `CARD_EDITIONS` / `CARD_SERIES` when a new season ships.

---

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

