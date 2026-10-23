# Bot de Telegram para YouTube (yt-dlp) en Render

## 1. Crear el bot
Habla con @BotFather en Telegram -> /newbot -> copia el token.

## 2. Subir a GitHub
Sube esta carpeta (bot.py, requirements.txt, Dockerfile) a un repositorio.

## 3. Desplegar en Render
1. New + -> Web Service -> conecta tu repo.
2. Runtime: Docker (detecta el Dockerfile).
3. Environment -> añade BOT_TOKEN = tu token.
4. Deploy.

Plan gratis: el servicio se duerme tras 15 min sin tráfico HTTP.
Usa UptimeRobot para hacer ping a la URL de Render cada 5 min,
o elige "Background Worker" (de pago) y elimina el servidor health de bot.py.

## Si YouTube bloquea ("Sign in to confirm you're not a bot")
Las IPs de Render suelen estar bloqueadas. Exporta tus cookies de YouTube
(extensión "Get cookies.txt LOCALLY") y pega el contenido en una variable
de entorno COOKIES. Usa una cuenta secundaria, hay riesgo de bloqueo.

## Mantener actualizado
yt-dlp se rompe seguido: redeploya de vez en cuando para obtener la última versión.
