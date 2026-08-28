
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
- Pick the edition you actually pulled (Base through Black Edge, 100 Club,
  Limited Edition…) and add it to your **packet**
- Browse other citizens' packets and **wishlist** the cards you're missing —
  the owner is notified
- Offer cards out of your own packet for theirs; accepting swaps them over

Needs `migration_card_trading.sql` run once in Supabase. The player lookup uses
TheSportsDB's free API — set `SPORTSDB_API_KEY` to use your own key instead of
the shared test key.

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

