import asyncio
from pathlib import Path
import subprocess
from unittest.mock import AsyncMock, Mock, call

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendAudio, SendVideo
from aiogram.types import FSInputFile, URLInputFile
import pytest

from app.bot.handlers import media as media_handler
from app.services.media_extractor import (
    DownloadedMedia,
    MediaExtractor,
    MediaInfo,
)
from app.services.state import InMemoryState
from app.services.telegram_sender import TelegramSender


def test_extractor_uses_direct_mp4_url_without_formats(monkeypatch):
    class DirectMp4:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def extract_info(self, url, download):
            return {
                "url": url,
                "title": "Direct clip",
                "ext": "mp4",
                "filesize": 100,
            }

    monkeypatch.setattr("app.services.media_extractor.yt_dlp.YoutubeDL", DirectMp4)

    info = MediaExtractor().analyze("https://cdn.example/video.mp4?signature=temporary")

    assert info.direct_url == "https://cdn.example/video.mp4?signature=temporary"
    assert info.is_video


def test_extractor_identifies_pinterest_image(monkeypatch):
    class PinterestImage:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def extract_info(self, url, download):
            return {
                "title": "Pinterest image",
                "url": "https://i.pinimg.com/originals/image.jpg",
                "ext": "jpg",
            }

    monkeypatch.setattr(
        "app.services.media_extractor.yt_dlp.YoutubeDL", PinterestImage
    )

    info = MediaExtractor().analyze("https://www.pinterest.com/pin/example")

    assert info.direct_url == "https://i.pinimg.com/originals/image.jpg"
    assert info.is_image
    assert not info.is_video


def test_extractor_prefers_progressive_mp4_format(monkeypatch):
    class Formats:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def extract_info(self, url, download):
            return {
                "title": "Clip",
                "ext": "webm",
                "formats": [
                    {
                        "url": "https://cdn.example/video.webm",
                        "ext": "webm",
                        "height": 1080,
                        "vcodec": "vp9",
                        "acodec": "opus",
                    },
                    {
                        "url": "https://cdn.example/video.mp4",
                        "ext": "mp4",
                        "height": 720,
                        "vcodec": "avc1",
                        "acodec": "mp4a",
                    },
                ],
            }

    monkeypatch.setattr("app.services.media_extractor.yt_dlp.YoutubeDL", Formats)

    info = MediaExtractor().analyze("https://www.youtube.com/watch?v=example")

    assert info.direct_url == "https://cdn.example/video.mp4"
    assert info.ext == "mp4"


def test_extractor_marks_adaptive_formats_as_video(monkeypatch):
    class AdaptiveFormats:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def extract_info(self, url, download):
            return {
                "title": "Adaptive clip",
                "formats": [
                    {"url": "https://cdn.example/video", "vcodec": "avc1", "acodec": "none"},
                    {"url": "https://cdn.example/audio", "vcodec": "none", "acodec": "mp4a"},
                ],
            }

    monkeypatch.setattr("app.services.media_extractor.yt_dlp.YoutubeDL", AdaptiveFormats)

    info = MediaExtractor().analyze("https://www.youtube.com/watch?v=adaptive")

    assert info.direct_url is None
    assert info.is_video


def test_extractor_downloads_combined_media_when_no_direct_url(monkeypatch, tmp_path):
    class Download:
        def __init__(self, options):
            self.options = options
            assert "height<=480" in options["format"]
            assert options["concurrent_fragment_downloads"] == 8
            assert options["retries"] == 1
            assert options["fragment_retries"] == 1

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def extract_info(self, url, download):
            assert not download
            return {
                "id": "clip",
                "title": "Downloaded clip",
                "ext": "mp4",
                "vcodec": "avc1",
            }

        def process_ie_result(self, info, download):
            assert download
            output_path = self.options["outtmpl"].replace(
                "%(id)s.%(ext)s", "clip.mp4"
            )
            Path(output_path).write_bytes(b"video")
            return info

    monkeypatch.setattr("app.services.media_extractor.yt_dlp.YoutubeDL", Download)

    path = MediaExtractor().download(
        "https://www.youtube.com/watch?v=adaptive", str(tmp_path)
    )

    assert Path(path.path).name == "clip.mp4"
    assert path.title == "Downloaded clip"
    assert path.is_video


