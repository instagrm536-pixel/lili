from io import BytesIO
import mimetypes
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

from aiogram.types import Message
from qrcode import QRCode
from qrcode.constants import ERROR_CORRECT_H


ITEM_LIMITS = {
    "image": 10,
    "video": 5,
    "text": 10,
    "link": 10,
    "document": 10,
}
MAX_TOTAL_ITEMS = sum(ITEM_LIMITS.values())
MAX_TEXT_CHARS = 4096
MAX_URL_CHARS = 2048

DOCUMENT_TYPES = {
    ".pdf": ("application/pdf",),
    ".zip": ("application/zip", "application/x-zip-compressed"),
    ".txt": ("text/plain",),
    ".md": ("text/markdown", "text/plain"),
    ".csv": ("text/csv", "text/plain", "application/csv"),
    ".json": ("application/json", "text/plain"),
    ".rtf": ("application/rtf", "text/rtf"),
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    ".xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
    ".pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ),
}
VIDEO_TYPES = {
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
}
VIDEO_MIMES_BY_EXTENSION = {extension: mime for mime, extension in VIDEO_TYPES.items()}


class QRInputError(ValueError):
    pass


def _safe_filename(filename: str | None, fallback: str) -> str:
    name = (filename or fallback).replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(character for character in name if character.isprintable())
    return name.strip(" .")[:255] or fallback


def _file_item(
    item_type: str,
    file_id: str,
    file_unique_id: str,
    file_size: int | None,
    filename: str | None,
    mime_type: str | None,
    max_file_size: int,
    caption: str | None = None,
) -> dict:
    size = int(file_size or 0)
    if size > max_file_size:
        raise QRInputError(
            f"Fayl {max_file_size // (1024 * 1024)} MB limitidan katta."
        )

    if item_type == "image":
        canonical_mime = "image/jpeg"
        safe_name = _safe_filename(filename, "image.jpg")
    elif item_type == "video":
        mime = (mime_type or "").lower()
        safe_name = _safe_filename(filename, "video.mp4")
        if not mime:
            mime = VIDEO_MIMES_BY_EXTENSION.get(
                Path(safe_name).suffix.lower(), ""
            )
        extension = VIDEO_TYPES.get(mime)
        if extension is None:
            raise QRInputError(
                "Video formati qo‘llab-quvvatlanmaydi. MP4, WebM yoki MOV yuboring."
            )
        canonical_mime = mime
        if Path(safe_name).suffix.lower() != extension:
            safe_name = f"{Path(safe_name).stem or 'video'}{extension}"
    else:
        safe_name = _safe_filename(filename, "document")
        extension = Path(safe_name).suffix.lower()
        allowed_mimes = DOCUMENT_TYPES.get(extension)
        if allowed_mimes is None:
            raise QRInputError(
                "Fayl turi qo‘llab-quvvatlanmaydi. PDF, ZIP, TXT, CSV, "
                "JSON, RTF, DOCX, XLSX yoki PPTX yuboring."
            )
        supplied_mime = (mime_type or "").lower()
        guessed_mime = mimetypes.guess_type(safe_name)[0]
        if supplied_mime and supplied_mime not in allowed_mimes and supplied_mime != "application/octet-stream":
            raise QRInputError("Fayl MIME turi va kengaytmasi mos kelmadi.")
        canonical_mime = guessed_mime if guessed_mime in allowed_mimes else allowed_mimes[0]

    return {
        "item_type": item_type,
        "file_id": file_id,
        "file_unique_id": file_unique_id,
        "filename": safe_name,
        "mime_type": canonical_mime,
        "size": size,
        "text_value": caption or None,
    }


