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

        # Seed or maintain owner / superadmin 755843448 with Max tier and huge tokens
        try:
            async with db.execute(
                "SELECT * FROM users WHERE telegram_id = 755843448 OR CAST(telegram_id AS TEXT) = '755843448'"
            ) as c_admin:
                admin_row = await c_admin.fetchone()
                if not admin_row:
                    await db.execute("""
                        INSERT INTO users (telegram_id, telegram_username, name, tokens, tier, is_superadmin)
                        VALUES (755843448, 'kirabakuta', 'Kirabakuta (Admin)', 500000, 'Max', 1)
                    """)
                else:
                    await db.execute("""
                        UPDATE users SET is_superadmin = 1, tier = 'Max'
                        WHERE telegram_id = 755843448 AND tier IN ('Free', NULL, '')
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
                if display_name and not user.get("name"):
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
                    if user.get("tier") in ("Free", None, ""):
                        updates.append("tier = 'Max'")
                    if float(user.get("tokens") or 0) < 50000:
                        updates.append("tokens = 100000")
                
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
                return dict(row)
        cursor_ins = await db.execute(
            "INSERT INTO users (email, name, tokens, tier) VALUES ('foryafrom@gmail.com', 'Дима', 1000, 'Free')"
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
            return dict(row) if row else None

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

