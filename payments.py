import os
import logging
import json
import urllib.request
import stripe
from database import create_transaction, complete_transaction

logger = logging.getLogger("zyko_payments")

# Stripe Configuration
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# Platega Configuration
PLATEGA_MERCHANT_ID = os.environ.get("PLATEGA_MERCHANT_ID", "")
PLATEGA_SECRET = os.environ.get("PLATEGA_SECRET", "")

# CryptoPay Configuration
CRYPTO_PAY_TOKEN = os.environ.get("CRYPTO_PAY_TOKEN", "")

# Plans Definitions (pricing & token bonuses)
PLANS = {
    "Basic": {
        "name": "Basic",
        "price_eur_monthly": 8.90,
        "price_eur_annual": 7.56,
        "price_rub": 890,
        "tokens": 3120,
        "desc": "Limited set of neural networks including Kling, Nano Banana"
    },
    "Pro": {
        "name": "Pro",
        "price_eur_monthly": 16.90,
        "price_eur_annual": 14.36,
        "price_rub": 1490,
        "tokens": 8160,
        "desc": "Full neural network package: Seedance 2.5, Kling, Nano Banana, Veo 3.1"
    },
    "VIP": {
        "name": "VIP",
        "price_eur_monthly": 39.90,
        "price_eur_annual": 33.90,
        "price_rub": 3490,
        "tokens": 20400,
        "desc": "Full package + Topaz 4K + 6x more requests"
    },
    "Elite": {
        "name": "Elite",
        "price_eur_monthly": 59.90,
        "price_eur_annual": 50.91,
        "price_rub": 5290,
        "tokens": 31200,
        "desc": "Unlimited generation in Veo 3.1 Lite, Runway, Topaz"
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

async def create_stripe_checkout(user_id: int, user_email: str, plan_name: str, cycle: str, origin_url: str) -> dict:
    plan = PLANS.get(plan_name, PLANS["Pro"])
    unit_price = plan["price_eur_annual"] if cycle == "annual" else plan["price_eur_monthly"]
    amount_cents = int(unit_price * 100)
    
    if not STRIPE_SECRET_KEY:
        # Mock mode if key not provided yet
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

async def create_platega_payment(user_id: int, plan_name: str, origin_url: str) -> dict:
    plan = PLANS.get(plan_name, PLANS["Pro"])
    amount_rub = plan["price_rub"]
    order_id = f"zk_plt_{user_id}_{int(os.times()[4])}"
    
    await create_transaction(user_id, "platega", order_id, amount_rub, "RUB", plan["tokens"], plan_name)
    
    if not PLATEGA_MERCHANT_ID or not PLATEGA_SECRET:
        # Fallback to simulated payment URL or bot link
        return {
            "url": f"https://t.me/voiceaichannel_bot?start=sub_{plan_name.lower()}",
            "order_id": order_id,
            "mock": True
        }

    try:
        url = "https://app.platega.com/api/v1/orders"
        data = {
            "merchant_id": PLATEGA_MERCHANT_ID,
            "order_id": order_id,
            "amount": amount_rub,
            "currency": "RUB",
            "description": f"ZYKO.AI: Тариф {plan_name} ({plan['tokens']} ZK)",
            "success_url": f"{origin_url}/?payment=success&order_id={order_id}",
            "fail_url": f"{origin_url}/?payment=cancelled"
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {PLATEGA_SECRET}"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            res = json.loads(resp.read().decode())
            return {"url": res.get("payment_url", f"{origin_url}/?payment=success&order_id={order_id}"), "order_id": order_id}
    except Exception as e:
        logger.error("Platega creation error: %s", e)
        return {"url": f"https://t.me/voiceaichannel_bot?start=sub_{plan_name.lower()}", "order_id": order_id}

async def create_cryptopay_payment(user_id: int, plan_name: str, cycle: str) -> dict:
    plan = PLANS.get(plan_name, PLANS["Pro"])
    amount_usd = plan["price_eur_annual"] if cycle == "annual" else plan["price_eur_monthly"]
    order_id = f"zk_crypto_{user_id}_{int(os.times()[4])}"
    
    await create_transaction(user_id, "crypto", order_id, amount_usd, "USD", plan["tokens"], plan_name)
    
    if not CRYPTO_PAY_TOKEN:
        return {
            "url": f"https://t.me/voiceaichannel_bot?start=crypto_{plan_name.lower()}",
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
    
    return {"url": f"https://t.me/voiceaichannel_bot?start=crypto_{plan_name.lower()}", "order_id": order_id}
