import os
import urllib.request
import json
import logging

logger = logging.getLogger("zyko_ai")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
HUGGINGFACE_API_KEY = os.environ.get("HUGGINGFACE_API_KEY", "")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")

async def generate_prompt_expansion(user_prompt: str) -> dict:
    """
    Expands a raw prompt into a studio-grade visual prompt using Gemini or fallback.
    """
    if GEMINI_API_KEY:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
            sys_instruct = (
                "You are an elite AI Prompt Architect. Expand the user's short idea into a rich, "
                "photorealistic 8K prompt suitable for FLUX.1 Ultra and Kling 3.0. "
                "Keep it under 3 sentences, mentioning lighting, camera angle, atmosphere, and textures."
            )
            body = {
                "contents": [{
                    "parts": [{"text": f"{sys_instruct}\n\nUser Idea: {user_prompt}"}]
                }]
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())
                cand = data.get("candidates", [{}])[0]
                text = cand.get("content", {}).get("parts", [{}])[0].get("text", "")
                if text:
                    return {"expanded": text.strip(), "provider": "gemini"}
        except Exception as e:
            logger.warning("Gemini expansion error: %s", e)

    # Smart algorithmic fallback if no Gemini key
    expanded = (
        f"{user_prompt}, 8k resolution, cinematic 35mm photograph, dramatic volumetric rim lighting, "
        "intricate photorealistic textures, award-winning composition, shot on Hasselblad"
    )
    return {"expanded": expanded, "provider": "neural_rules"}
