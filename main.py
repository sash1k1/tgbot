# language: Python 3.11+, framework: aiogram 3.x async, target: Linux/Windows/macOS
# *telegram business bot — no userbot, just add to group and it works*

import asyncio
import sqlite3
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, List, Set, Tuple
from aiogram import Bot, Dispatcher, types, filters, F
from aiogram.types import Message, Chat, User
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
import logging

# ─────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
BOT_COMMAND_PREFIX = "/"

CACHE_DIR = Path("./bot_data")
DB_PATH = CACHE_DIR / "bot.db"
MEDIA_CACHE_DIR = CACHE_DIR / "media"
LOG_LEVEL = logging.INFO

CACHE_DIR.mkdir(exist_ok=True, parents=True)
MEDIA_CACHE_DIR.mkdir(exist_ok=True, parents=True)

# ─────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.FileHandler(CACHE_DIR / "bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
logger.info("=" * 60)
logger.info("Telegram Business Bot Initialization")
logger.info("=" * 60)

# ─────────────────────────────────────────
# DATABASE INIT
# ─────────────────────────────────────────

def init_db():
    """Инициализация БД."""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        c = conn.cursor()
        
        c.execute("""
            CREATE TABLE IF NOT EXISTS muted_users (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                muted_until REAL NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (chat_id, user_id)
            )
        """)
        
        c.execute("""
            CREATE TABLE IF NOT EXISTS message_cache (
                msg_id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                user_id INTEGER,
                username TEXT,
                text TEXT,
                timestamp REAL NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_msg_chat ON message_cache(chat_id, msg_id DESC)")
        
        c.execute("""
            CREATE TABLE IF NOT EXISTS auto_reactions (
                user_id INTEGER PRIMARY KEY,
                emoji TEXT NOT NULL,
                created_at REAL
            )
        """)
        
        conn.commit()
        conn.close()
        logger.info("✅ Database initialized")
    except Exception as e:
        logger.error(f"❌ Database init failed: {e}")
        raise

# ─────────────────────────────────────────
# DATABASE HELPERS
# ─────────────────────────────────────────

def add_to_mute_list(chat_id: int, user_id: int, minutes: int = 0) -> bool:
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        c = conn.cursor()
        
        muted_until = (datetime.now() + timedelta(minutes=minutes)).timestamp() if minutes > 0 else 9999999999.0
        c.execute("""
            INSERT OR REPLACE INTO muted_users (chat_id, user_id, muted_until, created_at)
            VALUES (?, ?, ?, ?)
        """, (chat_id, user_id, muted_until, datetime.now().timestamp()))
        
        conn.commit()
        conn.close()
        logger.debug(f"Muted {user_id} in chat {chat_id}")
        return True
    except Exception as e:
        logger.error(f"Error adding to mute list: {e}")
        return False

def remove_from_mute_list(chat_id: int, user_id: int) -> bool:
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        c = conn.cursor()
        c.execute("DELETE FROM muted_users WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
        conn.commit()
        conn.close()
        logger.debug(f"Unmuted {user_id} in chat {chat_id}")
        return True
    except Exception as e:
        logger.error(f"Error removing from mute list: {e}")
        return False

def is_muted(chat_id: int, user_id: int) -> bool:
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        c = conn.cursor()
        c.execute("SELECT muted_until FROM muted_users WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
        row = c.fetchone()
        conn.close()
        
        if not row:
            return False
        
        muted_until = row[0]
        now = datetime.now().timestamp()
        
        if muted_until < 9999999999.0 and now > muted_until:
            remove_from_mute_list(chat_id, user_id)
            return False
        
        return True
    except Exception as e:
        logger.error(f"Error checking mute status: {e}")
        return False

def cache_message(msg: Message) -> bool:
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        c = conn.cursor()
        
        user_id = msg.from_user.id if msg.from_user else 0
        username = msg.from_user.username if msg.from_user else None
        
        c.execute("""
            INSERT OR REPLACE INTO message_cache (msg_id, chat_id, user_id, username, text, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (msg.message_id, msg.chat.id, user_id, username, msg.text or "", datetime.now().timestamp()))
        
        c.execute("SELECT COUNT(*) FROM message_cache WHERE chat_id = ?", (msg.chat.id,))
        count = c.fetchone()[0]
        if count > 500:
            c.execute("""
                DELETE FROM message_cache WHERE chat_id = ? AND msg_id NOT IN (
                    SELECT msg_id FROM message_cache WHERE chat_id = ? ORDER BY timestamp DESC LIMIT 500
                )
            """, (msg.chat.id, msg.chat.id))
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.warning(f"Error caching message: {e}")
        return False

def get_cached_message(chat_id: int, msg_id: int) -> Optional[Dict]:
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        c = conn.cursor()
        c.execute("""
            SELECT msg_id, user_id, username, text FROM message_cache 
            WHERE chat_id = ? AND msg_id = ?
        """, (chat_id, msg_id))
        row = c.fetchone()
        conn.close()
        
        if row:
            return {
                "msg_id": row[0],
                "user_id": row[1],
                "username": row[2],
                "text": row[3]
            }
        return None
    except Exception as e:
        logger.error(f"Error getting cached message: {e}")
        return None

def set_auto_reaction(user_id: int, emoji: str) -> bool:
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        c = conn.cursor()
        c.execute("""
            INSERT OR REPLACE INTO auto_reactions (user_id, emoji, created_at)
            VALUES (?, ?, ?)
        """, (user_id, emoji, datetime.now().timestamp()))
        conn.commit()
        conn.close()
        logger.debug(f"Set auto-reaction {emoji} for user {user_id}")
        return True
    except Exception as e:
        logger.error(f"Error setting auto-reaction: {e}")
        return False

def remove_auto_reaction(user_id: int) -> bool:
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        c = conn.cursor()
        c.execute("DELETE FROM auto_reactions WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        logger.debug(f"Removed auto-reaction for user {user_id}")
        return True
    except Exception as e:
        logger.error(f"Error removing auto-reaction: {e}")
        return False

def get_auto_reaction(user_id: int) -> Optional[str]:
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        c = conn.cursor()
        c.execute("SELECT emoji FROM auto_reactions WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception as e:
        logger.error(f"Error getting auto-reaction: {e}")
        return None

# ─────────────────────────────────────────
# BOT INIT
# ─────────────────────────────────────────

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ─────────────────────────────────────────
# COMMANDS
# ─────────────────────────────────────────

@dp.message(Command("мут"))
async def cmd_mute(message: Message):
    """Команда: /мут [минуты]"""
    try:
        if not message.reply_to_message:
            await message.reply("⚠️ Ответь на сообщение пользователя.")
            return
        
        target_user = message.reply_to_message.from_user
        if not target_user:
            return
        
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        if member.status not in ("creator", "administrator"):
            await message.reply("❌ Только администраторы могут мьютить.")
            return
        
        parts = message.text.split()
        minutes = 0
        if len(parts) > 1:
            try:
                minutes = int(parts[1])
            except ValueError:
                await message.reply("❌ Использование: `/мут [минуты]`")
                return
        
        success = add_to_mute_list(message.chat.id, target_user.id, minutes)
        if success:
            duration_text = f"на {minutes} минут" if minutes > 0 else "навсегда"
            await message.reply(f"🤐 **@{target_user.username or target_user.id}** мьютен {duration_text}.")
        else:
            await message.reply("❌ Ошибка при добавлении в мут-лист.")
    except Exception as e:
        logger.error(f"Error in cmd_mute: {e}")

@dp.message(Command("размут"))
async def cmd_unmute(message: Message):
    """Команда: /размут"""
    try:
        if not message.reply_to_message:
            await message.reply("⚠️ Ответь на сообщение пользователя.")
            return
        
        target_user = message.reply_to_message.from_user
        if not target_user:
            return
        
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        if member.status not in ("creator", "administrator"):
            await message.reply("❌ Только администраторы могут размьютить.")
            return
        
        success = remove_from_mute_list(message.chat.id, target_user.id)
        if success:
            await message.reply(f"✅ **@{target_user.username or target_user.id}** размьютен.")
        else:
            await message.reply("❌ Ошибка при удалении из мут-листа.")
    except Exception as e:
        logger.error(f"Error in cmd_unmute: {e}")

@dp.message(Command("спам"))
async def cmd_spam(message: Message):
    """Команда: /спам [текст] [кол-во]"""
    try:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        if member.status not in ("creator", "administrator"):
            await message.reply("❌ Только администраторы могут спамить.")
            return
        
        parts = message.text.split(maxsplit=2)
        
        if len(parts) < 3:
            await message.reply("❌ Использование: `/спам [текст] [кол-во]`\n\nПример: `/спам привет 5`")
            return
        
        try:
            count = int(parts[-1])
        except ValueError:
            await message.reply("❌ Последний аргумент должен быть числом.\nПример: `/спам привет 5`")
            return
        
        spam_text_content = " ".join(parts[1:-1])
        
        if count <= 0 or count > 100:
            await message.reply("❌ Количество должно быть от 1 до 100.")
            return
        
        chat_id = message.chat.id
        success_count = 0
        
        for i in range(count):
            try:
                await bot.send_message(chat_id, spam_text_content)
                success_count += 1
                await asyncio.sleep(0.2)
            except TelegramAPIError as e:
                logger.warning(f"Error sending spam: {e}")
                break
        
        await message.reply(f"✅ Отправлено {success_count}/{count} сообщений.")
    except Exception as e:
        logger.error(f"Error in cmd_spam: {e}")

@dp.message(Command("лесенка"))
async def cmd_ladder(message: Message):
    """Команда: /лесенка [текст]"""
    try:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        if member.status not in ("creator", "administrator"):
            await message.reply("❌ Только администраторы.")
            return
        
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            await message.reply("❌ Использование: `/лесенка [текст]`")
            return
        
        text_content = parts[1]
        words = text_content.split()
        
        if not words:
            await message.reply("❌ Текст пуст.")
            return
        
        chat_id = message.chat.id
        success_count = 0
        
        for word in words:
            try:
                await bot.send_message(chat_id, word)
                success_count += 1
                await asyncio.sleep(0.15)
            except TelegramAPIError:
                break
        
        await message.reply(f"✅ Отправлено {success_count}/{len(words)} слов.")
    except Exception as e:
        logger.error(f"Error in cmd_ladder: {e}")

@dp.message(Command("реакция"))
async def cmd_reaction(message: Message):
    """Команда: /реакция [эмодзи] или /реакция офф"""
    try:
        if not message.reply_to_message:
            await message.reply("⚠️ Ответь на сообщение пользователя.")
            return
        
        target_user = message.reply_to_message.from_user
        if not target_user:
            return
        
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            await message.reply("❌ Использование: `/реакция [эмодзи]` или `/реакция офф`")
            return
        
        emoji_or_cmd = parts[1].strip()
        
        if emoji_or_cmd.lower() == "офф":
            success = remove_auto_reaction(target_user.id)
            if success:
                await message.reply(f"✅ Авто-реакции для **@{target_user.username or target_user.id}** отключены.")
            else:
                await message.reply("❌ Ошибка при отключении.")
        else:
            success = set_auto_reaction(target_user.id, emoji_or_cmd)
            if success:
                await message.reply(f"✅ Авто-реакция `{emoji_or_cmd}` для **@{target_user.username or target_user.id}**.")
            else:
                await message.reply("❌ Ошибка при установке.")
    except Exception as e:
        logger.error(f"Error in cmd_reaction: {e}")

@dp.message(Command("помощь"))
async def cmd_help(message: Message):
    """Команда: /помощь"""
    help_text = """
🤖 **Доступные команды:**

**Модерация:**
`/мут [минуты]` — Мьютить пользователя (reply)
`/размут` — Размьютить пользователя (reply)

**Спам:**
`/спам [текст] [кол-во]` — Отправить текст N раз
`/лесенка [текст]` — Отправить каждое слово отдельно

**Реакции:**
`/реакция [эмодзи]` — Авто-реакция на всё от пользователя (reply)
`/реакция офф` — Отключить авто-реакцию (reply)

⚠️ Модерация доступна только администраторам.
    """
    await message.reply(help_text, parse_mode="markdown")

# ─────────────────────────────────────────
# MESSAGE HANDLERS
# ─────────────────────────────────────────

@dp.message(F.text)
async def handle_message(message: Message):
    """Обработчик всех сообщений."""
    try:
        if message.text and message.text.startswith("/"):
            return
        if message.from_user.is_bot:
            return
        
        cache_message(message)
        
        if is_muted(message.chat.id, message.from_user.id):
            try:
                await message.delete()
                logger.info(f"Deleted muted message from {message.from_user.id}")
            except Exception as e:
                logger.warning(f"Could not delete muted message: {e}")
            return
        
        emoji = get_auto_reaction(message.from_user.id)
        if emoji:
            try:
                await message.react(reaction=[types.ReactionTypeEmoji(emoji=emoji)])
            except Exception as e:
                logger.debug(f"Error adding reaction: {e}")
    
    except Exception as e:
        logger.error(f"Error in handle_message: {e}")

# ─────────────────────────────────────────
# BACKGROUND TASKS
# ─────────────────────────────────────────

async def mute_cleanup_task():
    """Очистка истекших мутов."""
    while True:
        try:
            await asyncio.sleep(60)
            conn = sqlite3.connect(DB_PATH, timeout=10.0)
            c = conn.cursor()
            now = datetime.now().timestamp()
            c.execute("DELETE FROM muted_users WHERE muted_until < ?", (now,))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error in mute_cleanup_task: {e}")

# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────

async def main():
    """Точка входа."""
    logger.info("Starting initialization...")
    init_db()
    
    logger.info("Starting Telegram bot...")
    
    cleanup_task = asyncio.create_task(mute_cleanup_task())
    
    logger.info("✅ Bot is running!")
    logger.info("Commands: /мут, /размут, /спам, /лесенка, /реакция, /помощь")
    
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except KeyboardInterrupt:
        logger.info("Shutdown signal received")
    finally:
        cleanup_task.cancel()
        await bot.session.close()

if __name__ == "__main__":
    if not BOT_TOKEN:
        print("❌ ERROR: Fill BOT_TOKEN in config!")
        print("   Get BOT_TOKEN from @BotFather in Telegram")
        exit(1)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Terminated by user")
    except Exception as e:
        logger.critical(f"Critical error: {e}", exc_info=True)
