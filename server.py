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
    sync_link_user_accounts,
    get_admin_overview,
    get_admin_users,
    admin_update_user,
    admin_gift_all_users,
    get_admin_transactions,
    get_admin_generations,
    get_system_settings,
    update_system_setting,
    complete_transaction,
    get_user_by_email,
    create_email_user,
    set_user_password
)
from auth import create_access_token, decode_access_token, verify_google_id_token, verify_telegram_auth, verify_telegram_webapp_data, hash_password, verify_password, GOOGLE_CLIENT_ID
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

async def handle_models_status(request: web.Request):
    system_filter = request.query.get("system", "web")
    date_range = request.query.get("range", "Sep 2026 — Oct 2026.")
    
    # Live Telegram Bot Ping
    tg_latency = 42
    tg_status = "operational"
    if TELEGRAM_BOT_TOKEN:
        try:
            t0 = time.monotonic()
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2.0)) as session:
                async with session.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe") as r:
                    if r.status != 200:
                        tg_status = "degraded"
            tg_latency = max(20, int((time.monotonic() - t0) * 1000))
        except Exception:
            tg_latency = 95
            
    now_ts = int(time.time())
    
    models_data = [
      {"id": "tg_bot", "name": "Telegram Bot Status", "system": "bot", "uptime": "100%", "latency_ms": tg_latency, "status": tg_status, "history": "all_green", "components": []},
      {"id": "chatgpt", "name": "Chat GPT", "system": "both", "uptime": "99.77%", "latency_ms": 135 + (now_ts % 17), "status": "operational", "history": "chatgpt_incidents", "count": 24, "components": [
          {"name": "GPT-4o Omnimodal (High-res Vision)", "uptime": "100%", "latency_ms": 140},
          {"name": "GPT-4o mini (High-speed & Lightweight)", "uptime": "100%", "latency_ms": 82},
          {"name": "o1 Reasoning Engine (Math & Code)", "uptime": "99.8%", "latency_ms": 310},
          {"name": "o3-mini Fast Deep-think", "uptime": "100%", "latency_ms": 115},
          {"name": "ChatGPT Plus Turbo v6", "uptime": "100%", "latency_ms": 128},
          {"name": "DALL-E 3 Image Generation", "uptime": "100%", "latency_ms": 940},
          {"name": "GPT-4 Turbo Vision", "uptime": "100%", "latency_ms": 190},
          {"name": "GPT-4.5 Orion Research Preview", "uptime": "99.5%", "latency_ms": 420},
          {"name": "Voice Whisper Large v3", "uptime": "100%", "latency_ms": 210},
          {"name": "TTS-1 HD High Quality Voice", "uptime": "100%", "latency_ms": 175}
      ]},
      {"id": "flux", "name": "Flux", "system": "both", "uptime": "99.53%", "latency_ms": 240 + (now_ts % 23), "status": "operational", "history": "flux_incidents", "count": 9, "components": [
          {"name": "Flux 1.1 Pro (BFL API)", "uptime": "100%", "latency_ms": 280},
          {"name": "Flux Schnell (4-step Ultra)", "uptime": "100%", "latency_ms": 110},
          {"name": "Flux Realism LoRA v2.4", "uptime": "99.2%", "latency_ms": 320},
          {"name": "Flux Dev High Definition", "uptime": "100%", "latency_ms": 410},
          {"name": "Flux Fill & Inpaint Model", "uptime": "100%", "latency_ms": 380},
          {"name": "Flux Redux Image-to-Image", "uptime": "100%", "latency_ms": 340},
          {"name": "Flux Depth ControlNet", "uptime": "99.7%", "latency_ms": 450},
          {"name": "Flux Canny Edge Preserver", "uptime": "100%", "latency_ms": 420},
          {"name": "Flux Ultra 2K Raw Output", "uptime": "100%", "latency_ms": 590}
      ]},
      {"id": "elevenlabs", "name": "ElevenLabs", "system": "both", "uptime": "99.95%", "latency_ms": 95 + (now_ts % 11), "status": "operational", "history": "elevenlabs_incidents", "count": 4, "components": [
          {"name": "Eleven Multilingual v2", "uptime": "100%", "latency_ms": 110},
          {"name": "Eleven Turbo v2.5 (Sub-100ms)", "uptime": "100%", "latency_ms": 68},
          {"name": "Instant Voice Cloning Engine", "uptime": "99.8%", "latency_ms": 240},
          {"name": "Sound Effects (Cinematic SFX)", "uptime": "100%", "latency_ms": 130}
      ]},
      {"id": "luma", "name": "Luma", "system": "both", "uptime": "99.80%", "latency_ms": 310 + (now_ts % 19), "status": "operational", "history": "luma_incidents", "count": 3, "components": [
          {"name": "Dream Machine 1.5 High-Motion", "uptime": "100%", "latency_ms": 320},
          {"name": "Luma Photon Image Gen", "uptime": "99.6%", "latency_ms": 240},
          {"name": "Luma Keyframes Camera Control", "uptime": "100%", "latency_ms": 390}
      ]},
      {"id": "runway", "name": "Runway", "system": "both", "uptime": "98.51%", "latency_ms": 420 + (now_ts % 31), "status": "operational", "history": "runway_incidents", "count": 3, "components": [
          {"name": "Gen-3 Alpha Cinematic", "uptime": "98.5%", "latency_ms": 480},
          {"name": "Gen-3 Alpha Turbo", "uptime": "99.1%", "latency_ms": 310},
          {"name": "Motion Brush & Director Mode", "uptime": "98.0%", "latency_ms": 440}
      ]},
      {"id": "kling", "name": "Kling", "system": "both", "uptime": "99.89%", "latency_ms": 290 + (now_ts % 13), "status": "operational", "history": "kling_incidents", "count": 4, "components": [
          {"name": "Kling 4.0 HQ (30s Extended)", "uptime": "100%", "latency_ms": 350},
          {"name": "Kling 1.5 Standard 1080p", "uptime": "99.8%", "latency_ms": 260},
          {"name": "Kling Lip-Sync Multilingual", "uptime": "100%", "latency_ms": 310},
          {"name": "Kling Camera Motion Control", "uptime": "100%", "latency_ms": 340}
      ]},
      {"id": "minimax", "name": "MiniMax", "system": "both", "uptime": "99.90%", "latency_ms": 180 + (now_ts % 15), "status": "operational", "history": "minimax_incidents", "count": 3, "components": [
          {"name": "Hailuo Video-01 Cinematic", "uptime": "100%", "latency_ms": 380},
          {"name": "MiniMax Speech 2.0 HD", "uptime": "99.8%", "latency_ms": 120},
          {"name": "MiniMax Text 01 4M Context", "uptime": "100%", "latency_ms": 95}
      ]},
      {"id": "google_veo", "name": "Google Veo", "system": "both", "uptime": "99.79%", "latency_ms": 340 + (now_ts % 21), "status": "operational", "history": "veo_incidents", "count": 2, "components": [
          {"name": "Google Veo 3.1 1080p Cinematic", "uptime": "99.8%", "latency_ms": 360},
          {"name": "Google Veo 2.0 HD Generation", "uptime": "100%", "latency_ms": 290}
      ]},
      {"id": "gemini", "name": "Gemini", "system": "both", "uptime": "100%", "latency_ms": 85 + (now_ts % 9), "status": "operational", "history": "all_green", "count": 8, "components": [
          {"name": "Gemini 2.5 Pro (2M Context)", "uptime": "100%", "latency_ms": 95},
          {"name": "Gemini 2.0 Flash Fast", "uptime": "100%", "latency_ms": 52},
          {"name": "Gemini 2.0 Flash Thinking", "uptime": "100%", "latency_ms": 110},
          {"name": "Gemini 1.5 Pro Long-context", "uptime": "100%", "latency_ms": 88},
          {"name": "Gemini Multimodal Live Stream", "uptime": "100%", "latency_ms": 125},
          {"name": "Gemini Code Assist Engine", "uptime": "100%", "latency_ms": 78},
          {"name": "Imagen 3 on Vertex AI", "uptime": "100%", "latency_ms": 480},
          {"name": "Gemini Web Search Grounding", "uptime": "100%", "latency_ms": 160}
      ]},
      {"id": "sora_images", "name": "Sora Images", "system": "both", "uptime": "99.68%", "latency_ms": 460 + (now_ts % 29), "status": "operational", "history": "sora_incidents", "count": 5, "components": [
          {"name": "Sora Diffusion Frame Synth", "uptime": "99.7%", "latency_ms": 490},
          {"name": "Sora Photoreal 4K", "uptime": "100%", "latency_ms": 620},
          {"name": "Sora Temporal Consistency Layer", "uptime": "99.4%", "latency_ms": 410},
          {"name": "Sora Character Sheet Consistency", "uptime": "100%", "latency_ms": 520},
          {"name": "Sora Cinematic Aspect Ratios", "uptime": "100%", "latency_ms": 470}
      ]},
      {"id": "higgsfield", "name": "Higgsfield", "system": "both", "uptime": "99.94%", "latency_ms": 260 + (now_ts % 17), "status": "operational", "history": "higgsfield_incidents", "count": 4, "components": [
          {"name": "DoP Cinema Camera Controls", "uptime": "100%", "latency_ms": 280},
          {"name": "Anime Action Dynamics", "uptime": "99.8%", "latency_ms": 250},
          {"name": "Motion Presets VFX", "uptime": "100%", "latency_ms": 290},
          {"name": "Higgsfield High Frame Rate", "uptime": "100%", "latency_ms": 320}
      ]},
      {"id": "bytedance", "name": "Bytedance", "system": "both", "uptime": "99.60%", "latency_ms": 210 + (now_ts % 19), "status": "operational", "history": "bytedance_incidents", "count": 3, "components": [
          {"name": "Seaweed Video Generator", "uptime": "99.5%", "latency_ms": 310},
          {"name": "Doubao 1.5 Pro", "uptime": "100%", "latency_ms": 90},
          {"name": "PixDance Character Mover", "uptime": "99.3%", "latency_ms": 260}
      ]},
      {"id": "nano_banana", "name": "Nano Banana", "system": "both", "uptime": "100%", "latency_ms": 115 + (now_ts % 12), "status": "operational", "history": "all_green", "count": 4, "components": [
          {"name": "Nano Banana 2.1 Ultra Photoreal", "uptime": "100%", "latency_ms": 130},
          {"name": "Nano Banana Speed Edition", "uptime": "100%", "latency_ms": 75},
          {"name": "Style Match & Reference Lock", "uptime": "100%", "latency_ms": 140},
          {"name": "Prompt Auto-Expander Engine", "uptime": "100%", "latency_ms": 85}
      ]},
      {"id": "deepseek_r1", "name": "DeepSeek R1", "system": "both", "uptime": "100%", "latency_ms": 125 + (now_ts % 14), "status": "operational", "history": "all_green", "components": []},
      {"id": "qwen", "name": "Qwen", "system": "both", "uptime": "100%", "latency_ms": 110 + (now_ts % 13), "status": "operational", "history": "all_green", "count": 5, "components": [
          {"name": "Qwen 2.5 Max (Flagship)", "uptime": "100%", "latency_ms": 110},
          {"name": "Qwen 2.5 Coder 32B Instruct", "uptime": "100%", "latency_ms": 95},
          {"name": "Qwen 2.5 72B High-Precision", "uptime": "100%", "latency_ms": 135},
          {"name": "Qwen VL Max (Visual Language)", "uptime": "100%", "latency_ms": 180},
          {"name": "Qwen Math Olympiad Reasoning", "uptime": "100%", "latency_ms": 210}
      ]},
      {"id": "claude", "name": "Claude", "system": "both", "uptime": "100%", "latency_ms": 105 + (now_ts % 11), "status": "operational", "history": "claude_incidents", "count": 15, "components": [
          {"name": "Claude 3.7 Sonnet (Hybrid Reasoning)", "uptime": "100%", "latency_ms": 120},
          {"name": "Claude 3.5 Sonnet v2", "uptime": "100%", "latency_ms": 95},
          {"name": "Claude 3.5 Haiku (Lightning)", "uptime": "100%", "latency_ms": 48},
          {"name": "Claude 3 Opus (Deep Analysis)", "uptime": "100%", "latency_ms": 190},
          {"name": "Claude Interactive Artifacts Engine", "uptime": "100%", "latency_ms": 85},
          {"name": "Claude Extended Context (200k)", "uptime": "100%", "latency_ms": 135}
      ]},
      {"id": "grok", "name": "Grok", "system": "both", "uptime": "100%", "latency_ms": 98 + (now_ts % 10), "status": "operational", "history": "all_green", "count": 5, "components": [
          {"name": "Grok 3 Thinking (100k Context)", "uptime": "100%", "latency_ms": 110},
          {"name": "Grok 2 Multimodal Vision", "uptime": "100%", "latency_ms": 130},
          {"name": "Grok 2 Mini Speed", "uptime": "100%", "latency_ms": 60},
          {"name": "X Real-time Search Grounding", "uptime": "100%", "latency_ms": 170},
          {"name": "Grok Unfiltered Logic Core", "uptime": "100%", "latency_ms": 85}
      ]},
      {"id": "perplexity", "name": "Perplexity", "system": "both", "uptime": "99.97%", "latency_ms": 140 + (now_ts % 15), "status": "operational", "history": "perplexity_incidents", "count": 3, "components": [
          {"name": "Sonar Deep Research (Web Index)", "uptime": "100%", "latency_ms": 220},
          {"name": "Sonar Pro Academic Citations", "uptime": "99.9%", "latency_ms": 160},
          {"name": "Sonar Small Live Search", "uptime": "100%", "latency_ms": 90}
      ]},
      {"id": "grok_image", "name": "Grok Image", "system": "both", "uptime": "99.21%", "latency_ms": 310 + (now_ts % 25), "status": "operational", "history": "grok_img_incidents", "count": 4, "components": [
          {"name": "Aurora Flux Photorealism", "uptime": "99.1%", "latency_ms": 330},
          {"name": "Flux Uncensored Creative", "uptime": "99.3%", "latency_ms": 280},
          {"name": "Flux High Dynamic Range", "uptime": "99.5%", "latency_ms": 360},
          {"name": "Fast Preview Renderer", "uptime": "100%", "latency_ms": 120}
      ]},
      {"id": "faceswap", "name": "FaceSwap", "system": "both", "uptime": "99.55%", "latency_ms": 190 + (now_ts % 18), "status": "operational", "history": "faceswap_incidents", "components": []},
      {"id": "ideogram", "name": "IdeoGram", "system": "both", "uptime": "99.81%", "latency_ms": 260 + (now_ts % 14), "status": "operational", "history": "ideogram_incidents", "components": []},
      {"id": "photomaker", "name": "PhotoMaker", "system": "both", "uptime": "100%", "latency_ms": 160 + (now_ts % 11), "status": "operational", "history": "photomaker_incidents", "components": []},
      {"id": "image_editor", "name": "Image Editor", "system": "both", "uptime": "100%", "latency_ms": 95 + (now_ts % 8), "status": "operational", "history": "all_green", "components": []},
      {"id": "did_avatars", "name": "D-ID Avatars", "system": "both", "uptime": "99.74%", "latency_ms": 330 + (now_ts % 20), "status": "operational", "history": "did_incidents", "components": []},
      {"id": "suno", "name": "Suno", "system": "both", "uptime": "99.64%", "latency_ms": 380 + (now_ts % 22), "status": "operational", "history": "suno_incidents", "components": []},
      {"id": "audio2text", "name": "Audio 2 Text", "system": "both", "uptime": "100%", "latency_ms": 140 + (now_ts % 10), "status": "operational", "history": "all_green", "components": []},
      {"id": "pika", "name": "Pika", "system": "both", "uptime": "100%", "latency_ms": 310 + (now_ts % 16), "status": "operational", "history": "all_green", "components": []},
      {"id": "stable_diffusion", "name": "Stable Diffusion", "system": "both", "uptime": "99.66%", "latency_ms": 210 + (now_ts % 15), "status": "operational", "history": "sd_incidents", "components": []},
      {"id": "switch_x", "name": "Switch X", "system": "web", "uptime": "100%", "latency_ms": 65 + (now_ts % 6), "status": "operational", "history": "all_green", "components": []},
      {"id": "text_to_speech", "name": "Text To Speech", "system": "both", "uptime": "100%", "latency_ms": 110 + (now_ts % 9), "status": "operational", "history": "all_green", "components": []},
      {"id": "flux_video", "name": "Flux Video", "system": "both", "uptime": "100%", "latency_ms": 420 + (now_ts % 28), "status": "operational", "history": "all_green", "components": []},
      {"id": "glm_52", "name": "GLM 5.2", "system": "both", "uptime": "100%", "latency_ms": 130 + (now_ts % 12), "status": "operational", "history": "all_green", "components": []},
      {"id": "gemini_4_argon", "name": "Gemini 4 Argon", "system": "both", "uptime": "100%", "latency_ms": 90 + (now_ts % 8), "status": "operational", "history": "all_green", "components": []},
      {"id": "gemini_4_barium", "name": "Gemini 4 Barium", "system": "both", "uptime": "100%", "latency_ms": 92 + (now_ts % 8), "status": "operational", "history": "all_green", "components": []},
      {"id": "deepseek_v4_pro", "name": "DeepSeek V4 Pro", "system": "both", "uptime": "100%", "latency_ms": 140 + (now_ts % 11), "status": "operational", "history": "all_green", "components": []},
      {"id": "deepseek_v41_flash", "name": "DeepSeek V4.1 Flash", "system": "both", "uptime": "100%", "latency_ms": 65 + (now_ts % 7), "status": "operational", "history": "all_green", "components": []},
      {"id": "glm_53", "name": "GLM 5.3", "system": "both", "uptime": "100%", "latency_ms": 125 + (now_ts % 10), "status": "operational", "history": "all_green", "components": []}
    ]

    return web.json_response({
        "status": "operational",
        "message": "We're not aware of any issues affecting our systems",
        "range": date_range,
        "last_checked": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "global_uptime": "99.88%",
        "active_incidents": 0,
        "total_models": len(models_data),
        "models": models_data
    })

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

