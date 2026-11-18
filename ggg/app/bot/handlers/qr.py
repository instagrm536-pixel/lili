import asyncio
import html
import json
import logging
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit

from aiogram import Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.config import settings
from app.services.qr import QRService
from app.services.qr_content import (
    ITEM_LIMITS,
    MAX_TOTAL_ITEMS,
    QRInputError,
    generate_qr_png,
    optimize_video_for_qr,
    parse_message,
    safe_storage_path,
    validate_file_content,
)
from app.services.state import state

router = Router()
log = logging.getLogger(__name__)
qr_service = QRService()
SESSION_KEY = "qr:draft:{}"
MAX_FILE_BYTES = settings.qr_max_item_size_mb * 1024 * 1024
MAX_COLLECTION_BYTES = settings.qr_max_total_size_mb * 1024 * 1024

ITEM_LABELS = {
    "image": "🖼️ Rasm",
    "video": "🎬 Video",
    "text": "📝 Text",
    "link": "🔗 Havola",
    "document": "📄 Hujjat",
}


def _session_key(user_id: int) -> str:
    return SESSION_KEY.format(user_id)


async def _load_session(user_id: int) -> dict | None:
    raw = await state.get(_session_key(user_id))
    if raw is None:
        return None
    try:
        session = json.loads(raw)
    except json.JSONDecodeError:
        await state.delete(_session_key(user_id))
        return None
    if (
        not isinstance(session, dict)
        or not isinstance(session.get("items"), list)
        or session.get("mode") not in {"collecting", "review", "creating"}
        or not isinstance(session.get("session_id"), str)
    ):
        await state.delete(_session_key(user_id))
        return None
    return session


async def _save_session(user_id: int, session: dict) -> None:
    await state.set(
        _session_key(user_id),
        json.dumps(session, ensure_ascii=False),
        ex=settings.qr_session_ttl_seconds,
    )


def _draft_keyboard(session: dict) -> InlineKeyboardMarkup:
    session_id = session["session_id"]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Tasdiqlash",
                    callback_data=f"qr:confirm:{session_id}",
                    style="success",
                ),
                InlineKeyboardButton(
                    text="➕ Yana qo‘shish",
                    callback_data=f"qr:add:{session_id}",
                    style="primary",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📋 Qo‘shilganlar",
                    callback_data=f"qr:list:{session_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Bekor qilish",
                    callback_data=f"qr:cancel:{session_id}",
                    style="danger",
                ),
            ],
        ]
    )


def _cancel_keyboard(session: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="❌ Bekor qilish",
                callback_data=f"qr:cancel:{session['session_id']}",
                style="danger",
            )
        ]]
    )


def _count_types(items: list[dict]) -> dict[str, int]:
    counts = {kind: 0 for kind in ITEM_LIMITS}
    for item in items:
        kind = item.get("item_type")
        if kind in counts:
            counts[kind] += 1
    return counts


def _validate_item_limit(items: list[dict], item: dict) -> None:
    if len(items) >= MAX_TOTAL_ITEMS:
        raise QRInputError("⚠️ Maksimal elementlar soniga yetdingiz.")
    counts = _count_types(items)
    kind = item.get("item_type")
    if kind not in ITEM_LIMITS or counts[kind] >= ITEM_LIMITS[kind]:
        raise QRInputError("⚠️ Bu turdagi elementlar uchun limit tugadi.")
    projected_size = sum(int(existing.get("size") or 0) for existing in items)
    projected_size += int(item.get("size") or 0)
    if projected_size > MAX_COLLECTION_BYTES:
        raise QRInputError(
            f"Jami fayllar {settings.qr_max_total_size_mb} MB limitidan oshadi."
        )


def _item_title(item: dict) -> str:
    return ITEM_LABELS.get(item.get("item_type"), "📦 Ma’lumot")


def _items_list_text(items: list[dict]) -> str:
    lines = ["📋 <b>Qo‘shilgan ma’lumotlar</b>"]
    for index, item in enumerate(items, start=1):
        label = _item_title(item)
        filename = item.get("filename")
        if filename:
            label += f" — {html.escape(filename)}"
        elif item.get("item_type") == "text":
            snippet = " ".join((item.get("text_value") or "").split())[:60]
            if snippet:
                label += f" — {html.escape(snippet)}"
        elif item.get("item_type") == "link":
            label += f" — {html.escape(item.get('url_value') or '')[:80]}"
        lines.append(f"{index}. {label}")
    return "\n".join(lines)


