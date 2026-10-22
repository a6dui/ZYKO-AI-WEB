import os
import aiosqlite
import logging
import time
import asyncio
import aiohttp
from datetime import datetime, timedelta

logger = logging.getLogger("zyko_db")

# Persistent volume resolution for Zeabur, Railway, Docker
def resolve_db_path() -> str:
    env_path = os.environ.get("DB_PATH")
    if env_path:
        return env_path
    for candidate_dir in ["/data", "/app/data", "data"]:
        try:
            if os.path.exists(candidate_dir) and os.access(candidate_dir, os.W_OK):
                return os.path.join(candidate_dir, "zyko_web.db")
        except Exception:
            pass
    return "zyko_web.db"

DB_PATH = resolve_db_path()

# Configured superadmin telegram IDs
ADMIN_IDS_STR = os.environ.get("ADMIN_IDS", "755843448")
ADMIN_TELEGRAM_IDS = {int(x.strip()) for x in ADMIN_IDS_STR.split(",") if x.strip().isdigit()}
ADMIN_TELEGRAM_IDS.add(755843448)

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE,
                google_id TEXT UNIQUE,
                telegram_id INTEGER UNIQUE,
                telegram_username TEXT,
                name TEXT,
                avatar TEXT,
                tokens INTEGER DEFAULT 10,
                tier TEXT DEFAULT 'Free',
                tier_expires_at TIMESTAMP,
                is_superadmin INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                provider TEXT,
                order_id TEXT UNIQUE,
                amount REAL,
                currency TEXT,
                tokens_credited INTEGER,
                tier_activated TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS generations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                model TEXT,
                prompt TEXT,
                result_url TEXT,
                tokens_cost INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS system_settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            INSERT OR IGNORE INTO system_settings (key, value) VALUES 
            ('banner_text', 'Meet Wan 3.0: Up to 30 Seconds of Limitless Creativity'),
            ('banner_link_tab', 'video'),
            ('announcement_active', 'true'),
            ('cost_image', '12'),
            ('cost_video', '25'),
            ('cost_music', '20'),
            ('cost_tts', '2'),
            ('cost_clone', '50'),
            ('cost_isolate', '15'),
            ('cost_sts', '15'),
            ('starting_tokens', '10'),
            ('welcome_bonus', '10'),
            ('maintenance_mode', 'false'),
            ('maintenance_message', 'Платформа ZYKO.AI обновляется. Мы скоро вернемся!'),
            ('llm_base_url', 'https://api.origapi.net/v1'),
            ('llm_api_key', ''),
            ('llm_default_model', 'gpt-4o-mini')
        """)
        # Ensure additional columns exist on older tables
        try:
            await db.execute("ALTER TABLE generations ADD COLUMN is_hidden INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE generations ADD COLUMN is_featured_trend INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN telegram_username TEXT")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN is_superadmin INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN referred_by INTEGER")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE transactions ADD COLUMN receipt_url TEXT")
        except Exception:
            pass

        # Create referral rewards system table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS referral_rewards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER NOT NULL,
                referred_id INTEGER NOT NULL,
                tokens_amount REAL NOT NULL,
                reward_type TEXT DEFAULT 'signup',
                order_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (referrer_id) REFERENCES users (id),
                FOREIGN KEY (referred_id) REFERENCES users (id)
            )
        """)

        # Create promo codes system tables
        await db.execute("""
            CREATE TABLE IF NOT EXISTS promo_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                tokens INTEGER NOT NULL DEFAULT 500,
                tier TEXT,
                max_uses INTEGER DEFAULT 100,
                times_used INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS promo_usages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                promo_code_id INTEGER,
                used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, promo_code_id)
            )
        """)

        # Create auth sessions table for seamless cross-device Telegram auth
        await db.execute("""
            CREATE TABLE IF NOT EXISTS auth_sessions (
                code TEXT PRIMARY KEY,
                status TEXT DEFAULT 'pending',
                token TEXT,
                user_id INTEGER,
                created_at REAL,
                host TEXT
            )
        """)

        # Create guest quotas table (strictly 10 tokens total for unregistered visitors)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS guest_quotas (
                identifier TEXT PRIMARY KEY,
                tokens_remaining INTEGER DEFAULT 10,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Ensure owner / superadmin 755843448 and any ADMIN_TELEGRAM_IDS is unified as superadmin with Max tier and at least 500,000 tokens
        try:
            for admin_tg_id in ADMIN_TELEGRAM_IDS:
                async with db.execute(
                    "SELECT * FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ? OR LOWER(email) = 'foryafrom@gmail.com' OR LOWER(telegram_username) = 'zykolandstore' ORDER BY id ASC LIMIT 1",
                    (admin_tg_id, str(admin_tg_id))
                ) as c_admin:
                    admin_row = await c_admin.fetchone()
                    if not admin_row:
                        await db.execute("""
                            INSERT INTO users (telegram_id, telegram_username, email, name, tokens, tier, is_superadmin)
                            VALUES (?, 'Zykolandstore', 'foryafrom@gmail.com', 'Дима', 500000, 'Max', 1)
                        """, (admin_tg_id,))
                    else:
                        await db.execute("""
                            UPDATE users SET is_superadmin = 1, tier = 'Max',
                                tokens = COALESCE(tokens, 500000),
                                telegram_id = COALESCE(telegram_id, ?),
                                telegram_username = COALESCE(telegram_username, 'Zykolandstore'),
                                email = COALESCE(email, 'foryafrom@gmail.com'),
                                name = COALESCE(name, 'Дима')
                            WHERE id = ?
                        """, (admin_tg_id, admin_row["id"]))

            # Also ensure all superadmins and demo accounts have at least initial tokens and Max tier
            await db.execute("""
                UPDATE users SET tokens = COALESCE(tokens, 500000),
                    tier = 'Max', is_superadmin = 1
                WHERE is_superadmin = 1 OR email IN ('foryafrom@gmail.com', 'demo@zyko.ai')
            """)
        except Exception as e:
            logger.warning("Admin seeding warning: %s", e)

        await db.commit()
        logger.info("Database initialized successfully at %s", DB_PATH)

