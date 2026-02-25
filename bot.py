import asyncio
import logging
import aiohttp
import os
import re
import yt_dlp
import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, BufferedInputFile, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramForbiddenError

# --- НАСТРОЙКИ ---
TOKEN = "8510454052:AAFtOQgHlvR6tff1zgalT7-YrkZ3k2tG5nE"
CHANNEL_USERNAME = "@intwitchsng"  # Юзернейм канала для подписки
# Впиши сюда ID всех админов через запятую
ADMIN_IDS = [6782438597] 
# -----------------

router = Router()

# --- СЛОВАРЬ ЯЗЫКОВ (ЛОКАЛИЗАЦИЯ) ---
TEXTS = {
    'ru': {
        'start': "Привет! Кидай ссылку на Pinterest, Instagram или TikTok, и я скачаю контент!",
        'sub_req': "🛑 Чтобы скачивать файлы, подпишись на наш канал!",
        'sub_btn': "📢 Подписаться на канал",
        'sub_check': "✅ Я подписался",
        'sub_ok': "✅ Спасибо за подписку! Теперь можешь присылать ссылки.",
        'sub_fail': "❌ Ты еще не подписался!",
        'wait_pin': "⏳ Достаю картинку из Pinterest...",
        'wait_vid': "⏳ Пробую скачать из {platform}...",
        'done_vid': "📥 Скачано из {platform}",
        'err_not_found': "❌ Не удалось найти файл.",
        'err_down': "❌ Ошибка загрузки: {error}",
        'err_private': "❌ Не удалось скачать. Возможно, аккаунт приватный.",
        'only_links': "Отправь мне ссылку на Pinterest, Instagram или TikTok."
    },
    'en': {
        'start': "Hi! Send me a link to Pinterest, Instagram, or TikTok, and I'll download it!",
        'sub_req': "🛑 Please subscribe to our channel to download files!",
        'sub_btn': "📢 Subscribe to Channel",
        'sub_check': "✅ I subscribed",
        'sub_ok': "✅ Thanks for subscribing! You can now send links.",
        'sub_fail': "❌ You haven't subscribed yet!",
        'wait_pin': "⏳ Fetching image from Pinterest...",
        'wait_vid': "⏳ Trying to download from {platform}...",
        'done_vid': "📥 Downloaded from {platform}",
        'err_not_found': "❌ Could not find the file.",
        'err_down': "❌ Download error: {error}",
        'err_private': "❌ Failed to download. The account might be private.",
        'only_links': "Please send a valid link to Pinterest, Instagram, or TikTok."
    },
    'uz': {
        'start': "Salom! Pinterest, Instagram yoki TikTok havolasini yuboring, men yuklab beraman!",
        'sub_req': "🛑 Fayllarni yuklab olish uchun kanalimizga obuna bo'ling!",
        'sub_btn': "📢 Kanalga obuna bo'lish",
        'sub_check': "✅ Obuna bo'ldim",
        'sub_ok': "✅ Obuna uchun rahmat! Endi havolalarni yuborishingiz mumkin.",
        'sub_fail': "❌ Siz hali obuna bo'lmadingiz!",
        'wait_pin': "⏳ Pinterest'dan rasm olinmoqda...",
        'wait_vid': "⏳ {platform} tarmog'idan yuklanmoqda...",
        'done_vid': "📥 {platform} tarmog'idan yuklandi",
        'err_not_found': "❌ Fayl topilmadi.",
        'err_down': "❌ Yuklashda xatolik: {error}",
        'err_private': "❌ Yuklab bo'lmadi. Ehtimol, akkaunt yopiq (privat).",
        'only_links': "Iltimos, Pinterest, Instagram yoki TikTok havolasini yuboring."
    }
}

# Функция для получения текста на нужном языке
def get_text(lang_code: str, key: str, **kwargs):
    # Если языка пользователя нет в словаре, ставим русский по умолчанию
    lang = lang_code if lang_code in TEXTS else 'ru'
    text = TEXTS[lang].get(key, TEXTS['ru'][key])
    return text.format(**kwargs) if kwargs else text

