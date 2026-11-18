from datetime import datetime, timezone
from html import escape
import logging
from pathlib import Path
import re
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from app.config import settings
from app.services.qr import QRService
from app.services.qr_content import safe_storage_path

router = APIRouter()
log = logging.getLogger(__name__)
qr_service = QRService()
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
COUNT_LABELS = {
    "image": "ta rasm",
    "video": "ta video",
    "text": "ta matn",
    "link": "ta havola",
    "document": "ta fayl",
}


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _active_collection(public_token: str):
    if not TOKEN_PATTERN.fullmatch(public_token):
        raise HTTPException(status_code=404, detail="QR topilmadi")
    return public_token


def _valid_link(value: str | None) -> bool:
    if not value:
        return False
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme.lower() in {"http", "https"}
            and parsed.hostname is not None
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


def _filename(value: str | None, fallback: str) -> str:
    name = (value or fallback).replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(character for character in name if character.isprintable())
    return name.strip(" .")[:255] or fallback


def _render_collection(public_token: str, items: list) -> str:
    counts = {kind: 0 for kind in COUNT_LABELS}
    for item in items:
        if item.item_type in counts:
            counts[item.item_type] += 1
    summary = " · ".join(
        f"{count} {COUNT_LABELS[kind]}"
        for kind, count in counts.items()
        if count
    )
    cards: list[str] = []
    for index, item in enumerate(items, start=1):
        title = escape(
            _filename(item.filename, item.item_type.title())
            if item.filename else item.item_type.title()
        )
        if item.item_type == "text":
            content = f"<pre>{escape(item.text_value or '')}</pre>"
        elif item.item_type == "link" and _valid_link(item.url_value):
            link = escape(item.url_value, quote=True)
            content = (
                f'<a class="action" href="{link}" target="_blank" '
                'rel="noopener noreferrer">🔗 Havolani ochish</a>'
            )
        elif item.item_type in {"image", "video", "document"}:
            media_url = f"/q/{public_token}/items/{item.id}"
            if item.item_type == "image":
                content = (
                    f'<img class="photo" src="{media_url}" alt="{title}" '
                    'loading="lazy">'
                )
                content += (
                    f'<div class="download-wrap">'
                    f'<a class="action download-action" href="{media_url}?download=1" '
                    'download rel="nofollow">⬇️ Rasmni saqlash</a>'
                    f'</div>'
                )
                if item.text_value:
                    content += f"<p>{escape(item.text_value)}</p>"
            elif item.item_type == "video":
                mime = escape(item.mime_type or "video/mp4", quote=True)
                content = (
                    '<video controls preload="metadata">'
                    f'<source src="{media_url}" type="{mime}">'
                    "Brauzeringiz videoni ocholmadi.</video>"
                )
                content += (
                    f'<div class="download-wrap">'
                    f'<a class="action download-action" href="{media_url}?download=1" '
                    'download rel="nofollow">⬇️ Videoni saqlash</a>'
                    f'</div>'
                )
                if item.text_value:
                    content += f"<p>{escape(item.text_value)}</p>"
            else:
                content = (
                    f'<div class="download-wrap">'
                    f'<a class="action download-action" href="{media_url}" '
                    'rel="nofollow" download>⬇️ Faylni yuklab olish</a>'
                    f'</div>'
                )
                if item.text_value:
                    content += f"<p>{escape(item.text_value)}</p>"
        else:
            content = "<p>Bu elementni ko‘rsatib bo‘lmadi.</p>"
        cards.append(
            '<article class="card">'
            f'<h2><span>{index:02d}</span> {title}</h2>'
            f"{content}</article>"
        )

    return f"""<!doctype html>
<html lang="uz">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex,nofollow">
  <title>QR Collection</title>
  <style>
    :root {{ color-scheme: light; font-family: system-ui, sans-serif; }}
    body {{ margin: 0; background: #f3f5f8; color: #172033; }}
    main {{ max-width: 760px; margin: auto; padding: 28px 16px 48px; }}
    header {{ padding: 24px; border-radius: 20px; color: white;
      background: linear-gradient(135deg,#405cf5,#7b4de2); }}
    header h1 {{ margin: 0 0 8px; font-size: 1.6rem; }}
    header p {{ margin: 0; opacity: .9; }}
    .card {{ margin-top: 16px; padding: 20px; border-radius: 16px;
      background: white; box-shadow: 0 5px 20px #1c2b4a12; overflow-wrap: anywhere; }}
    .card h2 {{ margin: 0 0 14px; font-size: 1.05rem; }}
    .card h2 span {{ color: #667085; font-variant-numeric: tabular-nums; }}
    pre {{ white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; }}
    .photo, video {{ display: block; max-width: 100%; max-height: 70vh;
      margin: 8px auto; border-radius: 12px; }}
    .action {{ display: inline-block; padding: 11px 16px; border-radius: 10px;
      color: white; background: #405cf5; text-decoration: none; }}
    .download-wrap {{ display: flex; justify-content: center; margin: 14px 0 6px; }}
    .download-action {{ min-width: 180px; text-align: center; box-shadow: 0 6px 18px #405cf52e; }}
    footer {{ margin-top: 24px; color: #667085; text-align: center; font-size: .9rem; }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>📦 QR Collection</h1>
      <p>{escape(summary or "Ma’lumotlar to‘plami")} · {len(items)} ta element</p>
    </header>
    {''.join(cards)}
    <footer>Ushbu sahifa QR kod egasi qo‘shgan ma’lumotlarni ko‘rsatadi.</footer>
  </main>
</body>
</html>"""


@router.get("/q/{public_token}", response_class=HTMLResponse)
async def qr_collection_page(public_token: str) -> HTMLResponse:
    _active_collection(public_token)
    record = await qr_service.get_public_collection(public_token)
    if record is None:
        raise HTTPException(status_code=404, detail="QR topilmadi")
    collection, items = record
    if collection.expires_at and collection.expires_at <= _utcnow_naive():
        raise HTTPException(status_code=404, detail="QR muddati tugagan")

    return HTMLResponse(
        _render_collection(public_token, items),
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'none'; img-src 'self'; media-src 'self'; "
                "style-src 'unsafe-inline'; base-uri 'none'; "
                "form-action 'none'; frame-ancestors 'none'"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/q/{public_token}/items/{item_id}")
async def qr_collection_item(
    public_token: str,
    item_id: int,
    download: bool = False,
) -> FileResponse:
    _active_collection(public_token)
    record = await qr_service.get_public_collection(public_token)
    if record is None:
        raise HTTPException(status_code=404, detail="QR topilmadi")
    collection, _ = record
    if collection.expires_at and collection.expires_at <= _utcnow_naive():
        raise HTTPException(status_code=404, detail="QR muddati tugagan")
    item = await qr_service.get_item(collection.id, item_id)
    if item is None or not item.storage_key or item.item_type not in {
        "image", "video", "document"
    }:
        raise HTTPException(status_code=404, detail="Fayl topilmadi")
    try:
        path = safe_storage_path(settings.qr_storage_dir, item.storage_key)
    except ValueError:
        log.error("invalid QR storage reference for item %s", item.id)
        raise HTTPException(status_code=404, detail="Fayl topilmadi") from None
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Fayl topilmadi")

    disposition = (
        "attachment"
        if item.item_type == "document" or download
        else "inline"
    )
    return FileResponse(
        path,
        media_type=item.mime_type or "application/octet-stream",
        filename=_filename(item.filename, f"file-{item.id}"),
        content_disposition_type=disposition,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )
