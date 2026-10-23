import asyncio
import html
import logging
import os
import re
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import yt_dlp
from telethon import Button, TelegramClient, events
from telethon.tl.types import DocumentAttributeAudio, DocumentAttributeVideo

logging.basicConfig(level=logging.INFO)
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
TOKEN = os.environ["BOT_TOKEN"]
MAX_MB = 2000  # límite de Telegram por MTProto (2 GB)
VIDEO_ID = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/|live/)([A-Za-z0-9_-]{11})")

# Opcional: cookies de YouTube (contenido de cookies.txt en la variable COOKIES)
COOKIES_FILE = None
if os.environ.get("COOKIES"):
    COOKIES_FILE = "/tmp/cookies.txt"
    with open(COOKIES_FILE, "w") as f:
        f.write(os.environ["COOKIES"])

client = TelegramClient("bot_session", API_ID, API_HASH)
client.parse_mode = "html"  # <b>negrita</b> y <i>cursiva</i>


def bar(pct: int) -> str:
    filled = max(0, min(10, pct // 10))
    return "▰" * filled + "▱" * (10 - filled)


async def animate(msg, build, stop: asyncio.Event):
    """Edita el mensaje cada 3 s con emojis que cambian, hasta que stop se activa."""
    i = 0
    while not stop.is_set():
        try:
            await msg.edit(build(i))
        except Exception:
            pass
        i += 1
        try:
            await asyncio.wait_for(stop.wait(), 3)
        except asyncio.TimeoutError:
            pass


def base_opts():
    opts = {"noplaylist": True, "quiet": True}
    if COOKIES_FILE:
        opts["cookiefile"] = COOKIES_FILE
    clients = os.environ.get("YT_CLIENTS")  # ej. "tv,web_safari"
    if clients:
        opts["extractor_args"] = {"youtube": {"player_client": clients.split(",")}}
    return opts


def get_heights(url):
    with yt_dlp.YoutubeDL(base_opts()) as ydl:
        info = ydl.extract_info(url, download=False)
    heights = {
        f["height"]
        for f in info.get("formats", [])
        if f.get("height") and f.get("vcodec") not in (None, "none")
    }
    return sorted(h for h in heights if h <= 1080)


def download(url, outdir, audio_only, height, prog):
    def hook(d):
        if d["status"] == "downloading":
            prog["phase"] = "down"
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                prog["pct"] = int(d["downloaded_bytes"] * 100 / total)
        elif d["status"] == "finished":
            prog["phase"] = "proc"

    opts = base_opts()
    opts["outtmpl"] = f"{outdir}/%(title).80s.%(ext)s"
    opts["max_filesize"] = MAX_MB * 1024 * 1024
    opts["progress_hooks"] = [hook]
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


@client.on(events.NewMessage(pattern=r"^/start"))
async def start(event):
    await event.respond(
        "👋 <b>¡Hola!</b>\n\n"
        "Envíame un <b>enlace de YouTube</b> y elige la calidad "
        "<i>(hasta 2 GB)</i> 🎬\n"
        "Usa <b>/audio</b> <i>&lt;enlace&gt;</i> para recibir solo el audio 🎵"
    )


@client.on(events.NewMessage(incoming=True, func=lambda e: e.raw_text and not e.raw_text.startswith("/")))
async def on_link(event):
    m = VIDEO_ID.search(event.raw_text)
    if "youtu" not in event.raw_text or not m:
        await event.respond("⚠️ <b>Envíame un enlace válido de YouTube.</b>")
        return
    vid = m.group(1)
    status = await event.respond("🔍 <b>Buscando calidades</b> <i>.</i>")
    stop = asyncio.Event()
    task = asyncio.create_task(
        animate(
            status,
            lambda i: f"{['🔍', '🔎'][i % 2]} <b>Buscando calidades</b> <i>{'.' * (i % 3 + 1)}</i>",
            stop,
        )
    )
    try:
        heights = await asyncio.to_thread(
            get_heights, f"https://www.youtube.com/watch?v={vid}"
        )
    except Exception as e:
        logging.exception("Error leyendo formatos")
        stop.set()
        await task
        await status.edit(f"❌ <b>No pude leer el video</b>\n<i>{html.escape(str(e)[:200])}</i>")
        return
    stop.set()
    await task
    buttons = [Button.inline(f"🎬 {h}p", f"{h}|{vid}".encode()) for h in heights]
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    rows.append([Button.inline("🎵 Solo audio", f"a|{vid}".encode())])
    label = "✨ <b>Elige la calidad:</b>"
    if len(heights) <= 1:
        label = "ℹ️ <b>YouTube solo ofrece esta calidad desde el servidor para este video:</b>"
    await status.edit(label, buttons=rows)


async def process(chat_id, status, url, audio, height):
    prog = {"pct": 0, "phase": "down"}
    frames = ["⏬", "📥", "⬇️"]

    def build(i):
        if prog["phase"] == "proc":
            return f"⚙️ <b>Procesando</b> <i>{'.' * (i % 3 + 1)}</i>"
        return f"{frames[i % 3]} <b>Descargando</b> <i>{prog['pct']}%</i>\n{bar(prog['pct'])}"

    stop = asyncio.Event()
    task = asyncio.create_task(animate(status, build, stop))
    last = {"t": 0.0}

    async def progress(sent, total):
        if time.time() - last["t"] > 3:
            last["t"] = time.time()
            pct = sent * 100 // total
            try:
                await status.edit(f"📤 <b>Subiendo</b> <i>{pct}%</i>\n{bar(pct)}")
            except Exception:
                pass

    with tempfile.TemporaryDirectory() as tmp:
        try:
            path, info = await asyncio.to_thread(download, url, tmp, audio, height, prog)
            stop.set()
            await task
            if not path or os.path.getsize(path) > MAX_MB * 1024 * 1024:
                await status.edit(
                    "⚠️ <b>El archivo supera los 2 GB.</b>\n<i>Prueba con una calidad menor.</i>"
                )
                return
            await status.edit("📤 <b>Subiendo</b> <i>0%</i>\n" + bar(0))
            duration = int(info.get("duration") or 0)
            title = html.escape(info.get("title") or "")
            if audio:
                attrs = [DocumentAttributeAudio(duration=duration, title=info.get("title"))]
                caption = f"🎵 <b>{title}</b>"
            else:
                attrs = [
                    DocumentAttributeVideo(
                        duration=duration,
                        w=info.get("width") or 0,
                        h=info.get("height") or 0,
                        supports_streaming=True,
                    )
                ]
                caption = f"🎬 <b>{title}</b>\n<i>{info.get('height')}p</i>"
            await client.send_file(
                chat_id,
                path,
                caption=caption,
                attributes=attrs,
                supports_streaming=not audio,
                progress_callback=progress,
            )
            await status.delete()
        except Exception as e:
            logging.exception("Error descargando")
            stop.set()
            await task
            await status.edit(
                f"❌ <b>No pude descargarlo</b>\n<i>{html.escape(str(e)[:200])}</i>"
            )


@client.on(events.CallbackQuery())
async def on_button(event):
    quality, vid = event.data.decode().split("|")
    await event.answer()
    url = f"https://www.youtube.com/watch?v={vid}"
    audio = quality == "a"
    height = None if audio else int(quality)
    status = await event.edit("⏬ <b>Descargando</b> <i>0%</i>\n" + bar(0))
    await process(event.chat_id, status, url, audio, height)


@client.on(events.NewMessage(pattern=r"^/audio"))
async def audio_cmd(event):
    parts = event.raw_text.split()
    url = parts[1] if len(parts) > 1 else ""
    if "youtu" not in url:
        await event.respond("⚠️ <b>Uso:</b> /audio <i>&lt;enlace de YouTube&gt;</i>")
        return
    status = await event.respond("⏬ <b>Descargando</b> <i>0%</i>\n" + bar(0))
    await process(event.chat_id, status, url, True, None)


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
    client.start(bot_token=TOKEN)
    logging.info("Bot iniciado")
    client.run_until_disconnected()
