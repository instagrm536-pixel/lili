import asyncio
import re
import logging
import time
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from aiogram import Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import Message
from app.services.media_extractor import DownloadedMedia, MediaExtractor
from app.services.ratelimit import RateLimiter
from app.services.state import state
from app.services.telegram_sender import TelegramSender

router = Router()
log = logging.getLogger(__name__)
extractor = MediaExtractor()
limiter = RateLimiter(state)

URL_RE = re.compile(r"https?://[^\s<>]+")


def _is_youtube_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host == "youtu.be" or any(
        host == domain or host.endswith(f".{domain}")
        for domain in ("youtube.com", "youtube-nocookie.com")
    )


def _is_pinterest_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host == "pin.it" or host == "pinterest.com" or host.endswith(
        ".pinterest.com"
    )


def _is_instagram_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host in {"instagr.am", "www.instagr.am"} or (
        host == "instagram.com" or host.endswith(".instagram.com")
    )


def _is_direct_mp4_url(url: str) -> bool:
    return urlsplit(url).path.lower().endswith(".mp4")


async def _send_downloaded_media(message: Message, url: str):
    with TemporaryDirectory(prefix="zyx-media-") as output_dir:
        media = await asyncio.to_thread(extractor.download, url, output_dir)
        await TelegramSender.send_file(
            message.bot,
            message.chat.id,
            media.path,
            media.title,
            media.is_video,
        )


def _log_media_performance(
    source: str,
    media: DownloadedMedia,
    url_processing_seconds: float,
    upload_seconds: float,
    request_started: float,
) -> None:
    metrics = media.metrics
    if metrics is None:
        log.info(
            "[URL] Processing: %.2fs | %s extraction/download metrics unavailable",
            url_processing_seconds,
            source,
        )
        log.info("[Telegram] Upload: %.2fs", upload_seconds)
        log.info("[TOTAL] %.2fs", time.perf_counter() - request_started)
        return

    average_mbps = (
        metrics.downloaded_bytes / (1024 * 1024) / metrics.download_seconds
        if metrics.download_seconds > 0 else 0
    )
    log.info("[URL] Processing: %.2fs", url_processing_seconds)
    log.info(
        "[%s] Extraction: %.2fs | Format selection: %.2fs",
        source,
        metrics.extraction_seconds,
        metrics.format_selection_seconds,
    )
    log.info(
        "[%s] Download: %.2fs | Downloaded: %.2f MB | Average: %.2f MB/s | "
        "Fragments: %d | Concurrent fragments: %d",
        source,
        metrics.download_seconds,
        metrics.downloaded_bytes / (1024 * 1024),
        average_mbps,
        metrics.fragment_count,
        metrics.concurrent_fragments,
    )
    log.info("[FFmpeg] Processing: %.2fs", metrics.ffmpeg_seconds)
    log.info("[Telegram] Upload: %.2fs", upload_seconds)
    log.info("[TOTAL] %.2fs", time.perf_counter() - request_started)


