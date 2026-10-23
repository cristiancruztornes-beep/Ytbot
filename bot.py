import asyncio
import logging
import os
import re
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

logging.basicConfig(level=logging.INFO)
TOKEN = os.environ["BOT_TOKEN"]
MAX_MB = 50  # límite de la Bot API de Telegram para subir archivos
VIDEO_ID = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/|live/)([A-Za-z0-9_-]{11})")

# Opcional: cookies de YouTube (contenido de cookies.txt en la variable COOKIES)
COOKIES_FILE = None
if os.environ.get("COOKIES"):
    COOKIES_FILE = "/tmp/cookies.txt"
    with open(COOKIES_FILE, "w") as f:
        f.write(os.environ["COOKIES"])


def base_opts():
    opts = {"noplaylist": True, "quiet": True}
    if COOKIES_FILE:
        opts["cookiefile"] = COOKIES_FILE
    # Opcional: variable YT_CLIENTS, ej. "tv,web_safari" para probar otros clientes
    clients = os.environ.get("YT_CLIENTS")
    if clients:
        opts["extractor_args"] = {"youtube": {"player_client": clients.split(",")}}
    return opts


def get_heights(url):
    """Devuelve las resoluciones que realmente ofrece YouTube para este video."""
    with yt_dlp.YoutubeDL(base_opts()) as ydl:
        info = ydl.extract_info(url, download=False)
    heights = {
        f["height"]
        for f in info.get("formats", [])
        if f.get("height") and f.get("vcodec") not in (None, "none")
    }
    return sorted(h for h in heights if h <= 1080)


def download(url, outdir, audio_only, height):
    opts = base_opts()
    opts["outtmpl"] = f"{outdir}/%(title).80s.%(ext)s"
    opts["max_filesize"] = MAX_MB * 1024 * 1024
    if audio_only:
        opts["format"] = "bestaudio/best"
    else:
        opts["format"] = f"bv*[height<={height}]+ba/b[height<={height}]/b"
        opts["merge_output_format"] = "mp4"
        opts["postprocessor_args"] = {"merger": ["-movflags", "+faststart"]}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        downloads = info.get("requested_downloads") or []
        if downloads and os.path.exists(downloads[0].get("filepath", "")):
            return downloads[0]["filepath"], info
        return None, info


async def process(message, status, url, audio, height):
    with tempfile.TemporaryDirectory() as tmp:
        try:
            path, info = await asyncio.to_thread(download, url, tmp, audio, height)
            if not path or os.path.getsize(path) > MAX_MB * 1024 * 1024:
                await status.edit_text(
                    "El archivo supera los 50 MB de Telegram. Prueba con una calidad menor."
                )
                return
            with open(path, "rb") as f:
                if audio:
                    await message.reply_audio(f, duration=info.get("duration"))
                else:
                    await message.reply_video(
                        f,
                        caption=f"{info.get('height')}p",
                        supports_streaming=True,
                        duration=info.get("duration"),
                        width=info.get("width"),
                        height=info.get("height"),
                    )
            await status.delete()
        except Exception as e:
            logging.exception("Error descargando")
            await status.edit_text(f"No pude descargarlo: {str(e)[:200]}")


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Envíame un enlace de YouTube y elige la calidad (máx. 50 MB).\n"
        "Usa /audio <enlace> para recibir solo el audio."
    )


async def link_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    m = VIDEO_ID.search(text)
    if "youtu" not in text or not m:
        await update.message.reply_text("Envíame un enlace válido de YouTube.")
        return
    vid = m.group(1)
    status = await update.message.reply_text("Buscando calidades disponibles… 🔎")
    try:
        heights = await asyncio.to_thread(
            get_heights, f"https://www.youtube.com/watch?v={vid}"
        )
    except Exception as e:
        logging.exception("Error leyendo formatos")
        await status.edit_text(f"No pude leer el video: {str(e)[:200]}")
        return
    buttons = [
        InlineKeyboardButton(f"{h}p", callback_data=f"{h}|{vid}") for h in heights
    ]
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton("🎵 Solo audio", callback_data=f"a|{vid}")])
    label = "Elige la calidad:"
    if len(heights) <= 1:
        label = "YouTube solo ofrece esta calidad desde el servidor para este video:"
    await status.edit_text(label, reply_markup=InlineKeyboardMarkup(rows))


async def choose(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    quality, vid = q.data.split("|")
    url = f"https://www.youtube.com/watch?v={vid}"
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
    app = Application.builder().token(TOKEN).concurrent_updates(True).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("audio", audio_cmd))
    app.add_handler(CallbackQueryHandler(choose))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, link_msg))
    app.run_polling(drop_pending_updates=True)