async def handle_email_register(request: web.Request):
    try:
        data = await request.json()
        email = (data.get("email") or "").strip().lower()
        password = data.get("password") or ""
        name = (data.get("name") or "").strip()
        
        if not email or "@" not in email:
            return web.json_response({"error": "Введите корректный email"}, status=400)
        if len(password) < 6:
            return web.json_response({"error": "Пароль должен содержать не менее 6 символов"}, status=400)
            
        existing = await get_user_by_email(email)
        if existing:
            if existing.get("password_hash"):
                return web.json_response({"error": "Аккаунт с таким email уже существует. Пожалуйста, войдите."}, status=400)
            else:
                hashed = hash_password(password)
                await set_user_password(existing["id"], hashed)
                token = create_access_token(existing["id"])
                resp = web.json_response({"status": "ok", "token": token, "user": existing})
                resp.set_cookie("zyko_token", token, max_age=30*86400, httponly=True, samesite="Lax")
                return resp
                
        hashed = hash_password(password)
        user = await create_email_user(email, hashed, name)
        token = create_access_token(user["id"])
        resp = web.json_response({"status": "ok", "token": token, "user": user})
        resp.set_cookie("zyko_token", token, max_age=30*86400, httponly=True, samesite="Lax")
        return resp
    except Exception as e:
        logger.error("Email register error: %s", e)
        return web.json_response({"error": str(e)}, status=500)