def _safe_public_base_url() -> str:
    base_url = settings.public_base_url.strip().rstrip("/")
    parsed = urlsplit(base_url)
    try:
        valid = (
            parsed.scheme.lower() in {"http", "https"}
            and parsed.hostname is not None
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and parsed.query == ""
            and parsed.fragment == ""
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(
            "QR sahifasi uchun .env faylida PUBLIC_BASE_URL "
            "(masalan, https://example.com) sozlang."
        )
    if settings.environment.lower() == "production" and parsed.scheme.lower() != "https":
        raise ValueError("Production muhitida PUBLIC_BASE_URL HTTPS bo‘lishi kerak.")
    return base_url


def _record_data(item: dict, storage_key: str | None = None) -> dict:
    return {
        "item_type": item["item_type"],
        "text_value": item.get("text_value"),
        "url_value": item.get("url_value"),
        "storage_key": storage_key,
        "filename": item.get("filename"),
        "mime_type": item.get("mime_type"),
        "size": int(item.get("size") or 0),
    }


def _cleanup_storage(paths: list[Path]) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            log.exception("failed to remove temporary QR media file")


async def _persist_items(bot, items: list[dict], created_paths: list[Path]) -> list[dict]:
    storage_root = Path(settings.qr_storage_dir).resolve()
    storage_root.mkdir(parents=True, exist_ok=True)
    async def persist_item(index: int, item: dict) -> tuple[int, dict]:
        item_type = item.get("item_type")
        if item_type in {"text", "link"}:
            return index, _record_data(item)
        if item_type not in {"image", "video", "document"}:
            raise QRInputError("QR sessiyasida noto‘g‘ri element bor.")

        telegram_file = None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                telegram_file = await bot.get_file(item["file_id"])
                break
            except TelegramBadRequest as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(1)
        if telegram_file is None:
            if last_error is not None:
                log.warning(
                    "Telegram file_id is unavailable for QR item %s",
                    item_type,
                    exc_info=(type(last_error), last_error, last_error.__traceback__),
                )
            raise QRInputError(
                "Faylni Telegram’dan olishning iloji bo‘lmadi. "
                "Rasm, video yoki faylni chatga qayta yuboring."
            )
        if not telegram_file.file_path:
            raise RuntimeError("Telegram fayl yo‘li qaytmadi.")
        expected_size = int(telegram_file.file_size or item.get("size") or 0)
        if expected_size <= 0 or expected_size > MAX_FILE_BYTES:
            raise QRInputError(
                f"Har bir fayl {settings.qr_max_item_size_mb} MB dan oshmasligi kerak."
            )

        storage_key = secrets.token_hex(16)
        path = safe_storage_path(settings.qr_storage_dir, storage_key)
        created_paths.append(path)
        download_error: Exception | None = None
        for attempt in range(3):
            try:
                await bot.download_file(
                    telegram_file.file_path,
                    destination=path,
                    timeout=30,
                    chunk_size=64 * 1024,
                )
                break
            except Exception as exc:
                download_error = exc
                if path.exists():
                    path.unlink(missing_ok=True)
                if attempt < 2:
                    await asyncio.sleep(1)
        if not path.is_file():
            if download_error is not None:
                log.warning(
                    "Telegram QR media download failed",
                    exc_info=(
                        type(download_error),
                        download_error,
                        download_error.__traceback__,
                    ),
                )
            raise QRInputError(
                "Faylni yuklab bo‘lmadi. Internet aloqasini tekshirib, "
                "faylni qayta yuboring."
            )
        actual_size = path.stat().st_size
        if actual_size <= 0 or actual_size > MAX_FILE_BYTES:
            raise QRInputError("Yuklangan fayl bo‘sh yoki hajm limitidan katta.")
        validate_file_content(path, item["mime_type"])
        if item_type == "video":
            if optimize_video_for_qr(path):
                item = dict(item)
                item["mime_type"] = "video/mp4"
                item["filename"] = (
                    f"{Path(item['filename']).stem or 'video'}-optimized.mp4"
                )
                actual_size = path.stat().st_size
        stored_item = dict(item)
        stored_item["size"] = actual_size
        return index, _record_data(stored_item, storage_key)

    indexed_records = await asyncio.gather(
        *(persist_item(index, item) for index, item in enumerate(items))
    )
    indexed_records.sort(key=lambda record: record[0])
    records = [record for _, record in indexed_records]
    actual_total_size = sum(int(record.get("size") or 0) for record in records)
    if actual_total_size > MAX_COLLECTION_BYTES:
        raise QRInputError(
            f"Jami fayllar {settings.qr_max_total_size_mb} MB limitidan oshadi."
        )

    return records


def _public_link_keyboard(collection_id: int, public_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔗 QR havolasini olish",
                    url=public_url,
                )
            ],
            [
                InlineKeyboardButton(
                    text="📋 Ma’lumotlarni ko‘rish",
                    url=public_url,
                ),
                InlineKeyboardButton(
                    text="🗑️ QR’ni o‘chirish",
                    callback_data=f"qr:delete:{collection_id}",
                    style="danger",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Yangi QR yaratish",
                    callback_data="qr:new",
                )
            ],
        ]
    )


