import os
import sys
import json
import logging
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()

from database import init_db, get_user_by_id, get_or_create_google_user, get_or_create_telegram_user
from auth import create_access_token, decode_access_token, verify_google_id_token, verify_telegram_auth, GOOGLE_CLIENT_ID
from payments import create_stripe_checkout, handle_stripe_webhook_payload, create_platega_payment, create_cryptopay_payment
from ai_service import generate_prompt_expansion

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("zyko_server")

PORT = int(os.environ.get("PORT", 8080))
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
TELEGRAM_BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME", "voiceaichannel_bot")

async def get_current_user(request: web.Request):
    auth_header = request.headers.get("Authorization", "")
    token = ""
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
    elif "zyko_token" in request.cookies:
        token = request.cookies["zyko_token"]
        
    if not token:
        return None
        
    user_id = decode_access_token(token)
    if not user_id:
        return None
    return await get_user_by_id(user_id)

# --- Routes ---

async def handle_health(request: web.Request):
    return web.json_response({"status": "ok", "app": "ZYKO.AI", "version": "2.0.0"})

async def handle_public_config(request: web.Request):
    return web.json_response({
        "google_client_id": GOOGLE_CLIENT_ID,
        "bot_username": TELEGRAM_BOT_USERNAME,
        "stripe_publishable_key": STRIPE_PUBLISHABLE_KEY
    })

async def handle_google_auth(request: web.Request):
    try:
        data = await request.json()
        credential = data.get("credential")
        if not credential:
            return web.json_response({"error": "Missing credential"}, status=400)
            
        g_info = verify_google_id_token(credential)
        user = await get_or_create_google_user(
            google_id=g_info["google_id"],
            email=g_info["email"],
            name=g_info["name"],
            avatar=g_info["avatar"]
        )
        token = create_access_token(user["id"])
        
        resp = web.json_response({"token": token, "user": user})
        resp.set_cookie("zyko_token", token, max_age=30*86400, httponly=True, samesite="Lax")
        return resp
    except Exception as e:
        logger.error("Google Auth error: %s", e)
        return web.json_response({"error": str(e)}, status=400)

async def handle_telegram_auth(request: web.Request):
    try:
        data = await request.json()
        if not verify_telegram_auth(data):
            return web.json_response({"error": "Invalid Telegram signature"}, status=403)
            
        user = await get_or_create_telegram_user(
            telegram_id=int(data["id"]),
            username=data.get("username", ""),
            first_name=data.get("first_name", ""),
            photo_url=data.get("photo_url", "")
        )
        token = create_access_token(user["id"])
        
        resp = web.json_response({"token": token, "user": user})
        resp.set_cookie("zyko_token", token, max_age=30*86400, httponly=True, samesite="Lax")
        return resp
    except Exception as e:
        logger.error("Telegram Auth error: %s", e)
        return web.json_response({"error": str(e)}, status=400)

async def handle_me(request: web.Request):
    user = await get_current_user(request)
    if not user:
        return web.json_response({"authenticated": False})
    return web.json_response({"authenticated": True, "user": user})

async def handle_logout(request: web.Request):
    resp = web.json_response({"success": True})
    resp.del_cookie("zyko_token")
    return resp

# --- Payments ---

async def handle_stripe_create_checkout(request: web.Request):
    try:
        data = await request.json()
        plan_name = data.get("plan", "Pro")
        cycle = data.get("cycle", "annual")
        
        user = await get_current_user(request)
        user_id = user["id"] if user else 0
        user_email = user["email"] if user else ""
        
        origin = f"{request.scheme}://{request.host}"
        res = await create_stripe_checkout(user_id, user_email, plan_name, cycle, origin)
        return web.json_response(res)
    except Exception as e:
        logger.error("Stripe create checkout error: %s", e)
        return web.json_response({"error": str(e)}, status=500)

async def handle_stripe_webhook(request: web.Request):
    payload = await request.read()
    sig_header = request.headers.get("Stripe-Signature", "")
    success = await handle_stripe_webhook_payload(payload, sig_header)
    if success:
        return web.Response(status=200, text="OK")
    return web.Response(status=400, text="Webhook verification failed")

async def handle_platega_create(request: web.Request):
    try:
        data = await request.json()
        plan_name = data.get("plan", "Pro")
        user = await get_current_user(request)
        user_id = user["id"] if user else 0
        origin = f"{request.scheme}://{request.host}"
        
        res = await create_platega_payment(user_id, plan_name, origin)
        return web.json_response(res)
    except Exception as e:
        logger.error("Platega create error: %s", e)
        return web.json_response({"error": str(e)}, status=500)

async def handle_crypto_create(request: web.Request):
    try:
        data = await request.json()
        plan_name = data.get("plan", "Pro")
        cycle = data.get("cycle", "annual")
        user = await get_current_user(request)
        user_id = user["id"] if user else 0
        
        res = await create_cryptopay_payment(user_id, plan_name, cycle)
        return web.json_response(res)
    except Exception as e:
        logger.error("CryptoPay create error: %s", e)
        return web.json_response({"error": str(e)}, status=500)

# --- AI Tools ---

async def handle_expand_prompt(request: web.Request):
    try:
        data = await request.json()
        prompt = data.get("prompt", "")
        if not prompt:
            return web.json_response({"error": "Prompt required"}, status=400)
            
        res = await generate_prompt_expansion(prompt)
        return web.json_response(res)
    except Exception as e:
        logger.error("Expand prompt error: %s", e)
        return web.json_response({"error": str(e)}, status=500)

# --- Static Landing Page ---

async def handle_index(request: web.Request):
    index_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public", "index.html")
    if os.path.exists(index_path):
        response = web.FileResponse(index_path)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
        return response
    return web.Response(text="ZYKO.AI Landing page is loading...", status=200)

def create_app():
    app = web.Application()
    
    # API endpoints
    app.router.add_get("/health", handle_health)
    app.router.add_get("/api/config/public", handle_public_config)
    
    # Auth
    app.router.add_post("/api/auth/google", handle_google_auth)
    app.router.add_post("/api/auth/telegram", handle_telegram_auth)
    app.router.add_get("/api/auth/me", handle_me)
    app.router.add_post("/api/auth/logout", handle_logout)
    
    # Payments
    app.router.add_post("/api/payments/stripe/create-checkout", handle_stripe_create_checkout)
    app.router.add_post("/api/payments/stripe/webhook", handle_stripe_webhook)
    app.router.add_post("/api/payments/platega/create-invoice", handle_platega_create)
    app.router.add_post("/api/payments/crypto/create-invoice", handle_crypto_create)
    
    # AI Services
    app.router.add_post("/api/ai/expand-prompt", handle_expand_prompt)
    
    # Frontend SPA
    app.router.add_get("/", handle_index)
    app.router.add_get("/landing", handle_index)
    
    public_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")
    if os.path.exists(public_dir):
        app.router.add_static("/static", public_dir)
        
    return app

async def start_background_tasks(app):
    await init_db()

if __name__ == "__main__":
    app = create_app()
    app.on_startup.append(start_background_tasks)
    logger.info("Starting ZYKO.AI Web Server on port %s", PORT)
    web.run_app(app, host="0.0.0.0", port=PORT)