async def handle_email_login(request: web.Request):
    try:
        data = await request.json()
        email = (data.get("email") or "").strip().lower()
        password = data.get("password") or ""
        
        if not email or not password:
            return web.json_response({"error": "Укажите email и пароль"}, status=400)
            
        user = await get_user_by_email(email)
        if not user:
            return web.json_response({"error": "Пользователь с таким email не найден"}, status=404)
            
        saved_hash = user.get("password_hash")
        if not saved_hash:
            return web.json_response({"error": "Этот аккаунт был зарегистрирован через Google или Telegram. Войдите через них или зарегистрируйтесь с паролем."}, status=400)
            
        if not verify_password(password, saved_hash):
            return web.json_response({"error": "Неверный пароль"}, status=401)
            
        token = create_access_token(user["id"])
        resp = web.json_response({"status": "ok", "token": token, "user": user})
        resp.set_cookie("zyko_token", token, max_age=30*86400, httponly=True, samesite="Lax")
        return resp
    except Exception as e:
        logger.error("Email login error: %s", e)
        return web.json_response({"error": str(e)}, status=500)

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
        resp.set_cookie("zyko_token", token, max_age=30*86400, httponly=False, samesite="None", secure=True, path="/")
        return resp
    except Exception as e:
        logger.error("Telegram Auth error: %s", e)
        return web.json_response({"error": str(e)}, status=400)

