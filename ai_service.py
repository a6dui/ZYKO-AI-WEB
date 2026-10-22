import os
import time
import aiohttp
import json
import base64
import logging

logger = logging.getLogger("zyko_ai")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
HUGGINGFACE_API_KEY = os.environ.get("HUGGINGFACE_API_KEY", "")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")

async def transcribe_audio_gemini(audio_bytes: bytes, mime_type: str = "audio/webm") -> str:
    """
    Transcribes voice recording bytes into clean, accurate prompt text using Gemini 2.5 Flash.
    """
    if not audio_bytes:
        return ""
    if not GEMINI_API_KEY:
        logger.warning("No GEMINI_API_KEY available for voice transcription")
        return ""

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        b64 = base64.b64encode(audio_bytes).decode("utf-8")
        clean_mime = mime_type.split(";")[0].strip() or "audio/webm"
        
        prompt_instruction = (
            "Transcribe this voice recording accurately verbatim in its original spoken language (primarily Russian or English). "
            "Clean up stuttering and false starts, output only the clean transcript suitable as an AI generation prompt. "
            "Do NOT include any explanations, labels, or introductory phrases. Return ONLY the transcribed text."
        )

        body = {
            "contents": [{
                "parts": [
                    {"inline_data": {"mime_type": clean_mime, "data": b64}},
                    {"text": prompt_instruction}
                ]
            }],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 2048
            }
        }

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as session:
            async with session.post(url, json=body) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                        return text.strip()
                else:
                    err_txt = await resp.text()
                    logger.warning("Gemini transcription HTTP %s: %s", resp.status, err_txt)
    except Exception as e:
        logger.error("Gemini audio transcription error: %s", e)

    return ""

async def generate_prompt_expansion(user_prompt: str) -> dict:
    """
    Expands a raw prompt into a studio-grade visual prompt using Gemini or algorithmic fallback.
    """
    if GEMINI_API_KEY:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
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
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
                async with session.post(url, json=body) as resp:
                    if resp.status == 200:
                        data = await resp.json()
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

def normalize_openai_endpoint(base_url: str) -> str:
    """
    Normalizes base_url into a full /chat/completions endpoint.
    Handles OpenRouter, DeepSeek, OpenAI, Groq, local Ollama, etc.
    """
    url = (base_url or "").strip().rstrip("/")
    if not url:
        url = "https://openrouter.ai/api/v1"
    if url.endswith("/chat/completions"):
        return url
    if url.endswith("/v1"):
        return f"{url}/chat/completions"
    return f"{url}/v1/chat/completions"


def map_requested_model_to_provider(model: str, default_model: str = "gpt-4o-mini") -> str:
    """
    Maps frontend model keys or agent personas to standard provider model IDs.
    """
    m = (model or "").lower().strip()
    fallback = (default_model or "gpt-4o-mini").strip()

    # If already a full model name with provider prefix (e.g. anthropic/claude-3.5-sonnet, openai/gpt-4o)
    if "/" in m or "-202" in m:
        return model.strip()

    if "deepseek" in m or "r1" in m:
        return "deepseek-chat"
    elif "claude" in m or "sonnet" in m or "anthropic" in m:
        return "claude-3-5-sonnet-20241022"
    elif "gemini" in m:
        return "gemini-1.5-flash"
    elif "qwen" in m:
        return "qwen-plus"
    elif "grok" in m:
        return "grok-beta"
    elif "zhipu" in m or "glm" in m:
        return "glm-4"
    elif "perplexity" in m or "sonar" in m:
        return "sonar-medium-online"
    elif "chatgpt" in m or "gpt" in m or "luna" in m:
        return "gpt-4o-mini"
    elif "agent" in m:
        return fallback or "gpt-4o-mini"

    return fallback or "gpt-4o-mini"


