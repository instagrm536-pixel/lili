import asyncio
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.enums import ChatType
from aiogram.types import ReplyKeyboardMarkup
from PIL import Image
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api import qr as qr_api
from app.bot.handlers import qr as qr_handler
from app.database.models import Base
from app.services.qr import QRService
from app.services.qr_content import (
    QRInputError,
    generate_qr_png,
    optimize_video_for_qr,
    parse_message,
)
from app.services.state import InMemoryState


TOKEN = "a" * 32


def _message(**kwargs):
    defaults = {
        "photo": None,
        "video": None,
        "document": None,
        "text": None,
        "caption": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_text_and_https_links_are_stored_without_fetching():
    text = "  Salom, bu mening QR kodim  "
    text_item = parse_message(_message(text=text), 1024)
    link = "https://youtube.com/watch?v=abc123"
    link_item = parse_message(_message(text=link), 1024)

    assert text_item == {
        "item_type": "text",
        "text_value": text,
        "size": 0,
    }
    assert link_item == {
        "item_type": "link",
        "url_value": link,
        "size": 0,
    }


def test_unsafe_url_is_not_treated_as_a_link():
    item = parse_message(_message(text="javascript:alert(1)"), 1024)

    assert item["item_type"] == "text"
    assert item["text_value"] == "javascript:alert(1)"


def test_document_filename_is_sanitized_and_unsafe_types_rejected():
    document = SimpleNamespace(
        file_id="private-file-id",
        file_unique_id="unique",
        file_size=20,
        file_name=r"..\folder\report.pdf",
        mime_type="application/pdf",
    )
    item = parse_message(_message(document=document), 1024)
    assert item["filename"] == "report.pdf"
    assert item["mime_type"] == "application/pdf"

    unsafe = SimpleNamespace(
        file_id="private-file-id",
        file_unique_id="unique",
        file_size=20,
        file_name="payload.exe",
        mime_type="application/x-msdownload",
    )
    with pytest.raises(QRInputError):
        parse_message(_message(document=unsafe), 1024)


def test_qr_png_uses_valid_png_image():
    payload = "https://example.test/q/" + TOKEN

    image = Image.open(BytesIO(generate_qr_png(payload)))

    assert image.format == "PNG"
    assert image.width >= 300
    assert image.width == image.height


def test_optimize_video_replaces_large_video_with_smaller_mp4(monkeypatch, tmp_path):
    source = tmp_path / "video-source"
    source.write_bytes(b"x" * 100)

    def fake_run(command, **kwargs):
        output = Path(command[-1])
        output.write_bytes(b"x" * 20)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(
        "app.services.qr_content.subprocess.run",
        fake_run,
    )

    assert optimize_video_for_qr(source) is True
    assert source.read_bytes() == b"x" * 20


def test_qr_service_persists_items_and_enforces_owner_delete(tmp_path):
    async def scenario():
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'qr.db'}"
        )
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        service = QRService(
            async_sessionmaker(engine, expire_on_commit=False)
        )
        collection_id = await service.create_collection(
            101,
            TOKEN,
            [
                {
                    "item_type": "text",
                    "text_value": "Hello",
                    "size": 0,
                },
                {
                    "item_type": "document",
                    "filename": "guide.pdf",
                    "mime_type": "application/pdf",
                    "storage_key": "b" * 32,
                    "size": 10,
                },
            ],
        )

        result = await service.get_public_collection(TOKEN)
        assert result is not None
        collection, items = result
        assert collection.owner_user_id == 101
        assert [item.sort_order for item in items] == [1, 2]
        assert [item.item_type for item in items] == ["text", "document"]

        assert await service.delete_owned_collection(collection_id, 202) is None
        assert await service.get_public_collection(TOKEN) is not None
        keys = await service.delete_owned_collection(collection_id, 101)
        assert keys == ["b" * 32]
        assert await service.get_public_collection(TOKEN) is None
        await engine.dispose()

    asyncio.run(scenario())


