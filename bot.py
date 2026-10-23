import asyncio
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import yt_dlp
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

TOKEN = os.environ["BOT_TOKEN"]
MAX_MB = 50  # límite de la Bot API de Telegram para subir archivos
QUALITIES = [144, 240, 360, 480, 720, 1080]

# Opcional: cookies de YouTube (contenido de cookies.txt en la variable COOKIES)
COOKIES_FILE = None
if os.environ.get("COOKIES"):
    COOKIES_FILE = "/tmp/cookies.txt"
    with open(COOKIES_FILE, "w") as f:
        f.write(os.environ["COOKIES"])


def download(url, outdir, audio_only, height):
    opts = {
        "outtmpl": f"{outdir}/%(title).80s.%(ext)s",
        "noplaylist": True,
        "quiet": True,
        "max_filesize": MAX_MB * 1024 * 1024,
    }
    if COOKIES_FILE:
        opts["cookiefile"] = COOKIES_FILE
    if audio_only:
        opts["format"] = "bestaudio/best"
    else:
        opts["format"] = f"bv*[height<={height}]+ba/b[height<={height}]/b"
        opts["merge_output_format"] = "mp4"
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        downloads = info.get("requested_downloads") or []
        if downloads and os.path.exists(downloads[0].get("filepath", "")):
            return downloads[0]["filepath"]
        return None


async def process(message, status, url, audio, height):
    with tempfile.TemporaryDirectory() as tmp:
        try:
            path = await asyncio.to_thread(download, url, tmp, audio, height)
            if not path or os.path.getsize(path) > MAX_MB * 1024 * 1024:
                await status.edit_text(
                    "El archivo supera los 50 MB de Telegram. Prueba con una calidad menor."
                )
                return
            with open(path, "rb") as f:
                if audio:
                    await message.reply_audio(f)
                else:
                    await message.reply_video(f, supports_streaming=True)
            await status.delete()
        except Exception as e:
            await status.edit_text(f"No pude descargarlo: {str(e)[:200]}")


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Envíame un enlace de YouTube y elige la calidad (máx. 50 MB).\n"
        "Usa /audio <enlace> para recibir solo el audio."
    )


async def link_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    url = (update.message.text or "").strip()
    if "youtu" not in url:
        await update.message.reply_text("Envíame un enlace válido de YouTube.")
        return
    key = str(update.message.message_id)
    ctx.chat_data[key] = url
    rows = [
        [InlineKeyboardButton(f"{q}p", callback_data=f"{q}|{key}") for q in QUALITIES[:3]],
        [InlineKeyboardButton(f"{q}p", callback_data=f"{q}|{key}") for q in QUALITIES[3:]],
        [InlineKeyboardButton("🎵 Solo audio", callback_data=f"a|{key}")],
    ]
    await update.message.reply_text(
        "Elige la calidad:", reply_markup=InlineKeyboardMarkup(rows)
    )


async def choose(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    quality, key = q.data.split("|")
    url = ctx.chat_data.get(key)
    if not url:
        await q.edit_message_text("El enlace expiró, envíalo de nuevo.")
        return
    audio = quality == "a"
    height = None if audio else int(quality)
    await q.edit_message_text("Descargando… ⏳")
    await process(q.message, q.message, url, audio, height)


async def audio_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    url = ctx.args[0] if ctx.args else ""
    if "youtu" not in url:
        await update.message.reply_text("Uso: /audio <enlace de YouTube>")
        return
    status = await update.message.reply_text("Descargando… ⏳")
    await process(update.message, status, url, True, None)


class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


def run_health():
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 10000))), Health).serve_forever()


if __name__ == "__main__":
    threading.Thread(target=run_health, daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("audio", audio_cmd))
    app.add_handler(CallbackQueryHandler(choose))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, link_msg))
    app.run_polling()
