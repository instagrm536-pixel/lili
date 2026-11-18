from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile, URLInputFile
from aiogram.exceptions import TelegramBadRequest


class TelegramSender:
    DIRECT_VIDEO_REQUEST_TIMEOUT = 1.3

    @staticmethod
    async def send_direct_video(bot: Bot, chat_id: int, url: str):
        return await bot.send_video(
            chat_id,
            url,
            request_timeout=TelegramSender.DIRECT_VIDEO_REQUEST_TIMEOUT,
        )

    @staticmethod
    async def send_photo_from_url(
        bot: Bot,
        chat_id: int,
        url: str,
        headers: dict[str, str] | None = None,
        ext: str | None = None,
    ):
        try:
            return await bot.send_photo(chat_id, url)
        except TelegramBadRequest:
            extension = (ext or "jpg").lower().lstrip(".")
            file = URLInputFile(
                url, headers=headers, filename=f"image.{extension}"
            )
            return await bot.send_photo(chat_id, file)

    @staticmethod
    async def send_from_url(
        bot: Bot,
        chat_id: int,
        url: str,
        headers: dict[str, str] | None = None,
        ext: str | None = None,
    ):
        try:
            return await bot.send_video(chat_id, url)
        except TelegramBadRequest:
            extension = (ext or "mp4").lower().lstrip(".")
            if extension != "mp4":
                raise
            file = URLInputFile(
                url, headers=headers, filename=f"video.{extension}"
            )
            return await bot.send_video(chat_id, file)

    @staticmethod
    async def send_file(
        bot: Bot,
        chat_id: int,
        path: str,
        title: str,
        is_video: bool,
    ):
        extension = Path(path).suffix.lower()
        file = FSInputFile(path)
        if is_video:
            if extension == ".mp4":
                return await bot.send_video(chat_id, file)
            return await bot.send_document(chat_id, file)
        if extension in {".jpg", ".jpeg", ".png"}:
            return await bot.send_photo(chat_id, file)
        if not is_video and extension in {".m4a", ".mp3"}:
            return await bot.send_audio(chat_id, file, title=title)
        return await bot.send_document(chat_id, file)

    @staticmethod
    async def send_audio_from_url(
        bot: Bot,
        chat_id: int,
        url: str,
        title: str,
        headers: dict[str, str] | None = None,
    ):
        try:
            return await bot.send_audio(chat_id, url, title=title)
        except TelegramBadRequest:
            file = URLInputFile(
                url, headers=headers, filename=f"{title[:80]}.mp3"
            )
            return await bot.send_audio(chat_id, file, title=title)