def test_public_page_renders_safe_mixed_content(monkeypatch):
    items = [
        SimpleNamespace(
            id=1,
            item_type="text",
            text_value="<script>alert(1)</script>",
            url_value=None,
            filename=None,
            mime_type=None,
        ),
        SimpleNamespace(
            id=2,
            item_type="link",
            text_value=None,
            url_value="https://youtube.com/watch?v=clip",
            filename=None,
            mime_type=None,
        ),
        SimpleNamespace(
            id=3,
            item_type="image",
            text_value=None,
            url_value=None,
            filename='photo"><script>.jpg',
            mime_type="image/jpeg",
        ),
        SimpleNamespace(
            id=4,
            item_type="video",
            text_value=None,
            url_value=None,
            filename="movie.mp4",
            mime_type="video/mp4",
        ),
        SimpleNamespace(
            id=5,
            item_type="document",
            text_value=None,
            url_value=None,
            filename="guide.pdf",
            mime_type="application/pdf",
        ),
    ]
    monkeypatch.setattr(
        qr_api.qr_service,
        "get_public_collection",
        AsyncMock(return_value=(SimpleNamespace(expires_at=None), items)),
    )

    response = asyncio.run(qr_api.qr_collection_page(TOKEN))
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "<script>alert(1)</script>" not in body
    assert "Havolani ochish" in body
    assert "<video controls" in body
    assert "Faylni yuklab olish" in body
    assert "Rasmni saqlash" in body
    assert "Videoni saqlash" in body
    assert "5 ta element" in body
    assert response.headers["content-security-policy"]


def test_public_media_route_checks_token_and_serves_private_storage_file(
    monkeypatch, tmp_path
):
    storage_key = "c" * 32
    stored_file = tmp_path / storage_key
    stored_file.write_bytes(b"video")
    collection = SimpleNamespace(id=77, expires_at=None)
    item = SimpleNamespace(
        id=5,
        item_type="video",
        storage_key=storage_key,
        filename="clip.mp4",
        mime_type="video/mp4",
    )
    monkeypatch.setattr(qr_api.settings, "qr_storage_dir", str(tmp_path))
    monkeypatch.setattr(
        qr_api.qr_service,
        "get_public_collection",
        AsyncMock(return_value=(collection, [item])),
    )
    monkeypatch.setattr(
        qr_api.qr_service, "get_item", AsyncMock(return_value=item)
    )

    response = asyncio.run(qr_api.qr_collection_item(TOKEN, 5))

    assert response.media_type == "video/mp4"
    assert response.headers["content-disposition"].startswith("inline;")

    download_response = asyncio.run(
        qr_api.qr_collection_item(TOKEN, 5, download=True)
    )
    assert download_response.headers["content-disposition"].startswith(
        "attachment;"
    )


def test_media_qr_creates_public_link(monkeypatch):
    async def scenario():
        local_state = InMemoryState()
        monkeypatch.setattr(qr_handler, "state", local_state)
        monkeypatch.setattr(
            qr_handler, "_safe_public_base_url", lambda: "https://qr.example"
        )
        create_qr = AsyncMock()
        monkeypatch.setattr(qr_handler, "_create_qr", create_qr)
        callback = SimpleNamespace(
            data="qr:confirm:test-media",
            from_user=SimpleNamespace(id=321),
            message=SimpleNamespace(answer=AsyncMock()),
            answer=AsyncMock(),
            bot=object(),
        )
        session = {
            "session_id": "test-media",
            "mode": "review",
            "items": [
                {
                    "item_type": "image",
                    "file_id": "telegram-image-id",
                    "size": 100,
                }
            ],
        }
        await local_state.set(
            qr_handler._session_key(321),
            json.dumps(session, ensure_ascii=False),
        )

        await qr_handler.qr_callback(callback)

        create_qr.assert_awaited_once()
        assert create_qr.await_args.args[2] == "https://qr.example"
        assert await local_state.get(qr_handler._session_key(321)) is not None

    asyncio.run(scenario())