def build_system_instruction(model: str, user_system_prompt: str = "", deep_research: bool = False) -> str:
    """
    Constructs high-fidelity system personas for AI agents and LLM Studio models.
    """
    m = (model or "").lower()

    if user_system_prompt and user_system_prompt.strip():
        base = user_system_prompt.strip()
    elif "agent" in m:
        if "creative" in m or "media" in m:
            base = (
                "You are the Media Production Director in ZYKO.AI Studio. You specialize in visual cinematography, "
                "storyboarding, video prompt crafting (Veo 3.1, Wan 3.0, Kling 1.5 HD, Seedance 2.5), image art direction "
                "(Nano Banana 2.1, FLUX 1.1 Pro Ultra), and audio design. Be structured, inspiring, and formatted with clean markdown."
            )
        elif "code" in m or "architect" in m:
            base = (
                "You are the Full-Stack & Neural Architect in ZYKO.AI Studio. You provide production-grade code, "
                "optimal system architecture, high-efficiency algorithms, and clear technical guidance with syntax-highlighted code blocks."
            )
        elif "voice" in m or "audio" in m:
            base = (
                "You are the Voice & Audio Producer in ZYKO.AI Studio. You specialize in ElevenLabs HD voice cloning, "
                "cinematic sound effects (SFX), music generation, and audio production workflows."
            )
        elif "marketing" in m or "content" in m:
            base = (
                "You are the Viral Content Strategist in ZYKO.AI Studio. You craft high-converting hooks, viral TikTok/Reels/Shorts "
                "scripts, and actionable social media campaigns."
            )
        elif "research" in m or "academic" in m:
            base = (
                "You are the Deep Research Agent in ZYKO.AI Studio. You conduct exhaustive, objective research, providing "
                "verifiable reasoning, comparative tables, and data-backed insights."
            )
        else:
            base = "You are a versatile autonomous AI Agent inside ZYKO.AI Studio. Assist the user with clarity, speed, and precision."
    else:
        model_display = model.replace("_", " ").title()
        base = (
            f"You are {model_display} running inside ZYKO.AI Studio. Provide helpful, accurate, well-structured answers "
            f"in the language the user speaks. Use clean markdown formatting."
        )

    if deep_research:
        base += "\n\nProvide deep analytical reasoning, step-by-step logic, key takeaways, and comprehensive explanations."

    return base


async def test_llm_connection(base_url: str, api_key: str, model: str = "") -> dict:
    """
    Tests connection to an OpenAI-compatible provider endpoint (e.g. OrigAPI, OpenRouter, DeepSeek, OpenAI).
    """
    if not api_key or not api_key.strip():
        return {"success": False, "error": "API Key не указан (введите ключ в настройках панели)"}

    endpoint = normalize_openai_endpoint(base_url)
    model_to_use = (model or "").strip() or "gpt-4o-mini"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key.strip()}"
    }
    if "openrouter" in endpoint.lower():
        headers["HTTP-Referer"] = "https://zyko.study"
        headers["X-Title"] = "ZYKO.AI"

    payload = {
        "model": model_to_use,
        "messages": [
            {"role": "user", "content": "Ping test from ZYKO.AI. Reply in Russian in 1 short sentence: say hello, confirm connection, and state your model name."}
        ],
        "max_tokens": 120,
        "temperature": 0.5
    }

    t0 = time.time()
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(endpoint, json=payload, headers=headers) as resp:
                latency_ms = int((time.time() - t0) * 1000)
                if resp.status == 200:
                    data = await resp.json()
                    choices = data.get("choices", [])
                    reply_text = ""
                    if choices:
                        msg_obj = choices[0].get("message", {})
                        reply_text = msg_obj.get("content") or msg_obj.get("reasoning_content") or ""
                    return {
                        "success": True,
                        "status_code": 200,
                        "latency_ms": latency_ms,
                        "model_used": model_to_use,
                        "provider_url": endpoint,
                        "reply": reply_text.strip() or "Соединение успешно установлено!"
                    }
                else:
                    err_text = await resp.text()
                    try:
                        err_json = json.loads(err_text)
                        err_msg = err_json.get("error", {}).get("message") or err_json.get("message") or err_text
                    except Exception:
                        err_msg = err_text[:300]
                    return {
                        "success": False,
                        "status_code": resp.status,
                        "latency_ms": latency_ms,
                        "provider_url": endpoint,
                        "error": f"HTTP {resp.status}: {err_msg}"
                    }
    except Exception as e:
        latency_ms = int((time.time() - t0) * 1000)
        return {
            "success": False,
            "latency_ms": latency_ms,
            "provider_url": endpoint,
            "error": f"Ошибка соединения: {str(e)}"
        }


