import asyncio
import os
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, FSInputFile
import yt_dlp

# =============================================
# SOZLAMALAR - Bu yerga o'z tokeningizni kiriting
# =============================================
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"  # @BotFather dan olingan token
DOWNLOAD_DIR = Path("downloads")   # MP3 fayllar saqlanadigan papka
# =============================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Bot va Dispatcher yaratish
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Downloads papkasini yaratish
DOWNLOAD_DIR.mkdir(exist_ok=True)


def search_and_download(query: str, output_path: Path) -> dict | None:
    """
    YouTube'dan qo'shiq qidiradi va MP3 formatida yuklab oladi.
    Muvaffaqiyatli bo'lsa video ma'lumotlarini qaytaradi, aks holda None.
    """
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(output_path / "%(id)s.%(ext)s"),
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
        "default_search": "ytsearch1",  # YouTube'dan 1 ta natija
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "max_filesize": 50 * 1024 * 1024,  # 50MB limit (Telegram limiti)
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"ytsearch1:{query}", download=True)
        if not info or "entries" not in info or not info["entries"]:
            return None

        video = info["entries"][0]
        video_id = video.get("id")
        title = video.get("title", query)
        duration = video.get("duration", 0)
        uploader = video.get("uploader", "Noma'lum")

        # MP3 fayl yo'li
        mp3_file = output_path / f"{video_id}.mp3"

        return {
            "file": mp3_file,
            "title": title,
            "duration": duration,
            "uploader": uploader,
            "video_id": video_id,
        }


def format_duration(seconds: int) -> str:
    """Soniyani MM:SS formatiga o'tkazadi."""
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


# ──────────────── HANDLERS ────────────────

@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "🎵 <b>Musiqa Bot</b>ga xush kelibsiz!\n\n"
        "Qo'shiq nomini yozing va men uni YouTube'dan topib, "
        "MP3 formatida yuboraman.\n\n"
        "<i>Misol: Seni Sevaman yoki Shape of You Ed Sheeran</i>",
        parse_mode="HTML"
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "📖 <b>Yordam</b>\n\n"
        "• Qo'shiq nomini yozing → bot MP3 yuboradi\n"
        "• O'zbek yoki xorijiy qo'shiqlarni qidirishingiz mumkin\n"
        "• Qidiruvda ijrochi nomini ham yozsangiz natija aniqroq bo'ladi\n\n"
        "<i>Misol: Ulug'bek Rahmatullayev Seni Ko'rgach</i>",
        parse_mode="HTML"
    )


@dp.message()
async def handle_song_request(message: Message):
    query = message.text.strip()

    if not query:
        await message.answer("❌ Qo'shiq nomini kiriting!")
        return

    # Foydalanuvchiga kutish xabarini yuborish
    status_msg = await message.answer(
        f"🔍 <b>Qidirilmoqda:</b> {query}\n"
        "⏳ Iltimos kuting...",
        parse_mode="HTML"
    )

    try:
        # YouTube'dan yuklash (bloklaydi, shuning uchun executor'da ishlatamiz)
        # asyncio.get_running_loop() — Python 3.7+ da to'g'ri usul
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, search_and_download, query, DOWNLOAD_DIR
        )

        if not result:
            await status_msg.edit_text(
                "❌ Qo'shiq topilmadi. Boshqa nom bilan urinib ko'ring."
            )
            return

        mp3_file: Path = result["file"]

        if not mp3_file.exists():
            await status_msg.edit_text(
                "❌ Fayl yuklab olishda xatolik yuz berdi."
            )
            return

        # Fayl hajmini tekshirish (Telegram max 50MB)
        file_size = mp3_file.stat().st_size
        if file_size > 50 * 1024 * 1024:
            await status_msg.edit_text(
                "❌ Fayl hajmi juda katta (50MB dan oshiq). "
                "Boshqa qo'shiqni urinib ko'ring."
            )
            mp3_file.unlink(missing_ok=True)
            return

        # Statusni yangilash
        await status_msg.edit_text(
            f"📤 <b>Yuklanmoqda...</b>\n"
            f"🎵 {result['title']}",
            parse_mode="HTML"
        )

        # MP3 ni Telegram'ga yuborish
        audio = FSInputFile(mp3_file, filename=f"{result['title']}.mp3")

        caption = (
            f"🎵 <b>{result['title']}</b>\n"
            f"👤 {result['uploader']}\n"
            f"⏱ {format_duration(result['duration'])}"
        )

        await message.answer_audio(
            audio=audio,
            caption=caption,
            parse_mode="HTML",
            title=result["title"],
            performer=result["uploader"],
        )

        # Status xabarini o'chirish
        await status_msg.delete()

        logger.info(f"Yuborildi: {result['title']} → {message.from_user.id}")

    except yt_dlp.utils.DownloadError as e:
        logger.error(f"yt-dlp xatoligi: {e}")
        await status_msg.edit_text(
            "❌ Yuklab olishda xatolik. Qo'shiq topilmadi yoki cheklangan."
        )
    except Exception as e:
        logger.error(f"Kutilmagan xatolik: {e}")
        await status_msg.edit_text(
            "❌ Xatolik yuz berdi. Qaytadan urinib ko'ring."
        )
    finally:
        # Faylni o'chirish (diskni tozalash)
        try:
            if result and result["file"].exists():
                result["file"].unlink()
        except Exception:
            pass


async def main():
    logger.info("Bot ishga tushmoqda...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