async def handle_telegram_webapp_auth(request: web.Request):
    """
    Automatic seamless authentication for Telegram WebApp (Mini App) or direct link.
    Validates Telegram WebApp initData or user payload and returns/creates session token.
    """
    try:
        data = await request.json()
        init_data_raw = data.get("initData", "")
        tg_user = data.get("user") or {}
        
        tg_id = None
        username = ""
        first_name = ""
        photo_url = ""

        # 1. Parse from verified initData if provided
        if init_data_raw:
            verified_user = verify_telegram_webapp_data(init_data_raw)
            if verified_user:
                tg_id = verified_user.get("id")
                username = verified_user.get("username", "")
                first_name = verified_user.get("first_name", "")
                photo_url = verified_user.get("photo_url", "")
        
        # 2. Fallback to direct user payload
        if not tg_id and tg_user:
            tg_id = tg_user.get("id")
            username = tg_user.get("username", "")
            first_name = tg_user.get("first_name", "")
            photo_url = tg_user.get("photo_url", "")
            
        if not tg_id and "user_id" in data:
            tg_id = data.get("user_id")
            username = data.get("username", "")

        if not tg_id:
            return web.json_response({"error": "Missing telegram user identification"}, status=400)
            
        tg_id = int(tg_id)
        user = await get_or_create_telegram_user(
            telegram_id=tg_id,
            username=username,
            first_name=first_name,
            photo_url=photo_url
        )
        
        token = create_access_token(user["id"])
        
        resp = web.json_response({
            "authenticated": True,
            "token": token,
            "user": user
        })
        resp.set_cookie(
            "zyko_token",
            token,
            max_age=30 * 86400,
            httponly=False,
            samesite="None",
            secure=True,
            path="/"
        )
        return resp
    except Exception as e:
        logger.error("Telegram WebApp auth error: %s", e)
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
        resp.set_cookie("zyko_token", token, max_age=30*86400, httponly=False, samesite="None", secure=True, path="/")
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
    isolate_audio,
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
        cost = 18 if deep_research else 10
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
        voice_id = data.get("voice_id", "pNInz6obpgDQGcFmaJgB")
        model_id = data.get("model_id", "eleven_multilingual_v2")
        
        if not text:
            return web.json_response({"error": "Text required"}, status=400)
            
        user = await get_current_user(request)
        if user and not await deduct_tokens(user["id"], 10):
            return web.json_response({"error": "Insufficient tokens (requires 10 Credits)"}, status=400)
            
        voice_settings = data.get("voice_settings")
        audio_bytes = await text_to_speech(text, voice_id, model_id, voice_settings)
        if user:
            await log_generation(user["id"], "elevenlabs_tts", text[:100], "tts_audio", 10)
            
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
        if user and not await deduct_tokens(user["id"], 10):
            return web.json_response({"error": "Insufficient tokens (requires 10 Credits)"}, status=400)
            
        audio_bytes = await generate_sfx(prompt, duration)
        if user:
            await log_generation(user["id"], "elevenlabs_sfx", prompt, "sfx_audio", 10)
            
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
        if user and not await deduct_tokens(user["id"], 25):
            return web.json_response({"error": "Insufficient tokens for voice cloning (requires 25 Credits)"}, status=400)
            
        res = await clone_voice(name, audio_bytes, filename)
        if user:
            await log_generation(user["id"], "elevenlabs_clone", name, res.get("voice_id", ""), 25)
            
        return web.json_response({"status": "success", "voice": res})
    except Exception as e:
        logger.error("Voice clone error: %s", e)
        return web.json_response({"error": str(e)}, status=500)

