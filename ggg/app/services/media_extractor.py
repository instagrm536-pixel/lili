from dataclasses import dataclass
import logging
from pathlib import Path
import subprocess
import time
from typing import Any
import yt_dlp

IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
log = logging.getLogger(__name__)


@dataclass
class MediaInfo:
    url: str
    title: str
    ext: str | None
    duration: float | None
    filesize: int | None
    direct_url: str | None
    is_video: bool
    http_headers: dict[str, str] | None = None
    is_image: bool = False


@dataclass
class DownloadMetrics:
    extraction_seconds: float = 0
    format_selection_seconds: float = 0
    download_seconds: float = 0
    ffmpeg_seconds: float = 0
    downloaded_bytes: int = 0
    fragment_count: int = 0
    concurrent_fragments: int = 8


@dataclass
class DownloadedMedia:
    path: str
    title: str
    is_video: bool
    metrics: DownloadMetrics | None = None


class MediaExtractor:
    def analyze(self, url: str) -> MediaInfo:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "extract_flat": False,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info: dict[str, Any] = ydl.extract_info(url, download=False)

        formats = info.get("formats") or []
        chosen = None

        # Prefer a single progressive format to avoid a local merge.
        progressive = [
            f for f in formats
            if f.get("url") and f.get("vcodec") not in (None, "none")
            and f.get("acodec") not in (None, "none")
        ]
        mp4_progressive = [f for f in progressive if f.get("ext") == "mp4"]
        candidates = mp4_progressive or progressive
        candidates.sort(
            key=lambda f: (f.get("height") or 0, f.get("tbr") or 0),
            reverse=True,
        )
        if candidates:
            chosen = candidates[0]

        image_formats = [
            f for f in formats
            if f.get("url") and f.get("ext") in IMAGE_EXTENSIONS
        ]
        thumbnails = [
            thumbnail for thumbnail in info.get("thumbnails") or []
            if thumbnail.get("url")
            and thumbnail.get("ext") in IMAGE_EXTENSIONS
        ]
        image_candidates = image_formats or thumbnails
        image_candidates.sort(
            key=lambda f: (f.get("width") or 0) * (f.get("height") or 0),
            reverse=True,
        )
        image = image_candidates[0] if image_candidates else None
        if chosen is None and info.get("ext") in IMAGE_EXTENSIONS:
            image = info

        selected = chosen or image or info
        is_video = selected.get("vcodec") not in (None, "none")
        if chosen is None and not is_video:
            is_video = info.get("ext") in {
                "3gp",
                "avi",
                "m4v",
                "mkv",
                "mov",
                "mp4",
                "mpeg",
                "mpg",
                "ts",
                "webm",
            } or any(
                f.get("vcodec") not in (None, "none") for f in formats
            )
        is_image = (
            not is_video
            and (selected.get("ext") or info.get("ext")) in IMAGE_EXTENSIONS
        )

        return MediaInfo(
            url=url,
            title=info.get("title") or "Media",
            ext=selected.get("ext") or info.get("ext"),
            duration=info.get("duration"),
            filesize=selected.get("filesize") or info.get("filesize"),
            direct_url=selected.get("url"),
            is_video=bool(is_video),
            http_headers=selected.get("http_headers"),
            is_image=bool(is_image),
        )

    def download(self, url: str, output_dir: str) -> DownloadedMedia:
        metrics = DownloadMetrics()
        download_started: float | None = None
        last_download_finished: float | None = None
        first_progress_at: float | None = None
        bytes_by_format: dict[str, int] = {}
        postprocessor_started: dict[str, float] = {}

        def progress_hook(data: dict[str, Any]) -> None:
            nonlocal download_started, last_download_finished, first_progress_at
            now = time.perf_counter()
            status = data.get("status")
            if status in {"downloading", "started"}:
                if first_progress_at is None:
                    first_progress_at = now
                    metrics.format_selection_seconds = max(
                        0, first_progress_at - processing_started
                    )
                if download_started is None:
                    download_started = now
            if status == "downloading":
                key = str(data.get("info_dict", {}).get("format_id") or "default")
                bytes_by_format[key] = max(
                    bytes_by_format.get(key, 0),
                    int(data.get("downloaded_bytes") or 0),
                )
                metrics.fragment_count = max(
                    metrics.fragment_count, int(data.get("fragment_count") or 0)
                )
            elif status == "finished":
                last_download_finished = now

        def postprocessor_hook(data: dict[str, Any]) -> None:
            key = str(data.get("postprocessor") or "unknown")
            status = data.get("status")
            now = time.perf_counter()
            if status == "started":
                postprocessor_started[key] = now
            elif status == "finished":
                started = postprocessor_started.pop(key, None)
                if started is not None:
                    metrics.ffmpeg_seconds += now - started

        opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "concurrent_fragment_downloads": 8,
            "retries": 1,
            "fragment_retries": 1,
            "file_access_retries": 1,
            "socket_timeout": 2,
            "continuedl": True,
            "progress_hooks": [progress_hook],
            "postprocessor_hooks": [postprocessor_hook],
            "format": (
                "best[height<=480][ext=mp4]/"
                "bestvideo[height<=480][ext=mp4]+"
                "bestaudio[ext=m4a]/best[height<=480]/"
                "bestvideo[height<=480]+bestaudio/best"
            ),
            "merge_output_format": "mp4",
            "outtmpl": str(Path(output_dir) / "%(id)s.%(ext)s"),
        }
        extraction_started = time.perf_counter()
        with yt_dlp.YoutubeDL(opts) as ydl:
            info: dict[str, Any] = ydl.extract_info(url, download=False)
            processing_started = time.perf_counter()
            metrics.extraction_seconds = processing_started - extraction_started
            info = ydl.process_ie_result(info, download=True)
        download_ended = time.perf_counter()
        if download_started is not None:
            metrics.download_seconds = max(
                0,
                (last_download_finished or download_ended) - download_started,
            )
        metrics.downloaded_bytes = sum(bytes_by_format.values())

        downloaded = [
            path
            for path in Path(output_dir).iterdir()
            if path.is_file() and path.suffix not in {".part", ".ytdl"}
        ]
        if not downloaded:
            raise RuntimeError("yt-dlp completed without producing a media file")

        downloaded.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        selected_formats = (
            info.get("requested_downloads")
            or info.get("requested_formats")
            or [info]
        )
        video_codec = next((
            fmt.get("vcodec") for fmt in selected_formats
            if fmt.get("vcodec") not in (None, "none")
        ), info.get("vcodec"))
        is_video = (
            video_codec not in (None, "none")
            or info.get("ext") in {
                "3gp", "avi", "m4v", "mkv", "mov", "mp4",
                "mpeg", "mpg", "ts", "webm",
            }
        )
        path = downloaded[0]
        if not metrics.downloaded_bytes:
            metrics.downloaded_bytes = path.stat().st_size
        if is_video and path.suffix.lower() != ".mp4":
            remux_started = time.perf_counter()
            try:
                path = Path(self._remux_to_mp4(path, output_dir))
            except RuntimeError as exc:
                log.warning("FFmpeg stream-copy remux failed; keeping original: %s", exc)
            finally:
                metrics.ffmpeg_seconds += time.perf_counter() - remux_started

        return DownloadedMedia(
            path=str(path),
            title=info.get("title") or "Media",
            is_video=bool(is_video),
            metrics=metrics,
        )

    @staticmethod
    def _remux_to_mp4(source: Path, output_dir: str) -> str:
        output = Path(output_dir) / "telegram-video.mp4"
        command = [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?",
            "-c", "copy", str(output),
        ]
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=120
        )
        if result.returncode:
            output.unlink(missing_ok=True)
            raise RuntimeError(f"FFmpeg could not prepare video: {result.stderr[-1500:]}")
        return str(output)

