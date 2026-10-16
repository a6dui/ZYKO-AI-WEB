import os
import sys
import json
import base64
import logging
import asyncio
import time
import uuid
import aiohttp
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()

from database import (
    init_db,
    get_user_by_id,
    get_or_create_google_user,
    get_or_create_telegram_user,
    get_or_create_demo_user,
    sync_link_user_accounts
)
from auth import create_access_token, decode_access_token, verify_google_id_token, verify_telegram_auth, GOOGLE_CLIENT_ID
from payments import (
    create_stripe_checkout,
    handle_stripe_webhook_payload,
    create_platega_payment,
    handle_platega_webhook_payload,
    create_cryptopay_payment,
    handle_cryptopay_webhook_payload,
    STRIPE_PUBLISHABLE_KEY,
    PLANS
)
from ai_service import generate_prompt_expansion, generate_llm_chat

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("zyko_server")

PORT = int(os.environ.get("PORT", 8080))
_raw_bot = os.environ.get("TELEGRAM_BOT_USERNAME", "").strip()
TELEGRAM_BOT_USERNAME = "ZykoAi_bot" if (not _raw_bot or _raw_bot == "ElevenlabsA_bot") else _raw_bot

_raw_token = os.environ.get("TELEGRAM_BOT_TOKEN", os.environ.get("ELEVENLABS_BOT_TOKEN", "")).strip()
TELEGRAM_BOT_TOKEN = "8824462987:AAFQ_K1QLmi9ZKiyq6gnYnFUGpgBWsXBFqs" if (not _raw_token or _raw_token.startswith("8921688906")) else _raw_token

# In-memory store for pending Telegram web logins: code -> {status, token, user, created_at}
PENDING_AUTH_SESSIONS = {}

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
        "stripe_publishable_key": STRIPE_PUBLISHABLE_KEY,
        "plans": PLANS
    })

async def handle_google_auth(request: web.Request):
    try:
        data = await request.json()
        credential = data.get("credential")
        email_direct = data.get("email")
        if credential:
            g_info = verify_google_id_token(credential)
            user = await get_or_create_google_user(
                google_id=g_info["google_id"],
                email=g_info["email"],
                name=g_info["name"],
                avatar=g_info["avatar"]
            )
        elif email_direct:
            clean_email = email_direct.strip().lower()
            name_val = data.get("name") or clean_email.split("@")[0].capitalize()
            user = await get_or_create_google_user(
                google_id=f"g_{clean_email}",
                email=clean_email,
                name=name_val,
                avatar=data.get("avatar") or "https://lh3.googleusercontent.com/a/default-user=s96-c"
            )
        else:
            return web.json_response({"error": "Missing credential or email"}, status=400)
            
        token = create_access_token(user["id"])
        resp = web.json_response({"token": token, "user": user})
        resp.set_cookie("zyko_token", token, max_age=30*86400, httponly=True, samesite="Lax")
        return resp
    except Exception as e:
        logger.error("Google Auth error: %s", e)
        return web.json_response({"error": str(e)}, status=400)

async def handle_user_link_account(request: web.Request):
    """
    Links and synchronizes Google and Telegram accounts with balance and subscription pooling.
    """
    try:
        user = await get_current_user(request)
        if not user:
            user = await get_or_create_demo_user()
        data = await request.json()
        google_email = data.get("google_email") or data.get("email")
        tg_id = data.get("telegram_id")
        tg_username = data.get("telegram_username")
        
        updated_user = await sync_link_user_accounts(
            user_id=user["id"],
            google_email=google_email,
            telegram_id=int(tg_id) if tg_id else None,
            telegram_username=tg_username
        )
        if not updated_user:
            return web.json_response({"error": "Failed to link account"}, status=400)
            
        new_token = create_access_token(updated_user["id"])
        resp = web.json_response({"success": True, "user": updated_user, "token": new_token})
        resp.set_cookie("zyko_token", new_token, max_age=30*86400, httponly=True, samesite="Lax")
        return resp
    except Exception as e:
        logger.error("Link account error: %s", e)
        return web.json_response({"error": str(e)}, status=500)

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

