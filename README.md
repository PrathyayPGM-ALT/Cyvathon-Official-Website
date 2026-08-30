
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