def test_extractor_records_transfer_and_fragment_metrics(monkeypatch, tmp_path):
    class Download:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def extract_info(self, url, download):
            assert not download
            return {"id": "clip", "title": "Clip", "ext": "mp4", "vcodec": "avc1"}

        def process_ie_result(self, info, download):
            assert download
            hook = self.options["progress_hooks"][0]
            hook({
                "status": "downloading",
                "downloaded_bytes": 512,
                "fragment_count": 3,
                "info_dict": {"format_id": "video"},
            })
            hook({
                "status": "downloading",
                "downloaded_bytes": 1024,
                "fragment_count": 3,
                "info_dict": {"format_id": "video"},
            })
            hook({"status": "finished", "info_dict": {"format_id": "video"}})
            output_path = Path(self.options["outtmpl"].replace(
                "%(id)s.%(ext)s", "clip.mp4"
            ))
            output_path.write_bytes(b"x" * 1024)
            return info

    monkeypatch.setattr("app.services.media_extractor.yt_dlp.YoutubeDL", Download)

    media = MediaExtractor().download("https://youtu.be/clip", str(tmp_path))

    assert media.metrics is not None
    assert media.metrics.downloaded_bytes == 1024
    assert media.metrics.fragment_count == 3
    assert media.metrics.concurrent_fragments == 8
    assert media.metrics.extraction_seconds >= 0
    assert media.metrics.format_selection_seconds >= 0
    assert media.metrics.download_seconds >= 0


def test_extractor_remuxes_non_mp4_without_reencoding(monkeypatch, tmp_path):
    class Download:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def extract_info(self, url, download):
            assert not download
            return {
                "title": "WebM clip",
                "ext": "webm",
                "requested_downloads": [
                    {"vcodec": "vp9", "acodec": "opus"}
                ],
            }

        def process_ie_result(self, info, download):
            assert download
            output_path = Path(self.options["outtmpl"].replace(
                "%(id)s.%(ext)s", "clip.webm"
            ))
            output_path.write_bytes(b"video")
            return info

    def remux(command, capture_output, text, timeout):
        assert "-c" in command
        assert command[command.index("-c") + 1] == "copy"
        assert "libx264" not in command
        Path(command[-1]).write_bytes(b"mp4")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("app.services.media_extractor.yt_dlp.YoutubeDL", Download)
    monkeypatch.setattr("app.services.media_extractor.subprocess.run", remux)

    media = MediaExtractor().download(
        "https://example.com/clip", str(tmp_path)
    )

    assert Path(media.path).suffix == ".mp4"
    assert media.is_video


def test_media_handler_downloads_when_extractor_has_no_direct_url(monkeypatch):
    status = type(
        "StatusStub",
        (),
        {
            "edit_text": AsyncMock(),
            "delete": AsyncMock(),
        },
    )()
    message = type("MessageStub", (), {})()
    message.text = "https://source.example/watch?v=adaptive"
    message.from_user = type("UserStub", (), {"id": 123})()
    message.chat = type("ChatStub", (), {"id": 123})()
    message.bot = object()
    message.answer = AsyncMock(return_value=status)

    monkeypatch.setattr(media_handler.limiter, "allowed", AsyncMock(return_value=True))
    monkeypatch.setattr(
        media_handler.extractor,
        "analyze",
        lambda url: MediaInfo(url, "Adaptive clip", "mp4", 10, None, None, True),
    )

    def download(url, output_dir):
        path = Path(output_dir) / "clip.mp4"
        path.write_bytes(b"video")
        return DownloadedMedia(str(path), "Adaptive clip", True)

    monkeypatch.setattr(media_handler.extractor, "download", download)
    send_file = AsyncMock()
    monkeypatch.setattr(media_handler.TelegramSender, "send_file", send_file)

    asyncio.run(media_handler.handle_message(message))

    send_file.assert_awaited_once()
    assert send_file.await_args.args[1] == 123
    assert send_file.await_args.args[3:] == ("Adaptive clip", True)
    status.delete.assert_awaited_once()