async def handle_telegram_create_session(request: web.Request):
    """
    Creates a unique temporary session code for QR/deep-link bot authentication with host callback.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
        
    raw_host = body.get("host") or request.host or "zyko.zeabur.app"
    clean_host = raw_host.strip()
    if "zeabur.app" in clean_host:
        clean_host = "zyko.zeabur.app"
        
    code = f"zk_{uuid.uuid4().hex[:6]}"
    
    PENDING_AUTH_SESSIONS[code] = {
        "status": "pending",
        "created_at": time.time(),
        "token": None,
        "user": None,
        "host": clean_host
    }
    
    # Encode host to base64url so bot can reach the exact site instance
    try:
        host_b64 = base64.urlsafe_b64encode(clean_host.encode()).decode().rstrip("=")
        param = f"a_{code}_{host_b64}"
        if len(param) > 64:
            param = f"auth_{code}"
    except Exception:
        param = f"auth_{code}"
        
    auth_url = f"https://t.me/{TELEGRAM_BOT_USERNAME}?start={param}"
    return web.json_response({
        "code": code,
        "bot_username": TELEGRAM_BOT_USERNAME,
        "auth_url": auth_url
    })

async def handle_telegram_check_session(request: web.Request):
    """
    Polls status of a pending Telegram login session.
    """
    code = request.query.get("code", "").strip()
    session = PENDING_AUTH_SESSIONS.get(code)
    if not session:
        return web.json_response({"authenticated": False, "status": "expired"})
        
    # Check expiration (10 minutes)
    if time.time() - session.get("created_at", 0) > 600:
        PENDING_AUTH_SESSIONS.pop(code, None)
        return web.json_response({"authenticated": False, "status": "expired"})
        
    if session.get("status") == "authenticated" and session.get("user"):
        token = session.get("token")
        resp = web.json_response({
            "authenticated": True,
            "status": "authenticated",
            "token": token,
            "user": session.get("user")
        })
        resp.set_cookie("zyko_token", token, max_age=30*86400, httponly=True, samesite="Lax")
        return resp
        
    return web.json_response({"authenticated": False, "status": "pending"})

async def handle_telegram_confirm_session(request: web.Request):
    """
    Called by internal bot poller or external bot webhook to confirm Telegram login.
    Returns token for direct link generation.
    """
    try:
        data = await request.json()
        code = data.get("code", "").strip()
        tg_id = int(data.get("telegram_id", 0))
        tg_username = data.get("username", "")
        first_name = data.get("first_name", "")
        photo_url = data.get("photo_url", "")
        
        if not tg_id:
            return web.json_response({"error": "Missing telegram_id"}, status=400)
            
        user = await get_or_create_telegram_user(tg_id, tg_username, first_name, photo_url)
        token = create_access_token(user["id"])
        
        if code:
            PENDING_AUTH_SESSIONS[code] = {
                "status": "authenticated",
                "token": token,
                "user": user,
                "created_at": time.time()
            }
            logger.info("Confirmed Telegram session %s for user %s (@%s)", code, tg_id, tg_username)
            
        return web.json_response({
            "success": True,
            "user_id": user["id"],
            "token": token,
            "user": user
        })
    except Exception as e:
        logger.error("Confirm session error: %s", e)
        return web.json_response({"error": str(e)}, status=500)

async def handle_demo_auth(request: web.Request):
    """
    Instant demo/test account access.
    """
    try:
        user = await get_or_create_demo_user()
        token = create_access_token(user["id"])
        resp = web.json_response({"token": token, "user": user})
        resp.set_cookie("zyko_token", token, max_age=30*86400, httponly=True, samesite="Lax")
        return resp
    except Exception as e:
        logger.error("Demo Auth error: %s", e)
        return web.json_response({"error": str(e)}, status=500)

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
        if not user:
            user = await get_or_create_demo_user()
        user_id = user["id"]
        user_email = user.get("email", "")
        
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
        method = data.get("method", "sbp")
        user = await get_current_user(request)
        if not user:
            user = await get_or_create_demo_user()
        user_id = user["id"]
        origin = f"{request.scheme}://{request.host}"
        
        res = await create_platega_payment(user_id, plan_name, origin, method=method)
        return web.json_response(res)
    except Exception as e:
        logger.error("Platega create error: %s", e)
        return web.json_response({"error": str(e)}, status=500)

async def handle_platega_webhook(request: web.Request):
    try:
        data = await request.json()
        logger.info("Incoming Platega webhook notification: %s", data)
        success = await handle_platega_webhook_payload(data)
        return web.json_response({"status": "ok", "processed": success}, status=200)
    except Exception as e:
        logger.error("Platega webhook error: %s", e)
        return web.json_response({"status": "error", "message": str(e)}, status=200)

async def handle_crypto_create(request: web.Request):
    try:
        data = await request.json()
        plan_name = data.get("plan", "Pro")
        cycle = data.get("cycle", "annual")
        user = await get_current_user(request)
        if not user:
            user = await get_or_create_demo_user()
        user_id = user["id"]
        
        res = await create_cryptopay_payment(user_id, plan_name, cycle)
        return web.json_response(res)
    except Exception as e:
        logger.error("CryptoPay create error: %s", e)
        return web.json_response({"error": str(e)}, status=500)

async def handle_crypto_webhook(request: web.Request):
    try:
        data = await request.json()
        sig = request.headers.get("crypto-pay-api-signature", "")
        success = await handle_cryptopay_webhook_payload(data, sig)
        return web.json_response({"status": "ok", "processed": success}, status=200)
    except Exception as e:
        logger.error("CryptoPay webhook error: %s", e)
        return web.json_response({"status": "error", "message": str(e)}, status=200)

# --- AI Tools & ElevenLabs Generation ---

from elevenlabs_service import (
    text_to_speech,
    generate_sfx,
    clone_voice,
    get_all_character_voices,
    generate_ai_image,
    generate_ai_video,
    generate_ai_music
)
from database import deduct_tokens, log_generation

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

async def handle_ai_chat(request: web.Request):
    try:
        data = await request.json()
        messages = data.get("messages", [])
        model = data.get("model", "chatgpt")
        system_prompt = data.get("system_prompt", "")
        deep_research = bool(data.get("deep_research", False))
        
        user = await get_current_user(request)
        cost = 2 if deep_research else 1
        if user:
            if not await deduct_tokens(user["id"], cost):
                return web.json_response({"error": f"Insufficient tokens (requires {cost} Credits)"}, status=400)
                
        res = await generate_llm_chat(messages, model, system_prompt, deep_research)
        if user:
            last_prompt = messages[-1].get("content", "") if messages else "chat"
            await log_generation(user["id"], f"llm_{model}", last_prompt[:100], "chat_response", cost)
            
        return web.json_response(res)
    except Exception as e:
        logger.error("AI Chat error: %s", e)
        return web.json_response({"error": str(e)}, status=500)

async def handle_generate_tts(request: web.Request):
    try:
        data = await request.json()
        text = data.get("text", "").strip()
        voice_id = data.get("voice_id", "21m0aC4C9KstEPqMsrxW")
        model_id = data.get("model_id", "eleven_multilingual_v2")
        
        if not text:
            return web.json_response({"error": "Text required"}, status=400)
            
        user = await get_current_user(request)
        if user and not await deduct_tokens(user["id"], 1):
            return web.json_response({"error": "Insufficient tokens (requires 1 Credit)"}, status=400)
            
        audio_bytes = await text_to_speech(text, voice_id, model_id)
        if user:
            await log_generation(user["id"], "elevenlabs_tts", text[:100], "tts_audio", 1)
            
        return web.Response(body=audio_bytes, content_type="audio/mpeg")
    except Exception as e:
        logger.error("TTS generation error: %s", e)
        return web.json_response({"error": str(e)}, status=500)

async def handle_generate_sfx(request: web.Request):
    try:
        data = await request.json()
        prompt = data.get("prompt", "").strip()
        duration = float(data.get("duration", 4.0))
        if not prompt:
            return web.json_response({"error": "Prompt required"}, status=400)
            
        user = await get_current_user(request)
        if user and not await deduct_tokens(user["id"], 1):
            return web.json_response({"error": "Insufficient tokens (requires 1 Credit)"}, status=400)
            
        audio_bytes = await generate_sfx(prompt, duration)
        if user:
            await log_generation(user["id"], "elevenlabs_sfx", prompt, "sfx_audio", 1)
            
        return web.Response(body=audio_bytes, content_type="audio/mpeg")
    except Exception as e:
        logger.error("SFX generation error: %s", e)
        return web.json_response({"error": str(e)}, status=500)

async def handle_generate_clone(request: web.Request):
    try:
        reader = await request.multipart()
        name = ""
        audio_bytes = b""
        filename = "sample.mp3"
        
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name == "name":
                name = (await part.read()).decode()
            elif part.name == "file":
                filename = part.filename or "sample.mp3"
                audio_bytes = await part.read()
                
        if not name or not audio_bytes:
            return web.json_response({"error": "Name and audio sample file required"}, status=400)
            
        user = await get_current_user(request)
        if user and not await deduct_tokens(user["id"], 5):
            return web.json_response({"error": "Insufficient tokens for voice cloning (requires 5 Credits)"}, status=400)
            
        res = await clone_voice(name, audio_bytes, filename)
        if user:
            await log_generation(user["id"], "elevenlabs_clone", name, res.get("voice_id", ""), 5)
            
        return web.json_response({"status": "success", "voice": res})
    except Exception as e:
        logger.error("Voice clone error: %s", e)
        return web.json_response({"error": str(e)}, status=500)

async def handle_get_voices(request: web.Request):
    return web.json_response({"voices": get_all_character_voices()})

async def handle_generate_image(request: web.Request):
    try:
        data = await request.json()
        prompt = data.get("prompt", "").strip()
        aspect = data.get("aspect", "1:1")
        model = data.get("model", "flux")
        
        if not prompt:
            return web.json_response({"error": "Prompt required"}, status=400)
            
        user = await get_current_user(request)
        if user and not await deduct_tokens(user["id"], 2):
            return web.json_response({"error": "Insufficient tokens (requires 2 Credits)"}, status=400)
            
        img_bytes = await generate_ai_image(prompt, aspect, model)
        if user:
            await log_generation(user["id"], f"image_{model}", prompt, "image_result", 2)
            
        return web.Response(body=img_bytes, content_type="image/jpeg")
    except Exception as e:
        logger.error("Image generation error: %s", e)
        return web.json_response({"error": str(e)}, status=500)

async def handle_generate_video(request: web.Request):
    try:
        data = await request.json()
        prompt = data.get("prompt", "").strip()
        aspect = data.get("aspect", "9:16")
        model = data.get("model", "kling")
        
        if not prompt:
            return web.json_response({"error": "Prompt required"}, status=400)
            
        user = await get_current_user(request)
        if user and not await deduct_tokens(user["id"], 19):
            return web.json_response({"error": "Insufficient tokens (requires 19 Credits)"}, status=400)
            
        res = await generate_ai_video(prompt, aspect, model)
        if user:
            await log_generation(user["id"], f"video_{model}", prompt, res.get("video_url", ""), 19)
            
        return web.json_response(res)
    except Exception as e:
        logger.error("Video generation error: %s", e)
        return web.json_response({"error": str(e)}, status=500)

async def handle_generate_music(request: web.Request):
    try:
        data = await request.json()
        prompt = data.get("prompt", "").strip()
        style = data.get("style", "Lo-Fi")
        title = data.get("title", "AI Track")
        
        user = await get_current_user(request)
        if user and not await deduct_tokens(user["id"], 5):
            return web.json_response({"error": "Insufficient tokens (requires 5 Credits)"}, status=400)
            
        res = await generate_ai_music(prompt, style, title)
        if user:
            await log_generation(user["id"], "ai_music", prompt, res.get("audio_url", ""), 5)
            
        return web.json_response(res)
    except Exception as e:
        logger.error("Music generation error: %s", e)
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
    app.router.add_post("/api/auth/telegram/create-session", handle_telegram_create_session)
    app.router.add_get("/api/auth/telegram/check-session", handle_telegram_check_session)
    app.router.add_post("/api/auth/telegram/confirm-session", handle_telegram_confirm_session)
    app.router.add_post("/api/auth/demo", handle_demo_auth)
    app.router.add_get("/api/auth/me", handle_me)
    app.router.add_post("/api/auth/logout", handle_logout)
    app.router.add_post("/api/user/link-account", handle_user_link_account)
    
    # Payments & Webhooks
    app.router.add_post("/api/payments/stripe/create-checkout", handle_stripe_create_checkout)
    app.router.add_post("/api/payments/stripe/webhook", handle_stripe_webhook)
    app.router.add_post("/api/payments/platega/create-invoice", handle_platega_create)
    app.router.add_post("/api/payments/platega/webhook", handle_platega_webhook)
    app.router.add_post("/api/webhook/platega", handle_platega_webhook)
    app.router.add_post("/api/payments/crypto/create-invoice", handle_crypto_create)
    app.router.add_post("/api/payments/crypto/webhook", handle_crypto_webhook)
    app.router.add_post("/api/webhook/cryptopay", handle_crypto_webhook)
    
    # AI Services & ElevenLabs Generations
    app.router.add_post("/api/ai/expand-prompt", handle_expand_prompt)
    app.router.add_post("/api/ai/chat", handle_ai_chat)
    app.router.add_post("/api/generate/tts", handle_generate_tts)
    app.router.add_post("/api/generate/sfx", handle_generate_sfx)
    app.router.add_post("/api/generate/clone", handle_generate_clone)
    app.router.add_get("/api/voices", handle_get_voices)
    app.router.add_post("/api/generate/image", handle_generate_image)
    app.router.add_post("/api/generate/video", handle_generate_video)
    app.router.add_post("/api/generate/music", handle_generate_music)
    
    # Frontend SPA
    app.router.add_get("/", handle_index)
    app.router.add_get("/landing", handle_index)
    
    public_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")
    if os.path.exists(public_dir):
        app.router.add_static("/static", public_dir)
        
    return app

async def run_telegram_bot_poller(app):
    bot_token = TELEGRAM_BOT_TOKEN
    if not bot_token:
        logger.warning("No Telegram Bot token configured for poller.")
        return

    logger.info("Starting background Telegram Bot poller for @%s...", TELEGRAM_BOT_USERNAME)
    offset = 0
    url = f"https://api.telegram.org/bot{bot_token}"

    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{url}/deleteWebhook?drop_pending_updates=False", timeout=8) as r:
                pass
    except Exception as e:
        logger.debug("Could not delete webhook: %s", e)

    while True:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40)) as session:
                get_url = f"{url}/getUpdates?offset={offset}&timeout=20&allowed_updates=[\"message\"]"
                async with session.get(get_url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        updates = data.get("result", [])
                        for update in updates:
                            offset = update["update_id"] + 1
                            msg = update.get("message", {})
                            text = msg.get("text", "").strip()
                            from_user = msg.get("from", {})
                            chat_id = msg.get("chat", {}).get("id")
                            
                            if not text or not chat_id:
                                continue
                                
                            tg_id = from_user.get("id")
                            tg_username = from_user.get("username", "")
                            tg_first_name = from_user.get("first_name", "")
                            
                            # Handle /start with auth code
                            if text.startswith("/start a_") or text.startswith("/start auth_"):
                                raw_param = text.split("/start", 1)[1].strip()
                                auth_code = ""
                                target_host = ""
                                
                                if raw_param.startswith("a_"):
                                    parts = raw_param.split("_")
                                    if len(parts) >= 4 and parts[1] == "zk":
                                        auth_code = f"zk_{parts[2]}"
                                        host_b64 = parts[3]
                                    elif len(parts) >= 3:
                                        auth_code = parts[1]
                                        host_b64 = parts[2]
                                    else:
                                        auth_code = raw_param
                                        host_b64 = ""
                                        
                                    if host_b64:
                                        try:
                                            pad = 4 - (len(host_b64) % 4)
                                            if pad != 4:
                                                host_b64 += "=" * pad
                                            target_host = base64.urlsafe_b64decode(host_b64.encode()).decode()
                                        except Exception:
                                            target_host = ""
                                else:
                                    auth_code = raw_param.replace("auth_", "").strip()

                                user = await get_or_create_telegram_user(
                                    telegram_id=tg_id,
                                    username=tg_username,
                                    first_name=tg_first_name,
                                    photo_url=""
                                )
                                token = create_access_token(user["id"])
                                if auth_code:
                                    PENDING_AUTH_SESSIONS[auth_code] = {
                                        "status": "authenticated",
                                        "token": token,
                                        "user": user,
                                        "created_at": time.time()
                                    }
                                    logger.info("Telegram session %s authenticated for user %s (@%s)", auth_code, tg_id, tg_username)

                                # Determine target URL - default strictly to https://zyko.zeabur.app
                                if target_host and ("localhost" in target_host or "127.0.0.1" in target_host):
                                    site_base = f"http://{target_host}"
                                elif target_host and "zeabur.app" in target_host:
                                    site_base = f"https://{target_host}"
                                else:
                                    site_base = os.environ.get("WEB_URL") or "https://zyko.zeabur.app"
                                site_base = site_base.rstrip("/")
                                    
                                redirect_url = f"{site_base}/?auth_token={token}"
                                
                                # Send cross-host confirm if target is external
                                if target_host and "localhost" not in target_host:
                                    try:
                                        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4)) as s:
                                            await s.post(f"{site_base}/api/auth/telegram/confirm-session", json={
                                                "code": auth_code,
                                                "telegram_id": tg_id,
                                                "username": tg_username,
                                                "first_name": tg_first_name
                                            })
                                    except Exception:
                                        pass

                                reply_text = (
                                    f"🎉 <b>Авторизация успешна!</b>\n\n"
                                    f"Вы успешно вошли на сайт <b>ZYKO.AI</b> под аккаунтом: <b>@{tg_username or tg_first_name}</b>\n\n"
                                    f"💎 Ваш баланс: <b>{user.get('tokens', 1000):,} ZK токенов</b>\n\n"
                                    f"👇 <b>Нажмите на кнопку ниже, чтобы перейти на сайт в свой аккаунт:</b>"
                                )
                                kb = {
                                    "inline_keyboard": [
                                        [{"text": "🚀 Войти на сайт ZYKO.AI", "url": redirect_url}],
                                        [{"text": "📱 Открыть Mini App", "web_app": {"url": redirect_url}}]
                                    ]
                                }
                                send_url = f"{url}/sendMessage"
                                await session.post(send_url, json={
                                    "chat_id": chat_id,
                                    "text": reply_text,
                                    "parse_mode": "HTML",
                                    "reply_markup": kb
                                })
                            elif text.startswith("/start"):
                                reply_text = (
                                    f"✨ <b>Добро пожаловать в ZYKO.AI!</b>\n\n"
                                    f"Медиа-студия нового поколения: озвучка 40 персонажей, клонирование голоса, генерация 4K фото и видео.\n\n"
                                    f"👇 Чтобы войти в аккаунт, откройте окно авторизации на сайте и нажмите <b>Войти через Telegram</b>."
                                )
                                kb = {
                                    "inline_keyboard": [
                                        [{"text": "🌐 Открыть ZYKO.AI Web", "url": "https://zyko.zeabur.app"}]
                                    ]
                                }
                                send_url = f"{url}/sendMessage"
                                await session.post(send_url, json={
                                    "chat_id": chat_id,
                                    "text": reply_text,
                                    "parse_mode": "HTML",
                                    "reply_markup": kb
                                })
                    elif resp.status == 409:
                        await asyncio.sleep(15)
                    else:
                        await asyncio.sleep(3)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug("Bot listener loop: %s", e)
            await asyncio.sleep(4)

async def start_background_tasks(app):
    await init_db()
    app["bot_task"] = asyncio.create_task(run_telegram_bot_poller(app))

async def cleanup_background_tasks(app):
    if "bot_task" in app and not app["bot_task"].done():
        app["bot_task"].cancel()
        try:
            await app["bot_task"]
        except asyncio.CancelledError:
            pass

if __name__ == "__main__":
    app = create_app()
    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)
    logger.info("Starting ZYKO.AI Web Server on port %s", PORT)
    web.run_app(app, host="0.0.0.0", port=PORT)