def _direct_qr_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="🔄 Yangi QR yaratish",
                callback_data="qr:new",
            )
        ]]
    )


async def _open_generator(message: Message, user_id: int) -> None:
    session = await _load_session(user_id)
    if session is None:
        session = {
            "session_id": secrets.token_hex(6),
            "mode": "collecting",
            "items": [],
        }
        await _save_session(user_id, session)
        await message.answer(
            "📱 QR Generator\n\n"
            "QR kod ichiga nimani joylashtirmoqchisiz?\n"
            "Matn, havola, rasm, video yoki fayl yuborishingiz mumkin.\n\n"
            "Bir nechta ma’lumotni bitta QR kodga qo‘shishingiz ham mumkin.",
            reply_markup=_cancel_keyboard(session),
        )
    elif session["mode"] == "collecting":
        await message.answer(
            "Davom etish uchun matn, havola, rasm, video yoki fayl yuboring.\n"
            f"📦 Hozirgacha {len(session['items'])} ta element qo‘shildi.",
            reply_markup=_cancel_keyboard(session),
        )
    else:
        await message.answer(
            _items_list_text(session["items"]),
            reply_markup=_draft_keyboard(session),
        )


@router.message(lambda message: message.text and message.text.casefold() == "📱 qr generator")
async def qr_generator_button(message: Message) -> None:
    if message.chat.type != "private" or message.from_user is None:
        await message.answer("QR Generator faqat shaxsiy chatda ishlaydi.")
        return
    await _open_generator(message, message.from_user.id)


@router.message(Command("qr"))
async def qr_generator_command(message: Message) -> None:
    if message.chat.type != "private" or message.from_user is None:
        await message.answer("QR Generator faqat shaxsiy chatda ishlaydi.")
        return
    await _open_generator(message, message.from_user.id)