def test_media_handler_downloads_when_telegram_rejects_direct_url(monkeypatch):
    status = type(
        "StatusStub",
        (),
        {
            "edit_text": AsyncMock(),
            "delete": AsyncMock(),
        },
    )()
    message = type("MessageStub", (), {})()
    message.text = "https://source.example/watch?v=clip"
    message.from_user = type("UserStub", (), {"id": 123})()
    message.chat = type("ChatStub", (), {"id": 123})()
    message.bot = object()
    message.answer = AsyncMock(return_value=status)

    monkeypatch.setattr(media_handler.limiter, "allowed", AsyncMock(return_value=True))
    monkeypatch.setattr(
        media_handler.extractor,
        "analyze",
        lambda url: MediaInfo(
            url, "YouTube clip", "mp4", 10, None, "https://cdn.example/clip.mp4", True
        ),
    )
    monkeypatch.setattr(
        media_handler.TelegramSender,
        "send_from_url",
        AsyncMock(side_effect=RuntimeError("Telegram could not fetch the URL")),
    )
    monkeypatch.setattr(
        media_handler.extractor,
        "download",
        lambda url, output_dir: DownloadedMedia(
            str(Path(output_dir) / "clip.mp4"), "YouTube clip", True
        ),
    )
    send_file = AsyncMock()
    monkeypatch.setattr(media_handler.TelegramSender, "send_file", send_file)

    asyncio.run(media_handler.handle_message(message))

    send_file.assert_awaited_once()
    status.delete.assert_awaited_once()


def test_youtube_uses_direct_delivery(monkeypatch):
    status = type(
        "StatusStub",
        (),
        {
            "edit_text": AsyncMock(),
            "delete": AsyncMock(),
        },
    )()
    message = type("MessageStub", (), {})()
    message.text = "https://youtu.be/clip"
    message.from_user = type("UserStub", (), {"id": 123})()
    message.chat = type("ChatStub", (), {"id": 123})()
    message.bot = object()
    message.answer = AsyncMock(return_value=status)

    url = "https://youtu.be/clip"
    monkeypatch.setattr(media_handler.limiter, "allowed", AsyncMock(return_value=True))
    direct = MediaInfo(
        url,
        "YouTube clip",
        "mp4",
        10,
        None,
        "https://cdn.example/youtube.mp4",
        True,
    )
    analyze = Mock(return_value=direct)
    monkeypatch.setattr(media_handler.extractor, "analyze", analyze)
    download = Mock(
        side_effect=AssertionError("direct YouTube delivery must not download")
    )
    monkeypatch.setattr(media_handler.extractor, "download", download)
    send_from_url = AsyncMock()
    monkeypatch.setattr(
        media_handler.TelegramSender, "send_from_url", send_from_url
    )

    asyncio.run(media_handler.handle_message(message))

    analyze.assert_called_once_with(url)
    download.assert_not_called()
    send_from_url.assert_awaited_once_with(
        message.bot,
        123,
        direct.direct_url,
        direct.http_headers,
        direct.ext,
    )
    message.answer.assert_awaited_once_with("⌛")
    status.delete.assert_awaited_once()


def test_instagram_uses_direct_delivery(monkeypatch):
    status = type(
        "StatusStub",
        (),
        {
            "edit_text": AsyncMock(),
            "delete": AsyncMock(),
        },
    )()
    message = type("MessageStub", (), {})()
    message.text = "https://www.instagram.com/reel/clip/"
    message.from_user = type("UserStub", (), {"id": 123})()
    message.chat = type("ChatStub", (), {"id": 123})()
    message.bot = object()
    message.answer = AsyncMock(return_value=status)

    url = message.text
    monkeypatch.setattr(media_handler.limiter, "allowed", AsyncMock(return_value=True))
    direct = MediaInfo(
        url,
        "Instagram reel",
        "mp4",
        10,
        None,
        "https://cdn.example/reel.mp4",
        True,
    )
    analyze = Mock(return_value=direct)
    monkeypatch.setattr(media_handler.extractor, "analyze", analyze)
    download = Mock(side_effect=AssertionError("direct delivery must not download"))
    monkeypatch.setattr(media_handler.extractor, "download", download)
    send_direct = AsyncMock()
    monkeypatch.setattr(
        media_handler.TelegramSender, "send_direct_video", send_direct
    )

    asyncio.run(media_handler.handle_message(message))

    analyze.assert_called_once_with(url)
    download.assert_not_called()
    send_direct.assert_awaited_once_with(
        message.bot, 123, direct.direct_url
    )
    status.delete.assert_awaited_once()


