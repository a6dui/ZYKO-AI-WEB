import os
import sys
import json
import logging
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()

from database import init_db, get_user_by_id, get_or_create_google_user, get_or_create_telegram_user
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
from ai_service import generate_prompt_expansion

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("zyko_server")

PORT = int(os.environ.get("PORT", 8080))
TELEGRAM_BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME", "ElevenlabsA_bot")

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
        method = data.get("method", "sbp")
        user = await get_current_user(request)
        user_id = user["id"] if user else 0
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
        user_id = user["id"] if user else 0
        
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

async def handle_generate_tts(request: web.Request):
    try:
        data = await request.json()
        text = data.get("text", "").strip()
        voice_id = data.get("voice_id", "21m0aC4C9KstEPqMsrxW")
        model_id = data.get("model_id", "eleven_multilingual_v2")
        
        if not text:
            return web.json_response({"error": "Text required"}, status=400)
            
        user = await get_current_user(request)
        if user and not await deduct_tokens(user["id"], 2):
            return web.json_response({"error": "Insufficient tokens (requires 2 ZK)"}, status=400)
            
        audio_bytes = await text_to_speech(text, voice_id, model_id)
        if user:
            await log_generation(user["id"], "elevenlabs_tts", text[:100], "tts_audio", 2)
            
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
        if user and not await deduct_tokens(user["id"], 5):
            return web.json_response({"error": "Insufficient tokens (requires 5 ZK)"}, status=400)
            
        audio_bytes = await generate_sfx(prompt, duration)
        if user:
            await log_generation(user["id"], "elevenlabs_sfx", prompt, "sfx_audio", 5)
            
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
        if user and not await deduct_tokens(user["id"], 20):
            return web.json_response({"error": "Insufficient tokens for voice cloning (requires 20 ZK)"}, status=400)
            
        res = await clone_voice(name, audio_bytes, filename)
        if user:
            await log_generation(user["id"], "elevenlabs_clone", name, res.get("voice_id", ""), 20)
            
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
            return web.json_response({"error": "Insufficient tokens (requires 2 ZK)"}, status=400)
            
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
        if user and not await deduct_tokens(user["id"], 15):
            return web.json_response({"error": "Insufficient tokens (requires 15 ZK)"}, status=400)
            
        res = await generate_ai_video(prompt, aspect, model)
        if user:
            await log_generation(user["id"], f"video_{model}", prompt, res.get("video_url", ""), 15)
            
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
        if user and not await deduct_tokens(user["id"], 10):
            return web.json_response({"error": "Insufficient tokens (requires 10 ZK)"}, status=400)
            
        res = await generate_ai_music(prompt, style, title)
        if user:
            await log_generation(user["id"], "ai_music", prompt, res.get("audio_url", ""), 10)
            
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
    app.router.add_get("/api/auth/me", handle_me)
    app.router.add_post("/api/auth/logout", handle_logout)
    
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

async def start_background_tasks(app):
    await init_db()

if __name__ == "__main__":
    app = create_app()
    app.on_startup.append(start_background_tasks)
    logger.info("Starting ZYKO.AI Web Server on port %s", PORT)
    web.run_app(app, host="0.0.0.0", port=PORT)