@router.message()
async def collect_qr_item(message: Message) -> None:
    if message.from_user is None or message.chat.type != "private":
        raise SkipHandler
    session = await _load_session(message.from_user.id)
    if session is None:
        raise SkipHandler

    if message.text and message.text.casefold() in {"/cancel", "❌ bekor qilish"}:
        await state.delete(_session_key(message.from_user.id))
        await message.answer("❌ QR yaratish bekor qilindi.")
        return
    if message.text and message.text.startswith("/"):
        raise SkipHandler
    if session["mode"] != "collecting":
        await message.answer(
            "Avval «➕ Yana qo‘shish» tugmasini bosing yoki QR yaratishni tasdiqlang.",
            reply_markup=_draft_keyboard(session),
        )
        return

    try:
        item = parse_message(message, MAX_FILE_BYTES)
        _validate_item_limit(session["items"], item)
    except QRInputError as exc:
        await message.answer(str(exc), reply_markup=_cancel_keyboard(session))
        return

    session["items"].append(item)
    session["mode"] = "review"
    await _save_session(message.from_user.id, session)
    summary = (
        "✅ Ma’lumot qabul qilindi.\n\n"
        f"📦 Jami elementlar: {len(session['items'])}\n\n"
        "Yana qo‘shishingiz yoki QR yaratishingiz mumkin."
    )
    markup = _draft_keyboard(session)
    item_type = item["item_type"]

    if item_type == "image":
        await message.answer_photo(item["file_id"], caption=summary, reply_markup=markup)
    elif item_type == "video":
        await message.answer_video(item["file_id"], caption=summary, reply_markup=markup)
    elif item_type == "document":
        await message.answer_document(item["file_id"], caption=summary, reply_markup=markup)
    elif item_type == "link":
        escaped_url = html.escape(item["url_value"], quote=True)
        await message.answer(
            f"{summary}\n\n🔗 <a href=\"{escaped_url}\">Havolani ochish</a>",
            reply_markup=markup,
        )
    else:
        escaped_text = html.escape(item["text_value"][:2000])
        await message.answer(
            f"{summary}\n\n📝 <blockquote>{escaped_text}</blockquote>",
            reply_markup=markup,
        )


async def _create_qr(
    callback: CallbackQuery, session: dict, base_url: str
) -> None:
    direct_items = [
        item["text_value"]
        if item["item_type"] == "text"
        else item["url_value"]
        for item in session["items"]
        if item["item_type"] in {"text", "link"}
    ]
    if (
        len(direct_items) == len(session["items"])
        and direct_items
    ):
        payload = "\n\n".join(
            str(value).strip()
            for value in direct_items
            if str(value).strip()
        )
        if payload:
            await state.delete(_session_key(callback.from_user.id))
            qr_png = await asyncio.to_thread(generate_qr_png, payload)
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.answer_photo(
                BufferedInputFile(qr_png, filename="qr-code.png"),
                caption="✅ QR kod tayyor!\n\nRasm havolasiz — ma’lumot to‘g‘ridan-to‘g‘ri QR ichida.",
                reply_markup=_direct_qr_keyboard(),
            )
            return

    token = secrets.token_urlsafe(24)
    public_url = f"{base_url}/q/{token}"
    status = None
    created_paths: list[Path] = []
    try:
        status = await callback.message.answer("⏳ QR kod tayyorlanmoqda...")
        qr_png, records = await asyncio.gather(
            asyncio.to_thread(generate_qr_png, public_url),
            _persist_items(callback.bot, session["items"], created_paths),
        )
        collection_id = await qr_service.create_collection(
            callback.from_user.id,
            token,
            records,
        )
    except Exception:
        _cleanup_storage(created_paths)
        log.exception("failed to create QR collection")
        session["mode"] = "review"
        await _save_session(callback.from_user.id, session)
        if status is not None:
            await status.edit_text(
                "❌ QR yaratilmadi. Elementlaringiz vaqtinchalik saqlandi; "
                "qayta urinib ko‘ring yoki bekor qiling.",
                reply_markup=_draft_keyboard(session),
            )
        else:
            await callback.message.answer(
                "❌ QR yaratilmadi. Qayta urinib ko‘ring yoki bekor qiling.",
                reply_markup=_draft_keyboard(session),
            )
        return

    await state.delete(_session_key(callback.from_user.id))
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        log.warning("could not remove the completed QR draft keyboard", exc_info=True)
    caption = (
        "✅ QR kod tayyor!\n\n"
        f"📦 {len(session['items'])} ta ma’lumot birlashtirildi."
    )
    try:
        await callback.message.answer_photo(
            BufferedInputFile(qr_png, filename="qr-code.png"),
            caption=caption,
            reply_markup=(
                _public_link_keyboard(collection_id, public_url)
                if urlsplit(public_url).scheme.lower() == "https"
                else None
            ),
        )
    except Exception:
        log.exception("QR collection created but QR image could not be sent")
        if status is not None:
            await status.edit_text(
                "✅ QR ma’lumotlari saqlandi, lekin rasmni yuborib bo‘lmadi.\n"
                f"🔗 Havola: {html.escape(public_url)}",
                reply_markup=(
                    _public_link_keyboard(collection_id, public_url)
                    if urlsplit(public_url).scheme.lower() == "https"
                    else None
                ),
            )
        return
    if status is not None:
        await status.delete()