# --- СОСТОЯНИЯ ДЛЯ РАССЫЛКИ ---
class AdminState(StatesGroup):
    waiting_for_broadcast_message = State()

# --- БАЗА ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect("users.db") as db:
        await db.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)")
        # Безопасно пытаемся добавить колонку статистики (если ее еще нет)
        try:
            await db.execute("ALTER TABLE users ADD COLUMN downloads INTEGER DEFAULT 0")
        except:
            pass # Если колонка уже есть, игнорируем ошибку
        await db.commit()

async def add_user(user_id: int):
    async with aiosqlite.connect("users.db") as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        await db.commit()

async def add_download_stat(user_id: int):
    async with aiosqlite.connect("users.db") as db:
        await db.execute("UPDATE users SET downloads = downloads + 1 WHERE user_id = ?", (user_id,))
        await db.commit()

async def get_stats():
    async with aiosqlite.connect("users.db") as db:
        async with db.execute("SELECT COUNT(*), SUM(downloads) FROM users") as cursor:
            row = await cursor.fetchone()
            users_count = row[0]
            downloads_count = row[1] if row[1] else 0
            return users_count, downloads_count

async def get_all_users():
    async with aiosqlite.connect("users.db") as db:
        async with db.execute("SELECT user_id FROM users") as cursor:
            return [row[0] for row in await cursor.fetchall()]

# --- ПРОВЕРКА ПОДПИСКИ ---
async def check_subscription(bot: Bot, user_id: int) -> bool:
    if user_id in ADMIN_IDS: return True
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        if member.status in ['member', 'administrator', 'creator']: return True
        return False
    except Exception:
        return False

def get_sub_keyboard(lang: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=get_text(lang, 'sub_btn'), url=f"https://t.me/{CHANNEL_USERNAME.replace('@', '')}")],
        [InlineKeyboardButton(text=get_text(lang, 'sub_check'), callback_data="check_sub")]
    ])

# --- АДМИН-ПАНЕЛЬ ---
def get_admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="💾 Скачать БД", callback_data="admin_export")]
    ])

@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    await message.answer("👑 Добро пожаловать в панель администратора!", reply_markup=get_admin_keyboard())

@router.callback_query(F.data.startswith("admin_"))
async def admin_callbacks(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if callback.from_user.id not in ADMIN_IDS: return
    action = callback.data.split("_")[1]

    if action == "stats":
        users, downloads = await get_stats()
        text = f"📊 <b>Статистика бота:</b>\n👥 Всего пользователей: {users}\n📥 Скачано файлов: {downloads}"
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=get_admin_keyboard())
    elif action == "export":
        await callback.answer("⏳ Выгружаю базу...")
        await callback.message.answer_document(document=FSInputFile("users.db"), caption="💾 База данных")
    elif action == "broadcast":
        await callback.message.answer("📢 Отправь мне сообщение для рассылки.\nДля отмены напиши 'отмена'.")
        await state.set_state(AdminState.waiting_for_broadcast_message)

@router.message(AdminState.waiting_for_broadcast_message)
async def process_broadcast(message: Message, state: FSMContext, bot: Bot):
    if message.text and message.text.lower() == 'отмена':
        await message.answer("❌ Рассылка отменена.", reply_markup=get_admin_keyboard())
        return await state.clear()

    users = await get_all_users()
    await message.answer(f"⏳ Начинаю рассылку для {len(users)} пользователей...")
    success, failed = 0, 0
    for user_id in users:
        try:
            await message.send_copy(chat_id=user_id)
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    await message.answer(f"✅ <b>Рассылка завершена!</b>\nУспешно: {success}\nЗаблокировали: {failed}", parse_mode="HTML", reply_markup=get_admin_keyboard())
    await state.clear()

