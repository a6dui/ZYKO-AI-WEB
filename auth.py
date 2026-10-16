import os
import time
import hmac
import hashlib
import jwt
import urllib.request
import json
import logging
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

logger = logging.getLogger("zyko_auth")

JWT_SECRET = os.environ.get("JWT_SECRET", "zyko_super_secret_jwt_key_2026_change_in_prod")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
TELEGRAM_BOT_TOKEN = os.environ.get("ELEVENLABS_BOT_TOKEN", os.environ.get("TELEGRAM_BOT_TOKEN", ""))

def create_access_token(user_id: int, expires_in_days: int = 30) -> str:
    payload = {
        "sub": str(user_id),
        "iat": int(time.time()),
        "exp": int(time.time()) + (expires_in_days * 86400)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def decode_access_token(token: str) -> int:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return int(payload["sub"])
    except Exception as e:
        logger.debug("JWT decode error: %s", e)
def hash_password(password: str) -> str:
    salt = os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000)
    return f"{salt}:{dk.hex()}"

def verify_password(password: str, hashed: str) -> bool:
    if not hashed or ":" not in hashed:
        return False
    try:
        salt, key = hashed.split(":", 1)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000)
        return hmac.compare_digest(dk.hex(), key)
    except Exception:
        return False

def verify_google_id_token(token_str: str) -> dict:
    """
    Verifies a Google ID token from Google One Tap or Google Sign-In.
    Returns dict with google_id, email, name, avatar or raises ValueError.
    """
    try:
        # 1. Try official google-auth library if Client ID is configured
        if GOOGLE_CLIENT_ID:
            id_info = id_token.verify_oauth2_token(token_str, google_requests.Request(), GOOGLE_CLIENT_ID)
            return {
                "google_id": id_info["sub"],
                "email": id_info.get("email", ""),
                "name": id_info.get("name", "Google User"),
                "avatar": id_info.get("picture", "")
            }
    except Exception as e:
        logger.warning("google-auth verification exception: %s, trying tokeninfo endpoint...", e)

    # 2. Fallback to Google's public tokeninfo endpoint
    try:
        url = f"https://oauth2.googleapis.com/tokeninfo?id_token={token_str}"
        req = urllib.request.Request(url, headers={"User-Agent": "ZYKO-AI-Auth"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            if "error_description" in data:
                raise ValueError(data["error_description"])
            return {
                "google_id": data["sub"],
                "email": data.get("email", ""),
                "name": data.get("name", "Google User"),
                "avatar": data.get("picture", "")
            }
    except Exception as e:
        logger.error("Failed to verify Google token: %s", e)
        raise ValueError(f"Invalid Google ID token: {e}")

def verify_telegram_auth(auth_data: dict) -> bool:
    """
    Verifies data received from Telegram Login Widget using HMAC-SHA256.
    auth_data should contain: id, first_name, username, photo_url, auth_date, hash
    """
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not set, skipping HMAC check for testing")
        return True

    check_hash = auth_data.get("hash")
    if not check_hash:
        return False

    # Check expiration (max 24 hours)
    auth_date = int(auth_data.get("auth_date", 0))
    if time.time() - auth_date > 86400:
        return False

    data_check_list = []
    for k, v in sorted(auth_data.items()):
        if k != "hash" and v is not None:
            data_check_list.append(f"{k}={v}")
    data_check_string = "\n".join(data_check_list)

    secret_key = hashlib.sha256(TELEGRAM_BOT_TOKEN.encode()).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    return hmac.compare_digest(calculated_hash, check_hash)