def test_instagram_download_failure_keeps_direct_delivery_fallback(monkeypatch):
    status = type("StatusStub", (), {"delete": AsyncMock()})()
    message = type("MessageStub", (), {})()
    message.text = "https://www.instagram.com/reel/clip/"
    message.from_user = type("UserStub", (), {"id": 123})()
    message.chat = type("ChatStub", (), {"id": 123})()
    message.bot = object()
    message.answer = AsyncMock(return_value=status)

    monkeypatch.setattr(media_handler.limiter, "allowed", AsyncMock(return_value=True))
    monkeypatch.setattr(
        media_handler.extractor,
        "download",
        Mock(side_effect=RuntimeError("yt-dlp cannot download this reel")),
    )
    direct = MediaInfo(
        message.text,
        "Instagram reel",
        "mp4",
        10,
        None,
        "https://cdn.example/reel.mp4",
        True,
    )
    monkeypatch.setattr(media_handler.extractor, "analyze", Mock(return_value=direct))
    send_direct = AsyncMock()
    monkeypatch.setattr(
        media_handler.TelegramSender, "send_direct_video", send_direct
    )

    asyncio.run(media_handler.handle_message(message))

    send_direct.assert_awaited_once_with(
        message.bot, message.chat.id, direct.direct_url
    )
    status.delete.assert_awaited_once()


def test_youtube_uses_direct_delivery_without_separate_download(monkeypatch):
    status = type(
        "StatusStub",
        (),
        {
            "edit_text": AsyncMock(),
            "delete": AsyncMock(),
        },
    )()
    message = type("MessageStub", (), {})()
    message.text = "https://youtu.be/clip"
    message.from_user = type("UserStub", (), {"id": 123})()
    message.chat = type("ChatStub", (), {"id": 123})()
    message.bot = object()
    message.answer = AsyncMock(return_value=status)

    monkeypatch.setattr(media_handler.limiter, "allowed", AsyncMock(return_value=True))
    direct = MediaInfo(
        message.text,
        "YouTube clip",
        "mp4",
        10,
        None,
        "https://cdn.example/youtube.mp4",
        True,
    )
    monkeypatch.setattr(
        media_handler.extractor,
        "analyze",
        Mock(return_value=direct),
    )
    download = Mock(
        side_effect=AssertionError("direct YouTube delivery must not download")
    )
    monkeypatch.setattr(media_handler.extractor, "download", download)
    send_from_url = AsyncMock()
    monkeypatch.setattr(media_handler.TelegramSender, "send_from_url", send_from_url)
    asyncio.run(media_handler.handle_message(message))

    download.assert_not_called()
    send_from_url.assert_awaited_once_with(
        message.bot,
        123,
        direct.direct_url,
        direct.http_headers,
        direct.ext,
    )
    status.delete.assert_awaited_once()


def test_youtube_falls_back_to_download_when_direct_delivery_fails(monkeypatch):
    status = type(
        "StatusStub",
        (),
        {
            "edit_text": AsyncMock(),
            "delete": AsyncMock(),
        },
    )()
    message = type("MessageStub", (), {})()
    message.text = "https://youtu.be/clip"
    message.from_user = type("UserStub", (), {"id": 123})()
    message.chat = type("ChatStub", (), {"id": 123})()
    message.bot = object()
    message.answer = AsyncMock(return_value=status)

    monkeypatch.setattr(media_handler.limiter, "allowed", AsyncMock(return_value=True))
    direct = MediaInfo(
        message.text,
        "YouTube clip",
        "mp4",
        10,
        None,
        "https://cdn.example/youtube.mp4",
        True,
    )
    monkeypatch.setattr(
        media_handler.extractor,
        "analyze",
        Mock(return_value=direct),
    )
    download = Mock(
        return_value=DownloadedMedia("clip.mp4", "YouTube clip", True)
    )
    monkeypatch.setattr(media_handler.extractor, "download", download)
    send_from_url = AsyncMock(side_effect=TelegramBadRequest(
        method=SendVideo(chat_id=123, video=direct.direct_url),
        message="URL unavailable",
    ))
    monkeypatch.setattr(
        media_handler.TelegramSender,
        "send_from_url",
        send_from_url,
    )
    send_file = AsyncMock()
    monkeypatch.setattr(media_handler.TelegramSender, "send_file", send_file)

    asyncio.run(media_handler.handle_message(message))

    download.assert_called_once()
    send_file.assert_awaited_once_with(
        message.bot, 123, "clip.mp4", "YouTube clip", True
    )
    status.delete.assert_awaited_once()