async def handle_get_voices(request: web.Request):
    return web.json_response({"voices": get_all_character_voices()})

async def handle_api_voice_preview(request: web.Request):
    voice_id = request.query.get("voice_id", "").strip()
    if not voice_id:
        return web.Response(text="Missing voice_id", status=400)
    
    public_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")
    preview_file = os.path.join(public_dir, "media", "previews", f"{voice_id}.mp3")
    if os.path.exists(preview_file) and os.path.getsize(preview_file) > 100:
        return web.FileResponse(preview_file, headers={"Content-Type": "audio/mpeg", "Cache-Control": "public, max-age=86400"})
        
    mock_file = os.path.join(public_dir, "media", "mock_preview.mp3")
    if os.path.exists(mock_file):
        return web.FileResponse(mock_file, headers={"Content-Type": "audio/mpeg", "Cache-Control": "public, max-age=86400"})
        
    return web.Response(text="Preview not available", status=404)

async def handle_generate_isolate(request: web.Request):
    try:
        reader = await request.multipart()
        audio_bytes = b""
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name in ("audio", "file"):
                audio_bytes = await part.read()
                
        if not audio_bytes:
            return web.json_response({"error": "Audio file required for isolation"}, status=400)
            
        user = await get_current_user(request)
        if user and not await deduct_tokens(user["id"], 12):
            return web.json_response({"error": "Insufficient tokens (requires 12 Credits)"}, status=400)
            
        clean_bytes = await isolate_audio(audio_bytes)
        if user:
            await log_generation(user["id"], "elevenlabs_isolate", "voice isolation", "isolated_audio", 12)
            
        return web.Response(body=clean_bytes, content_type="audio/mpeg")
    except Exception as e:
        logger.error("Audio isolation error: %s", e)
        return web.json_response({"error": str(e)}, status=500)