async def get_user_by_email(email: str) -> dict:
    if not email:
        return None
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE LOWER(email) = LOWER(?)", (email.strip(),)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def create_email_user(email: str, password_hash: str, name: str = None) -> dict:
    clean_email = email.strip().lower()
    display_name = name or clean_email.split("@")[0].capitalize()
    sys_settings = await get_system_settings()
    try:
        bonus = int(float(sys_settings.get("starting_tokens") or sys_settings.get("welcome_bonus") or 10))
    except Exception:
        bonus = 10
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "INSERT INTO users (email, name, password_hash, tokens, tier) VALUES (?, ?, ?, ?, 'Free')",
            (clean_email, display_name, password_hash, bonus)
        )
        await db.commit()
        user_id = cursor.lastrowid
        async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as c2:
            return dict(await c2.fetchone())

async def set_user_password(user_id: int, password_hash: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
        await db.commit()

async def get_or_create_google_user(google_id: str, email: str, name: str, avatar: str) -> dict:
    clean_email = email.strip().lower() if email else ""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE google_id = ? OR (email IS NOT NULL AND LOWER(email) = ?) ORDER BY id ASC LIMIT 1",
            (google_id, clean_email)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                user = dict(row)
                await db.execute(
                    "UPDATE users SET last_login = CURRENT_TIMESTAMP, name = COALESCE(?, name), avatar = COALESCE(?, avatar), google_id = COALESCE(?, google_id), email = COALESCE(?, email) WHERE id = ?",
                    (name, avatar, google_id, clean_email, user["id"])
                )
                await db.commit()
                async with db.execute("SELECT * FROM users WHERE id = ?", (user["id"],)) as c2:
                    return dict(await c2.fetchone())
            else:
                sys_settings = await get_system_settings()
                try:
                    bonus = int(float(sys_settings.get("starting_tokens") or sys_settings.get("welcome_bonus") or 10))
                except Exception:
                    bonus = 10
                cursor_ins = await db.execute(
                    "INSERT INTO users (google_id, email, name, avatar, tokens, tier) VALUES (?, ?, ?, ?, ?, 'Free')",
                    (google_id, clean_email, name, avatar, bonus)
                )
                await db.commit()
                user_id = cursor_ins.lastrowid
                async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as c2:
                    return dict(await c2.fetchone())

async def get_or_create_telegram_user(telegram_id: int, username: str = "", first_name: str = "", photo_url: str = "") -> dict:
    if not telegram_id:
        return None
    telegram_id = int(telegram_id)
    clean_username = username.lstrip("@").strip() if username else ""
    display_name = first_name or (f"@{clean_username}" if clean_username else f"User {telegram_id}")
    is_admin = 1 if telegram_id in ADMIN_TELEGRAM_IDS or clean_username.lower() in ("zykolandstore", "zykoai_bot") else 0
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        
        # 1. Exact match by telegram_id
        row = None
        async with db.execute(
            "SELECT * FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?",
            (telegram_id, str(telegram_id))
        ) as cursor:
            row = await cursor.fetchone()
            
        # 2. If not found by telegram_id, link by username
        if not row and clean_username:
            async with db.execute(
                "SELECT * FROM users WHERE LOWER(telegram_username) = LOWER(?) ORDER BY id ASC LIMIT 1",
                (clean_username,)
            ) as c2:
                row = await c2.fetchone()
                
        # 3. If admin, link to the unified admin account
        if not row and is_admin:
            async with db.execute(
                "SELECT * FROM users WHERE LOWER(email) = 'foryafrom@gmail.com' OR is_superadmin = 1 ORDER BY id ASC LIMIT 1"
            ) as c3:
                row = await c3.fetchone()
                
        if row:
            user = dict(row)
            updates = ["last_login = CURRENT_TIMESTAMP"]
            params = []
            if not user.get("telegram_id"):
                updates.append("telegram_id = ?")
                params.append(telegram_id)
            if clean_username and not user.get("telegram_username"):
                updates.append("telegram_username = ?")
                params.append(clean_username)
            if display_name and (not user.get("name") or "kirabakuta" in str(user.get("name")).lower()):
                updates.append("name = ?")
                params.append(display_name)
            if photo_url and not user.get("avatar"):
                updates.append("avatar = ?")
                params.append(photo_url)
            if is_admin:
                updates.append("is_superadmin = 1")
                updates.append("tier = 'Max'")
                if user.get("tokens") is None:
                    updates.append("tokens = 500000")
                if not user.get("email"):
                    updates.append("email = 'foryafrom@gmail.com'")
            
            query = f"UPDATE users SET {', '.join(updates)} WHERE id = ?"
            params.append(user["id"])
            await db.execute(query, params)
            await db.commit()
            
            async with db.execute("SELECT * FROM users WHERE id = ?", (user["id"],)) as c_res:
                return dict(await c_res.fetchone())
        else:
            sys_settings = await get_system_settings()
            try:
                bonus = int(float(sys_settings.get("starting_tokens") or sys_settings.get("welcome_bonus") or 10))
            except Exception:
                bonus = 10
            initial_tier = "Max" if is_admin else "Free"
            initial_tokens = 500000 if is_admin else bonus
            cursor_ins = await db.execute(
                """
                INSERT INTO users (telegram_id, telegram_username, name, avatar, tokens, tier, is_superadmin) 
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (telegram_id, clean_username, display_name, photo_url, initial_tokens, initial_tier, is_admin)
            )
            await db.commit()
            user_id = cursor_ins.lastrowid
            async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as c_ins:
                return dict(await c_ins.fetchone())

async def get_or_create_demo_user() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE telegram_id = 755843448 OR email = 'foryafrom@gmail.com' OR is_superadmin = 1 ORDER BY id ASC LIMIT 1") as cursor:
            row = await cursor.fetchone()
            if row:
                user = dict(row)
                if user.get("tokens") is None or user.get("tier") != "Max":
                    await db.execute("UPDATE users SET tokens = COALESCE(tokens, 500000), tier = 'Max', is_superadmin = 1 WHERE id = ?", (user["id"],))
                    await db.commit()
                    if user.get("tokens") is None:
                        user["tokens"] = 500000
                    user["tier"] = "Max"
                    user["is_superadmin"] = 1
                return user
        cursor_ins = await db.execute(
            "INSERT INTO users (telegram_id, telegram_username, email, name, tokens, tier, is_superadmin) VALUES (755843448, 'Zykolandstore', 'foryafrom@gmail.com', 'Дима', 500000, 'Max', 1)"
        )
        await db.commit()
        user_id = cursor_ins.lastrowid
        async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as c2:
            return dict(await c2.fetchone())

async def get_user_by_id(user_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            user = dict(row)
            tg_id = user.get("telegram_id")
            is_admin = bool(user.get("is_superadmin") or (tg_id and int(tg_id) in ADMIN_TELEGRAM_IDS))
            if is_admin and user.get("tokens") is None:
                await db.execute("UPDATE users SET tokens = 500000, tier = 'Max', is_superadmin = 1 WHERE id = ?", (user_id,))
                await db.commit()
                user["tokens"] = 500000
                user["tier"] = "Max"
                user["is_superadmin"] = 1
            elif is_admin and (user.get("tier") != "Max" or not user.get("is_superadmin")):
                await db.execute("UPDATE users SET tier = 'Max', is_superadmin = 1 WHERE id = ?", (user_id,))
                await db.commit()
                user["tier"] = "Max"
                user["is_superadmin"] = 1
            return user

async def add_tokens_and_tier(user_id: int, tokens_to_add: int, tier: str = None) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if tier:
            await db.execute(
                "UPDATE users SET tokens = tokens + ?, tier = ? WHERE id = ?",
                (tokens_to_add, tier, user_id)
            )
        else:
            await db.execute(
                "UPDATE users SET tokens = tokens + ? WHERE id = ?",
                (tokens_to_add, user_id)
            )
        await db.commit()
        async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def deduct_tokens(user_id: int, cost: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT tokens FROM users WHERE id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if not row or row["tokens"] < cost:
                return False
        await db.execute("UPDATE users SET tokens = tokens - ? WHERE id = ?", (cost, user_id))
        await db.commit()
        return True

# --- Telegram Auth Sessions (Cross-Worker / Cross-Device Storage) ---

async def create_auth_session(code: str, host: str = "") -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO auth_sessions (code, status, host, created_at) VALUES (?, 'pending', ?, ?)",
            (code, host, time.time())
        )
        await db.commit()
        return {"code": code, "status": "pending", "host": host}

async def get_auth_session(code: str) -> dict:
    if not code:
        return None
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM auth_sessions WHERE code = ?", (code,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def confirm_auth_session(code: str, user_id: int, token: str) -> bool:
    if not code:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE auth_sessions SET status = 'authenticated', user_id = ?, token = ?, created_at = ? WHERE code = ?",
            (user_id, token, time.time(), code)
        )
        await db.commit()
        return True

async def cleanup_expired_auth_sessions():
    try:
        ten_mins_ago = time.time() - 600
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM auth_sessions WHERE created_at < ?", (ten_mins_ago,))
            await db.commit()
    except Exception as e:
        logger.warning("cleanup_expired_auth_sessions error: %s", e)

# --- Guest Quotas (Strictly 10 ZK for unregistered users) ---

async def get_or_init_guest_quota(identifier: str) -> int:
    if not identifier:
        return 10
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT tokens_remaining FROM guest_quotas WHERE identifier = ?", (identifier,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return int(row["tokens_remaining"])
            else:
                await db.execute("INSERT OR IGNORE INTO guest_quotas (identifier, tokens_remaining) VALUES (?, 10)", (identifier,))
                await db.commit()
                return 10

async def deduct_guest_quota(identifier: str, amount: int) -> bool:
    if not identifier:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT tokens_remaining FROM guest_quotas WHERE identifier = ?", (identifier,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                current = 10
                if current < amount:
                    return False
                await db.execute("INSERT INTO guest_quotas (identifier, tokens_remaining) VALUES (?, ?)", (identifier, current - amount))
                await db.commit()
                return True
            current = int(row["tokens_remaining"])
            if current < amount:
                return False
            await db.execute(
                "UPDATE guest_quotas SET tokens_remaining = ?, updated_at = CURRENT_TIMESTAMP WHERE identifier = ?",
                (current - amount, identifier)
            )
            await db.commit()
            return True

async def create_transaction(user_id: int, provider: str, order_id: str, amount: float, currency: str, tokens: int, tier: str, receipt_url: str = None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO transactions 
            (user_id, provider, order_id, amount, currency, tokens_credited, tier_activated, status, receipt_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
        """, (user_id, provider, order_id, amount, currency, tokens, tier, receipt_url))
        await db.commit()

async def complete_transaction(order_id: str, receipt_url: str = None) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM transactions WHERE order_id = ?", (order_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            tx = dict(row)
            if tx["status"] == "completed":
                if receipt_url and not tx.get("receipt_url"):
                    await db.execute("UPDATE transactions SET receipt_url = ? WHERE order_id = ?", (receipt_url, order_id))
                    await db.commit()
                return tx

        if receipt_url:
            await db.execute("UPDATE transactions SET status = 'completed', receipt_url = ? WHERE order_id = ?", (receipt_url, order_id))
        else:
            await db.execute("UPDATE transactions SET status = 'completed' WHERE order_id = ?", (order_id,))

        if tx["user_id"]:
            await db.execute(
                "UPDATE users SET tokens = tokens + ?, tier = COALESCE(?, tier) WHERE id = ?",
                (tx["tokens_credited"], tx["tier_activated"], tx["user_id"])
            )
            # Referral Cashback / Commission: 15% to referrer
            try:
                tokens_credited = float(tx.get("tokens_credited") or 0)
                if tokens_credited > 0:
                    async with db.execute("SELECT referred_by FROM users WHERE id = ?", (tx["user_id"],)) as c_ref:
                        ref_row = await c_ref.fetchone()
                        if ref_row and ref_row[0]:
                            referrer_id = ref_row[0]
                            commission = round(tokens_credited * 0.15)
                            if commission > 0:
                                await db.execute("UPDATE users SET tokens = tokens + ? WHERE id = ?", (commission, referrer_id))
                                await db.execute("""
                                    INSERT INTO referral_rewards (referrer_id, referred_id, tokens_amount, reward_type, order_id)
                                    VALUES (?, ?, ?, 'purchase', ?)
                                """, (referrer_id, tx["user_id"], commission, order_id))
                                logger.info(f"Referral commission awarded: +{commission} ZK to user #{referrer_id} for purchase by #{tx['user_id']}")
            except Exception as e:
                logger.error("Error processing referral commission: %s", e)

        # Send instant Telegram push notification to superadmins
        try:
            user_data = None
            if tx.get("user_id"):
                async with db.execute("SELECT id, name, email, telegram_id, telegram_username FROM users WHERE id = ?", (tx["user_id"],)) as cu:
                    u_row = await cu.fetchone()
                    if u_row:
                        user_data = dict(u_row)
            asyncio.create_task(notify_superadmins_of_payment(tx, user_data))
        except Exception as e_notify:
            logger.error("Error scheduling payment notification: %s", e_notify)

        await db.commit()
        return tx

async def get_user_transactions(user_id: int) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT id, provider, order_id, amount, currency, tokens_credited, 
                      tier_activated, status, receipt_url, created_at 
               FROM transactions 
               WHERE user_id = ? 
               ORDER BY id DESC""",
            (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def log_generation(user_id: int, model: str, prompt: str, result_url: str, cost: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO generations (user_id, model, prompt, result_url, tokens_cost) VALUES (?, ?, ?, ?, ?)",
            (user_id, model, prompt, result_url, cost)
        )
        await db.commit()

async def sync_link_user_accounts(user_id: int, google_email: str = None, telegram_id: int = None, telegram_username: str = None) -> dict:
    """
    Links and synchronizes Google and Telegram accounts, merging balances and subscriptions.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        
        async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as cursor:
            current_user = await cursor.fetchone()
            if not current_user:
                return None
            current_user = dict(current_user)
            
        if google_email:
            clean_email = google_email.strip().lower()
            async with db.execute("SELECT * FROM users WHERE LOWER(email) = LOWER(?) AND id != ?", (clean_email, user_id)) as c_email:
                other_user = await c_email.fetchone()
                if other_user:
                    other_user = dict(other_user)
                    new_tokens = max(float(current_user.get("tokens") or 0), float(other_user.get("tokens") or 0))
                    tg_id = current_user.get("telegram_id") or other_user.get("telegram_id")
                    chosen_tier = other_user.get("tier") if other_user.get("tier") != "Free" else current_user.get("tier", "Free")
                    await db.execute(
                        "UPDATE users SET tokens = ?, telegram_id = COALESCE(?, telegram_id), tier = ? WHERE id = ?",
                        (new_tokens, tg_id, chosen_tier, other_user["id"])
                    )
                    user_id = other_user["id"]
                    await db.commit()
                else:
                    await db.execute("UPDATE users SET email = ? WHERE id = ?", (clean_email, user_id))
                    await db.commit()
                    
        if telegram_id:
            telegram_id = int(telegram_id)
            async with db.execute("SELECT * FROM users WHERE (telegram_id = ? OR CAST(telegram_id AS TEXT) = ?) AND id != ?", (telegram_id, str(telegram_id), user_id)) as c_tg:
                other_tg = await c_tg.fetchone()
                if other_tg:
                    other_tg = dict(other_tg)
                    new_tokens = max(float(current_user.get("tokens") or 0), float(other_tg.get("tokens") or 0))
                    # Preserve higher tier, never downgrade to Free
                    tier_priority = {"Elite": 4, "Max": 3, "VIP": 3, "Pro": 2, "Basic": 1, "Free": 0}
                    t1 = current_user.get("tier", "Free")
                    t2 = other_tg.get("tier", "Free")
                    chosen_tier = t2 if tier_priority.get(t2, 0) >= tier_priority.get(t1, 0) else t1
                    is_admin = 1 if (current_user.get("is_superadmin") or other_tg.get("is_superadmin") or telegram_id in ADMIN_TELEGRAM_IDS) else 0
                    
                    await db.execute(
                        "UPDATE users SET tokens = ?, tier = ?, telegram_id = ?, is_superadmin = ? WHERE id = ?",
                        (new_tokens, chosen_tier, telegram_id, is_admin, user_id)
                    )
                    await db.execute("DELETE FROM users WHERE id = ?", (other_tg["id"],))
                    await db.commit()
                else:
                    is_admin = 1 if telegram_id in ADMIN_TELEGRAM_IDS else 0
                    await db.execute(
                        "UPDATE users SET telegram_id = ?, is_superadmin = MAX(COALESCE(is_superadmin, 0), ?) WHERE id = ?",
                        (telegram_id, is_admin, user_id)
                    )
                    await db.commit()
                    
        async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as c_final:
            row = await c_final.fetchone()
            return dict(row) if row else None

# --- Admin Operations ---

async def get_admin_overview():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT COUNT(*) as count FROM users") as c:
            total_users = (await c.fetchone())["count"]
            
        async with db.execute("SELECT SUM(tokens) as total_tokens FROM users") as c:
            tokens_row = await c.fetchone()
            total_tokens = tokens_row["total_tokens"] or 0
            
        async with db.execute("SELECT tier, COUNT(*) as count FROM users GROUP BY tier") as c:
            tier_rows = await c.fetchall()
            tier_dist = {r["tier"]: r["count"] for r in tier_rows}
            
        async with db.execute("SELECT COUNT(*) as count, SUM(amount) as revenue FROM transactions WHERE status = 'completed'") as c:
            tx_row = await c.fetchone()
            completed_tx_count = tx_row["count"] or 0
            total_revenue = round(tx_row["revenue"] or 0, 2)
            
        async with db.execute("SELECT COUNT(*) as count FROM generations") as c:
            total_generations = (await c.fetchone())["count"]
            
        return {
            "total_users": total_users,
            "total_tokens": total_tokens,
            "tier_distribution": tier_dist,
            "completed_transactions": completed_tx_count,
            "total_revenue": total_revenue,
            "total_generations": total_generations
        }

async def get_admin_users(search=None, tier=None, limit=100, offset=0):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT * FROM users WHERE 1=1"
        params = []
        if search:
            query += " AND (name LIKE ? OR email LIKE ? OR CAST(telegram_id AS TEXT) LIKE ?)"
            s = f"%{search}%"
            params.extend([s, s, s])
        if tier and tier != 'All':
            query += " AND tier = ?"
            params.append(tier)
        query += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def admin_update_user(user_id: int, tokens: float = None, tier: str = None) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if tokens is not None and tier is not None:
            await db.execute("UPDATE users SET tokens = ?, tier = ? WHERE id = ?", (tokens, tier, user_id))
        elif tokens is not None:
            await db.execute("UPDATE users SET tokens = ? WHERE id = ?", (tokens, user_id))
        elif tier is not None:
            await db.execute("UPDATE users SET tier = ? WHERE id = ?", (tier, user_id))
        await db.commit()
        async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as c:
            row = await c.fetchone()
            return dict(row) if row else None

async def admin_gift_all_users(token_amount: float) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("UPDATE users SET tokens = tokens + ?", (token_amount,))
        await db.commit()
        return cursor.rowcount

async def get_admin_transactions(limit=100, offset=0):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT t.*, u.name as user_name, u.email as user_email 
            FROM transactions t 
            LEFT JOIN users u ON t.user_id = u.id 
            ORDER BY t.id DESC LIMIT ? OFFSET ?
        """, (limit, offset)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def get_admin_generations(limit=50):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT g.*, u.name as user_name, u.email as user_email 
            FROM generations g 
            LEFT JOIN users u ON g.user_id = u.id 
            ORDER BY g.id DESC LIMIT ?
        """, (limit,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def get_system_settings() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT key, value FROM system_settings") as cursor:
            rows = await cursor.fetchall()
            return {r["key"]: r["value"] for r in rows}

async def update_system_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO system_settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)", (key, value))
        await db.commit()

async def get_telegram_broadcast_recipients(tier_filter: str = "all") -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT id, telegram_id, name, telegram_username, tier FROM users WHERE telegram_id IS NOT NULL AND is_banned = 0"
        params = []
        if tier_filter == "free":
            query += " AND (tier = 'Free' OR tier IS NULL)"
        elif tier_filter == "paid":
            query += " AND tier != 'Free' AND tier IS NOT NULL"
        elif tier_filter and tier_filter != "all":
            query += " AND tier = ?"
            params.append(tier_filter)
        async with db.execute(query, tuple(params)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def get_admin_studio_analytics() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Last 24h by model
        async with db.execute("""
            SELECT model, COUNT(*) as count, SUM(tokens_cost) as tokens 
            FROM generations 
            WHERE created_at >= datetime('now', '-24 hours') 
            GROUP BY model
        """) as c24:
            rows24 = [dict(r) for r in await c24.fetchall()]
        
        studios_24h = {"video": 0, "image": 0, "audio": 0, "llm": 0, "other": 0}
        tokens_24h = {"video": 0, "image": 0, "audio": 0, "llm": 0, "other": 0}
        for r in rows24:
            m = (r.get("model") or "").lower()
            cnt = r.get("count", 0)
            tok = r.get("tokens", 0) or 0
            if any(k in m for k in ["video", "veo", "kling", "sora", "wan"]):
                studios_24h["video"] += cnt
                tokens_24h["video"] += tok
            elif any(k in m for k in ["image", "flux", "midjourney", "dall", "photo"]):
                studios_24h["image"] += cnt
                tokens_24h["image"] += tok
            elif any(k in m for k in ["audio", "tts", "music", "sfx", "clone", "sts", "isolate"]):
                studios_24h["audio"] += cnt
                tokens_24h["audio"] += tok
            elif any(k in m for k in ["gpt", "claude", "gemini", "chat", "llm"]):
                studios_24h["llm"] += cnt
                tokens_24h["llm"] += tok
            else:
                studios_24h["other"] += cnt
                tokens_24h["other"] += tok

        # All-time by studio
        async with db.execute("""
            SELECT 
                SUM(CASE WHEN model LIKE '%video%' OR model LIKE '%veo%' OR model LIKE '%kling%' OR model LIKE '%sora%' THEN 1 ELSE 0 END) as video_count,
                SUM(CASE WHEN model LIKE '%image%' OR model LIKE '%flux%' OR model LIKE '%midjourney%' THEN 1 ELSE 0 END) as image_count,
                SUM(CASE WHEN model LIKE '%audio%' OR model LIKE '%tts%' OR model LIKE '%music%' OR model LIKE '%sfx%' OR model LIKE '%clone%' OR model LIKE '%sts%' THEN 1 ELSE 0 END) as audio_count,
                SUM(CASE WHEN model LIKE '%chat%' OR model LIKE '%gemini%' OR model LIKE '%gpt%' OR model LIKE '%claude%' THEN 1 ELSE 0 END) as llm_count,
                COUNT(*) as total_count
            FROM generations
        """) as ct:
            row_all = await ct.fetchone()
            studios_all_time = dict(row_all) if row_all else {}

        # Daily trend last 7 days
        async with db.execute("""
            SELECT strftime('%Y-%m-%d', created_at) as day, COUNT(*) as count 
            FROM generations 
            WHERE created_at >= datetime('now', '-7 days') 
            GROUP BY day ORDER BY day ASC
        """) as cd:
            daily_trend = [dict(r) for r in await cd.fetchall()]

        return {
            "studios_24h": studios_24h,
            "tokens_24h": tokens_24h,
            "studios_all_time": studios_all_time,
            "daily_trend": daily_trend
        }

async def admin_toggle_generation_hidden(generation_id: int, is_hidden: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE generations SET is_hidden = ? WHERE id = ?", (is_hidden, generation_id))
        await db.commit()
        return True

async def admin_quick_grant(identifier: str, tokens: float = None, tier: str = None, days: int = 30) -> dict:
    clean_id = (identifier or "").strip().lstrip("@")
    if not clean_id:
        return {"error": "Identifier required"}

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        query = """
            SELECT * FROM users WHERE 
                telegram_username LIKE ? OR 
                CAST(telegram_id AS TEXT) = ? OR 
                email = ? OR 
                CAST(id AS TEXT) = ?
            LIMIT 1
        """
        async with db.execute(query, (f"%{clean_id}%", clean_id, clean_id, clean_id)) as c:
            user = await c.fetchone()
        
        if not user:
            return {"error": f"Пользователь '{identifier}' не найден в базе"}

        user_dict = dict(user)
        user_id = user_dict["id"]
        
        updates = []
        params = []
        
        if tokens is not None:
            updates.append("tokens = tokens + ?")
            params.append(float(tokens))
            
        if tier:
            updates.append("tier = ?")
            params.append(tier)
            expires = datetime.now() + timedelta(days=days)
            updates.append("tier_expires_at = ?")
            params.append(expires.isoformat())

        if updates:
            params.append(user_id)
            await db.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", tuple(params))
            await db.commit()
            
        async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as c2:
            updated_user = await c2.fetchone()
            return {"status": "ok", "user": dict(updated_user)}

TIER_LIMITS = {
    "Free": {"session": 150, "weekly": 600},
    "Basic": {"session": 500, "weekly": 2000},
    "Pro": {"session": 1200, "weekly": 5000},
    "VIP": {"session": 3000, "weekly": 12000},
    "Elite": {"session": 5000, "weekly": 20000},
    "Ultra Elite": {"session": 8000, "weekly": 30000},
    "Max": {"session": 500000, "weekly": 2000000},
    "MAX": {"session": 500000, "weekly": 2000000},
}

async def get_user_usage_stats(user_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        tier = "Free"
        async with db.execute("SELECT tier FROM users WHERE id = ?", (user_id,)) as cursor:
            user_row = await cursor.fetchone()
            if user_row and user_row["tier"]:
                tier = user_row["tier"]
        
        limits = TIER_LIMITS.get(tier, TIER_LIMITS.get(tier.upper(), TIER_LIMITS.get(tier.title(), TIER_LIMITS["Free"])))
        session_limit = limits["session"]
        weekly_limit = limits["weekly"]

        # 24h usage for current session
        async with db.execute(
            "SELECT COALESCE(SUM(tokens_cost), 0) as session_spent FROM generations WHERE user_id = ? AND created_at >= datetime('now', '-24 hours')",
            (user_id,)
        ) as cursor:
            row_session = await cursor.fetchone()
            session_spent = int(row_session["session_spent"]) if row_session else 0

        # Weekly usage (last 7 days)
        async with db.execute(
            "SELECT COALESCE(SUM(tokens_cost), 0) as weekly_spent FROM generations WHERE user_id = ? AND created_at >= datetime('now', '-7 days')",
            (user_id,)
        ) as cursor:
            row_weekly = await cursor.fetchone()
            weekly_spent = int(row_weekly["weekly_spent"]) if row_weekly else 0

        session_pct = min(100, round((session_spent / session_limit) * 100)) if session_limit > 0 else 0
        weekly_pct = min(100, round((weekly_spent / weekly_limit) * 100)) if weekly_limit > 0 else 0

        return {
            "tier": tier,
            "session_spent": session_spent,
            "session_limit": session_limit,
            "session_pct": session_pct,
            "weekly_spent": weekly_spent,
            "weekly_limit": weekly_limit,
            "weekly_pct": weekly_pct
        }

async def get_user_generations(user_id: int, gen_type: str = None, limit: int = 100) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT * FROM generations WHERE user_id = ?"
        params = [user_id]

        if gen_type and gen_type != 'all':
            if gen_type == 'image':
                query += " AND (model LIKE 'image_%' OR model LIKE '%flux%' OR model LIKE '%midjourney%' OR model LIKE '%seedream%' OR model LIKE '%recraft%' OR model LIKE '%imagen%' OR model LIKE '%banana%')"
            elif gen_type == 'video':
                query += " AND (model LIKE 'video_%' OR model LIKE '%kling%' OR model LIKE '%runway%' OR model LIKE '%luma%' OR model LIKE '%wan%' OR model LIKE '%seedance%' OR model LIKE '%minimax%' OR model LIKE '%veo%')"
            elif gen_type == 'audio':
                query += " AND (model LIKE 'elevenlabs_%' OR model LIKE '%audio%' OR model LIKE '%music%' OR model LIKE '%tts%' OR model LIKE '%sfx%' OR model LIKE '%clone%' OR model LIKE '%isolate%')"
            elif gen_type == 'text':
                query += " AND (model LIKE 'llm_%' OR model LIKE '%chat%' OR model LIKE '%gemini%' OR model LIKE '%gpt%' OR model LIKE '%claude%' OR model LIKE '%deepseek%' OR model LIKE '%agent%')"

        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        async with db.execute(query, tuple(params)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def get_user_token_history(user_id: int, filter_type: str = 'all', limit: int = 100) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        
        current_balance = 1000
        user_created_at = None
        user_tier = "Free"
        async with db.execute("SELECT tokens, created_at, tier FROM users WHERE id = ?", (user_id,)) as cursor:
            user_row = await cursor.fetchone()
            if user_row:
                current_balance = int(user_row["tokens"] if user_row["tokens"] is not None else 1000)
                user_created_at = user_row["created_at"]
                user_tier = user_row["tier"] or "Free"

        events = []

        # 1. Transactions (Top ups / Credits)
        async with db.execute(
            "SELECT * FROM transactions WHERE user_id = ? AND status = 'completed' ORDER BY id DESC",
            (user_id,)
        ) as cursor:
            tx_rows = await cursor.fetchall()
            for tx in tx_rows:
                events.append({
                    "id": f"tx_{tx['id']}",
                    "type": "added",
                    "description": f"Subscription {tx['tier_activated'] or 'Credits'} ({tx['provider'].capitalize()})",
                    "amount": int(tx["tokens_credited"] or 0),
                    "created_at": tx["created_at"]
                })

        # 2. Generations (Tokens used)
        async with db.execute(
            "SELECT * FROM generations WHERE user_id = ? ORDER BY id DESC",
            (user_id,)
        ) as cursor:
            gen_rows = await cursor.fetchall()
            for g in gen_rows:
                m = (g["model"] or "").lower()
                cost = int(g["tokens_cost"] or 0)
                prompt_snippet = (g["prompt"] or "").strip()[:40]
                if len((g["prompt"] or "").strip()) > 40:
                    prompt_snippet += "..."

                if m.startswith("image_"):
                    model_clean = g["model"][6:].upper()
                    desc = f"Image Generation • {model_clean} ({prompt_snippet})"
                elif m.startswith("video_"):
                    model_clean = g["model"][6:].upper()
                    desc = f"Video Generation • {model_clean} ({prompt_snippet})"
                elif m.startswith("llm_"):
                    model_clean = g["model"][4:].upper()
                    desc = f"Chat Query • {model_clean} ({prompt_snippet})"
                elif "tts" in m:
                    desc = f"Voice Synthesis TTS ({prompt_snippet})"
                elif "sfx" in m:
                    desc = f"Sound Effect SFX ({prompt_snippet})"
                elif "music" in m:
                    desc = f"AI Music Generation ({prompt_snippet})"
                elif "isolate" in m:
                    desc = f"Voice Isolation ({prompt_snippet})"
                elif "clone" in m:
                    desc = f"Voice Clone ({prompt_snippet})"
                else:
                    desc = f"AI Generation • {g['model']} ({prompt_snippet})"

                events.append({
                    "id": f"gen_{g['id']}",
                    "type": "used",
                    "description": desc,
                    "amount": -cost,
                    "created_at": g["created_at"]
                })

        # 3. Initial welcome bonus or plan allocation if user exists
        if user_created_at:
            sys_settings = await get_system_settings()
            try:
                dyn_bonus = int(float(sys_settings.get("starting_tokens") or sys_settings.get("welcome_bonus") or 10))
            except Exception:
                dyn_bonus = 10
            if current_balance > dyn_bonus and not tx_rows and user_tier != 'Free':
                bonus_desc = f"{user_tier} Plan Initial Allocation"
                bonus_amt = current_balance
            else:
                bonus_desc = "Welcome Starter Gift Bonus"
                bonus_amt = dyn_bonus

            events.append({
                "id": "init_bonus",
                "type": "added",
                "description": bonus_desc,
                "amount": bonus_amt,
                "created_at": user_created_at
            })

        # Sort all events chronologically (newest first)
        events.sort(key=lambda x: str(x["created_at"]), reverse=True)

        # Calculate backward running balances so the latest matches current_balance
        running = current_balance
        for ev in events:
            ev["balance"] = running
            running = max(0, running - ev["amount"])

        # Filter by type
        if filter_type == "added":
            filtered = [e for e in events if e["type"] == "added"]
        elif filter_type == "used":
            filtered = [e for e in events if e["type"] == "used"]
        else:
            filtered = events

        return filtered[:limit]

async def admin_toggle_ban_user(user_id: int, is_banned: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("UPDATE users SET is_banned = ? WHERE id = ?", (1 if is_banned else 0, user_id))
        await db.commit()
        async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as c:
            row = await c.fetchone()
            return dict(row) if row else None

async def admin_adjust_user_tokens(user_id: int, delta: float, reason: str = "Admin adjustment") -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("UPDATE users SET tokens = MAX(0, tokens + ?) WHERE id = ?", (delta, user_id))
        await db.commit()
        async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as c:
            row = await c.fetchone()
            return dict(row) if row else None

async def admin_get_promo_codes() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM promo_codes ORDER BY created_at DESC") as c:
            rows = await c.fetchall()
            return [dict(r) for r in rows]

async def admin_create_promo(code: str, tokens: int = 500, tier: str = None, max_uses: int = 100) -> dict:
    clean_code = code.strip().upper()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            INSERT INTO promo_codes (code, tokens, tier, max_uses, times_used, is_active)
            VALUES (?, ?, ?, ?, 0, 1)
            """,
            (clean_code, tokens, tier if tier else None, max_uses)
        )
        await db.commit()
        promo_id = cursor.lastrowid
        async with db.execute("SELECT * FROM promo_codes WHERE id = ?", (promo_id,)) as c:
            return dict(await c.fetchone())

async def admin_toggle_promo(promo_id: int, is_active: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("UPDATE promo_codes SET is_active = ? WHERE id = ?", (1 if is_active else 0, promo_id))
        await db.commit()
        async with db.execute("SELECT * FROM promo_codes WHERE id = ?", (promo_id,)) as c:
            row = await c.fetchone()
            return dict(row) if row else None

async def redeem_promo_code(user_id: int, code: str) -> dict:
    clean_code = code.strip().upper()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM promo_codes WHERE UPPER(code) = ? AND is_active = 1", (clean_code,)) as c:
            promo = await c.fetchone()
            if not promo:
                raise ValueError("Промокод не найден или неактивен")
            promo = dict(promo)

        if promo["max_uses"] and promo["times_used"] >= promo["max_uses"]:
            raise ValueError("Лимит активаций этого промокода исчерпан")

        # Check if user already used this promo
        async with db.execute(
            "SELECT id FROM promo_usages WHERE user_id = ? AND promo_code_id = ?",
            (user_id, promo["id"])
        ) as c_used:
            if await c_used.fetchone():
                raise ValueError("Вы уже активировали этот промокод ранее")

        # Record usage
        await db.execute(
            "INSERT INTO promo_usages (user_id, promo_code_id) VALUES (?, ?)",
            (user_id, promo["id"])
        )
        await db.execute(
            "UPDATE promo_codes SET times_used = times_used + 1 WHERE id = ?",
            (promo["id"],)
        )

        # Credit tokens & tier
        tokens_to_add = promo["tokens"] or 0
        tier_to_set = promo["tier"]
        if tier_to_set:
            await db.execute(
                "UPDATE users SET tokens = tokens + ?, tier = ? WHERE id = ?",
                (tokens_to_add, tier_to_set, user_id)
            )
        else:
            await db.execute(
                "UPDATE users SET tokens = tokens + ? WHERE id = ?",
                (tokens_to_add, user_id)
            )
        await db.commit()

        async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as c_u:
            updated_user = dict(await c_u.fetchone())

        return {
            "status": "ok",
            "message": f"Промокод успешно активирован! Начислено {tokens_to_add} ZK",
            "tokens_added": tokens_to_add,
            "tier_activated": tier_to_set,
            "user": updated_user
        }

# =========================================================================
# REFERRAL SYSTEM BACKEND
# =========================================================================

async def set_user_referrer(user_id: int, referrer_ident) -> bool:
    """Sets the referrer for a new user and awards initial signup bonus (500 ZK to each)."""
    if not referrer_ident or not user_id:
        return False
    clean_ident = str(referrer_ident).lower().replace("ref_", "").replace("ref", "").replace("r_", "").replace("r", "").strip()
    if not clean_ident:
        return False
        
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        
        # Check if user already has referred_by
        async with db.execute("SELECT id, referred_by FROM users WHERE id = ?", (user_id,)) as c:
            user = await c.fetchone()
            if not user or user["referred_by"]:
                return False
                
        # Find referrer by id, telegram_id, or telegram_username
        referrer = None
        if clean_ident.isdigit():
            num_id = int(clean_ident)
            async with db.execute("SELECT * FROM users WHERE id = ? OR telegram_id = ?", (num_id, num_id)) as cr:
                referrer = await cr.fetchone()
        if not referrer:
            async with db.execute("SELECT * FROM users WHERE LOWER(telegram_username) = LOWER(?)", (clean_ident.lstrip("@"),)) as cr2:
                referrer = await cr2.fetchone()
                
        if not referrer:
            return False
            
        referrer_id = referrer["id"]
        if referrer_id == user_id:
            return False # cannot refer oneself
            
        # Set referred_by
        await db.execute("UPDATE users SET referred_by = ? WHERE id = ?", (referrer_id, user_id))
        
        # Award welcome signup bonus (500 ZK to user, 500 ZK to referrer)
        signup_bonus = 500
        await db.execute("UPDATE users SET tokens = tokens + ? WHERE id = ?", (signup_bonus, user_id))
        await db.execute("UPDATE users SET tokens = tokens + ? WHERE id = ?", (signup_bonus, referrer_id))
        
        await db.execute("""
            INSERT INTO referral_rewards (referrer_id, referred_id, tokens_amount, reward_type)
            VALUES (?, ?, ?, 'signup')
        """, (referrer_id, user_id, signup_bonus))
        
        await db.commit()
        logger.info(f"Referral registered: user #{user_id} referred by #{referrer_id}, awarded +{signup_bonus} ZK each")
        return True

async def get_user_referrals_stats(user_id: int) -> dict:
    """Returns detailed statistics and referral list for the user."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        
        # Total referrals count
        async with db.execute("SELECT COUNT(*) as cnt FROM users WHERE referred_by = ?", (user_id,)) as c:
            row = await c.fetchone()
            total_invited = row["cnt"] if row else 0
            
        # Total tokens earned from referral_rewards
        async with db.execute("SELECT COALESCE(SUM(tokens_amount), 0) as total_tokens FROM referral_rewards WHERE referrer_id = ?", (user_id,)) as c2:
            row2 = await c2.fetchone()
            partner_tokens = float(row2["total_tokens"] if row2 else 0)
            
        # Recent referrals list
        async with db.execute("""
            SELECT u.id, u.name, u.telegram_username, u.tier, u.created_at,
                   COALESCE(SUM(r.tokens_amount), 0) as earned_tokens
            FROM users u
            LEFT JOIN referral_rewards r ON r.referred_id = u.id AND r.referrer_id = ?
            WHERE u.referred_by = ?
            GROUP BY u.id
            ORDER BY u.id DESC LIMIT 30
        """, (user_id, user_id)) as c3:
            referrals = [dict(r) for r in await c3.fetchall()]
            
        return {
            "total_invited": total_invited,
            "partner_tokens": partner_tokens,
            "available_funds_usd": round(partner_tokens * 0.001, 2),
            "referrals": referrals
        }

async def notify_superadmins_of_payment(tx: dict, user_data: dict = None):
    """
    Sends an instant, beautiful Telegram Push Notification to project administrators
    whenever a payment is completed (Stripe, Platega, CryptoPay, or Admin grant).
    """
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN") or "8824462987:AAFQ_K1QLmi9ZKiyq6gnYnFUGpgBWsXBFqs"
    if not bot_token:
        return

    admin_ids = set()
    admin_ids.add(755843448) # Default Project Owner Telegram ID
    for aid in ADMIN_TELEGRAM_IDS:
        admin_ids.add(aid)

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT telegram_id FROM users WHERE is_superadmin = 1 AND telegram_id IS NOT NULL") as c:
                rows = await c.fetchall()
                for r in rows:
                    if r["telegram_id"]:
                        try:
                            admin_ids.add(int(r["telegram_id"]))
                        except Exception:
                            pass
    except Exception as e:
        logger.error("Error retrieving admin telegram IDs: %s", e)

    user_label = "Клиент"
    user_id = tx.get("user_id") or "—"
    if user_data:
        tg_u = user_data.get("telegram_username")
        name = user_data.get("name") or user_data.get("email") or f"ID #{user_id}"
        if tg_u:
            user_label = f"@{tg_u} ({name})"
        else:
            user_label = name

    provider_names = {
        "stripe": "Stripe Card / Apple Pay 💳",
        "platega": "Platega (СБП / РФ Карты) 🇷🇺",
        "cryptopay": "CryptoPay (USDT / TON / BTC) 💎",
        "admin": "Админ-Панель (Ручное) 🛠️"
    }
    prov = (tx.get("provider") or "stripe").lower()
    prov_label = provider_names.get(prov, prov.capitalize())

    tier_plan = tx.get("tier_activated") or "Пакет токенов ZK"
    tokens = int(tx.get("tokens_credited") or 0)
    amount = tx.get("amount") or 0
    curr = (tx.get("currency") or "EUR").upper()
    curr_sym = "€" if curr == "EUR" else ("₽" if curr in ("RUB", "RUBLE") else curr)
    order_id = tx.get("order_id") or "—"
    receipt_url = tx.get("receipt_url")

    msg_text = (
        f"💰 <b>Новая оплата на ZYKO.STUDY!</b>\n\n"
        f"👤 <b>Пользователь:</b> {user_label} (ID #{user_id})\n"
        f"💎 <b>Тариф:</b> <b>{tier_plan}</b>\n"
        f"💵 <b>Сумма:</b> <b>{amount} {curr_sym}</b>\n"
        f"⚡ <b>Начислено:</b> <b>+{tokens:,} ZK</b>\n"
        f"💳 <b>Метод:</b> {prov_label}\n"
        f"🧾 <b>ID заказа:</b> <code>{order_id}</code>"
    )

    payload = {
        "text": msg_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if receipt_url and str(receipt_url).startswith("http"):
        payload["reply_markup"] = {
            "inline_keyboard": [[
                {"text": "📄 Посмотреть чек Stripe / Инвойс", "url": receipt_url}
            ]]
        }

    async with aiohttp.ClientSession() as session:
        for tg_id in admin_ids:
            try:
                p = dict(payload)
                p["chat_id"] = tg_id
                async with session.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json=p,
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        logger.info("Admin payment push successfully sent to TG chat %s", tg_id)
            except Exception as ex:
                logger.error("Failed sending admin payment push to tg %s: %s", tg_id, ex)

async def admin_toggle_generation_trend(generation_id: int, is_featured: int) -> bool:
    """Toggles whether a generation is curated and displayed in the main Trends showcase."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE generations SET is_featured_trend = ? WHERE id = ?", (is_featured, generation_id))
        await db.commit()
        return True

async def get_public_featured_trends(limit: int = 24) -> list:
    """Fetches administrator-curated generations for the public showcase."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT g.id, g.model, g.prompt, g.result_url, g.created_at, g.tokens_cost,
                   u.name as user_name, u.telegram_username
            FROM generations g
            LEFT JOIN users u ON g.user_id = u.id
            WHERE g.is_featured_trend = 1 AND (g.is_hidden = 0 OR g.is_hidden IS NULL)
            ORDER BY g.id DESC
            LIMIT ?
        """, (limit,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def get_admin_top_spenders(hours: int = 24, limit: int = 10) -> list:
    """Fetches users with the highest token burn in the last N hours for anti-abuse & cost monitoring."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        query = """
            SELECT 
                u.id as user_id,
                u.name,
                u.email,
                u.telegram_username,
                u.telegram_id,
                u.tier,
                u.tokens as current_tokens,
                u.is_banned,
                COUNT(g.id) as total_gens,
                COALESCE(SUM(g.tokens_cost), 0) as tokens_spent,
                MAX(g.created_at) as last_gen_at
            FROM generations g
            JOIN users u ON g.user_id = u.id
            WHERE g.created_at >= datetime('now', ?)
            GROUP BY u.id
            ORDER BY tokens_spent DESC
            LIMIT ?
        """
        async with db.execute(query, (f"-{hours} hours", limit)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]



