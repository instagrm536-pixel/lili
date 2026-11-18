# AEwolf Media Bot

Python Telegram media bot that extracts media links with yt-dlp and sends them through the Telegram Bot API.

## Features
- Accepts HTTP/HTTPS media links and uses yt-dlp to prefer a single-file MP4 video format; non-MP4 video fallbacks are sent as documents.
- Supports public media URLs recognized by yt-dlp; adaptive formats and direct-delivery failures are downloaded before upload. Private, DRM-protected, or login-required media may not be accessible.
- YouTube, Instagram, and Pinterest links use one local yt-dlp extraction/download, then upload the result to Telegram. Progressive MP4 is preferred up to 480p for faster mobile delivery; adaptive formats use eight concurrent fragments and are merged without re-encoding. Direct MP4 links use Telegram's fastest source delivery with a local-download fallback.
- Direct `.mp4` URLs bypass yt-dlp and are passed to Telegram directly; Telegram combines its source fetch and delivery, so those two timings cannot be separated by the bot.
- Social media downloads log extraction, format selection, download, FFmpeg/remux, Telegram upload, total time, bytes, average MB/s, and fragment counts.
- The dating menu registers adults (18+). Registered full name, age, and gender are visible to other adults; Telegram usernames are shared only after both people consent. Phone contacts are checked and discarded.
- Dating registration updates the active prompt as users move through its steps; the help screen explains media links and privacy.
- QR Generator encodes a single text message or HTTP(S) link directly in the QR image. It combines photos, videos, documents, or multiple items in one tokenized collection. Media is stored outside the public web root, while the result page serves it only through the unguessable QR token.
- `/start` shows a persistent bottom keyboard with Tanishuv, Profil, and QR Generator; `/help` opens the formatted help page.
- In-memory rate limiting.
- FastAPI health endpoint.

Delivery speed depends on the source, media size, and Telegram. Direct URL delivery avoids routing the video through this computer, but cannot guarantee a fixed delivery time.

Dating profiles and consent requests are stored in the configured database. The bot must be able to create tables there on startup.

## Run locally
1. Install Python 3.12 or newer.
2. Install the requirements: `python -m pip install -r requirements.txt`.
3. Copy `.env.example` to `.env` and set `BOT_TOKEN` to your BotFather token.
4. Set `PUBLIC_BASE_URL` to the public HTTPS origin that serves the FastAPI app, and keep `QR_STORAGE_DIR` on a private persistent volume shared by the bot and API processes.
5. Install FFmpeg and make sure `ffmpeg` is on `PATH` to merge adaptive streams or remux incompatible containers. The bot does not re-encode video.
6. Start the API: `uvicorn api.main:app --host 0.0.0.0 --port 8000`.
7. Start the bot separately: `python run_bot.py`.
8. Open the bot in Telegram and send `/start`.

The database creates new QR tables on startup using the project's existing SQLAlchemy `create_all` pattern. QR URLs are bearer links: anyone who has the unguessable link can view its collection. User-supplied links are stored as text and are never fetched by the server.

Keep `BOT_API_BASE_URL` empty when using the standard Telegram Bot API. Optional local Bot API server support is available by setting it to that server's URL.