def test_direct_mp4_uses_fast_telegram_delivery(monkeypatch):
    status = type("StatusStub", (), {"delete": AsyncMock()})()
    message = type("MessageStub", (), {})()
    message.text = "https://cdn.example/video.mp4?token=abc"
    message.from_user = type("UserStub", (), {"id": 123})()
    message.chat = type("ChatStub", (), {"id": 123})()
    message.bot = object()
    message.answer = AsyncMock(return_value=status)

    monkeypatch.setattr(media_handler.limiter, "allowed", AsyncMock(return_value=True))
    monkeypatch.setattr(
        media_handler.extractor,
        "analyze",
        Mock(side_effect=AssertionError("direct MP4 must skip yt-dlp")),
    )
    send_direct = AsyncMock()
    monkeypatch.setattr(
        media_handler.TelegramSender, "send_direct_video", send_direct
    )

    asyncio.run(media_handler.handle_message(message))

    send_direct.assert_awaited_once_with(
        message.bot, 123, message.text
    )
    status.delete.assert_awaited_once()


def test_pinterest_uses_single_download_pipeline(monkeypatch):
    status = type(
        "StatusStub",
        (),
        {
            "edit_text": AsyncMock(),
            "delete": AsyncMock(),
        },
    )()
    message = type("MessageStub", (), {})()
    message.text = "https://www.pinterest.com/pin/example"
    message.from_user = type("UserStub", (), {"id": 123})()
    message.chat = type("ChatStub", (), {"id": 123})()
    message.bot = object()
    message.answer = AsyncMock(return_value=status)

    monkeypatch.setattr(media_handler.limiter, "allowed", AsyncMock(return_value=True))
    monkeypatch.setattr(
        media_handler.extractor,
        "analyze",
        Mock(side_effect=AssertionError("Pinterest must not be analyzed twice")),
    )
    download = Mock(
        return_value=DownloadedMedia("pin.mp4", "Pinterest video", True)
    )
    monkeypatch.setattr(media_handler.extractor, "download", download)
    send_file = AsyncMock()
    monkeypatch.setattr(media_handler.TelegramSender, "send_file", send_file)

    asyncio.run(media_handler.handle_message(message))

    download.assert_called_once_with(message.text, download.call_args.args[1])
    send_file.assert_awaited_once_with(
        message.bot, 123, "pin.mp4", "Pinterest video", True
    )
    status.delete.assert_awaited_once()


def test_local_pinterest_image_is_sent_as_photo(tmp_path):
    path = tmp_path / "pinterest-image.jpg"
    path.write_bytes(b"image")
    bot = type(
        "BotStub",
        (),
        {
            "send_photo": AsyncMock(return_value="sent"),
            "send_document": AsyncMock(),
        },
    )()

    result = asyncio.run(
        TelegramSender.send_file(bot, 123, str(path), "Pinterest image", False)
    )

    assert result == "sent"
    bot.send_photo.assert_awaited_once()
    bot.send_document.assert_not_awaited()


def test_direct_video_delivery_uses_1_3_second_request_timeout():
    bot = type("BotStub", (), {"send_video": AsyncMock(return_value="sent")})()

    result = asyncio.run(
        TelegramSender.send_direct_video(
            bot, 123, "https://cdn.example/video.mp4"
        )
    )

    assert result == "sent"
    bot.send_video.assert_awaited_once_with(
        123,
        "https://cdn.example/video.mp4",
        request_timeout=1.3,
    )


