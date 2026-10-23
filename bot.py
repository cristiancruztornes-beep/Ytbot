import asyncio
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import yt_dlp
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

TOKEN = os.environ["BOT_TOKEN"]
MAX_MB = 50  # límite de la Bot API de Telegram para subir archivos

# Opcional: cookies de YouTube (contenido de cookies.txt en la variable COOKIES)
COOKIES_FILE = None
if os.environ.get("COOKIES"):
    COOKIES_FILE = "/tmp/cookies.txt"
    with open(COOKIES_FILE, "w") as f:
        f.write(os.environ["COOKIES"])


def download(url: str, outdir: str, audio_only: bool) -> str:
    opts = {
        "outtmpl": f"{outdir}/%(title).80s.%(ext)s",
        "noplaylist": True,
        "quiet": True,
        "max_filesize": MAX_MB * 1024 * 1024,
    }
    if COOKIES_FILE:
        opts["cookiefile"] = COOKIES_FILE
    if audio_only:
        opts["format"] = "bestaudio[ext=m4a]/bestaudio"
    else:
        opts["format"] = "bv*[height<=480][ext=mp4]+ba[ext=m4a]/b[height<=480]/b"
        opts["merge_output_format"] = "mp4"
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Envíame un enlace de YouTube y te devuelvo el video (máx. 50 MB).\n"
        "Usa /audio <enlace> para recibir solo el audio."
    )


async def handle(update: Update, ctx: ContextTypes.DEFAULT_TYPE, audio=False):
    text = update.message.text or ""
    url = (ctx.args[0] if ctx.args else text).strip()
    if "youtu" not in url:
        await update.message.reply_text("Envíame un enlace válido de YouTube.")
        return
    msg = await update.message.reply_text("Descargando… ⏳")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            path = await asyncio.to_thread(download, url, tmp, audio)
            if not os.path.exists(path):
                raise RuntimeError("no file")
            if os.path.getsize(path) > MAX_MB * 1024 * 1024:
                await msg.edit_text("El archivo supera los 50 MB de Telegram.")
                return
            with open(path, "rb") as f:
                if audio:
                    await update.message.reply_audio(f)
                else:
                    await update.message.reply_video(f, supports_streaming=True)
            await msg.delete()
        except Exception as e:
            await msg.edit_text(f"No pude descargarlo: {str(e)[:200]}")


async def audio_cmd(update, ctx):
    await handle(update, ctx, audio=True)


async def video_msg(update, ctx):
    await handle(update, ctx, audio=False)


class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


def run_health():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), Health).serve_forever()


if __name__ == "__main__":
    threading.Thread(target=run_health, daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("audio", audio_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, video_msg))
    app.run_polling()