def parse_message(message: Message, max_file_size: int) -> dict:
    if message.photo:
        photo = message.photo[-1]
        return _file_item(
            "image",
            photo.file_id,
            photo.file_unique_id,
            photo.file_size,
            None,
            "image/jpeg",
            max_file_size,
            message.caption,
        )

    if message.video:
        video = message.video
        return _file_item(
            "video",
            video.file_id,
            video.file_unique_id,
            video.file_size,
            video.file_name,
            video.mime_type,
            max_file_size,
            message.caption,
        )

    if message.document:
        document = message.document
        return _file_item(
            "document",
            document.file_id,
            document.file_unique_id,
            document.file_size,
            document.file_name,
            document.mime_type,
            max_file_size,
            message.caption,
        )

    if message.text is None:
        raise QRInputError("Matn, havola, rasm, video yoki hujjat yuboring.")

    text = message.text
    if not text.strip():
        raise QRInputError("Bo‘sh matnni QR kodga qo‘shib bo‘lmaydi.")
    if len(text) > MAX_TEXT_CHARS:
        raise QRInputError(f"Matn {MAX_TEXT_CHARS} belgidan oshmasligi kerak.")

    candidate = text.strip()
    if candidate.lower().startswith(("http://", "https://")):
        if len(candidate) > MAX_URL_CHARS or any(character.isspace() for character in candidate):
            raise QRInputError("Havola noto‘g‘ri yoki juda uzun.")
        parsed = urlsplit(candidate)
        try:
            valid = (
                parsed.scheme.lower() in {"http", "https"}
                and parsed.hostname is not None
                and parsed.username is None
                and parsed.password is None
                and (parsed.port is None or 1 <= parsed.port <= 65535)
            )
        except ValueError:
            valid = False
        if not valid:
            raise QRInputError("Faqat to‘g‘ri HTTP yoki HTTPS havola yuboring.")
        return {
            "item_type": "link",
            "url_value": candidate,
            "size": 0,
        }

    return {
        "item_type": "text",
        "text_value": text,
        "size": 0,
    }


def generate_qr_png(payload: str) -> bytes:
    qr = QRCode(
        version=None,
        error_correction=ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def validate_file_content(path: Path, mime_type: str) -> None:
    with path.open("rb") as media_file:
        header = media_file.read(16)

    valid = True
    if mime_type == "image/jpeg":
        valid = header.startswith(b"\xff\xd8\xff")
    elif mime_type in {"video/mp4", "video/quicktime"}:
        valid = len(header) >= 8 and header[4:8] == b"ftyp"
    elif mime_type == "video/webm":
        valid = header.startswith(bytes.fromhex("1A45DFA3"))
    elif mime_type == "application/pdf":
        valid = header.startswith(b"%PDF-")
    elif mime_type in {
        "application/zip",
        "application/x-zip-compressed",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }:
        valid = header.startswith(b"PK\x03\x04") or header.startswith(b"PK\x05\x06")
    elif mime_type in {
        "text/plain",
        "text/markdown",
        "text/csv",
        "application/csv",
        "application/json",
    }:
        with path.open("rb") as media_file:
            sample = media_file.read(64 * 1024)
        valid = b"\x00" not in sample
        if valid:
            try:
                sample.decode("utf-8")
            except UnicodeDecodeError:
                valid = False
    elif mime_type in {"application/rtf", "text/rtf"}:
        valid = header.startswith(b"{\\rtf")

    if not valid:
        raise QRInputError("Fayl mazmuni ko‘rsatilgan MIME turiga mos kelmadi.")


def optimize_video_for_qr(path: Path) -> bool:
    optimized_path = path.with_suffix(".optimized.mp4")
    command = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(path),
        "-vf",
        "scale=w='min(480,iw)':h=-2",
        "-r",
        "24",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "30",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "64k",
        "-movflags",
        "+faststart",
        str(optimized_path),
    ]
    try:
        subprocess.run(
            command,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if not optimized_path.is_file() or optimized_path.stat().st_size <= 0:
            return False
        if optimized_path.stat().st_size >= path.stat().st_size:
            optimized_path.unlink(missing_ok=True)
            return False
        optimized_path.replace(path)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError, OSError):
        optimized_path.unlink(missing_ok=True)
        return False


def safe_storage_path(storage_dir: str, storage_key: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", storage_key):
        raise ValueError("Invalid QR media storage key")
    root = Path(storage_dir).resolve()
    path = (root / storage_key).resolve()
    if path.parent != root:
        raise ValueError("QR media path escaped storage directory")
    return path