def test_single_text_qr_encodes_text_without_public_link(monkeypatch):
    async def scenario():
        local_state = InMemoryState()
        monkeypatch.setattr(qr_handler, "state", local_state)
        create_collection = AsyncMock()
        monkeypatch.setattr(
            qr_handler.qr_service, "create_collection", create_collection
        )

        user = SimpleNamespace(id=321)
        message = SimpleNamespace(
            answer_photo=AsyncMock(),
            edit_reply_markup=AsyncMock(),
        )
        callback = SimpleNamespace(
            data="qr:confirm:test-session",
            from_user=user,
            message=message,
            answer=AsyncMock(),
        )
        session = {
            "session_id": "test-session",
            "mode": "review",
            "items": [
                {
                    "item_type": "text",
                    "text_value": "Salom, mening ma'lumotim",
                    "size": 0,
                }
            ],
        }
        await local_state.set(
            qr_handler._session_key(321),
            json.dumps(session, ensure_ascii=False),
        )

        await qr_handler.qr_callback(callback)

        create_collection.assert_not_awaited()
        message.answer_photo.assert_awaited_once()
        sent_file = message.answer_photo.await_args.args[0]
        assert sent_file.filename == "qr-code.png"
        assert "ma’lumot to‘g‘ridan-to‘g‘ri QR ichida" in message.answer_photo.await_args.kwargs["caption"]
        assert await local_state.get(qr_handler._session_key(321)) is None

    asyncio.run(scenario())


def test_qr_generator_adds_multiple_items_then_confirms(monkeypatch):
    async def scenario():
        local_state = InMemoryState()
        monkeypatch.setattr(qr_handler, "state", local_state)
        create_collection = AsyncMock()
        monkeypatch.setattr(
            qr_handler.qr_service, "create_collection", create_collection
        )

        user = SimpleNamespace(id=321)
        start_message = SimpleNamespace(
            chat=SimpleNamespace(type=ChatType.PRIVATE, id=321),
            from_user=user,
            answer=AsyncMock(),
        )
        await qr_handler.qr_generator_button(start_message)
        session = json.loads(await local_state.get(qr_handler._session_key(321)))
        assert "Bir nechta ma’lumotni bitta QR kodga" in (
            start_message.answer.await_args.args[0]
        )

        text_message = SimpleNamespace(
            chat=SimpleNamespace(type=ChatType.PRIVATE, id=321),
            from_user=user,
            photo=None,
            video=None,
            document=None,
            text=None,
            caption=None,
            answer_photo=AsyncMock(),
            answer=AsyncMock(),
        )
        text_message.text = "Birinchi matn"
        await qr_handler.collect_qr_item(text_message)
        session = json.loads(await local_state.get(qr_handler._session_key(321)))
        assert session["mode"] == "review"

        add_message = SimpleNamespace(answer=AsyncMock())
        add_callback = SimpleNamespace(
            data=f"qr:add:{session['session_id']}",
            from_user=user,
            message=add_message,
            answer=AsyncMock(),
        )
        await qr_handler.qr_callback(add_callback)
        assert "Siz hozirgacha 1 ta element qo‘shdingiz." in (
            add_message.answer.await_args.args[0]
        )

        link_message = SimpleNamespace(
            chat=SimpleNamespace(type=ChatType.PRIVATE, id=321),
            from_user=user,
            photo=None,
            video=None,
            document=None,
            text="https://youtube.com/watch?v=sample",
            caption=None,
            answer=AsyncMock(),
            answer_photo=AsyncMock(),
        )
        await qr_handler.collect_qr_item(link_message)
        session = json.loads(await local_state.get(qr_handler._session_key(321)))
        assert [item["item_type"] for item in session["items"]] == ["text", "link"]
        assert session["items"][1]["url_value"] == link_message.text

        status = SimpleNamespace(delete=AsyncMock(), edit_text=AsyncMock())
        preview_message = SimpleNamespace(
            answer=AsyncMock(return_value=status),
            answer_photo=AsyncMock(),
            edit_reply_markup=AsyncMock(),
        )
        confirm_callback = SimpleNamespace(
            data=f"qr:confirm:{session['session_id']}",
            from_user=user,
            bot=object(),
            message=preview_message,
            answer=AsyncMock(),
        )
        await qr_handler.qr_callback(confirm_callback)

        create_collection.assert_not_awaited()
        assert preview_message.answer_photo.await_count == 1
        assert await local_state.get(qr_handler._session_key(321)) is None

    asyncio.run(scenario())
