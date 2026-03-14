import logging
import asyncio
import aiosqlite
import html
import aiohttp
import os
from datetime import datetime
from typing import Dict, List, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes
)
from telegram.constants import ParseMode

# ================== الإعدادات الأساسية ==================
BOT_TOKEN = "8541494828:AAFWIBbIyacrQWkRh5zxIy-Gcq2m_7_LNv4"
OWNER_ID = 6889992779
DATABASE_FILE = "vikings.db"
LOG_FILE = "vikings_bot.log"

# صور احتياطية للترحيب
DEFAULT_WELCOME_IMAGE = "https://telegra.ph/file/8c3e7b8d3f7a1c9b2e5d4.jpg"
FALLBACK_IMAGE = "https://telegra.ph/file/1a9c3b8d4e7f5a2b6c8d9.jpg"

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ================== قاعدة البيانات (مع تحديث تلقائي) ==================
async def init_db():
    # محاولة حذف الملف القديم إذا كان موجودًا (اختياري)
    # if os.path.exists(DATABASE_FILE):
    #     os.remove(DATABASE_FILE)
    
    async with aiosqlite.connect(DATABASE_FILE) as db:
        # الإعدادات
        await db.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        # المشرفين
        await db.execute('''
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                added_by INTEGER,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # قنوات الاشتراك الإجباري
        await db.execute('''
            CREATE TABLE IF NOT EXISTS required_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_username TEXT UNIQUE,
                channel_url TEXT,
                added_by INTEGER,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # المستخدمين
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                first_name TEXT,
                username TEXT,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP,
                is_banned INTEGER DEFAULT 0
            )
        ''')
        # المسلسلات
        await db.execute('''
            CREATE TABLE IF NOT EXISTS series (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                description TEXT,
                poster TEXT,
                type TEXT DEFAULT 'series',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # المواسم
        await db.execute('''
            CREATE TABLE IF NOT EXISTS seasons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_id INTEGER,
                season_number INTEGER,
                name TEXT,
                FOREIGN KEY (series_id) REFERENCES series (id) ON DELETE CASCADE,
                UNIQUE(series_id, season_number)
            )
        ''')
        # الحلقات - إنشاء الجدول إذا لم يكن موجودًا
        await db.execute('''
            CREATE TABLE IF NOT EXISTS episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_id INTEGER,
                season_number INTEGER,
                episode_number INTEGER,
                title TEXT,
                description TEXT DEFAULT '',
                video_file_id TEXT,
                video_url TEXT DEFAULT '',
                added_by INTEGER,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (series_id) REFERENCES series (id) ON DELETE CASCADE,
                UNIQUE(series_id, season_number, episode_number)
            )
        ''')
        
        # التحقق من وجود الأعمدة وإضافتها إذا كانت مفقودة
        # نتحقق من وجود عمود video_url
        try:
            await db.execute('SELECT video_url FROM episodes LIMIT 1')
        except aiosqlite.OperationalError:
            # العمود غير موجود، نضيفه
            await db.execute('ALTER TABLE episodes ADD COLUMN video_url TEXT DEFAULT ""')
            logger.info("تم إضافة عمود video_url إلى جدول episodes")
        
        try:
            await db.execute('SELECT description FROM episodes LIMIT 1')
        except aiosqlite.OperationalError:
            await db.execute('ALTER TABLE episodes ADD COLUMN description TEXT DEFAULT ""')
            logger.info("تم إضافة عمود description إلى جدول episodes")
        
        try:
            await db.execute('SELECT added_by FROM episodes LIMIT 1')
        except aiosqlite.OperationalError:
            await db.execute('ALTER TABLE episodes ADD COLUMN added_by INTEGER DEFAULT 0')
            logger.info("تم إضافة عمود added_by إلى جدول episodes")
        
        try:
            await db.execute('SELECT added_at FROM episodes LIMIT 1')
        except aiosqlite.OperationalError:
            await db.execute('ALTER TABLE episodes ADD COLUMN added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP')
            logger.info("تم إضافة عمود added_at إلى جدول episodes")
        
        # إعدادات افتراضية
        default_settings = [
            ('welcome_message', '⚔️ <b>مرحباً بك في عالم الفايكنج!</b>\n\nاختر من القائمة أدناه:'),
            ('welcome_image', DEFAULT_WELCOME_IMAGE),
            ('show_welcome_image', 'true'),
            ('enable_subscription', 'false'),
            ('bot_description', '🎬 بوت الفايكنج - مسلسلات، اقتباسات، قصص')
        ]
        for key, value in default_settings:
            await db.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', (key, value))
        
        # إضافة المالك كمشرف
        await db.execute('INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, ?)', (OWNER_ID, OWNER_ID))
        await db.commit()

# ================== البذور الأولية (اقتباسات وقصص) ==================
async def seed_initial_data():
    async with aiosqlite.connect(DATABASE_FILE) as db:
        async with db.execute('SELECT COUNT(*) FROM quotes') as cur:
            if (await cur.fetchone())[0] == 0:
                quotes = [
                    ("لا تخش الموت، اخشَ الحياة غير المكتملة", "راغنار لوثبروك"),
                    ("من يجرؤ على العبور، يعبر", "راغنار لوثبروك"),
                    ("الرجل العظيم لا يولد عظيماً، بل يصنع نفسه", "راغنار لوثبروك"),
                    ("الخوف هو الموت الصغير", "راغنار لوثبروك"),
                    ("الحكمة تأتي من المعاناة", "راغنار لوثبروك"),
                    ("الانتقام طبق يؤكل بارداً", "رولو"),
                    ("قد لا تكون الآلهة معنا دائماً، لكننا نبقى معها", "فلوكي"),
                    ("أنا لا أؤمن بالصدف، أؤمن بالإرادة", "لاغيرتا"),
                    ("المرأة المحاربة لا تبكي، تنتقم", "لاغيرتا"),
                    ("أنا لست وحشاً، أنا فايكنغ", "إيفar"),
                ]
                for _ in range(100):
                    for q in quotes:
                        await db.execute('INSERT INTO quotes (text, speaker) VALUES (?, ?)', q)
                await db.commit()

        async with db.execute('SELECT COUNT(*) FROM stories') as cur:
            if (await cur.fetchone())[0] == 0:
                stories = [
                    ('شخصية', 'راغنار لوثبروك', 'راغنار لوثبروك هو مزارع ومحارب إسكندنافي أصبح ملكاً بفضل جرأته.', ''),
                    ('شخصية', 'لاغيرتا', 'لاغيرتا هي درع حربية وأول زوجة لراغنار، أصبحت ملكة.', ''),
                    ('شخصية', 'بيورن إيرنسايد', 'ابن راغنار الأكبر، قاد رحلة استكشافية إلى البحر المتوسط.', ''),
                    ('شخصية', 'إيفار الخالي من العظم', 'ابن راغنار الأكثر ذكاءً ودهاءً.', ''),
                    ('ملك', 'الملك إيكبرت', 'ملك وسكس وأول ملك يوحد الممالك السبع.', ''),
                    ('معركة', 'معركة باريس', 'حاصر راغنار باريس وحصل على فدية ضخمة.', ''),
                ]
                for s in stories:
                    await db.execute('INSERT INTO stories (category, title, content, image_url) VALUES (?, ?, ?, ?)', s)
                await db.commit()

# ================== دوال المساعدة العامة ==================
async def get_setting(key: str, default: str = "") -> str:
    async with aiosqlite.connect(DATABASE_FILE) as db:
        async with db.execute('SELECT value FROM settings WHERE key = ?', (key,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else default

async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, value))
        await db.commit()

async def check_admin(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    async with aiosqlite.connect(DATABASE_FILE) as db:
        async with db.execute('SELECT user_id FROM admins WHERE user_id = ?', (user_id,)) as cur:
            return await cur.fetchone() is not None

async def add_user(user_id: int, first_name: str, username: str = None):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute('''
            INSERT INTO users (user_id, first_name, username, last_active)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                first_name=excluded.first_name,
                username=excluded.username,
                last_active=CURRENT_TIMESTAMP
        ''', (user_id, first_name, username))
        await db.commit()

async def get_user(user_id: int) -> Optional[Dict]:
    async with aiosqlite.connect(DATABASE_FILE) as db:
        async with db.execute('SELECT user_id, is_banned FROM users WHERE user_id = ?', (user_id,)) as cur:
            row = await cur.fetchone()
            return {'user_id': row[0], 'is_banned': row[1]} if row else None

async def ban_user(user_id: int):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute('UPDATE users SET is_banned = 1 WHERE user_id = ?', (user_id,))
        await db.commit()

async def unban_user(user_id: int):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute('UPDATE users SET is_banned = 0 WHERE user_id = ?', (user_id,))
        await db.commit()

async def get_stats():
    async with aiosqlite.connect(DATABASE_FILE) as db:
        async with db.execute('SELECT COUNT(*) FROM users WHERE is_banned = 0') as cur:
            users = (await cur.fetchone())[0]
        async with db.execute('SELECT COUNT(*) FROM users WHERE is_banned = 1') as cur:
            banned = (await cur.fetchone())[0]
        async with db.execute('SELECT COUNT(*) FROM series') as cur:
            series = (await cur.fetchone())[0]
        async with db.execute('SELECT COUNT(*) FROM episodes') as cur:
            episodes = (await cur.fetchone())[0]
        async with db.execute('SELECT COUNT(*) FROM quotes') as cur:
            quotes = (await cur.fetchone())[0]
        async with db.execute('SELECT COUNT(*) FROM stories') as cur:
            stories = (await cur.fetchone())[0]
    return {'users': users, 'banned': banned, 'series': series, 'episodes': episodes, 'quotes': quotes, 'stories': stories}

# ================== دوال المسلسلات والمواسم والحلقات ==================
async def get_all_series():
    async with aiosqlite.connect(DATABASE_FILE) as db:
        async with db.execute('SELECT id, name, description, poster, type FROM series ORDER BY name') as cur:
            return await cur.fetchall()

async def get_series(series_id: int):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        async with db.execute('SELECT id, name, description, poster, type FROM series WHERE id=?', (series_id,)) as cur:
            return await cur.fetchone()

async def add_series(name: str, description: str = "", poster: str = "", type: str = "series"):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute('INSERT INTO series (name, description, poster, type) VALUES (?, ?, ?, ?)', (name, description, poster, type))
        await db.commit()

async def delete_series(series_id: int):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute('DELETE FROM series WHERE id=?', (series_id,))
        await db.commit()

async def get_seasons(series_id: int):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        async with db.execute('SELECT season_number, name FROM seasons WHERE series_id=? ORDER BY season_number', (series_id,)) as cur:
            return await cur.fetchall()

async def add_season(series_id: int, season_number: int, name: str = ""):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute('INSERT OR IGNORE INTO seasons (series_id, season_number, name) VALUES (?, ?, ?)', (series_id, season_number, name))
        await db.commit()

async def delete_season(series_id: int, season_number: int):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute('DELETE FROM seasons WHERE series_id=? AND season_number=?', (series_id, season_number))
        await db.commit()

async def get_episodes(series_id: int, season_number: int):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        async with db.execute('SELECT episode_number, title, video_file_id FROM episodes WHERE series_id=? AND season_number=? ORDER BY episode_number', (series_id, season_number)) as cur:
            return await cur.fetchall()

async def get_episode(series_id: int, season_number: int, episode_number: int):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        async with db.execute('SELECT title, description, video_file_id, video_url FROM episodes WHERE series_id=? AND season_number=? AND episode_number=?', (series_id, season_number, episode_number)) as cur:
            return await cur.fetchone()

async def add_episode(series_id: int, season_number: int, episode_number: int, title: str, description: str, video_file_id: str = None, video_url: str = None, added_by: int = 0):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute('''
            INSERT OR REPLACE INTO episodes 
            (series_id, season_number, episode_number, title, description, video_file_id, video_url, added_by) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (series_id, season_number, episode_number, title, description, video_file_id, video_url, added_by))
        await db.commit()

async def delete_episode(series_id: int, season_number: int, episode_number: int):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute('DELETE FROM episodes WHERE series_id=? AND season_number=? AND episode_number=?', (series_id, season_number, episode_number))
        await db.commit()

async def get_next_episode_number(series_id: int, season_number: int) -> int:
    async with aiosqlite.connect(DATABASE_FILE) as db:
        async with db.execute('SELECT MAX(episode_number) FROM episodes WHERE series_id=? AND season_number=?', (series_id, season_number)) as cur:
            max_num = (await cur.fetchone())[0]
            return (max_num + 1) if max_num else 1

# ================== دوال الاقتباسات ==================
async def add_quote(text: str, speaker: str, series_id: int = None, added_by: int = 0):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute('INSERT INTO quotes (text, speaker, series_id, added_by) VALUES (?, ?, ?, ?)', (text, speaker, series_id, added_by))
        await db.commit()

async def get_quotes_count() -> int:
    async with aiosqlite.connect(DATABASE_FILE) as db:
        async with db.execute('SELECT COUNT(*) FROM quotes') as cur:
            return (await cur.fetchone())[0]

async def get_quotes_page(page: int = 0, per_page: int = 10):
    offset = page * per_page
    async with aiosqlite.connect(DATABASE_FILE) as db:
        async with db.execute('SELECT id, text, speaker FROM quotes ORDER BY id DESC LIMIT ? OFFSET ?', (per_page, offset)) as cur:
            return await cur.fetchall()

async def delete_quote(quote_id: int):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute('DELETE FROM quotes WHERE id=?', (quote_id,))
        await db.commit()

# ================== دوال القصص ==================
async def add_story(category: str, title: str, content: str, image_url: str = "", added_by: int = 0):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute('INSERT INTO stories (category, title, content, image_url, added_by) VALUES (?, ?, ?, ?, ?)', (category, title, content, image_url, added_by))
        await db.commit()

async def get_stories_by_category(category: str):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        async with db.execute('SELECT id, title FROM stories WHERE category=? ORDER BY title', (category,)) as cur:
            return await cur.fetchall()

async def get_story(story_id: int):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        async with db.execute('SELECT category, title, content, image_url FROM stories WHERE id=?', (story_id,)) as cur:
            return await cur.fetchone()

async def delete_story(story_id: int):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute('DELETE FROM stories WHERE id=?', (story_id,))
        await db.commit()

# ================== دوال المشرفين وقنوات الاشتراك ==================
async def add_admin(user_id: int, added_by: int):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute('INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, ?)', (user_id, added_by))
        await db.commit()

async def remove_admin(user_id: int):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute('DELETE FROM admins WHERE user_id = ?', (user_id,))
        await db.commit()

async def get_all_admins():
    async with aiosqlite.connect(DATABASE_FILE) as db:
        async with db.execute('SELECT user_id FROM admins ORDER BY added_at') as cur:
            rows = await cur.fetchall()
            return [r[0] for r in rows]

async def add_channel(username: str, url: str, added_by: int):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute('INSERT OR IGNORE INTO required_channels (channel_username, channel_url, added_by) VALUES (?, ?, ?)', (username, url, added_by))
        await db.commit()

async def remove_channel(username: str):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute('DELETE FROM required_channels WHERE channel_username = ?', (username,))
        await db.commit()

async def get_all_channels():
    async with aiosqlite.connect(DATABASE_FILE) as db:
        async with db.execute('SELECT channel_username, channel_url FROM required_channels ORDER BY channel_username') as cur:
            return await cur.fetchall()

# ================== دالة التحقق من الاشتراك ==================
async def check_subscription(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    enabled = await get_setting('enable_subscription', 'false')
    if enabled != 'true':
        return True, None
    channels = await get_all_channels()
    if not channels:
        return True, None
    not_joined = []
    for ch in channels:
        try:
            member = await context.bot.get_chat_member(chat_id=ch[0], user_id=user_id)
            if member.status in ['left', 'kicked']:
                not_joined.append(ch)
        except:
            not_joined.append(ch)
    if not not_joined:
        return True, None
    keyboard = []
    for ch in not_joined:
        keyboard.append([InlineKeyboardButton(ch[0], url=ch[1])])
    keyboard.append([InlineKeyboardButton("✅ تحقق", callback_data="check_sub")])
    return False, InlineKeyboardMarkup(keyboard)

def escape_html(text: str) -> str:
    return html.escape(text) if text else ''

async def test_image_url(url: str) -> bool:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.head(url, timeout=5, allow_redirects=True) as resp:
                return resp.status == 200 and 'image' in resp.headers.get('content-type', '')
    except:
        return False

# ================== دالة الترحيب ==================
async def send_welcome_message(update_or_query, context, user_id: int, user_first_name: str, is_callback: bool = False):
    welcome_message = await get_setting('welcome_message')
    welcome_image = await get_setting('welcome_image')
    show_image = await get_setting('show_welcome_image', 'true')
    formatted_message = welcome_message.replace('{name}', escape_html(user_first_name))
    keyboard = [
        [InlineKeyboardButton("📺 المسلسلات", callback_data="list_series")],
        [InlineKeyboardButton("💬 اقتباسات", callback_data="quotes_page_0")],
        [InlineKeyboardButton("📖 قصص الفايكنج", callback_data="stories_menu")],
    ]
    if await check_admin(user_id):
        keyboard.append([InlineKeyboardButton("⚙️ الإدارة", callback_data="admin_panel")])

    if show_image == 'true' and welcome_image:
        try:
            if is_callback:
                await update_or_query.message.delete()
                await update_or_query.message.reply_photo(photo=welcome_image, caption=formatted_message, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await update_or_query.message.reply_photo(photo=welcome_image, caption=formatted_message, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
            return
        except Exception as e:
            logger.error(f"فشل إرسال الصورة: {e}")
            try:
                if is_callback:
                    await update_or_query.message.reply_photo(photo=FALLBACK_IMAGE, caption=formatted_message, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
                else:
                    await update_or_query.message.reply_photo(photo=FALLBACK_IMAGE, caption=formatted_message, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
                return
            except:
                pass
    if is_callback:
        await update_or_query.edit_message_text(text=formatted_message, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update_or_query.message.reply_text(text=formatted_message, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

# ================== بدء البوت ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = update.effective_user
    db_user = await get_user(user_id)
    if db_user and db_user['is_banned']:
        await update.message.reply_text("⛔ أنت محظور.")
        return
    await add_user(user_id, user.first_name, user.username)
    allowed, sub_keyboard = await check_subscription(user_id, context)
    if not allowed:
        await update.message.reply_text("❌ يجب الاشتراك في القنوات التالية أولاً:", reply_markup=sub_keyboard)
        return
    await send_welcome_message(update, context, user_id, user.first_name, is_callback=False)

async def check_subscription_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    allowed, sub_keyboard = await check_subscription(user_id, context)
    if allowed:
        await query.edit_message_text("✅ شكراً! أرسل /start مرة أخرى.")
    else:
        await query.edit_message_text("❌ لم تشترك بعد في:", reply_markup=sub_keyboard)

# ================== المسلسلات (مع أزرار حذف للمشرفين) ==================
async def list_series(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    series = await get_all_series()
    if not series:
        text = "📺 لا توجد مسلسلات."
        keyboard = [[InlineKeyboardButton("🏠 الرئيسية", callback_data="main_menu")]]
    else:
        text = "📺 <b>اختر مسلسلاً:</b>"
        keyboard = []
        for s in series:
            row = [InlineKeyboardButton(s[1], callback_data=f"series_{s[0]}")]
            if await check_admin(query.from_user.id):
                row.append(InlineKeyboardButton("❌", callback_data=f"del_series_{s[0]}"))
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="main_menu")])
    await query.edit_message_text(text=text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

async def show_series(update: Update, context: ContextTypes.DEFAULT_TYPE, series_id: int):
    query = update.callback_query
    s = await get_series(series_id)
    if not s:
        await query.edit_message_text("❌ غير موجود.")
        return
    seasons = await get_seasons(series_id)
    if not seasons:
        text = f"<b>{s[1]}</b>\n\n{s[2]}\n\nلا توجد مواسم."
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="list_series")]]
    else:
        text = f"<b>{s[1]}</b>\n\n{s[2]}\n\nاختر الموسم:"
        keyboard = []
        for season in seasons:
            row = [InlineKeyboardButton(f"📦 الموسم {season[0]} - {season[1]}", callback_data=f"season_{series_id}_{season[0]}")]
            if await check_admin(query.from_user.id):
                row.append(InlineKeyboardButton("❌", callback_data=f"del_season_{series_id}_{season[0]}"))
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="list_series")])
    if s[3]:
        try:
            await query.message.delete()
            await query.message.reply_photo(photo=s[3], caption=text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
            return
        except:
            pass
    await query.edit_message_text(text=text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

async def show_season(update: Update, context: ContextTypes.DEFAULT_TYPE, series_id: int, season_num: int):
    query = update.callback_query
    episodes = await get_episodes(series_id, season_num)
    if not episodes:
        text = f"📺 الموسم {season_num}\n\nلا توجد حلقات."
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data=f"series_{series_id}")]]
    else:
        text = f"📺 <b>الموسم {season_num}</b> - اختر الحلقة:"
        keyboard = []
        for ep in episodes:
            row = [InlineKeyboardButton(f"{ep[0]:02d} - {ep[1]}", callback_data=f"ep_{series_id}_{season_num}_{ep[0]}")]
            if await check_admin(query.from_user.id):
                row.append(InlineKeyboardButton("❌", callback_data=f"del_ep_{series_id}_{season_num}_{ep[0]}"))
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"series_{series_id}")])
    await query.edit_message_text(text=text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

async def play_episode(update: Update, context: ContextTypes.DEFAULT_TYPE, series_id: int, season: int, episode: int):
    query = update.callback_query
    ep = await get_episode(series_id, season, episode)
    if not ep:
        await query.edit_message_text("❌ الحلقة غير موجودة.")
        return
    text = f"<b>{ep[0]}</b>\n\n{ep[1] if ep[1] else ''}"
    if ep[2]:
        await query.message.delete()
        await context.bot.send_video(chat_id=query.message.chat_id, video=ep[2], caption=text, parse_mode=ParseMode.HTML,
                                     reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"season_{series_id}_{season}")]]))
    elif ep[3]:
        text += f"\n\n🔗 <a href='{ep[3]}'>رابط المشاهدة</a>"
        await query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"season_{series_id}_{season}")]]))
    else:
        text += "\n\n⚠️ لا توجد روابط مشاهدة."
        await query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"season_{series_id}_{season}")]]))

# ================== الاقتباسات ==================
async def show_quotes_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int):
    query = update.callback_query
    total = await get_quotes_count()
    per_page = 10
    total_pages = (total + per_page - 1) // per_page
    page = max(0, min(page, total_pages - 1))
    quotes = await get_quotes_page(page, per_page)
    text = f"💬 <b>اقتباسات</b> (صفحة {page+1}/{total_pages}):\n\n"
    for q in quotes:
        text += f"“{q[1]}”\n— <i>{q[2]}</i>\n\n"
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"quotes_page_{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"quotes_page_{page+1}"))
    keyboard = [nav] if nav else []
    if await check_admin(query.from_user.id):
        keyboard.append([InlineKeyboardButton("➕ إضافة اقتباس", callback_data="admin_add_quote")])
    keyboard.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="main_menu")])
    await query.edit_message_text(text=text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

# ================== القصص ==================
async def stories_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    text = "📖 <b>قصص الفايكنج</b>\n\nاختر الفئة:"
    keyboard = [
        [InlineKeyboardButton("👤 الشخصيات", callback_data="stories_category_شخصية")],
        [InlineKeyboardButton("👑 الملوك", callback_data="stories_category_ملك")],
        [InlineKeyboardButton("⚔️ المعارك", callback_data="stories_category_معركة")],
        [InlineKeyboardButton("🏠 الرئيسية", callback_data="main_menu")]
    ]
    await query.edit_message_text(text=text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

async def show_stories_by_category(update: Update, context: ContextTypes.DEFAULT_TYPE, category: str):
    query = update.callback_query
    stories = await get_stories_by_category(category)
    cat_name = {'شخصية': 'الشخصيات', 'ملك': 'الملوك', 'معركة': 'المعارك'}.get(category, category)
    if not stories:
        text = f"📖 لا توجد قصص في فئة {cat_name} بعد."
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="stories_menu")]]
    else:
        text = f"📖 <b>{cat_name}</b>\n\nاختر قصة:"
        keyboard = []
        for s in stories:
            row = [InlineKeyboardButton(s[1], callback_data=f"story_{s[0]}")]
            if await check_admin(query.from_user.id):
                row.append(InlineKeyboardButton("❌", callback_data=f"del_story_{s[0]}"))
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="stories_menu")])
    await query.edit_message_text(text=text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

async def show_story(update: Update, context: ContextTypes.DEFAULT_TYPE, story_id: int):
    query = update.callback_query
    story = await get_story(story_id)
    if not story:
        await query.edit_message_text("❌ القصة غير موجودة.")
        return
    text = f"<b>{story[1]}</b>\n\n{story[2]}"
    if story[3]:
        try:
            await query.message.delete()
            await query.message.reply_photo(photo=story[3], caption=text, parse_mode=ParseMode.HTML,
                                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"stories_category_{story[0]}")]]))
            return
        except:
            pass
    await query.edit_message_text(text=text, parse_mode=ParseMode.HTML,
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"stories_category_{story[0]}")]]))

# ================== لوحة الإدارة ==================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await check_admin(query.from_user.id):
        await query.answer("⛔ غير مصرح", show_alert=True)
        return
    is_owner = (query.from_user.id == OWNER_ID)
    text = "⚙️ <b>الإدارة المتقدمة</b>"
    keyboard = [
        [InlineKeyboardButton("🖼️ إعدادات الترحيب", callback_data="admin_welcome_settings")],
        [InlineKeyboardButton("📢 إدارة قنوات الاشتراك", callback_data="admin_channels")],
        [InlineKeyboardButton("➕ إضافة مسلسل", callback_data="admin_add_series")],
        [InlineKeyboardButton("➕ إضافة موسم", callback_data="admin_add_season")],
        [InlineKeyboardButton("➕ إضافة حلقة", callback_data="admin_add_episode")],
        [InlineKeyboardButton("💬 إضافة اقتباس", callback_data="admin_add_quote")],
        [InlineKeyboardButton("📖 إضافة قصة", callback_data="admin_add_story")],
    ]
    if is_owner:
        keyboard.append([InlineKeyboardButton("👥 إدارة المشرفين", callback_data="admin_manage_admins")])
        keyboard.append([InlineKeyboardButton("👤 حظر/رفع حظر", callback_data="admin_ban_menu")])
    keyboard.append([InlineKeyboardButton("📊 إحصائيات", callback_data="admin_stats")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")])
    await query.edit_message_text(text=text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

# ================== إضافة مسلسل ==================
async def admin_add_series(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text(
        "أرسل اسم المسلسل في سطر، ثم الوصف في السطر التالي (اختياري). مثال:\n"
        "فايكنج\nمسلسل تاريخي رائع",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("إلغاء", callback_data="admin_panel")]])
    )
    context.user_data['admin_action'] = 'add_series'

# ================== إضافة موسم ==================
async def admin_add_season(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    series = await get_all_series()
    if not series:
        await query.edit_message_text("❌ لا توجد مسلسلات.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("رجوع", callback_data="admin_panel")]]))
        return
    keyboard = []
    for s in series:
        keyboard.append([InlineKeyboardButton(s[1], callback_data=f"admin_add_season_series_{s[0]}")])
    keyboard.append([InlineKeyboardButton("إلغاء", callback_data="admin_panel")])
    await query.edit_message_text("اختر المسلسل:", reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_choose_series_season(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    series_id = int(query.data.split('_')[-1])
    context.user_data['admin_temp_series_id'] = series_id
    await query.edit_message_text(
        "أرسل رقم الموسم واسمه (اختياري). مثال:\n1 الموسم الأول",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("إلغاء", callback_data="admin_panel")]])
    )
    context.user_data['admin_action'] = 'add_season'

# ================== إضافة حلقة ==================
async def admin_add_episode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    series = await get_all_series()
    if not series:
        await query.edit_message_text("❌ لا توجد مسلسلات.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("رجوع", callback_data="admin_panel")]]))
        return
    keyboard = []
    for s in series:
        keyboard.append([InlineKeyboardButton(s[1], callback_data=f"admin_add_episode_series_{s[0]}")])
    keyboard.append([InlineKeyboardButton("إلغاء", callback_data="admin_panel")])
    await query.edit_message_text("اختر المسلسل:", reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_choose_series_for_episode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    series_id = int(query.data.split('_')[-1])
    seasons = await get_seasons(series_id)
    if not seasons:
        await query.edit_message_text("❌ لا توجد مواسم في هذا المسلسل.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("رجوع", callback_data="admin_add_episode")]]))
        return
    context.user_data['ep_series_id'] = series_id
    keyboard = []
    for s in seasons:
        keyboard.append([InlineKeyboardButton(f"الموسم {s[0]} - {s[1]}", callback_data=f"admin_choose_season_{series_id}_{s[0]}")])
    keyboard.append([InlineKeyboardButton("إلغاء", callback_data="admin_panel")])
    await query.edit_message_text("اختر الموسم:", reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_choose_season_for_episode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split('_')
    series_id = int(parts[3])
    season_number = int(parts[4])
    context.user_data['ep_series_id'] = series_id
    context.user_data['ep_season'] = season_number
    await query.edit_message_text(
        "أرسل الفيديو الآن.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("إلغاء", callback_data="admin_panel")]])
    )
    context.user_data['awaiting_episode_video'] = True

# ================== إضافة اقتباس ==================
async def admin_add_quote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text(
        "أرسل الاقتباس:\n<code>النص | المتحدث</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("إلغاء", callback_data="admin_panel")]])
    )
    context.user_data['admin_action'] = 'add_quote'

# ================== إضافة قصة ==================
async def admin_add_story(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text(
        "أرسل القصة:\n<code>الفئة | العنوان | المحتوى | رابط صورة (اختياري)</code>\n\nالفئات: شخصية, ملك, معركة",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("إلغاء", callback_data="admin_panel")]])
    )
    context.user_data['admin_action'] = 'add_story'

# ================== إعدادات الترحيب ==================
async def admin_welcome_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    show_image = await get_setting('show_welcome_image', 'true')
    welcome_message = await get_setting('welcome_message')
    welcome_image = await get_setting('welcome_image')
    text = (
        f"🖼️ <b>إعدادات الترحيب</b>\n\n"
        f"📝 الرسالة الحالية:\n{welcome_message}\n\n"
        f"🖼️ الصورة: {'✅ موجودة' if welcome_image else '❌ لا توجد'}\n"
        f"👁️ عرض الصورة: {'✅' if show_image == 'true' else '❌'}\n\n"
        f"اختر ما تريد تعديله:"
    )
    keyboard = [
        [InlineKeyboardButton("📝 تغيير رسالة الترحيب", callback_data="admin_set_welcome_message")],
        [InlineKeyboardButton("🖼️ إضافة/تغيير الصورة", callback_data="admin_set_welcome_image")],
        [InlineKeyboardButton("👁️ تفعيل/تعطيل عرض الصورة", callback_data="admin_toggle_show_image")],
        [InlineKeyboardButton("🗑️ حذف الصورة", callback_data="admin_delete_welcome_image")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]
    ]
    await query.edit_message_text(text=text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_set_welcome_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text(
        text="📝 أرسل رسالة الترحيب الجديدة.\nيمكنك استخدام {name} لاسم المستخدم.\nمثال: مرحباً {name} في عالم الفايكنج!",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("إلغاء", callback_data="admin_welcome_settings")]])
    )
    context.user_data['admin_action'] = 'set_welcome_message'

async def admin_set_welcome_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text(
        text="🖼️ أرسل رابط الصورة الجديدة (أو أرسل الصورة مباشرة):",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("إلغاء", callback_data="admin_welcome_settings")]])
    )
    context.user_data['admin_action'] = 'set_welcome_image'

async def admin_toggle_show_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = await get_setting('show_welcome_image', 'true')
    new_value = 'false' if current == 'true' else 'true'
    await set_setting('show_welcome_image', new_value)
    await update.callback_query.answer(f"تم {'تفعيل' if new_value == 'true' else 'تعطيل'} عرض الصورة")
    await admin_welcome_settings(update, context)

async def admin_delete_welcome_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await set_setting('welcome_image', '')
    await update.callback_query.answer("✅ تم حذف الصورة")
    await admin_welcome_settings(update, context)

# ================== إدارة قنوات الاشتراك ==================
async def admin_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    enabled = await get_setting('enable_subscription', 'false')
    channels = await get_all_channels()
    channels_text = ""
    if channels:
        for i, ch in enumerate(channels, 1):
            channels_text += f"{i}. {ch[0]}\n"
    else:
        channels_text = "لا توجد قنوات مضافة"
    text = (
        f"📢 <b>إدارة قنوات الاشتراك</b>\n\n"
        f"حالة الاشتراك الإجباري: {'✅ مفعل' if enabled == 'true' else '❌ معطل'}\n\n"
        f"<b>القنوات المضافة:</b>\n{channels_text}\n\n"
        f"اختر إجراء:"
    )
    keyboard = [
        [InlineKeyboardButton("➕ إضافة قناة", callback_data="admin_add_channel")],
        [InlineKeyboardButton("🗑️ حذف قناة", callback_data="admin_remove_channel")],
        [InlineKeyboardButton("⚡ تفعيل/تعطيل", callback_data="admin_toggle_subscription")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]
    ]
    await query.edit_message_text(text=text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text(
        text="📢 أرسل معرف القناة ورابطها بالتنسيق:\n<code>@channel | https://t.me/channel</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("إلغاء", callback_data="admin_channels")]])
    )
    context.user_data['admin_action'] = 'add_channel'

async def admin_remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    channels = await get_all_channels()
    if not channels:
        await query.answer("لا توجد قنوات لحذفها", show_alert=True)
        return
    keyboard = []
    for ch in channels:
        keyboard.append([InlineKeyboardButton(ch[0], callback_data=f"remove_channel_{ch[0]}")])
    keyboard.append([InlineKeyboardButton("إلغاء", callback_data="admin_channels")])
    await query.edit_message_text(
        text="اختر القناة للحذف:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def admin_toggle_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = await get_setting('enable_subscription', 'false')
    new_value = 'false' if current == 'true' else 'true'
    await set_setting('enable_subscription', new_value)
    await update.callback_query.answer(f"تم {'تفعيل' if new_value == 'true' else 'تعطيل'} الاشتراك الإجباري")
    await admin_channels(update, context)

# ================== إدارة المشرفين ==================
async def admin_manage_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != OWNER_ID:
        await query.answer("⛔ غير مصرح", show_alert=True)
        return
    admins = await get_all_admins()
    admins_text = ""
    for aid in admins:
        if aid == OWNER_ID:
            admins_text += f"• <code>{aid}</code> (المالك)\n"
        else:
            admins_text += f"• <code>{aid}</code>\n"
    text = f"👥 <b>المشرفون الحاليون</b>\n\n{admins_text}\nاختر إجراء:"
    keyboard = [
        [InlineKeyboardButton("➕ إضافة مشرف", callback_data="admin_add_admin")],
        [InlineKeyboardButton("🗑️ حذف مشرف", callback_data="admin_remove_admin")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]
    ]
    await query.edit_message_text(text=text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text(
        text="أرسل معرف المستخدم (user_id) الذي تريد إضافته كمشرف:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("إلغاء", callback_data="admin_manage_admins")]])
    )
    context.user_data['admin_action'] = 'add_admin'

async def admin_remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    admins = await get_all_admins()
    if not admins:
        await query.answer("لا يوجد مشرفين", show_alert=True)
        return
    keyboard = []
    for aid in admins:
        if aid == OWNER_ID:
            continue
        keyboard.append([InlineKeyboardButton(f"🔻 {aid}", callback_data=f"remove_admin_{aid}")])
    keyboard.append([InlineKeyboardButton("إلغاء", callback_data="admin_manage_admins")])
    await query.edit_message_text("اختر المشرف لحذفه:", reply_markup=InlineKeyboardMarkup(keyboard))

# ================== إدارة الحظر ==================
async def admin_ban_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    text = "👤 <b>إدارة الحظر</b>"
    keyboard = [
        [InlineKeyboardButton("🔨 حظر", callback_data="admin_ban")],
        [InlineKeyboardButton("🔓 رفع حظر", callback_data="admin_unban")],
        [InlineKeyboardButton("📋 المحظورين", callback_data="admin_banned_list")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]
    ]
    await query.edit_message_text(text=text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    stats = await get_stats()
    text = (
        f"📊 <b>إحصائيات</b>\n\n"
        f"👥 المستخدمين: {stats['users']}\n"
        f"🔨 المحظورين: {stats['banned']}\n"
        f"📺 المسلسلات: {stats['series']}\n"
        f"🎬 الحلقات: {stats['episodes']}\n"
        f"💬 الاقتباسات: {stats['quotes']}\n"
        f"📖 القصص: {stats['stories']}"
    )
    keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]]
    await query.edit_message_text(text=text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

# ================== معالج الفيديو ==================
async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await check_admin(user_id):
        return
    if not context.user_data.get('awaiting_episode_video'):
        return
    series_id = context.user_data.get('ep_series_id')
    season = context.user_data.get('ep_season')
    if not series_id or season is None:
        context.user_data.clear()
        return
    video = update.message.video
    if not video:
        await update.message.reply_text("❌ لم أستقبل فيديو")
        return
    context.user_data['temp_video_id'] = video.file_id
    context.user_data['awaiting_episode_title'] = True
    context.user_data.pop('awaiting_episode_video')
    next_ep = await get_next_episode_number(series_id, season)
    context.user_data['ep_next_number'] = next_ep
    await update.message.reply_text(
        f"📝 أرسل عنوان الحلقة (رقم الحلقة سيكون {next_ep} تلقائياً).\n"
        f"يمكنك تغيير الرقم بكتابة: رقم_الحلقة عنوان الحلقة\n"
        f"مثال: 5 عنوان الحلقة الخامسة"
    )

# ================== معالج النصوص ==================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await check_admin(user_id):
        return
    text = update.message.text.strip()
    action = context.user_data.get('admin_action')

    if action == 'add_series':
        lines = text.split('\n', 1)
        name = lines[0].strip()
        desc = lines[1].strip() if len(lines) > 1 else ''
        if not name:
            await update.message.reply_text("❌ الاسم مطلوب.")
            return
        await add_series(name, desc)
        await update.message.reply_text(f"✅ تم إضافة مسلسل {name}")
        context.user_data.pop('admin_action')
        return

    elif action == 'add_season':
        parts = text.split(maxsplit=1)
        try:
            num = int(parts[0])
            name = parts[1] if len(parts) > 1 else f"الموسم {num}"
        except:
            await update.message.reply_text("❌ الرجاء إدخال رقم صحيح.")
            return
        series_id = context.user_data.get('admin_temp_series_id')
        if series_id:
            await add_season(series_id, num, name)
            await update.message.reply_text(f"✅ تم إضافة الموسم {num}")
        context.user_data.pop('admin_action', None)
        context.user_data.pop('admin_temp_series_id', None)
        return

    elif context.user_data.get('awaiting_episode_title'):
        series_id = context.user_data.get('ep_series_id')
        season = context.user_data.get('ep_season')
        video_id = context.user_data.get('temp_video_id')
        next_ep = context.user_data.get('ep_next_number', 1)
        if not series_id or season is None or not video_id:
            context.user_data.clear()
            return
        parts = text.split(maxsplit=1)
        if len(parts) == 2 and parts[0].isdigit():
            episode_number = int(parts[0])
            title = parts[1]
        else:
            episode_number = next_ep
            title = text
        await add_episode(series_id, season, episode_number, title, '', video_file_id=video_id, added_by=user_id)
        await update.message.reply_text(f"✅ تم إضافة الحلقة {episode_number} ({title})")
        context.user_data.clear()
        return

    elif action == 'add_quote':
        if '|' not in text:
            await update.message.reply_text("❌ استخدم: النص | المتحدث")
            return
        q, s = text.split('|', 1)
        await add_quote(q.strip(), s.strip(), added_by=user_id)
        await update.message.reply_text("✅ تمت إضافة الاقتباس")
        context.user_data.pop('admin_action')
        return

    elif action == 'add_story':
        parts = text.split('|')
        if len(parts) < 3:
            await update.message.reply_text("❌ استخدم: الفئة | العنوان | المحتوى | رابط صورة (اختياري)")
            return
        cat = parts[0].strip()
        title = parts[1].strip()
        content = parts[2].strip()
        img = parts[3].strip() if len(parts) > 3 else ""
        if cat not in ('شخصية', 'ملك', 'معركة'):
            await update.message.reply_text("❌ الفئة يجب أن تكون: شخصية, ملك, معركة")
            return
        await add_story(cat, title, content, img, user_id)
        await update.message.reply_text(f"✅ تم إضافة القصة: {title}")
        context.user_data.pop('admin_action')
        return

    elif action == 'add_admin':
        try:
            new_id = int(text)
            if new_id == OWNER_ID:
                await update.message.reply_text("❌ هذا هو المالك.")
                return
            await add_admin(new_id, user_id)
            await update.message.reply_text(f"✅ تم إضافة مشرف {new_id}")
        except:
            await update.message.reply_text("❌ أدخل رقماً صحيحاً.")
        context.user_data.pop('admin_action')
        return

    elif action == 'add_channel':
        if '|' not in text:
            await update.message.reply_text("❌ استخدم: @channel | https://t.me/channel")
            return
        username, url = text.split('|', 1)
        await add_channel(username.strip(), url.strip(), user_id)
        await update.message.reply_text(f"✅ تم إضافة القناة {username}")
        context.user_data.pop('admin_action')
        return

    elif action == 'set_welcome_message':
        await set_setting('welcome_message', text)
        await update.message.reply_text("✅ تم تغيير رسالة الترحيب!")
        context.user_data.pop('admin_action')
        return

    elif action == 'set_welcome_image':
        if text.startswith('http'):
            if await test_image_url(text):
                await set_setting('welcome_image', text)
                await update.message.reply_text("✅ تم تغيير صورة الترحيب!")
            else:
                await update.message.reply_text("❌ الرابط غير صالح، سيتم استخدام الصورة الاحتياطية")
                await set_setting('welcome_image', FALLBACK_IMAGE)
        else:
            await set_setting('welcome_image', text)
            await update.message.reply_text("✅ تم تغيير صورة الترحيب!")
        context.user_data.pop('admin_action')
        return

    elif action in ('ban', 'unban'):
        try:
            target = int(text)
        except:
            await update.message.reply_text("❌ أدخل رقماً صحيحاً.")
            return
        if action == 'ban':
            await ban_user(target)
            await update.message.reply_text(f"✅ تم حظر {target}")
        else:
            await unban_user(target)
            await update.message.reply_text(f"✅ تم رفع الحظر عن {target}")
        context.user_data.pop('admin_action')
        return

# ================== معالج الصور ==================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await check_admin(user_id):
        return
    if context.user_data.get('admin_action') != 'set_welcome_image':
        return
    photo = update.message.photo[-1]
    file = await photo.get_file()
    file_url = file.file_path
    await set_setting('welcome_image', file_url)
    await update.message.reply_text("✅ تم تغيير صورة الترحيب!")
    context.user_data.pop('admin_action')

# ================== معالج الأزرار الرئيسي ==================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    user = await get_user(user_id)
    if user and user['is_banned']:
        await query.edit_message_text("⛔ أنت محظور.")
        return

    # التحقق من الاشتراك لجميع الأزرار ما عدا check_sub
    if data != "check_sub":
        allowed, sub_keyboard = await check_subscription(user_id, context)
        if not allowed:
            await query.edit_message_text("❌ يجب الاشتراك في القنوات التالية أولاً:", reply_markup=sub_keyboard)
            return

    if data == "main_menu":
        await send_welcome_message(query, context, user_id, query.from_user.first_name, is_callback=True)
        return
    if data == "check_sub":
        allowed, sub_keyboard = await check_subscription(user_id, context)
        if allowed:
            await query.edit_message_text("✅ شكراً! أرسل /start مرة أخرى.")
        else:
            await query.edit_message_text("❌ لم تشترك بعد في:", reply_markup=sub_keyboard)
        return

    # المسلسلات
    if data == "list_series":
        await list_series(update, context)
        return
    if data.startswith("series_"):
        sid = int(data.split('_')[1])
        await show_series(update, context, sid)
        return
    if data.startswith("season_"):
        parts = data.split('_')
        sid, snum = int(parts[1]), int(parts[2])
        await show_season(update, context, sid, snum)
        return
    if data.startswith("ep_"):
        parts = data.split('_')
        sid, snum, enum = int(parts[1]), int(parts[2]), int(parts[3])
        await play_episode(update, context, sid, snum, enum)
        return

    # حذف المسلسلات والمواسم والحلقات
    if data.startswith("del_series_"):
        if not await check_admin(user_id):
            await query.answer("⛔ غير مصرح", show_alert=True)
            return
        sid = int(data.split('_')[2])
        await delete_series(sid)
        await query.answer("✅ تم الحذف")
        await list_series(update, context)
        return
    if data.startswith("del_season_"):
        if not await check_admin(user_id):
            await query.answer("⛔ غير مصرح", show_alert=True)
            return
        parts = data.split('_')
        sid = int(parts[2])
        snum = int(parts[3])
        await delete_season(sid, snum)
        await query.answer("✅ تم الحذف")
        await show_series(update, context, sid)
        return
    if data.startswith("del_ep_"):
        if not await check_admin(user_id):
            await query.answer("⛔ غير مصرح", show_alert=True)
            return
        parts = data.split('_')
        sid = int(parts[2])
        snum = int(parts[3])
        enum = int(parts[4])
        await delete_episode(sid, snum, enum)
        await query.answer("✅ تم الحذف")
        await show_season(update, context, sid, snum)
        return
    if data.startswith("del_story_"):
        if not await check_admin(user_id):
            await query.answer("⛔ غير مصرح", show_alert=True)
            return
        sid = int(data.split('_')[2])
        await delete_story(sid)
        await query.answer("✅ تم الحذف")
        await stories_menu(update, context)
        return

    # الاقتباسات
    if data.startswith("quotes_page_"):
        page = int(data.split('_')[2])
        await show_quotes_page(update, context, page)
        return

    # القصص
    if data == "stories_menu":
        await stories_menu(update, context)
        return
    if data.startswith("stories_category_"):
        cat = data[17:]
        await show_stories_by_category(update, context, cat)
        return
    if data.startswith("story_"):
        sid = int(data.split('_')[1])
        await show_story(update, context, sid)
        return

    # الإدارة
    if data == "admin_panel":
        await admin_panel(update, context)
        return
    if data == "admin_welcome_settings":
        await admin_welcome_settings(update, context)
        return
    if data == "admin_set_welcome_message":
        await admin_set_welcome_message(update, context)
        return
    if data == "admin_set_welcome_image":
        await admin_set_welcome_image(update, context)
        return
    if data == "admin_toggle_show_image":
        await admin_toggle_show_image(update, context)
        return
    if data == "admin_delete_welcome_image":
        await admin_delete_welcome_image(update, context)
        return
    if data == "admin_channels":
        await admin_channels(update, context)
        return
    if data == "admin_add_channel":
        await admin_add_channel(update, context)
        return
    if data == "admin_remove_channel":
        await admin_remove_channel(update, context)
        return
    if data.startswith("remove_channel_"):
        channel = data[15:]
        await remove_channel(channel)
        await query.answer("✅ تم حذف القناة")
        await admin_channels(update, context)
        return
    if data == "admin_toggle_subscription":
        await admin_toggle_subscription(update, context)
        return
    if data == "admin_manage_admins":
        await admin_manage_admins(update, context)
        return
    if data == "admin_add_admin":
        await admin_add_admin(update, context)
        return
    if data == "admin_remove_admin":
        await admin_remove_admin(update, context)
        return
    if data.startswith("remove_admin_"):
        if user_id != OWNER_ID:
            await query.answer("⛔ غير مصرح", show_alert=True)
            return
        aid = int(data.split('_')[2])
        await remove_admin(aid)
        await query.answer("✅ تم الحذف")
        await admin_manage_admins(update, context)
        return
    if data == "admin_add_series":
        await admin_add_series(update, context)
        return
    if data == "admin_add_season":
        await admin_add_season(update, context)
        return
    if data.startswith("admin_add_season_series_"):
        await admin_choose_series_season(update, context)
        return
    if data == "admin_add_episode":
        await admin_add_episode(update, context)
        return
    if data.startswith("admin_add_episode_series_"):
        await admin_choose_series_for_episode(update, context)
        return
    if data.startswith("admin_choose_season_"):
        await admin_choose_season_for_episode(update, context)
        return
    if data == "admin_add_quote":
        await admin_add_quote(update, context)
        return
    if data == "admin_add_story":
        await admin_add_story(update, context)
        return
    if data == "admin_ban_menu":
        await admin_ban_menu(update, context)
        return
    if data == "admin_stats":
        await admin_stats(update, context)
        return
    if data == "admin_ban":
        await query.edit_message_text("أرسل معرف المستخدم للحظر:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("إلغاء", callback_data="admin_ban_menu")]]))
        context.user_data['admin_action'] = 'ban'
        return
    if data == "admin_unban":
        await query.edit_message_text("أرسل معرف المستخدم لرفع الحظر:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("إلغاء", callback_data="admin_ban_menu")]]))
        context.user_data['admin_action'] = 'unban'
        return
    if data == "admin_banned_list":
        async with aiosqlite.connect(DATABASE_FILE) as db:
            async with db.execute('SELECT user_id, first_name, username FROM users WHERE is_banned = 1') as cur:
                rows = await cur.fetchall()
        if not rows:
            text = "📋 لا يوجد محظورين."
        else:
            text = "📋 <b>المحظورين:</b>\n\n"
            for r in rows:
                text += f"• {r[1]} (@{r[2]}) – <code>{r[0]}</code>\n"
        await query.edit_message_text(text=text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("رجوع", callback_data="admin_ban_menu")]]))
        return

# ================== post_init ==================
async def post_init(app: Application):
    await init_db()
    await seed_initial_data()
    logger.info("✅ قاعدة البيانات جاهزة")

# ================== التشغيل ==================
if __name__ == "__main__":
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("✅ البوت يعمل...")
    app.run_polling()