async def _send_platform_media(
    message: Message,
    url: str,
    source: str,
    url_processing_seconds: float,
    request_started: float,
):
    if source in {"Instagram", "YouTube"}:
        direct_started = time.perf_counter()
        try:
            info = await asyncio.to_thread(extractor.analyze, url)
            if info.is_video and info.direct_url:
                if source == "Instagram":
                    await TelegramSender.send_direct_video(
                        message.bot,
                        message.chat.id,
                        info.direct_url,
                    )
                else:
                    await TelegramSender.send_from_url(
                        message.bot,
                        message.chat.id,
                        info.direct_url,
                        info.http_headers,
                        info.ext,
                    )
                log.info(
                    "[%s] Direct URL delivery: %.2fs",
                    source,
                    time.perf_counter() - direct_started,
                )
                log.info("[TOTAL] %.2fs", time.perf_counter() - request_started)
                return
        except Exception:
            log.warning(
                "Instagram direct delivery failed; falling back to local download",
                exc_info=True,
            )

    with TemporaryDirectory(prefix=f"zyx-{source.lower()}-") as output_dir:
        try:
            media = await asyncio.to_thread(extractor.download, url, output_dir)
        except Exception as download_error:
            if source not in {"Instagram", "Pinterest"}:
                raise
            log.warning(
                "%s yt-dlp download failed; trying direct Telegram delivery",
                source,
                exc_info=True,
            )
            extraction_started = time.perf_counter()
            info = await asyncio.to_thread(extractor.analyze, url)
            extraction_seconds = time.perf_counter() - extraction_started
            if not info.direct_url:
                raise download_error

            transfer_started = time.perf_counter()
            if source == "Pinterest" and info.is_image:
                await TelegramSender.send_photo_from_url(
                    message.bot,
                    message.chat.id,
                    info.direct_url,
                    info.http_headers,
                    info.ext,
                )
            elif info.is_video:
                if source == "Instagram":
                    await TelegramSender.send_direct_video(
                        message.bot, message.chat.id, info.direct_url
                    )
                else:
                    await TelegramSender.send_from_url(
                        message.bot,
                        message.chat.id,
                        info.direct_url,
                        info.http_headers,
                        info.ext,
                    )
            else:
                raise download_error
            log.info("[%s] Fallback extraction: %.2fs", source, extraction_seconds)
            log.info(
                "[Telegram] Source fetch + delivery: %.2fs (combined)",
                time.perf_counter() - transfer_started,
            )
            log.info("[TOTAL] %.2fs", time.perf_counter() - request_started)
            return

        upload_started = time.perf_counter()
        try:
            await TelegramSender.send_file(
                message.bot,
                message.chat.id,
                media.path,
                media.title,
                media.is_video,
            )
        finally:
            _log_media_performance(
                source,
                media,
                url_processing_seconds,
                time.perf_counter() - upload_started,
                request_started,
            )


@router.message()
async def handle_message(message: Message):
    if not message.text:
        raise SkipHandler

    request_started = time.perf_counter()
    match = URL_RE.search(message.text.strip())
    if not match:
        raise SkipHandler
    url_processing_seconds = time.perf_counter() - request_started

    if not await limiter.allowed(message.from_user.id):
        await message.answer("⏳ Juda tez-tez so‘rov yuborildi. Biroz kuting.")
        return

    url = match.group(0)
    status = await message.answer("⌛")

    try:
        if _is_youtube_url(url):
            await _send_platform_media(
                message, url, "YouTube", url_processing_seconds, request_started
            )
            await status.delete()
            return

        if _is_pinterest_url(url):
            await _send_platform_media(
                message, url, "Pinterest", url_processing_seconds, request_started
            )
            await status.delete()
            return

        if _is_instagram_url(url):
            await _send_platform_media(
                message, url, "Instagram", url_processing_seconds, request_started
            )
            await status.delete()
            return

        if _is_direct_mp4_url(url):
            transfer_started = time.perf_counter()
            try:
                await TelegramSender.send_direct_video(
                    message.bot, message.chat.id, url
                )
            except Exception:
                log.warning(
                    "direct Telegram delivery failed; falling back to local download",
                    exc_info=True,
                )
                await _send_downloaded_media(message, url)
            log.info(
                "[Direct HTTP] URL processing: %.2fs | Telegram direct delivery "
                "or fallback: %.2fs | Total: %.2fs",
                url_processing_seconds,
                time.perf_counter() - transfer_started,
                time.perf_counter() - request_started,
            )
            await status.delete()
            return

        info = await asyncio.to_thread(extractor.analyze, url)

        if info.direct_url:
            try:
                if info.is_video:
                    await TelegramSender.send_from_url(
                        message.bot,
                        message.chat.id,
                        info.direct_url,
                        info.http_headers,
                        info.ext,
                    )
                else:
                    await TelegramSender.send_audio_from_url(
                        message.bot,
                        message.chat.id,
                        info.direct_url,
                        info.title,
                        info.http_headers,
                    )
            except Exception:
                log.warning(
                    "direct media delivery failed; downloading with yt-dlp",
                    exc_info=True,
                )
                await _send_downloaded_media(message, url)
        else:
            await _send_downloaded_media(message, url)

        await status.delete()

    except Exception as exc:
        log.exception("media processing failed")
        if _is_instagram_url(url) or _is_pinterest_url(url):
            await status.edit_text(
                f"❌ Media havolasidan fayl olib bo‘lmadi ({type(exc).__name__})."
            )
        else:
            await status.edit_text(
                f"❌ Media yuborilmadi ({type(exc).__name__})."
            )