def test_video_delivery_prefers_direct_telegram_url():
    bot = type("BotStub", (), {"send_video": AsyncMock(return_value="sent")})()

    result = asyncio.run(
        TelegramSender.send_from_url(
            bot, 123, "https://cdn.example/video.mp4"
        )
    )

    assert result == "sent"
    bot.send_video.assert_awaited_once_with(123, "https://cdn.example/video.mp4")


def test_video_delivery_falls_back_to_streamed_file():
    bot = type(
        "BotStub",
        (),
        {
            "send_video": AsyncMock(
                side_effect=[
                    TelegramBadRequest(
                        method=SendVideo(chat_id=123, video="https://cdn.example/video.mp4"),
                        message="URL unavailable",
                    ),
                    "sent",
                ]
            )
        },
    )()
    headers = {"Referer": "https://source.example"}

    result = asyncio.run(
        TelegramSender.send_from_url(
            bot, 123, "https://cdn.example/video.mp4", headers
        )
    )

    assert result == "sent"
    assert bot.send_video.await_args_list[0] == call(123, "https://cdn.example/video.mp4")
    fallback = bot.send_video.await_args_list[1].args[1]
    assert isinstance(fallback, URLInputFile)
    assert fallback.headers == headers
    assert fallback.filename == "video.mp4"


def test_audio_delivery_stream_fallback_keeps_source_headers():
    url = "https://cdn.example/audio"
    headers = {"User-Agent": "test-agent"}
    bot = type(
        "BotStub",
        (),
        {
            "send_audio": AsyncMock(
                side_effect=[
                    TelegramBadRequest(
                        method=SendAudio(chat_id=123, audio=url),
                        message="URL unavailable",
                    ),
                    "sent",
                ]
            )
        },
    )()

    result = asyncio.run(
        TelegramSender.send_audio_from_url(
            bot, 123, url, "Track", headers
        )
    )

    assert result == "sent"
    assert bot.send_audio.await_args_list[0] == call(123, url, title="Track")
    fallback = bot.send_audio.await_args_list[1].args[1]
    assert isinstance(fallback, URLInputFile)
    assert fallback.headers == headers


def test_non_mp4_video_url_is_left_for_local_conversion():
    bot = type(
        "BotStub",
        (),
        {
            "send_video": AsyncMock(
                side_effect=TelegramBadRequest(
                    method=SendVideo(chat_id=123, video="https://cdn.example/video.webm"),
                    message="Unsupported video format",
                )
            ),
            "send_document": AsyncMock(return_value="sent"),
        },
    )()

    with pytest.raises(TelegramBadRequest):
        asyncio.run(
            TelegramSender.send_from_url(
                bot, 123, "https://cdn.example/video.webm", ext="webm"
            )
        )

    bot.send_document.assert_not_awaited()


def test_local_video_is_sent_as_video_not_document(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"video")
    bot = type(
        "BotStub",
        (),
        {
            "send_video": AsyncMock(return_value="sent"),
            "send_document": AsyncMock(),
        },
    )()

    result = asyncio.run(
        TelegramSender.send_file(bot, 123, str(path), "Clip", True)
    )

    assert result == "sent"
    bot.send_video.assert_awaited_once()
    bot.send_document.assert_not_awaited()


def test_non_mp4_local_video_is_sent_as_document(tmp_path):
    path = tmp_path / "clip.webm"
    path.write_bytes(b"video")
    bot = type(
        "BotStub",
        (),
        {
            "send_video": AsyncMock(),
            "send_document": AsyncMock(return_value="sent"),
        },
    )()

    result = asyncio.run(
        TelegramSender.send_file(bot, 123, str(path), "Clip", True)
    )

    assert result == "sent"
    bot.send_video.assert_not_awaited()
    bot.send_document.assert_awaited_once()


def test_in_memory_state_expires_values(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("app.services.state.time.monotonic", lambda: now[0])
    state = InMemoryState()

    async def scenario():
        assert await state.set("key", "value", ex=5, nx=True)
        assert not await state.set("key", "other", ex=5, nx=True)
        now[0] += 5
        assert await state.get("key") is None
        assert await state.set("key", "other", ex=5, nx=True)

    asyncio.run(scenario())