# --- ФУНКЦИИ СКАЧИВАНИЯ ---
async def get_pinterest_image_url(url: str):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, allow_redirects=True) as response:
                html = await response.text()
                match = re.search(r'(https://i\.pinimg\.com/(?:736x|474x|originals)/[^"\']+\.(?:jpg|png|jpeg))', html)
                if match: return match.group(1).replace("736x", "originals").replace("474x", "originals")
    except Exception: pass
    return None

def download_media(url: str, filename: str):
    ydl_opts = {'outtmpl': filename, 'format': 'best', 'quiet': True, 'no_warnings': True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl: ydl.download([url])
        return True
    except Exception: return False

# --- ОСНОВНЫЕ ОБРАБОТЧИКИ ---
@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot):
    await add_user(message.from_user.id)
    lang = message.from_user.language_code
    
    if not await check_subscription(bot, message.from_user.id):
        await message.answer(get_text(lang, 'sub_req'), reply_markup=get_sub_keyboard(lang))
        return
    await message.answer(get_text(lang, 'start'))

@router.callback_query(F.data == "check_sub")
async def callback_check_sub(callback: CallbackQuery, bot: Bot):
    lang = callback.from_user.language_code
    if await check_subscription(bot, callback.from_user.id):
        await callback.message.edit_text(get_text(lang, 'sub_ok'))
    else:
        await callback.answer(get_text(lang, 'sub_fail'), show_alert=True)

@router.message(F.text.contains("pin.it") | F.text.contains("pinterest.com") | F.text.contains("instagram.com") | F.text.contains("tiktok.com"))
async def handle_links(message: Message, bot: Bot):
    lang = message.from_user.language_code
    if not await check_subscription(bot, message.from_user.id):
        return await message.answer(get_text(lang, 'sub_req'), reply_markup=get_sub_keyboard(lang))

    url = message.text.strip()
    if "pin.it" in url or "pinterest.com" in url:
        status_msg = await message.answer(get_text(lang, 'wait_pin'))
        direct_image_url = await get_pinterest_image_url(url)
        if not direct_image_url:
            return await status_msg.edit_text(get_text(lang, 'err_not_found'))
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(direct_image_url) as response:
                    if response.status == 200:
                        image_bytes = await response.read()
                        photo_file = BufferedInputFile(image_bytes, filename="pinterest_original.jpg")
                        await message.answer_photo(photo=photo_file)
                        await message.answer_document(document=photo_file)
                        await status_msg.delete()
                        await add_download_stat(message.from_user.id) # Плюсуем в статистику
                    else:
                        await status_msg.edit_text(get_text(lang, 'err_down', error="Server error"))
        except Exception as e:
            await status_msg.edit_text(get_text(lang, 'err_down', error=str(e)))
    else:
        platform = "TikTok" if "tiktok.com" in url else "Instagram"
        status_msg = await message.answer(get_text(lang, 'wait_vid', platform=platform))
        filename = f"video_{message.from_user.id}_{message.message_id}.mp4"
        success = await asyncio.to_thread(download_media, url, filename)
        if success and os.path.exists(filename):
            try:
                await message.answer_video(video=FSInputFile(filename), caption=get_text(lang, 'done_vid', platform=platform))
                await status_msg.delete()
                await add_download_stat(message.from_user.id) # Плюсуем в статистику
            except Exception as e:
                await message.answer(get_text(lang, 'err_down', error=str(e)))
            finally:
                if os.path.exists(filename): os.remove(filename)
        else:
            await status_msg.edit_text(get_text(lang, 'err_private'))

@router.message(F.text)
async def handle_other_text(message: Message):
    lang = message.from_user.language_code
    await message.answer(get_text(lang, 'only_links'))

from aiohttp import web

# --- ВЕБ-ЗАГЛУШКА ДЛЯ БЕСПЛАТНОГО ХОСТИНГА ---
async def ping_handler(request):
    return web.Response(text="Бот работает и не спит!")

async def main():
    await init_db()
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)

    # Запускаем мини-сайт на порту, который выдаст Render
    app = web.Application()
    app.router.add_get('/', ping_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

    # Запускаем самого бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())