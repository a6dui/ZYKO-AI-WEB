import os
import aiosqlite
import logging

logger = logging.getLogger("zyko_db")
DB_PATH = os.environ.get("DB_PATH", "zyko_web.db")

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE,
                google_id TEXT UNIQUE,
                telegram_id INTEGER UNIQUE,
                name TEXT,
                avatar TEXT,
                tokens INTEGER DEFAULT 1000,
                tier TEXT DEFAULT 'Free',
                tier_expires_at TIMESTAMP,
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
        await db.commit()
        logger.info("Database initialized successfully at %s", DB_PATH)

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

async def get_or_create_telegram_user(telegram_id: int, username: str, first_name: str, photo_url: str) -> dict:
    display_name = first_name or (f"@{username}" if username else f"User {telegram_id}")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                user = dict(row)
                await db.execute(
                    "UPDATE users SET last_login = CURRENT_TIMESTAMP, name = COALESCE(?, name), avatar = COALESCE(?, avatar) WHERE id = ?",
                    (display_name, photo_url, user["id"])
                )
                await db.commit()
                async with db.execute("SELECT * FROM users WHERE id = ?", (user["id"],)) as c2:
                    return dict(await c2.fetchone())
            else:
                cursor_ins = await db.execute(
                    "INSERT INTO users (telegram_id, name, avatar, tokens, tier) VALUES (?, ?, ?, 1000, 'Free')",
                    (telegram_id, display_name, photo_url)
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
            "INSERT INTO users (email, name, tokens, tier) VALUES ('foryafrom@gmail.com', 'Дима', 5.5, 'Free')"
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
            async with db.execute("SELECT * FROM users WHERE telegram_id = ? AND id != ?", (telegram_id, user_id)) as c_tg:
                other_tg = await c_tg.fetchone()
                if other_tg:
                    other_tg = dict(other_tg)
                    new_tokens = max(float(current_user.get("tokens") or 0), float(other_tg.get("tokens") or 0))
                    await db.execute("UPDATE users SET tokens = ?, telegram_id = ? WHERE id = ?", (new_tokens, telegram_id, user_id))
                    await db.execute("DELETE FROM users WHERE id = ?", (other_tg["id"],))
                    await db.commit()
                else:
                    await db.execute("UPDATE users SET telegram_id = ? WHERE id = ?", (telegram_id, user_id))
                    await db.commit()
                    
        async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as c_final:
            row = await c_final.fetchone()
            return dict(row) if row else None