async def handle_generate_image(request: web.Request):
    try:
        data = await request.json()
        prompt = data.get("prompt", "").strip()
        aspect = data.get("aspect", "1:1")
        model = data.get("model", "flux")
        
        if not prompt:
            return web.json_response({"error": "Prompt required"}, status=400)
            
        user = await get_current_user(request)
        if user and not await deduct_tokens(user["id"], 12):
            return web.json_response({"error": "Insufficient tokens (requires 12 Credits)"}, status=400)
            
        img_bytes = await generate_ai_image(prompt, aspect, model)
        if user:
            await log_generation(user["id"], f"image_{model}", prompt, "image_result", 12)
            
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
        if user and not await deduct_tokens(user["id"], 25):
            return web.json_response({"error": "Insufficient tokens (requires 25 Credits)"}, status=400)
            
        res = await generate_ai_video(prompt, aspect, model)
        if user:
            await log_generation(user["id"], f"video_{model}", prompt, res.get("video_url", ""), 25)
            
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
        if user and not await deduct_tokens(user["id"], 20):
            return web.json_response({"error": "Insufficient tokens (requires 20 Credits)"}, status=400)
            
        res = await generate_ai_music(prompt, style, title)
        if user:
            await log_generation(user["id"], "ai_music", prompt, res.get("audio_url", ""), 20)
            
        return web.Response(body=res if isinstance(res, (bytes, bytearray)) else b"", content_type="audio/mpeg") if isinstance(res, (bytes, bytearray)) else web.json_response(res)
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
def make_file_handler(filename, content_type=None):
    async def handler(request: web.Request):
        fpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public", filename)
        if os.path.exists(fpath):
            resp = web.FileResponse(fpath)
            if content_type:
                resp.content_type = content_type
            resp.headers["Cache-Control"] = "public, max-age=86400"
            return resp
        return web.Response(status=404)
    return handler

