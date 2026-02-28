#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Python 3.11+ YouTube Video + Musiqa Yuklovchi Telegram Bot
"""

import os
import asyncio
import logging
import contextlib
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field
from datetime import datetime

from dotenv import load_dotenv
import yt_dlp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest

# ── Sozlamalar ──────────────────────────────────────────────────────────────
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
MAX_FILE_SIZE: int = 50 * 1024 * 1024   # 50 MB
DOWNLOAD_PATH: Path = Path("downloads")
ALLOWED_DOMAINS: tuple[str, ...] = (
    "youtube.com",
    "youtu.be",
    "m.youtube.com",
    "www.youtube.com",
)

DOWNLOAD_PATH.mkdir(exist_ok=True)

# User-Agent (bot detection'ni kamaytiradi)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


# ── Ma'lumot modellari ───────────────────────────────────────────────────────
@dataclass
class FormatInfo:
    height: int
    format_id: str
    filesize: int
    ext: str

    @property
    def quality_label(self) -> str:
        return f"{self.height}p"

    @property
    def size_mb(self) -> float:
        return self.filesize / (1024 * 1024)


@dataclass
class VideoInfo:
    title: str
    duration: int
    thumbnail: str
    video_id: str
    url: str
    formats: list[FormatInfo] = field(default_factory=list)

    @property
    def duration_str(self) -> str:
        m, s = divmod(self.duration, 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ── FSM holatlari ────────────────────────────────────────────────────────────
class DownloadStates(StatesGroup):
    choosing_type = State()       # video yoki audio tanlash
    waiting_for_quality = State() # video sifat tanlash
    downloading = State()


# ── Yuklovchi xizmat ─────────────────────────────────────────────────────────
class YouTubeDownloader:

    @staticmethod
    def is_valid_url(url: str) -> bool:
        url = url.lower().strip()
        return any(domain in url for domain in ALLOWED_DOMAINS)

    def _base_opts(self) -> dict:
        return {
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 20,
            "http_headers": {"User-Agent": USER_AGENT},
        }

    async def fetch_info(self, url: str) -> Optional[VideoInfo]:
        """Video haqida ma'lumot olish."""
        ydl_opts = {**self._base_opts(), "extract_flat": False}

        try:
            loop = asyncio.get_running_loop()

            def _extract() -> dict:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(url, download=False)

            info: dict = await loop.run_in_executor(None, _extract)
            if not info:
                return None

            formats: list[FormatInfo] = []
            seen_heights: set[int] = set()

            for f in info.get("formats", []):
                height: Optional[int] = f.get("height")
                if not height or height > 1080 or height in seen_heights:
                    continue

                # Faqat audio+video birlashgan yoki faqat video streamlar
                vcodec = f.get("vcodec", "none")
                if not vcodec or vcodec == "none":
                    continue

                filesize: int = f.get("filesize") or f.get("filesize_approx") or 0
                if filesize <= 0 or filesize > MAX_FILE_SIZE * 2:
                    continue

                seen_heights.add(height)
                formats.append(
                    FormatInfo(
                        height=height,
                        format_id=f["format_id"],
                        filesize=filesize,
                        ext=f.get("ext", "mp4"),
                    )
                )

            formats.sort(key=lambda x: x.height)

            return VideoInfo(
                title=info.get("title", "Noma'lum"),
                duration=info.get("duration", 0),
                thumbnail=info.get("thumbnail", ""),
                video_id=info.get("id", ""),
                url=url,
                formats=formats,
            )

        except Exception as exc:
            logger.error("Video ma'lumotlari olishda xatolik: %s", exc)
            return None

    async def download_video(self, url: str, format_id: str) -> Optional[Path]:
        """Videoni yuklab olish (ffmpeg shart emas)."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        output_template = str(DOWNLOAD_PATH / f"vid_{timestamp}_%(title)s.%(ext)s")

        ydl_opts = {
            **self._base_opts(),
            # ffmpeg shart bo'lmagan format — birlashgan stream
            "format": f"{format_id}/best[height<=1080][ext=mp4]/best[height<=1080]",
            "outtmpl": output_template,
            "restrictfilenames": True,
            "retries": 3,
        }

        return await self._run_download(url, ydl_opts, f"vid_{timestamp}_")

    async def download_audio(self, url: str) -> Optional[Path]:
        """Musiqa (MP3) yuklab olish (ffmpeg shart emas)."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        output_template = str(DOWNLOAD_PATH / f"aud_{timestamp}_%(title)s.%(ext)s")

        ydl_opts = {
            **self._base_opts(),
            # ffmpeg yo'q bo'lsa m4a/webm yuklanadi, bor bo'lsa mp3
            "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio",
            "outtmpl": output_template,
            "restrictfilenames": True,
            "retries": 3,
            # ffmpeg mavjud bo'lsa mp3 ga o'tkazadi, bo'lmasa o'tkazmasdan qoldiradi
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            "ignoreerrors": False,
        }

        # ffmpeg yo'q bo'lsa postprocessor'siz yuklaymiz
        try:
            return await self._run_download(url, ydl_opts, f"aud_{timestamp}_")
        except Exception:
            # ffmpeg yo'q — postprocessor'siz qayta urinish
            ydl_opts.pop("postprocessors", None)
            return await self._run_download(url, ydl_opts, f"aud_{timestamp}_")

    async def _run_download(self, url: str, ydl_opts: dict, prefix: str) -> Optional[Path]:
        """Umumiy yuklab olish funksiyasi."""
        try:
            loop = asyncio.get_running_loop()
            downloaded_paths: list[Path] = []

            def _on_finish(filepath: str) -> None:
                downloaded_paths.append(Path(filepath))

            def _download() -> None:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.add_post_hook(_on_finish)
                    ydl.extract_info(url, download=True)

            await loop.run_in_executor(None, _download)

            if downloaded_paths and downloaded_paths[0].exists():
                return downloaded_paths[0]

            # Zaxira: glob orqali qidirish
            for f in sorted(DOWNLOAD_PATH.glob(f"{prefix}*")):
                if f.is_file():
                    return f

        except Exception as exc:
            logger.error("Yuklab olishda xatolik: %s", exc)

        return None