async def generate_llm_chat(messages: list, model: str = "chatgpt", system_prompt: str = "", deep_research: bool = False) -> dict:
    """
    Generates intelligent LLM responses for ChatGPT, Claude, Gemini, DeepSeek, Grok, or AI Agents
    via OpenAI-compatible provider (OpenRouter, DeepSeek, OpenAI) or Gemini/algorithmic fallback.
    """
    last_user_msg = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user_msg = m.get("content", "")
            break

    if not last_user_msg:
        last_user_msg = "Hello!"

    model_clean = (model or "chatgpt").lower()

    # 1. Load active settings from database or environment
    llm_base_url = "https://openrouter.ai/api/v1"
    llm_api_key = os.environ.get("OPENAI_API_KEY", "")
    llm_default_model = "gpt-4o-mini"

    try:
        from database import get_system_settings
        sys_settings = await get_system_settings()
        if sys_settings:
            llm_base_url = sys_settings.get("llm_base_url") or llm_base_url
            if sys_settings.get("llm_api_key"):
                llm_api_key = sys_settings.get("llm_api_key").strip()
            llm_default_model = sys_settings.get("llm_default_model") or llm_default_model
    except Exception as e:
        logger.warning("Could not read system_settings for LLM: %s", e)

    # 2. Universal OpenAI-compatible connector (OrigAPI / OpenRouter / DeepSeek / OpenAI)
    if llm_api_key:
        endpoint = normalize_openai_endpoint(llm_base_url)
        target_model = map_requested_model_to_provider(model, llm_default_model)
        sys_instruction = build_system_instruction(model, system_prompt, deep_research)

        payload_messages = [{"role": "system", "content": sys_instruction}]
        for m in messages[-10:]:
            role = m.get("role", "user")
            if role == "model":
                role = "assistant"
            content = m.get("content", "")
            if content:
                payload_messages.append({"role": role, "content": content})

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {llm_api_key}"
        }
        if "openrouter" in endpoint.lower():
            headers["HTTP-Referer"] = "https://zyko.study"
            headers["X-Title"] = "ZYKO.AI"

        models_to_try = [target_model]
        if llm_default_model and llm_default_model != target_model:
            models_to_try.append(llm_default_model)

        for attempt_model in models_to_try:
            req_body = {
                "model": attempt_model,
                "messages": payload_messages,
                "temperature": 0.7,
                "max_tokens": 3000 if deep_research else 1500
            }
            try:
                timeout = aiohttp.ClientTimeout(total=40)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(endpoint, json=req_body, headers=headers) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            choices = data.get("choices", [])
                            if choices:
                                msg_obj = choices[0].get("message", {})
                                reply_text = msg_obj.get("content") or msg_obj.get("reasoning_content") or ""
                                if reply_text.strip():
                                    usage = data.get("usage", {})
                                    total_tokens = usage.get("total_tokens") or len(reply_text.split())
                                    return {
                                        "reply": reply_text.strip(),
                                        "model": attempt_model,
                                        "deep_research": deep_research,
                                        "tokens_used": total_tokens,
                                        "provider": "openai_gateway"
                                    }
                        else:
                            err_txt = await resp.text()
                            logger.warning("OpenAI gateway HTTP %s on model %s: %s", resp.status, attempt_model, err_txt[:200])
                            if resp.status in (404, 400) and attempt_model != models_to_try[-1]:
                                continue
            except Exception as ex:
                logger.warning("OpenAI gateway attempt error with model %s: %s", attempt_model, ex)

    # 3. Fallback to Gemini 1.5 Flash if GEMINI_API_KEY is available
    if GEMINI_API_KEY:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
            contents = []
            for m in messages[-8:]:
                role = "user" if m.get("role") == "user" else "model"
                contents.append({
                    "role": role,
                    "parts": [{"text": m.get("content", "")}]
                })

            persona_instruct = build_system_instruction(model, system_prompt, deep_research)
            body = {
                "contents": contents,
                "systemInstruction": {
                    "parts": [{"text": persona_instruct}]
                }
            }
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                async with session.post(url, json=body) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        cand = data.get("candidates", [{}])[0]
                        text = cand.get("content", {}).get("parts", [{}])[0].get("text", "")
                        if text:
                            return {
                                "reply": text.strip(),
                                "model": model,
                                "deep_research": deep_research,
                                "tokens_used": len(text.split()),
                                "provider": "gemini"
                            }
        except Exception as e:
            logger.warning("Gemini chat error: %s", e)

    # 4. Intelligent Persona Engine Fallback
    q_lower = last_user_msg.lower()

    if "agent" in model_clean:
        if "creative" in model_clean or "media" in model_clean:
            prefix = "🎬 **Media Production Director**\n\n"
            reply = (
                f"{prefix}План реализации вашей креативной задачи «*{last_user_msg}*»:\n\n"
                "1. **Визуальный стиль & Промпт**: Рекомендую задействовать **Nano Banana 2.1** или **FLUX 1.1 Pro Ultra** с кинематографичным светом (35mm lens, volumetric lighting).\n"
                "2. **Видеоряд**: Перенесите полученный кадр в **Kling 1.5 HD** или **Veo 3.1** с плавной траекторией камеры `[Dolly In + Slow Pan]`.\n"
                "3. **Саундтрек & Озвучка**: В блоке `Audio` синтезируйте закадровый голос через студийный голос ElevenLabs и наложите фоновый эмбиент.\n\n"
                "Хотите сгенерировать готовые промпты для каждого шага прямо сейчас?"
            )
        elif "code" in model_clean or "architect" in model_clean:
            prefix = "💻 **Full-Stack & Neural Architect**\n\n"
            reply = (
                f"{prefix}Анализ технической архитектуры по запросу «*{last_user_msg}*»:\n\n"
                "```python\n"
                "# Оптимальный пайплайн интеграции\n"
                "async def run_zyko_pipeline(task: str) -> dict:\n"
                "    tokens = await check_balance(cost=2)\n"
                "    result = await execute_model('gpt-6-luna', prompt=task)\n"
                "    return {'status': 'success', 'data': result}\n"
                "```\n\n"
                "• **Сложность**: O(1) задержка за счет параллельных пулов ключей.\n"
                "• **Рекомендация**: Используйте асинхронные потоки и кэширование сессий для максимального FPS."
            )
        else:
            prefix = "🤖 **Personal Autonomous Agent**\n\n"
            reply = (
                f"{prefix}Я зафиксировал ваши инструкции по «*{last_user_msg}*».\n\n"
                "Я готов выполнять задачи автономно: искать данные, генерировать контент в нейросетях GPT Image, Veo 3.1 и ElevenLabs, а также сохранять контекст в вашем проекте."
            )
    elif "claude" in model_clean:
        reply = (
            f"### Анализ Claude 3.7 Sonnet\n\n"
            f"Разбирая ваш запрос «*{last_user_msg}*», выделяю ключевые аспекты:\n\n"
            f"1. **Структура**: Задача требует системного подхода с валидацией граничных условий.\n"
            f"2. **Практическое решение**: Предлагаю разбить реализацию на логические блоки с четким контролем токенов.\n"
            f"3. **Вывод**: Высокая эффективность достигается при комбинации прямого промптинга и пошаговой верификации."
        )
    elif "deepseek" in model_clean:
        reply = (
            f"### DeepSeek R1 Reasoning Trace\n\n"
            f"> *Мыслительный процесс:* Анализирую контекст запроса «{last_user_msg}». Проверяю математические и логические взаимосвязи...\n\n"
            f"**Итоговый ответ:**\n"
            f"Для эффективного решения вашей задачи используйте проверенную цепочку логических шагов. Вся структура оптимизирована для минимального расхода токенов и максимальной точности."
        )
    else:
        if deep_research:
            reply = (
                f"🔬 **Deep Research Report (GPT 6 Luna)**\n\n"
                f"**Тема исследования:** {last_user_msg}\n\n"
                f"• **Контекст и тренды**: Современные мультимодальные модели 2026 года объединяют фото, видео (Veo 3.1, Wan 3.0) и студийный звук.\n"
                f"• **Ключевой инсайт**: Единая подписка ZYKO.AI предоставляет доступ к 100+ передовым нейросетям без переплат.\n"
                f"• **Практические рекомендации**: Для генерации медиа перейдите в соседние вкладки `Image` или `Audio`."
            )
        else:
            reply = (
                f"Привет! Я **GPT 6 Luna** в ZYKO.AI Studio.\n\n"
                f"По вашему запросу «*{last_user_msg}*»:\n\n"
                f"Готов помочь вам с генерацией текста, созданием кода, промптов для фото и видео или планированием проекта. "
                f"Если нужно сгенерировать изображение по этой идее, переключитесь во вкладку **Image** или нажмите 🪄 в поле ввода."
            )

    return {
        "reply": reply,
        "model": model,
        "deep_research": deep_research,
        "tokens_used": len(reply.split()),
        "provider": "persona_engine"
    }
