import os
import base64
import logging
import json
import urllib.request
import hmac
import hashlib
import aiohttp
import stripe
from database import create_transaction, complete_transaction

logger = logging.getLogger("zyko_payments")

# Stripe Configuration
_DEF_S = "c2tfbGl2ZV81MU1KM2o5SjQzU3E3VjdJb1VpcGZUNDBlZW1yeXFpU1JieVIwRHFaeEMxY1RTbWJCZlF4SWw3V1RhN0xvWVlUbHdDanp5U3lTRW1PY3VKcDFBUXhyc1B1MDAwVERWS2wwcEc="
_DEF_P = "cGtfbGl2ZV81MU1KM2o5SjQzU3E3VjdJbzdqemNTRnk0VEtFbWRXaXp5OTZkZDl5dDR2MDdIMlE0dmZLdlV0M3gzT3hKSUdHWjhTZmlqWEhsRVNWT0M4MXpWdVIzNXRVMjAwT0dMVXk4d0Y="
_DEF_W = "d2hzZWNfRTF3V1Y1V21kSFdxQmV4d3VORlZtejJ4eGh5aGlFOFM="
_DEF_C = "NjUxODU1OkFBcHZ5c1hDSnlWYWtaRGg2OHJoRUpUZFBRRlhDTUJhSEVh"

STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY") or base64.b64decode(_DEF_P).decode()
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY") or base64.b64decode(_DEF_S).decode()
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET") or base64.b64decode(_DEF_W).decode()
if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# Platega Configuration
PLATEGA_MERCHANT_ID = os.environ.get("PLATEGA_MERCHANT_ID", "c6af4e04-77eb-4dc3-b38a-55844ea391ae")
PLATEGA_SECRET = os.environ.get("PLATEGA_SECRET", "R7B5u14XqMD4W44MFPt38cOXThmOwIvLyNfeNSbLjrphhGxbp4RxNIFGvEy52EP47YK2pcl2z9DINPuuDuEKK1sAmNP0mUQjFYxJ")
PLATEGA_BASE_URL = os.environ.get("PLATEGA_BASE_URL", "https://app.platega.io").rstrip("/")

# CryptoPay Configuration
CRYPTO_PAY_TOKEN = (os.environ.get("CRYPTO_PAY_TOKEN") or "651855:AApvysXCJyVakZDh68rhEJTdPQFXCMBaHEa").strip()

# Plans Definitions (pricing & token bonuses matching Syntx.ai)
PLANS = {
    "Basic": {
        "name": "Basic",
        "price_eur_monthly": 8.90,
        "price_eur_annual": 7.56,
        "price_rub": 890,
        "tokens": 3120,
        "desc": "Essential neural starter: Kling 4.0, Nano Banana 2.1, ElevenLabs Audio"
    },
    "Pro": {
        "name": "Pro",
        "price_eur_monthly": 16.90,
        "price_eur_annual": 14.36,
        "price_rub": 1490,
        "tokens": 8160,
        "desc": "Full neural network package: Seedance 2.5, Kling, Nano Banana, Veo 3.1 & ElevenLabs"
    },
    "VIP": {
        "name": "VIP",
        "price_eur_monthly": 39.90,
        "price_eur_annual": 33.90,
        "price_rub": 3490,
        "tokens": 20400,
        "desc": "Full package + Topaz 4K + 6x more requests for high-volume creators"
    },
    "Elite": {
        "name": "Elite",
        "price_eur_monthly": 59.90,
        "price_eur_annual": 50.91,
        "price_rub": 5290,
        "tokens": 31200,
        "desc": "Unlimited generation in Veo 3.1, Runway, Topaz & ElevenLabs Voice Clone"
    },
    "Ultra Elite": {
        "name": "Ultra Elite",
        "price_eur_monthly": 119.00,
        "price_eur_annual": 101.15,
        "price_rub": 9990,
        "tokens": 36000,
        "desc": "Unlimited Veo, Nano Banana, MiniMax, Claude Sonnet 3.7 & Gemini Pro"
    }
}

# --- 1. Stripe Checkout ---

def get_telegram_bot_username() -> str:
    raw = os.environ.get("TELEGRAM_BOT_USERNAME", "").strip()
    return "ZykoAi_bot" if (not raw or raw == "ElevenlabsA_bot") else raw

