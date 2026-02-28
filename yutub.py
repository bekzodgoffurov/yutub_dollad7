import os
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
import yt_dlp

# --- SOZLAMALAR ---
TOKEN = "8286268772:AAEW_0xhwZP2I80-e0NPuZvr03cd2vEWfD0"  # BotFather'dan olingan token
DOWNLOAD_PATH = "downloads"

# Yuklamalar papkasini yaratish
if not os.path.exists(DOWNLOAD_PATH):
    os.makedirs(DOWNLOAD_PATH)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Botni ishga tushirish xabari."""
    await update.message.reply_text(
        "Salom! Menga YouTube linkini yuboring va men uni yuklab beraman. 🎥"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Linkni qabul qilish va sifat tanlash menyusini ko'rsatish."""
    url = update.message.text
    
    if "youtube.com" not in url and "youtu.be" not in url:
        await update.message.reply_text("Iltimos, haqiqiy YouTube linkini yuboring! ⚠️")
        return

    # Foydalanuvchiga sifat tanlash tugmalarini yuboramiz
    keyboard = [
        [
            InlineKeyboardButton("360p (Video)", callback_data=f"360|{url}"),
            InlineKeyboardButton("720p (Video)", callback_data=f"720|{url}"),
        ],
        [InlineKeyboardButton("MP3 (Audio)", callback_data=f"mp3|{url}")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text("Sifatni tanlang:", reply_markup=reply_markup)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tugma bosilganda yuklab olishni boshlash."""
    query = update.callback_query
    await query.answer()
    
    choice, url = query.data.split("|")
    await query.edit_message_text(f"Yuklanmoqda... Kuting ⏳ (Sifat: {choice})")

    # Yuklab olish sozlamalari
    ydl_opts = {
        'outtmpl': f'{DOWNLOAD_PATH}/%(title)s.%(ext)s',
    }

    if choice == "mp3":
        ydl_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        })
    else:
        # Berilgan sifatdan oshmagan eng yaxshi videoni tanlash
        ydl_opts.update({
            'format': f'bestvideo[height<={choice}]+bestaudio/best[height<={choice}]/best',
            'merge_output_format': 'mp4',
        })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            
            # Agar mp3 bo'lsa, kengaytmani o'zgartirish
            if choice == "mp3":
                filename = os.path.splitext(filename)[0] + ".mp3"

        # Faylni yuborish
        with open(filename, 'rb') as file:
            if choice == "mp3":
                await context.bot.send_audio(chat_id=query.message.chat_id, audio=file)
            else:
                await context.bot.send_video(chat_id=query.message.chat_id, video=file)

        # Yuklangan faylni serverdan o'chirish (joy tejash uchun)
        os.remove(filename)
        await query.delete_message()

    except Exception as e:
        await query.message.reply_text(f"Xatolik yuz berdi: {str(e)} ❌")

def main():
    """Botni yurgizish."""
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(button_callback))

    print("Bot ishga tushdi...")
    application.run_polling()

if name == 'main':
    main()