# ── Klaviaturalar ────────────────────────────────────────────────────────────
def build_type_keyboard() -> types.InlineKeyboardMarkup:
    """Video yoki Audio tanlash."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🎥 Video (MP4)", callback_data="type:video")
    builder.button(text="🎵 Musiqa (MP3)", callback_data="type:audio")
    builder.button(text="❌ Bekor qilish", callback_data="cancel")
    builder.adjust(2)
    return builder.as_markup()


def build_quality_keyboard(formats: list[FormatInfo]) -> types.InlineKeyboardMarkup:
    """Video sifat tanlash."""
    builder = InlineKeyboardBuilder()
    for fmt in formats[:6]:
        builder.button(
            text=f"🎥 {fmt.quality_label}  ({fmt.size_mb:.1f} MB)",
            callback_data=f"dl:{fmt.format_id}",
        )
    builder.button(text="❌ Bekor qilish", callback_data="cancel")
    builder.adjust(2)
    return builder.as_markup()


# ── Bot va Dispatcher ────────────────────────────────────────────────────────
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher(storage=MemoryStorage())
downloader = YouTubeDownloader()


# ── Yordamchi funksiyalar ────────────────────────────────────────────────────
async def safe_edit(msg: Message, text: str, **kwargs) -> None:
    with contextlib.suppress(TelegramBadRequest):
        await msg.edit_text(text, **kwargs)


async def cleanup(path: Optional[Path]) -> None:
    if path and path.exists():
        with contextlib.suppress(OSError):
            path.unlink()
            logger.info("Fayl o'chirildi: %s", path.name)


# ── Handlerlar ───────────────────────────────────────────────────────────────
@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        f"👋 <b>Salom, {message.from_user.full_name}!</b>\n\n"
        "🤖 Men YouTube video va musiqa yuklab beruvchi botman.\n\n"
        "<b>📥 Qanday ishlatish:</b>\n"
        "1. YouTube havolasini yuboring\n"
        "2. 🎥 Video yoki 🎵 Musiqa tanlang\n"
        "3. Fayl yuklanib sizga yuboriladi\n\n"
        "⚠️ <b>Cheklov:</b> Maksimal hajm — 50 MB\n\n"
        "<i>Havolani yuboring va boshlang!</i>"
    )


@dp.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "❓ <b>Yordam</b>\n\n"
        "YouTube havolasini yuboring — bot video yoki musiqa yuklaydi.\n\n"
        "<b>Buyruqlar:</b>\n"
        "/start — Botni qayta ishga tushirish\n"
        "/help — Yordam\n"
        "/cancel — Joriy amalni bekor qilish\n\n"
        "⚠️ <b>Eslatmalar:</b>\n"
        "• Fayl 50 MB dan oshmasligi kerak\n"
        "• Maxfiy videolar yuklanmaydi\n"
        "• Musiqa MP3 yoki M4A formatida yuklanadi"
    )


@dp.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("✅ Joriy amal bekor qilindi.")


# ── URL qabul qilish ─────────────────────────────────────────────────────────
@dp.message(F.text)
async def handle_url(message: Message, state: FSMContext) -> None:
    url = message.text.strip()

    if not downloader.is_valid_url(url):
        await message.answer(
            "❌ <b>Noto'g'ri havola</b>\n\n"
            "Iltimos, to'g'ri YouTube havolasini yuboring.\n"
            "Masalan: <code>https://youtube.com/watch?v=...</code>"
        )
        return

    status = await message.answer("⏳ Video ma'lumotlari olinmoqda...")
    video_info = await downloader.fetch_info(url)

    if not video_info:
        await safe_edit(
            status,
            "❌ <b>Video ma'lumotlarini olishda xatolik</b>\n\n"
            "Mumkin sabablar:\n"
            "• Video mavjud emas yoki o'chirilgan\n"
            "• Video maxfiy (private)\n"
            "• Mualliflik huquqi cheklovi\n"
            "• Tarmoq xatosi",
        )
        return

    await safe_edit(
        status,
        f"📹 <b>{video_info.title}</b>\n\n"
        f"⏱ Davomiylik: <b>{video_info.duration_str}</b>\n\n"
        "👇 <b>Nimani yuklab olmoqchisiz?</b>",
        reply_markup=build_type_keyboard(),
    )

    await state.update_data(
        url=url,
        title=video_info.title,
        duration_str=video_info.duration_str,
        format_ids=[f.format_id for f in video_info.formats if f.filesize <= MAX_FILE_SIZE],
        formats_data=[
            {"height": f.height, "format_id": f.format_id,
             "filesize": f.filesize, "ext": f.ext}
            for f in video_info.formats if f.filesize <= MAX_FILE_SIZE
        ],
    )
    await state.set_state(DownloadStates.choosing_type)


# ── Tur tanlash (Video / Audio) ──────────────────────────────────────────────
@dp.callback_query(F.data.startswith("type:"))
async def type_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    chosen = callback.data.removeprefix("type:")
    data = await state.get_data()
    title = data.get("title", "Video")

    if chosen == "audio":
        # Musiqa yuklab olish
        await safe_edit(
            callback.message,
            f"🎵 <b>Musiqa yuklanmoqda:</b> {title}\n\n⏳ Iltimos, kuting...",
        )
        await state.set_state(DownloadStates.downloading)

        url = data.get("url")
        file_path = await downloader.download_audio(url)
        await send_audio_file(callback, file_path, title, state)

    elif chosen == "video":
        # Video sifat tanlash
        formats_data = data.get("formats_data", [])
        formats = [
            FormatInfo(
                height=f["height"], format_id=f["format_id"],
                filesize=f["filesize"], ext=f["ext"]
            )
            for f in formats_data
        ]

        if not formats:
            await safe_edit(
                callback.message,
                "❌ <b>Mos video format topilmadi</b>\n\n"
                "50 MB dan kichik video mavjud emas. Musiqa sifatida yuklab ko'ring.",
            )
            await state.clear()
            return

        await safe_edit(
            callback.message,
            f"📹 <b>{title}</b>\n\n"
            f"📊 Mavjud sifatlar: <b>{len(formats)} ta</b>\n\n"
            "👇 <b>Kerakli sifatni tanlang:</b>",
            reply_markup=build_quality_keyboard(formats),
        )
        await state.set_state(DownloadStates.waiting_for_quality)


# ── Bekor qilish ─────────────────────────────────────────────────────────────
@dp.callback_query(F.data == "cancel")
async def cancel_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer("Bekor qilindi")
    await state.clear()
    await safe_edit(callback.message, "✅ Yuklash bekor qilindi.")


# ── Video sifat tanlash ───────────────────────────────────────────────────────
@dp.callback_query(F.data.startswith("dl:"))
async def quality_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()

    format_id = callback.data.removeprefix("dl:")
    data = await state.get_data()

    url: Optional[str] = data.get("url")
    title: str = data.get("title", "Video")
    valid_ids: list[str] = data.get("format_ids", [])

    if not url or format_id not in valid_ids:
        await safe_edit(
            callback.message,
            "❌ <b>Xatolik</b>\n\nMa'lumotlar topilmadi. Qaytadan urinib ko'ring.",
        )
        await state.clear()
        return

    await state.set_state(DownloadStates.downloading)
    await safe_edit(
        callback.message,
        f"🎥 <b>Video yuklanmoqda:</b> {title}\n\n⏳ Iltimos, kuting...",
    )

    file_path = await downloader.download_video(url, format_id)
    await send_video_file(callback, file_path, title, state)


# ── Fayl yuborish yordamchilari ───────────────────────────────────────────────
async def send_video_file(
    callback: CallbackQuery,
    file_path: Optional[Path],
    title: str,
    state: FSMContext,
) -> None:
    if not file_path or not file_path.exists():
        await safe_edit(
            callback.message,
            "❌ <b>Yuklab olishda xatolik</b>\n\nVideo yuklanmadi. Qaytadan urinib ko'ring.",
        )
        await state.clear()
        return

    try:
        file_size = file_path.stat().st_size
        if file_size > MAX_FILE_SIZE:
            await safe_edit(
                callback.message,
                f"⚠️ <b>Video juda katta</b>\n\n"
                f"Hajm: <b>{file_size / (1024*1024):.1f} MB</b>\n"
                "Kichikroq sifatni tanlang yoki musiqani yuklab ko'ring.",
            )
            return

        await safe_edit(callback.message, "📤 Video yuborilmoqda...")
        await callback.message.answer_video(
            video=FSInputFile(file_path),
            caption=f"🎥 <b>{title}</b>",
            supports_streaming=True,
        )
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.delete()
        await callback.message.answer("✅ <b>Video muvaffaqiyatli yuklandi!</b>")

    except Exception as exc:
        logger.exception("Video yuborishda xatolik: %s", exc)
        await callback.message.answer(
            f"❌ <b>Videoni yuborishda xatolik</b>\n\n<code>{str(exc)[:150]}</code>"
        )
    finally:
        await cleanup(file_path)
        await state.clear()


async def send_audio_file(
    callback: CallbackQuery,
    file_path: Optional[Path],
    title: str,
    state: FSMContext,
) -> None:
    if not file_path or not file_path.exists():
        await safe_edit(
            callback.message,
            "❌ <b>Yuklab olishda xatolik</b>\n\nMusiqa yuklanmadi. Qaytadan urinib ko'ring.",
        )
        await state.clear()
        return

    try:
        file_size = file_path.stat().st_size
        if file_size > MAX_FILE_SIZE:
            await safe_edit(
                callback.message,
                f"⚠️ <b>Fayl juda katta</b>\n\n"
                f"Hajm: <b>{file_size / (1024*1024):.1f} MB</b>\n"
                "Telegram cheklovi: 50 MB",
            )
            return

        await safe_edit(callback.message, "📤 Musiqa yuborilmoqda...")
        await callback.message.answer_audio(
            audio=FSInputFile(file_path),
            caption=f"🎵 <b>{title}</b>",
            title=title,
        )
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.delete()
        await callback.message.answer("✅ <b>Musiqa muvaffaqiyatli yuklandi!</b>")

    except Exception as exc:
        logger.exception("Musiqa yuborishda xatolik: %s", exc)
        await callback.message.answer(
            f"❌ <b>Musiqani yuborishda xatolik</b>\n\n<code>{str(exc)[:150]}</code>"
        )
    finally:
        await cleanup(file_path)
        await state.clear()


# ── Noma'lum xabarlar ────────────────────────────────────────────────────────
@dp.message()
async def handle_unknown(message: Message) -> None:
    await message.answer(
        "❓ YouTube havolasini yuboring yoki /help ni bosing."
    )


# ── Asosiy funksiya ──────────────────────────────────────────────────────────
async def main() -> None:
    if not BOT_TOKEN:
        logger.critical("BOT_TOKEN topilmadi! .env faylini tekshiring.")
        return

    logger.info("Bot ishga tushmoqda...")

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        await bot.session.close()
        logger.info("Bot to'xtatildi.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot foydalanuvchi tomonidan to'xtatildi.")