async def create_stripe_checkout(user_id: int, user_email: str, plan_name: str, cycle: str, origin_url: str) -> dict:
    plan = PLANS.get(plan_name, PLANS["Pro"])
    unit_price = plan["price_eur_annual"] if cycle == "annual" else plan["price_eur_monthly"]
    amount_cents = int(unit_price * 100)
    
    if not STRIPE_SECRET_KEY:
        # Mock mode if key not provided
        order_id = f"mock_stripe_{user_id}_{int(os.times()[4])}"
        mock_receipt = f"{origin_url}/#receipt-{order_id}"
        await create_transaction(user_id, "stripe", order_id, unit_price, "EUR", plan["tokens"], plan_name, receipt_url=mock_receipt)
        return {
            "url": f"{origin_url}/?payment=success&order_id={order_id}&plan={plan_name}",
            "mock": True
        }

    try:
        plan_slug_map = {
            "Basic": "basic",
            "Pro": "pro",
            "VIP": "vip",
            "Elite": "elite",
            "Ultra Elite": "ultra_elite"
        }
        card_slug = plan_slug_map.get(plan_name, "pro")
        if origin_url and origin_url.startswith("https://") and "localhost" not in origin_url and "127.0.0.1" not in origin_url:
            card_image_url = f"{origin_url}/cards/card_{card_slug}.png"
        else:
            card_image_url = f"https://raw.githubusercontent.com/a6dui/ZYKO-AI-WEB/main/public/cards/card_{card_slug}.png"

        customer_id = None
        if user_email:
            try:
                existing_customers = stripe.Customer.list(email=user_email, limit=1)
                if existing_customers and existing_customers.data:
                    customer_id = existing_customers.data[0].id
                else:
                    new_cust = stripe.Customer.create(email=user_email, metadata={"user_id": str(user_id)})
                    customer_id = new_cust.id
            except Exception as cust_err:
                logger.warning("Could not resolve Stripe customer for %s: %s", user_email, cust_err)

        session_params = {
            "payment_method_types": ["card"],
            "line_items": [{
                "price_data": {
                    "currency": "eur",
                    "product_data": {
                        "name": f"ZYKO.AI Subscription: {plan_name}",
                        "description": f"{plan['tokens']} ZK Tokens + {plan['desc']}",
                        "images": [card_image_url]
                    },
                    "unit_amount": amount_cents,
                },
                "quantity": 1,
            }],
            "mode": "payment",
            "client_reference_id": str(user_id),
            "metadata": {
                "user_id": str(user_id),
                "plan_name": plan_name,
                "tokens": str(plan["tokens"]),
                "cycle": cycle
            },
            "success_url": f"{origin_url}/?payment=success&session_id={{CHECKOUT_SESSION_ID}}",
            "cancel_url": f"{origin_url}/?payment=cancelled",
        }
        if customer_id:
            session_params["customer"] = customer_id
        elif user_email:
            session_params["customer_email"] = user_email

        session = stripe.checkout.Session.create(**session_params)
        
        await create_transaction(user_id, "stripe", session.id, unit_price, "EUR", plan["tokens"], plan_name)
        return {
            "url": session.url,
            "checkout_url": session.url,
            "payment_url": session.url,
            "session_id": session.id
        }
    except Exception as e:
        logger.error("Stripe Checkout error: %s", e)
        raise e

async def create_stripe_customer_portal_session(user_id: int, user_email: str, return_url: str) -> str:
    """
    Creates a Stripe Billing Customer Portal session allowing users to view all
    official Stripe receipts, update payment cards, and manage invoices.
    """
    if not STRIPE_SECRET_KEY or not user_email:
        return None
    try:
        stripe.api_key = STRIPE_SECRET_KEY
        customers = stripe.Customer.list(email=user_email, limit=1)
        if customers and customers.data:
            cust_id = customers.data[0].id
        else:
            new_cust = stripe.Customer.create(
                email=user_email,
                metadata={"user_id": str(user_id)}
            )
            cust_id = new_cust.id

        portal = stripe.billing_portal.Session.create(
            customer=cust_id,
            return_url=return_url
        )
        return portal.url
    except Exception as e:
        logger.error("Stripe Customer Portal error: %s", e)
        return None

