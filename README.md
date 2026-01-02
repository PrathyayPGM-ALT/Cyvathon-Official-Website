
# Cyvathon – Official Website & Banking System

> **Code. Conquer. Cause Creativity.**

Cyvathon is a playful micronation built around coding, creativity, and community.  
This repository contains the code for the **Cyvathon Official Website**, th **Cyvatho Chat** and the **Cybucks Banking System**, the virtual economy inside Cyvathon.

---

## 🌍 Live Websites

- **Main Cyvathon Website:**  
  https://cyvathon.onrender.com  

- **Cybucks Banking System (from this repo):**  
  https://cyvathon-official-website.onrender.com/bank
- **Cyvathon Chat (from this repo):**  
  https://cyvathon-official-website.onrender.com/chat  

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

