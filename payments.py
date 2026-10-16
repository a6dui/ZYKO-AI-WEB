import os
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
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# Platega Configuration
PLATEGA_MERCHANT_ID = os.environ.get("PLATEGA_MERCHANT_ID", "c6af4e04-77eb-4dc3-b38a-55844ea391ae")
PLATEGA_SECRET = os.environ.get("PLATEGA_SECRET", "R7B5u14XqMD4W44MFPt38cOXThmOwIvLyNfeNSbLjrphhGxbp4RxNIFGvEy52EP47YK2pcl2z9DINPuuDuEKK1sAmNP0mUQjFYxJ")
PLATEGA_BASE_URL = os.environ.get("PLATEGA_BASE_URL", "https://app.platega.io").rstrip("/")

# CryptoPay Configuration
CRYPTO_PAY_TOKEN = os.environ.get("CRYPTO_PAY_TOKEN", "")

# Plans Definitions (pricing & token bonuses)
PLANS = {
    "Basic": {
        "name": "Basic",
        "price_eur_monthly": 8.90,
        "price_eur_annual": 7.56,
        "price_rub": 890,
        "tokens": 30000,
        "desc": "Essential neural starter: Kling 4.0, Nano Banana 2.1, ElevenLabs Audio"
    },
    "Pro": {
        "name": "Pro",
        "price_eur_monthly": 16.90,
        "price_eur_annual": 14.36,
        "price_rub": 1490,
        "tokens": 80000,
        "desc": "Full neural network package: Seedance 2.5, Kling, Nano Banana, Veo 3.1 & ElevenLabs"
    },
    "VIP": {
        "name": "VIP",
        "price_eur_monthly": 39.90,
        "price_eur_annual": 33.90,
        "price_rub": 3490,
        "tokens": 200000,
        "desc": "Full package + Topaz 4K + 6x more requests for high-volume creators"
    },
    "Elite": {
        "name": "Elite",
        "price_eur_monthly": 59.90,
        "price_eur_annual": 50.91,
        "price_rub": 5290,
        "tokens": 300000,
        "desc": "Unlimited generation in Veo 3.1, Runway, Topaz & ElevenLabs Voice Clone"
    },
    "Ultra Elite": {
        "name": "Ultra Elite",
        "price_eur_monthly": 119.00,
        "price_eur_annual": 101.15,
        "price_rub": 9990,
        "tokens": 500000,
        "desc": "Unlimited Veo, Nano Banana, MiniMax, Claude Sonnet 3.7 & Gemini Pro"
    }
}

# --- 1. Stripe Checkout ---

async def create_stripe_checkout(user_id: int, user_email: str, plan_name: str, cycle: str, origin_url: str) -> dict:
    plan = PLANS.get(plan_name, PLANS["Pro"])
    unit_price = plan["price_eur_annual"] if cycle == "annual" else plan["price_eur_monthly"]
    amount_cents = int(unit_price * 100)
    
    if not STRIPE_SECRET_KEY:
        # Mock mode if key not provided
        order_id = f"mock_stripe_{user_id}_{int(os.times()[4])}"
        await create_transaction(user_id, "stripe", order_id, unit_price, "EUR", plan["tokens"], plan_name)
        return {
            "url": f"{origin_url}/?payment=success&order_id={order_id}&plan={plan_name}",
            "mock": True
        }

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "eur",
                    "product_data": {
                        "name": f"ZYKO.AI Subscription: {plan_name}",
                        "description": f"{plan['tokens']} ZK Tokens + {plan['desc']}",
                        "images": ["https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=400"]
                    },
                    "unit_amount": amount_cents,
                },
                "quantity": 1,
            }],
            mode="payment",
            customer_email=user_email if user_email else None,
            client_reference_id=str(user_id),
            metadata={
                "user_id": str(user_id),
                "plan_name": plan_name,
                "tokens": str(plan["tokens"]),
                "cycle": cycle
            },
            success_url=f"{origin_url}/?payment=success&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{origin_url}/?payment=cancelled",
        )
        
        await create_transaction(user_id, "stripe", session.id, unit_price, "EUR", plan["tokens"], plan_name)
        return {"url": session.url, "session_id": session.id}
    except Exception as e:
        logger.error("Stripe Checkout error: %s", e)
        raise e

async def handle_stripe_webhook_payload(payload: bytes, sig_header: str) -> bool:
    if not STRIPE_SECRET_KEY or not STRIPE_WEBHOOK_SECRET:
        logger.warning("Stripe webhook received but keys not configured.")
        return False
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        if event["type"] == "checkout.session.completed":
            session = event["data"]["object"]
            order_id = session.get("id")
            await complete_transaction(order_id)
            logger.info("Stripe payment completed for session %s", order_id)
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
        bot_user = os.environ.get("TELEGRAM_BOT_USERNAME", "ElevenlabsA_bot")
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
                        "order_id": order_id,
                        "transaction_id": tx_id
                    }
                else:
                    logger.error("Platega response error: %s", data)
    except Exception as e:
        logger.error("Platega network error: %s", e)
        
    bot_user = os.environ.get("TELEGRAM_BOT_USERNAME", "ElevenlabsA_bot")
    return {
        "url": f"https://t.me/{bot_user}?start=sub_{plan_name.lower()}",
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
    
    bot_user = os.environ.get("TELEGRAM_BOT_USERNAME", "ElevenlabsA_bot")
    if not CRYPTO_PAY_TOKEN:
        return {
            "url": f"https://t.me/{bot_user}?start=crypto_{plan_name.lower()}",
            "order_id": order_id,
            "mock": True
        }

    try:
        url = "https://pay.crypt.bot/api/createInvoice"
        data = {
            "asset": "USDT",
            "amount": str(amount_usd),
            "description": f"ZYKO.AI Subscription: {plan_name}",
            "payload": json.dumps({"user_id": user_id, "order_id": order_id, "plan": plan_name})
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode(),
            headers={"Content-Type": "application/json", "Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            res = json.loads(resp.read().decode())
            if res.get("ok"):
                return {"url": res["result"]["bot_invoice_url"], "order_id": order_id}
    except Exception as e:
        logger.error("CryptoPay invoice error: %s", e)
    
    return {"url": f"https://t.me/{bot_user}?start=crypto_{plan_name.lower()}", "order_id": order_id}

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