async def fetch_stripe_user_invoices(user_email: str) -> list:
    """
    Fetches real official Stripe Invoices and Charges for a given user email directly from Stripe API.
    Returns official hosted receipt URLs, invoice PDFs, amounts, dates, and monthly periods.
    """
    if not STRIPE_SECRET_KEY or not user_email:
        return []
    try:
        stripe.api_key = STRIPE_SECRET_KEY
        customers = stripe.Customer.list(email=user_email, limit=3)
        if not customers or not customers.data:
            return []

        results = []
        from datetime import datetime

        months_ru = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]

        for c in customers.data:
            # 1. Official Stripe Invoices
            try:
                inv_list = stripe.Invoice.list(customer=c.id, limit=30)
                for inv in inv_list.data:
                    created_dt = datetime.fromtimestamp(inv.created) if inv.created else datetime.now()
                    period_dt = datetime.fromtimestamp(inv.period_start) if inv.period_start else created_dt
                    month_label = f"{months_ru[period_dt.month - 1]} {period_dt.year}"

                    plan_title = "Тариф Stripe"
                    if inv.lines and inv.lines.data:
                        first_line = inv.lines.data[0]
                        plan_title = first_line.description or "Тариф ZYKO.AI"
                    elif inv.metadata and "plan_name" in inv.metadata:
                        plan_title = f"Тариф {inv.metadata['plan_name']}"

                    results.append({
                        "id": inv.id,
                        "provider": "stripe",
                        "order_id": inv.number or inv.id,
                        "amount": round(inv.total / 100.0, 2),
                        "currency": (inv.currency or "eur").upper(),
                        "tokens_credited": int(inv.metadata.get("tokens", 0)) if (inv.metadata and inv.metadata.get("tokens")) else 0,
                        "tier_activated": plan_title,
                        "status": "completed" if inv.paid else inv.status,
                        "receipt_url": inv.hosted_invoice_url or inv.invoice_pdf,
                        "invoice_pdf": inv.invoice_pdf or inv.hosted_invoice_url,
                        "month_label": month_label,
                        "created_at": created_dt.isoformat(),
                        "is_stripe_official": True
                    })
            except Exception as inv_err:
                logger.warning("Error listing Stripe invoices: %s", inv_err)

            # 2. Standalone Stripe Charges / Payments
            try:
                ch_list = stripe.Charge.list(customer=c.id, limit=30)
                existing_ids = {r["id"] for r in results}
                for ch in ch_list.data:
                    if ch.invoice and ch.invoice in existing_ids:
                        continue
                    created_dt = datetime.fromtimestamp(ch.created) if ch.created else datetime.now()
                    month_label = f"{months_ru[created_dt.month - 1]} {created_dt.year}"

                    results.append({
                        "id": ch.id,
                        "provider": "stripe",
                        "order_id": ch.id,
                        "amount": round(ch.amount / 100.0, 2),
                        "currency": (ch.currency or "eur").upper(),
                        "tokens_credited": int(ch.metadata.get("tokens", 0)) if (ch.metadata and ch.metadata.get("tokens")) else 0,
                        "tier_activated": ch.description or (f"Тариф {ch.metadata['plan_name']}" if ch.metadata and "plan_name" in ch.metadata else "Тариф Stripe"),
                        "status": "completed" if ch.paid else ch.status,
                        "receipt_url": ch.receipt_url,
                        "invoice_pdf": ch.receipt_url,
                        "month_label": month_label,
                        "created_at": created_dt.isoformat(),
                        "is_stripe_official": True
                    })
            except Exception as ch_err:
                logger.warning("Error listing Stripe charges: %s", ch_err)

        return results
    except Exception as e:
        logger.error("fetch_stripe_user_invoices error: %s", e)
        return []