@router.callback_query(lambda callback: callback.data and callback.data.startswith("qr:"))
async def qr_callback(callback: CallbackQuery) -> None:
    data = callback.data.split(":")
    action = data[1] if len(data) > 1 else ""

    if action == "new":
        await callback.answer()
        if callback.message is not None:
            await _open_generator(callback.message, callback.from_user.id)
        return

    if action == "delete":
        if len(data) != 3 or callback.message is None:
            await callback.answer("QR topilmadi.", show_alert=True)
            return
        try:
            collection_id = int(data[2])
        except ValueError:
            await callback.answer("QR identifikatori noto‘g‘ri.", show_alert=True)
            return
        await callback.answer()
        try:
            storage_keys = await qr_service.delete_owned_collection(
                collection_id, callback.from_user.id
            )
        except Exception:
            log.exception("failed to delete QR collection")
            await callback.message.answer(
                "❌ QR o‘chirilmadi. Keyinroq qayta urinib ko‘ring."
            )
            return
        if storage_keys is None:
            await callback.message.answer(
                "QR topilmadi yoki uni o‘chirish huquqingiz yo‘q.",
            )
            return
        cleanup_failed = False
        for storage_key in storage_keys:
            try:
                safe_storage_path(settings.qr_storage_dir, storage_key).unlink(
                    missing_ok=True
                )
            except (OSError, ValueError):
                cleanup_failed = True
                log.exception("failed to delete QR collection media")
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "✅ QR o‘chirildi."
            + (" Ba’zi fayllarni storage’dan tozalashda xato yuz berdi." if cleanup_failed else "")
        )
        return

    if action not in {"add", "list", "cancel", "confirm"} or len(data) != 3:
        await callback.answer("QR amali noto‘g‘ri.", show_alert=True)
        return

    session = await _load_session(callback.from_user.id)
    if session is None or session["session_id"] != data[2]:
        await callback.answer(
            "QR sessiyasi tugagan. Yangi QR boshlang.",
            show_alert=True,
        )
        return
    if callback.message is None:
        return
    if session["mode"] == "creating":
        await callback.answer("QR tayyorlanmoqda, kuting.", show_alert=True)
        return

    if action == "cancel":
        await callback.answer()
        await state.delete(_session_key(callback.from_user.id))
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("❌ QR yaratish bekor qilindi.")
        return

    if action == "add":
        await callback.answer()
        if len(session["items"]) >= MAX_TOTAL_ITEMS:
            await callback.message.answer(
                "⚠️ Maksimal elementlar soniga yetdingiz.",
                reply_markup=_draft_keyboard(session),
            )
            return
        session["mode"] = "collecting"
        await _save_session(callback.from_user.id, session)
        await callback.message.answer(
            "➕ Yangi ma’lumot yuboring.\n\n"
            f"Siz hozirgacha {len(session['items'])} ta element qo‘shdingiz.",
            reply_markup=_cancel_keyboard(session),
        )
        return

    if action == "list":
        await callback.answer()
        await callback.message.answer(
            _items_list_text(session["items"]),
            reply_markup=_draft_keyboard(session),
        )
        return

    if action == "confirm":
        if session["mode"] != "review" or not session["items"]:
            await callback.answer(
                "Avval QR kodga kamida bitta ma’lumot yuboring.",
                show_alert=True,
            )
            return
        media_items = any(
            item["item_type"] in {"image", "video", "document"}
            for item in session["items"]
        )
        text_only_items = all(
            item["item_type"] in {"text", "link"}
            for item in session["items"]
        )
        if media_items or not text_only_items:
            try:
                base_url = _safe_public_base_url()
            except ValueError as exc:
                await callback.answer(str(exc), show_alert=True)
                return
        else:
            base_url = ""
        session["mode"] = "creating"
        await _save_session(callback.from_user.id, session)
        await callback.answer()
        await _create_qr(callback, session, base_url)

