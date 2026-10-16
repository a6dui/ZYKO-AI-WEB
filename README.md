# ZYKO.AI — Unified AI Platform

Production-ready web platform for **ZYKO.AI** with 100+ neural network models, Cyber Emerald & Teal design, Google & Telegram authentication, and multi-gateway billing (**Stripe**, **Platega**, **CryptoPay**).

---

## 🚀 Quick Deploy to Zeabur

1. Go to your **[Zeabur Dashboard](https://dash.zeabur.com/)**.
2. Click **Create Project** (or open an existing one).
3. Click **Deploy New Service** -> **Git** -> Select **`a6dui/ZYKO-AI-WEB`**.
4. In the service settings under **Variables**, set the environment variables (see `.env.example`).
5. Under **Networking**, click **Generate Domain** or bind your custom domain (e.g. `zyko.ai`).
6. Zeabur automatically detects the `Dockerfile` and builds the service!

---

## ⚡ Features

- **Cyber Emerald & Teal Branding:** Deep obsidian background (`#050807`), atmospheric neon glows, and editorial typography (`Instrument Serif` + `Inter`).
- **Hero Video Background:** Autoplaying responsive video background with pause/play control.
- **Interactive 3D Perspective Slider:** Dynamic model showcases (Veo, Seedance 2.5, Nano Banana, Seedream, Kling) with floating 3D tilt cards.
- **Interactive Enhancer Slider:** Real-time draggable Before/After slider demonstrating 4K upscaling.
- **Authentication:**
  - **Google One Tap & Google Sign-In** via Google Identity Services (GSI).
  - **Telegram Login** via widget HMAC-SHA256 signature verification.
  - JWT session token stored in secure cookie & header.
- **Multi-Gateway Billing:**
  - **Stripe:** International Visa, MasterCard, Apple Pay, Google Pay with automatic webhook processing.
  - **Platega:** SBP (СБП), Mir, and Russian bank cards with instant callback confirmation.
  - **CryptoPay:** Crypto invoices in USDT, TON, BTC via @CryptoBot.
- **AI Neural Services:**
  - Prompt expansion via Google Gemini Flash / algorithmic prompt engineering.
  - Token balance deduction and generation tracking.

---

## 🛠 Local Development

```bash
# Clone the repository
git clone https://github.com/a6dui/ZYKO-AI-WEB.git
cd ZYKO-AI-WEB

# Create virtual environment and install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Copy environment variables
cp .env.example .env

# Run server
python3 server.py
```

The application will be accessible at `http://localhost:8080`.