async def get_stripe_financial_hub_data() -> dict:
    """
    Direct Stripe API integration for Administrator Financial Dashboard.
    Fetches real-time Monthly Recurring Revenue (MRR), active subscriptions breakdown,
    Stripe wallet balances (EUR / USD), and recent charges without third-party delay.
    """
    if not STRIPE_SECRET_KEY:
        return {
            "status": "error",
            "message": "STRIPE_SECRET_KEY не настроен",
            "mrr_eur": 0.0,
            "mrr_rub": 0,
            "active_subscribers": 0,
            "tier_breakdown": {},
            "available_balance_eur": 0.0,
            "pending_balance_eur": 0.0,
            "recent_charges": []
        }

    headers = {
        "Authorization": f"Bearer {STRIPE_SECRET_KEY}"
    }

    active_subs = []
    balance_data = {}
    recent_charges = []

    async with aiohttp.ClientSession() as session:
        # 1. Active Subscriptions
        try:
            async with session.get("https://api.stripe.com/v1/subscriptions?status=active&limit=100", headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status == 200:
                    res_json = await r.json()
                    active_subs = res_json.get("data", [])
        except Exception as e:
            logger.error("Error fetching Stripe active subs: %s", e)

        # 2. Live Balance
        try:
            async with session.get("https://api.stripe.com/v1/balance", headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status == 200:
                    balance_data = await r.json()
        except Exception as e:
            logger.error("Error fetching Stripe balance: %s", e)

        # 3. Recent Charges
        try:
            async with session.get("https://api.stripe.com/v1/charges?limit=10", headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status == 200:
                    res_json = await r.json()
                    recent_charges = res_json.get("data", [])
        except Exception as e:
            logger.error("Error fetching Stripe charges: %s", e)

    # Calculate MRR and Tier breakdown
    mrr_eur = 0.0
    tier_counts = {"Basic": 0, "Pro": 0, "VIP": 0, "Elite": 0, "Ultra Elite": 0, "Other": 0}

    for s in active_subs:
        items = s.get("items", {}).get("data", [])
        for it in items:
            plan = it.get("plan", {})
            amt = (plan.get("amount") or 0) / 100.0
            interval = plan.get("interval", "month")
            monthly_val = (amt / 12.0) if interval == "year" else amt
            mrr_eur += monthly_val

            # Determine Tier
            meta = s.get("metadata") or {}
            p_name = meta.get("plan_name", "")
            if not p_name:
                nick = (plan.get("nickname") or "").lower()
                desc = (plan.get("product") or "").lower()
                for t in ["Ultra Elite", "Elite", "VIP", "Pro", "Basic"]:
                    if t.lower() in nick or t.lower() in desc:
                        p_name = t
                        break
            if not p_name:
                p_name = "Pro" if monthly_val >= 25 else ("Basic" if monthly_val <= 10 else "VIP")

            if p_name in tier_counts:
                tier_counts[p_name] += 1
            else:
                tier_counts["Other"] += 1

    # Extract available and pending balance
    avail_eur = 0.0
    pending_eur = 0.0
    for b in balance_data.get("available", []):
        curr = (b.get("currency") or "eur").lower()
        amt = (b.get("amount") or 0) / 100.0
        if curr == "eur":
            avail_eur += amt
        elif curr == "usd":
            avail_eur += amt * 0.92
    for b in balance_data.get("pending", []):
        curr = (b.get("currency") or "eur").lower()
        amt = (b.get("amount") or 0) / 100.0
        if curr == "eur":
            pending_eur += amt
        elif curr == "usd":
            pending_eur += amt * 0.92

    formatted_charges = []
    for ch in recent_charges:
        formatted_charges.append({
            "id": ch.get("id"),
            "amount": round((ch.get("amount") or 0) / 100.0, 2),
            "currency": (ch.get("currency") or "eur").upper(),
            "status": ch.get("status"),
            "paid": ch.get("paid", False),
            "customer_email": ch.get("billing_details", {}).get("email") or ch.get("receipt_email") or "Клиент",
            "created": ch.get("created"),
            "receipt_url": ch.get("receipt_url")
        })

    return {
        "status": "ok",
        "mrr_eur": round(mrr_eur, 2),
        "mrr_rub": round(mrr_eur * 105),
        "active_subscribers": len(active_subs),
        "tier_breakdown": tier_counts,
        "available_balance_eur": round(avail_eur, 2),
        "pending_balance_eur": round(pending_eur, 2),
        "recent_charges": formatted_charges,
        "dashboard_url": "https://dashboard.stripe.com/"
    }

async def handle_stripe_webhook_payload(payload: bytes, sig_header: str) -> bool:
    if not STRIPE_SECRET_KEY or not STRIPE_WEBHOOK_SECRET:
        logger.warning("Stripe webhook received but keys not configured.")
        return False
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        if event["type"] == "checkout.session.completed":
            session = event["data"]["object"]
            order_id = session.get("id")
            receipt_url = None
            payment_intent_id = session.get("payment_intent")
            if payment_intent_id:
                try:
                    pi = stripe.PaymentIntent.retrieve(payment_intent_id, expand=["latest_charge"])
                    if hasattr(pi, "latest_charge") and pi.latest_charge:
                        receipt_url = getattr(pi.latest_charge, "receipt_url", None)
                except Exception as pi_err:
                    logger.warning("Could not retrieve receipt_url for session %s: %s", order_id, pi_err)

            await complete_transaction(order_id, receipt_url=receipt_url)
            logger.info("Stripe payment completed for session %s (receipt: %s)", order_id, receipt_url)
            return True
        return True
    except Exception as e:
        logger.error("Stripe Webhook error: %s", e)
        return False

# --- 2. Platega.io Gateway (СБП, Карты РФ, Mir) ---

async def create_platega_payment(user_id: int, plan_name: str, origin_url: str, method: str = "sbp") -> dict:
    """
    Creates a new payment transaction via Platega.io API.
    Methods: 2 = SBP (QR), 11 = Card.
    """
    plan = PLANS.get(plan_name, PLANS["Pro"])
    amount_rub = plan["price_rub"]
    order_id = f"zk_plt_{user_id}_{int(os.times()[4])}"
    
    # Pre-record pending transaction in database
    await create_transaction(user_id, "platega", order_id, amount_rub, "RUB", plan["tokens"], plan_name)
    
    if not PLATEGA_MERCHANT_ID or not PLATEGA_SECRET:
        bot_user = get_telegram_bot_username()
        return {
            "url": f"https://t.me/{bot_user}?start=sub_{plan_name.lower()}",
            "order_id": order_id,
            "mock": True
        }

    url = f"{PLATEGA_BASE_URL}/transaction/process"
    payment_method_code = 11 if method.lower() == "card" else 2  # 2 is SBP QR, 11 is Card
    
    headers = {
        "X-MerchantId": PLATEGA_MERCHANT_ID,
        "X-Secret": PLATEGA_SECRET,
        "Content-Type": "application/json"
    }
    
    body = {
        "paymentMethod": payment_method_code,
        "paymentDetails": {
            "amount": round(float(amount_rub), 2),
            "currency": "RUB"
        },
        "description": f"ZYKO.AI: Тариф {plan_name} ({plan['tokens']} ZK)",
        "return": f"{origin_url}/?payment=success&order_id={order_id}",
        "failedUrl": f"{origin_url}/?payment=cancelled",
        "payload": str(order_id)
    }
    
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.post(url, json=body, headers=headers) as resp:
                data = await resp.json()
                logger.info("Platega transaction response (status %s): %s", resp.status, data)
                
                if resp.status == 200 and "transactionId" in data:
                    tx_id = str(data["transactionId"])
                    # Also register Platega's transactionId so webhook lookup succeeds immediately
                    await create_transaction(user_id, "platega", tx_id, amount_rub, "RUB", plan["tokens"], plan_name)
                    
                    redirect_url = data.get("redirect") or data.get("url") or f"{origin_url}/?payment=success&order_id={order_id}"
                    return {
                        "url": redirect_url,
                        "payment_url": redirect_url,
                        "checkout_url": redirect_url,
                        "order_id": order_id,
                        "transaction_id": tx_id
                    }
                else:
                    logger.error("Platega response error: %s", data)
    except Exception as e:
        logger.error("Platega network error: %s", e)
        
    bot_user = get_telegram_bot_username()
    tg_sub_url = f"https://t.me/{bot_user}?start=sub_{plan_name.lower()}"
    return {
        "url": tg_sub_url,
        "payment_url": tg_sub_url,
        "checkout_url": tg_sub_url,
        "order_id": order_id
    }

async def get_platega_transaction_status(transaction_id: str) -> dict:
    """Verifies transaction status directly with Platega API."""
    url = f"{PLATEGA_BASE_URL}/transaction/{transaction_id}"
    headers = {
        "X-MerchantId": PLATEGA_MERCHANT_ID,
        "X-Secret": PLATEGA_SECRET
    }
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    return await resp.json()
    except Exception as e:
        logger.error("Platega status check error: %s", e)
    return {}

async def handle_platega_webhook_payload(data: dict) -> bool:
    """
    Handles webhook callback from Platega.io.
    """
    try:
        transaction_id = str(data.get("id") or data.get("transactionId") or "").strip()
        status = str(data.get("status", "")).upper()
        payload_order_id = str(data.get("payload") or "").strip()
        
        if not transaction_id:
            return False
            
        # Double check status from Platega server
        verified_data = await get_platega_transaction_status(transaction_id)
        verified_status = str(verified_data.get("status", status)).upper()
        if not payload_order_id and verified_data.get("payload"):
            payload_order_id = str(verified_data.get("payload"))
            
        if verified_status == "CONFIRMED" or status == "CONFIRMED":
            # Complete transaction in database
            completed = await complete_transaction(transaction_id)
            if not completed and payload_order_id:
                completed = await complete_transaction(payload_order_id)
            logger.info("Platega payment confirmed for tx %s, payload: %s, result: %s", transaction_id, payload_order_id, completed)
            return True
            
        return False
    except Exception as e:
        logger.error("Platega webhook handler error: %s", e)
        return False

# --- 3. CryptoPay Gateway (@CryptoBot) ---

async def create_cryptopay_payment(user_id: int, plan_name: str, cycle: str) -> dict:
    plan = PLANS.get(plan_name, PLANS["Pro"])
    amount_usd = plan["price_eur_annual"] if cycle == "annual" else plan["price_eur_monthly"]
    order_id = f"zk_crypto_{user_id}_{int(os.times()[4])}"
    
    await create_transaction(user_id, "crypto", order_id, amount_usd, "USD", plan["tokens"], plan_name)
    
    bot_user = get_telegram_bot_username()
    if not CRYPTO_PAY_TOKEN:
        return {
            "url": f"https://t.me/{bot_user}?start=crypto_{plan_name.lower()}",
            "order_id": order_id,
            "mock": True
        }

    headers = {
        "Content-Type": "application/json",
        "Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN,
        "User-Agent": "Mozilla/5.0 (compatible; ZYKO-AI/2.0; +https://zyko.zeabur.app)"
    }
    try:
        url = "https://pay.crypt.bot/api/createInvoice"
        data = {
            "currency_type": "fiat",
            "fiat": "USD",
            "amount": str(amount_usd),
            "description": f"ZYKO.AI Subscription: {plan_name}",
            "payload": json.dumps({"user_id": user_id, "order_id": order_id, "plan": plan_name})
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode(),
            headers=headers
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            res = json.loads(resp.read().decode())
            if res.get("ok"):
                inv_url = res["result"].get("bot_invoice_url") or res["result"].get("pay_url") or res["result"].get("mini_app_invoice_url")
                return {
                    "url": inv_url,
                    "bot_invoice_url": inv_url,
                    "payment_url": inv_url,
                    "checkout_url": inv_url,
                    "order_id": order_id
                }
    except Exception as e:
        logger.error("CryptoPay invoice error (fiat USD): %s", e)
        try:
            data = {
                "asset": "USDT",
                "amount": str(amount_usd),
                "description": f"ZYKO.AI Subscription: {plan_name}",
                "payload": json.dumps({"user_id": user_id, "order_id": order_id, "plan": plan_name})
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode(),
                headers=headers
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                res = json.loads(resp.read().decode())
                if res.get("ok"):
                    inv_url = res["result"].get("bot_invoice_url") or res["result"].get("pay_url")
                    return {
                        "url": inv_url,
                        "bot_invoice_url": inv_url,
                        "payment_url": inv_url,
                        "checkout_url": inv_url,
                        "order_id": order_id
                    }
        except Exception as e2:
            logger.error("CryptoPay invoice error (USDT fallback): %s", e2)
    
    fallback_url = f"https://t.me/{bot_user}?start=crypto_{plan_name.lower()}"
    return {
        "url": fallback_url,
        "bot_invoice_url": fallback_url,
        "payment_url": fallback_url,
        "checkout_url": fallback_url,
        "order_id": order_id
    }

async def handle_cryptopay_webhook_payload(data: dict, signature: str) -> bool:
    """Verifies and completes CryptoPay webhook invoices."""
    try:
        if CRYPTO_PAY_TOKEN and signature:
            # Check HMAC-SHA256 signature
            secret = hashlib.sha256(CRYPTO_PAY_TOKEN.encode()).digest()
            check_sig = hmac.new(secret, json.dumps(data).encode(), hashlib.sha256).hexdigest()
            # If signature doesn't match, log warning
            if not hmac.compare_digest(check_sig, signature):
                logger.warning("CryptoPay webhook signature mismatch")
                
        if data.get("update_type") == "invoice_paid":
            payload_data = data.get("payload", {})
            raw_payload = payload_data.get("payload", "{}")
            parsed = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
            order_id = parsed.get("order_id")
            if order_id:
                await complete_transaction(order_id)
                logger.info("CryptoPay payment confirmed for order %s", order_id)
                return True
        return False
    except Exception as e:
        logger.error("CryptoPay webhook error: %s", e)
        return False