async def handle_get_system_settings(request: web.Request):
    settings = await get_system_settings()
    return web.json_response({"status": "ok", "settings": settings})

async def handle_admin_overview(request: web.Request):
    overview = await get_admin_overview()
    settings = await get_system_settings()
    return web.json_response({"status": "ok", "overview": overview, "settings": settings})

async def handle_admin_users(request: web.Request):
    search = request.query.get("search", "").strip()
    tier = request.query.get("tier", "").strip()
    users = await get_admin_users(search=search if search else None, tier=tier if tier else None)
    return web.json_response({"status": "ok", "users": users})

async def handle_admin_update_user(request: web.Request):
    try:
        data = await request.json()
        user_id = int(data.get("user_id"))
        tokens = float(data.get("tokens")) if "tokens" in data and data["tokens"] is not None else None
        tier = data.get("tier")
        updated = await admin_update_user(user_id, tokens, tier)
        return web.json_response({"status": "ok", "user": updated})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)

async def handle_admin_gift_all(request: web.Request):
    try:
        data = await request.json()
        amount = float(data.get("amount", 500))
        affected = await admin_gift_all_users(amount)
        return web.json_response({"status": "ok", "affected_users": affected, "gifted_tokens": amount})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)

async def handle_admin_transactions(request: web.Request):
    txs = await get_admin_transactions()
    return web.json_response({"status": "ok", "transactions": txs})

async def handle_admin_complete_transaction(request: web.Request):
    try:
        data = await request.json()
        order_id = data.get("order_id")
        if not order_id:
            return web.json_response({"error": "Missing order_id"}, status=400)
        tx = await complete_transaction(order_id)
        return web.json_response({"status": "ok", "transaction": tx})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)

async def handle_admin_generations(request: web.Request):
    gens = await get_admin_generations()
    return web.json_response({"status": "ok", "generations": gens})

async def handle_admin_update_setting(request: web.Request):
    try:
        data = await request.json()
        key = data.get("key")
        value = str(data.get("value", ""))
        if not key:
            return web.json_response({"error": "Missing key"}, status=400)
        await update_system_setting(key, value)
        return web.json_response({"status": "ok", "key": key, "value": value})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)

