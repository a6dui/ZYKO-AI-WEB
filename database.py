import os
import aiosqlite
import logging

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
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE,
                google_id TEXT UNIQUE,
                telegram_id INTEGER UNIQUE,
                telegram_username TEXT,
                name TEXT,
                avatar TEXT,
                tokens INTEGER DEFAULT 1000,
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
            ('announcement_active', 'true')
        """)
        # Ensure additional columns exist on older tables
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

        # Ensure owner / superadmin 755843448 and any ADMIN_TELEGRAM_IDS is superadmin with Max tier and at least 500,000 tokens
        try:
            for admin_tg_id in ADMIN_TELEGRAM_IDS:
                async with db.execute(
                    "SELECT * FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?",
                    (admin_tg_id, str(admin_tg_id))
                ) as c_admin:
                    admin_row = await c_admin.fetchone()
                    if not admin_row:
                        await db.execute("""
                            INSERT INTO users (telegram_id, telegram_username, name, tokens, tier, is_superadmin)
                            VALUES (?, 'Zykolandstore', 'Zykolandstore', 500000, 'Max', 1)
                        """, (admin_tg_id,))
                    else:
                        await db.execute("""
                            UPDATE users SET is_superadmin = 1, tier = 'Max',
                                tokens = CASE WHEN tokens < 500000 THEN 500000 ELSE tokens END,
                                telegram_username = COALESCE(telegram_username, 'Zykolandstore'),
                                name = COALESCE(name, 'Zykolandstore')
                            WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?
                        """, (admin_tg_id, str(admin_tg_id)))

            # Also ensure all superadmins and demo accounts have at least 500,000 tokens and Max tier
            await db.execute("""
                UPDATE users SET tokens = CASE WHEN tokens < 500000 THEN 500000 ELSE tokens END,
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
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "INSERT INTO users (email, name, password_hash, tokens, tier) VALUES (?, ?, ?, 1000, 'Free')",
            (clean_email, display_name, password_hash)
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
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE google_id = ? OR email = ?", (google_id, email)) as cursor:
            row = await cursor.fetchone()
            if row:
                user = dict(row)
                await db.execute(
                    "UPDATE users SET last_login = CURRENT_TIMESTAMP, name = COALESCE(?, name), avatar = COALESCE(?, avatar), google_id = COALESCE(?, google_id) WHERE id = ?",
                    (name, avatar, google_id, user["id"])
                )
                await db.commit()
                async with db.execute("SELECT * FROM users WHERE id = ?", (user["id"],)) as c2:
                    return dict(await c2.fetchone())
            else:
                cursor_ins = await db.execute(
                    "INSERT INTO users (google_id, email, name, avatar, tokens, tier) VALUES (?, ?, ?, ?, 1000, 'Free')",
                    (google_id, email, name, avatar)
                )
                await db.commit()
                user_id = cursor_ins.lastrowid
                async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as c2:
                    return dict(await c2.fetchone())

async def get_or_create_telegram_user(telegram_id: int, username: str = "", first_name: str = "", photo_url: str = "") -> dict:
    if not telegram_id:
        return None
    telegram_id = int(telegram_id)
    display_name = first_name or (f"@{username}" if username else f"User {telegram_id}")
    clean_username = username.lstrip("@") if username else ""
    is_admin = 1 if telegram_id in ADMIN_TELEGRAM_IDS else 0
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE telegram_id = ? OR CAST(telegram_id AS TEXT) = ?",
            (telegram_id, str(telegram_id))
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                user = dict(row)
                updates = ["last_login = CURRENT_TIMESTAMP"]
                params = []
                if display_name and (not user.get("name") or "kirabakuta" in str(user.get("name")).lower()):
                    updates.append("name = ?")
                    params.append(display_name)
                if photo_url and not user.get("avatar"):
                    updates.append("avatar = ?")
                    params.append(photo_url)
                if clean_username:
                    updates.append("telegram_username = ?")
                    params.append(clean_username)
                if is_admin:
                    updates.append("is_superadmin = 1")
                    updates.append("tier = 'Max'")
                    if (user.get("tokens") or 0) < 500000:
                        updates.append("tokens = 500000")
                
                query = f"UPDATE users SET {', '.join(updates)} WHERE id = ?"
                params.append(user["id"])
                await db.execute(query, params)
                await db.commit()
                
                async with db.execute("SELECT * FROM users WHERE id = ?", (user["id"],)) as c2:
                    return dict(await c2.fetchone())
            else:
                initial_tier = "Max" if is_admin else "Free"
                initial_tokens = 500000 if is_admin else 1000
                cursor_ins = await db.execute(
                    """
                    INSERT INTO users (telegram_id, telegram_username, name, avatar, tokens, tier, is_superadmin) 
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (telegram_id, clean_username, display_name, photo_url, initial_tokens, initial_tier, is_admin)
                )
                await db.commit()
                user_id = cursor_ins.lastrowid
                async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as c2:
                    return dict(await c2.fetchone())

async def get_or_create_demo_user() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE email = 'foryafrom@gmail.com' OR email = 'demo@zyko.ai'") as cursor:
            row = await cursor.fetchone()
            if row:
                user = dict(row)
                if (user.get("tokens") or 0) < 500000 or user.get("tier") != "Max":
                    await db.execute("UPDATE users SET tokens = 500000, tier = 'Max', is_superadmin = 1 WHERE id = ?", (user["id"],))
                    await db.commit()
                    user["tokens"] = 500000
                    user["tier"] = "Max"
                    user["is_superadmin"] = 1
                return user
        cursor_ins = await db.execute(
            "INSERT INTO users (email, name, tokens, tier, is_superadmin) VALUES ('foryafrom@gmail.com', 'Дима', 500000, 'Max', 1)"
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
            if is_admin and (user.get("tokens") or 0) < 500000:
                await db.execute("UPDATE users SET tokens = 500000, tier = 'Max', is_superadmin = 1 WHERE id = ?", (user_id,))
                await db.commit()
                user["tokens"] = 500000
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

async def create_transaction(user_id: int, provider: str, order_id: str, amount: float, currency: str, tokens: int, tier: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO transactions 
            (user_id, provider, order_id, amount, currency, tokens_credited, tier_activated, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
        """, (user_id, provider, order_id, amount, currency, tokens, tier))
        await db.commit()

async def complete_transaction(order_id: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM transactions WHERE order_id = ?", (order_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            tx = dict(row)
            if tx["status"] == "completed":
                return tx

        await db.execute("UPDATE transactions SET status = 'completed' WHERE order_id = ?", (order_id,))
        if tx["user_id"]:
            await db.execute(
                "UPDATE users SET tokens = tokens + ?, tier = COALESCE(?, tier) WHERE id = ?",
                (tx["tokens_credited"], tx["tier_activated"], tx["user_id"])
            )
        await db.commit()
        return tx

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
            if current_balance > 1000 and not tx_rows:
                bonus_desc = f"{user_tier} Plan Initial Allocation"
                bonus_amt = current_balance
            else:
                bonus_desc = "Welcome Starter Gift Bonus"
                bonus_amt = 1000

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