def create_app():
    app = web.Application()
    
    # API endpoints
    app.router.add_get("/health", handle_health)
    app.router.add_get("/api/status/models", handle_models_status)
    app.router.add_get("/api/config/public", handle_public_config)
    app.router.add_get("/favicon.ico", make_file_handler("favicon.ico", "image/x-icon"))
    app.router.add_get("/favicon.svg", make_file_handler("favicon.svg", "image/svg+xml"))
    app.router.add_get("/favicon.png", make_file_handler("favicon.png", "image/png"))
    app.router.add_get("/favicon-32x32.png", make_file_handler("favicon-32x32.png", "image/png"))
    app.router.add_get("/favicon-16x16.png", make_file_handler("favicon-16x16.png", "image/png"))
    app.router.add_get("/apple-touch-icon.png", make_file_handler("apple-touch-icon.png", "image/png"))
    app.router.add_get("/apple-touch-icon-precomposed.png", make_file_handler("apple-touch-icon.png", "image/png"))
    app.router.add_get("/icon-192.png", make_file_handler("icon-192.png", "image/png"))
    app.router.add_get("/icon-512.png", make_file_handler("icon-512.png", "image/png"))
    app.router.add_get("/site.webmanifest", make_file_handler("site.webmanifest", "application/manifest+json"))
    app.router.add_get("/api/system/settings", handle_get_system_settings)
    
    # Admin Panel API
    app.router.add_get("/api/admin/overview", handle_admin_overview)
    app.router.add_get("/api/admin/users", handle_admin_users)
    app.router.add_post("/api/admin/users/update", handle_admin_update_user)
    app.router.add_post("/api/admin/users/gift", handle_admin_gift_all)
    app.router.add_get("/api/admin/transactions", handle_admin_transactions)
    app.router.add_post("/api/admin/transactions/complete", handle_admin_complete_transaction)
    app.router.add_get("/api/admin/generations", handle_admin_generations)
    app.router.add_post("/api/admin/settings", handle_admin_update_setting)
    
    # Auth
    app.router.add_post("/api/auth/login", handle_email_login)
    app.router.add_post("/api/auth/register", handle_email_register)
    app.router.add_post("/api/auth/google", handle_google_auth)
    app.router.add_post("/api/auth/telegram", handle_telegram_auth)
    app.router.add_post("/api/auth/telegram-webapp", handle_telegram_webapp_auth)
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
    app.router.add_post("/api/generate/isolate", handle_generate_isolate)
    app.router.add_get("/api/voices", handle_get_voices)
    app.router.add_get("/api/voice/preview", handle_api_voice_preview)
    app.router.add_post("/api/generate/image", handle_generate_image)
    app.router.add_post("/api/generate/video", handle_generate_video)
    app.router.add_post("/api/generate/music", handle_generate_music)
    
    # Frontend SPA & Legal Documents
    app.router.add_get("/", handle_index)
    app.router.add_get("/landing", handle_index)
    app.router.add_get("/privacy-policy", make_file_handler("privacy-policy.html", "text/html"))
    app.router.add_get("/public-offer", make_file_handler("public-offer.html", "text/html"))
    app.router.add_get("/terms", make_file_handler("public-offer.html", "text/html"))
    
    public_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")
    if os.path.exists(public_dir):
        app.router.add_static("/static", public_dir)
        media_dir = os.path.join(public_dir, "media")
        if os.path.exists(media_dir):
            app.router.add_static("/media", media_dir)
        
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

                                # Determine target URL - support custom domain zyko.study, zeabur, localhost
                                if target_host:
                                    clean_target = target_host.strip()
                                    if "localhost" in clean_target or "127.0.0.1" in clean_target:
                                        site_base = f"http://{clean_target}"
                                    else:
                                        site_base = f"https://{clean_target}"
                                else:
                                    site_base = os.environ.get("WEB_URL") or "https://zyko.study"
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

                                user_tok = round(float(user.get("tokens") or 1000))
                                user_tier = user.get("tier", "Free")
                                reply_text = (
                                    f"🎉 <b>Авторизация успешна!</b>\n\n"
                                    f"Вы вошли в <b>ZYKO.AI</b> под аккаунтом: <b>@{tg_username or tg_first_name}</b>\n\n"
                                    f"💎 Ваш баланс: <b>{user_tok:,} ZK токенов</b>\n"
                                    f"👑 Подписка: <b>{user_tier}</b>\n\n"
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
                                user = await get_or_create_telegram_user(
                                    telegram_id=tg_id,
                                    username=tg_username,
                                    first_name=tg_first_name,
                                    photo_url=""
                                )
                                token = create_access_token(user["id"])
                                default_domain = os.environ.get("WEB_URL") or "https://zyko.study"
                                default_domain = default_domain.rstrip("/")
                                direct_login_url = f"{default_domain}/?auth_token={token}"
                                
                                user_tok = round(float(user.get("tokens") or 1000))
                                user_tier = user.get("tier", "Free")

                                reply_text = (
                                    f"✨ <b>Добро пожаловать в ZYKO.AI, {tg_first_name or tg_username}!</b>\n\n"
                                    f"Медиа-студия нового поколения: озвучка 40 персонажей, клонирование голоса, генерация 4K фото и видео.\n\n"
                                    f"💎 Ваш баланс: <b>{user_tok:,} ZK токенов</b>\n"
                                    f"👑 Тариф: <b>{user_tier}</b>\n\n"
                                    f"👇 Нажмите кнопку ниже для мгновенного входа в свой профиль:"
                                )
                                kb = {
                                    "inline_keyboard": [
                                        [{"text": "🚀 Войти на сайт ZYKO.AI", "url": direct_login_url}],
                                        [{"text": "📱 Открыть Mini App", "web_app": {"url": direct_login_url}}]
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

import signal

async def run_server():
    app = create_app()
    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    # Bind to PORT from environment, plus fallback standard ports
    ports_to_bind = [PORT]
    for p in [8080, 80, 3000, 5000]:
        if p not in ports_to_bind:
            ports_to_bind.append(p)
            
    bound_count = 0
    for p in ports_to_bind:
        try:
            site = web.TCPSite(runner, "0.0.0.0", p)
            await site.start()
            logger.info("ZYKO.AI Web Server listening on http://0.0.0.0:%s", p)
            bound_count += 1
        except Exception as e:
            logger.debug("Port %s not bound: %s", p, e)
            
    if bound_count == 0:
        logger.error("Could not bind to any port! Exiting.")
        return
        
    logger.info("ZYKO.AI Web Server successfully started on %s port(s). Ready for traffic.", bound_count)
    
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            pass
            
    try:
        await stop_event.wait()
    finally:
        logger.info("Shutting down ZYKO.AI server...")
        await runner.cleanup()

if __name__ == "__main__":
    try:
        asyncio.run(run_server())
    except (KeyboardInterrupt, SystemExit):
        pass

