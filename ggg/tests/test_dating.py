import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.enums import ChatType
import pytest
from sqlalchemy.exc import IntegrityError

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.models import Base
from app.bot.handlers import dating as dating_handler
from app.services.dating import DatingService


def test_dating_requests_require_recipient_consent_and_hide_contacted_profiles(
    tmp_path,
):
    async def scenario():
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'dating.db'}"
        )
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        service = DatingService(
            async_sessionmaker(engine, expire_on_commit=False)
        )
        await service.create_profile(
            101, "alice", "Alice Example", 24, "woman"
        )
        await service.create_profile(
            202, "bob", "Bob Example", 28, "man"
        )

        profiles, has_more = await service.list_profiles(101, 0, 8)
        assert [profile.telegram_id for profile in profiles] == [202]
        assert not has_more

        request, status = await service.create_request(101, 202)
        assert status == "created"
        assert request.status == "pending"

        unauthorized = await service.respond_to_request(
            request.id, 101, accept=True
        )
        assert unauthorized is None

        profiles, _ = await service.list_profiles(101, 0, 8)
        assert profiles == []

        accepted = await service.respond_to_request(
            request.id, 202, accept=True
        )
        assert accepted is not None
        saved_request, sender, recipient = accepted
        assert saved_request.status == "accepted"
        assert sender.username == "alice"
        assert recipient.username == "bob"

        duplicate, duplicate_status = await service.create_request(101, 202)
        assert duplicate.id == request.id
        assert duplicate_status == "already_contacted"
        await engine.dispose()

    asyncio.run(scenario())


def test_dating_profile_database_rejects_underage_profile(tmp_path):
    async def scenario():
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'adults-only.db'}"
        )
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        service = DatingService(
            async_sessionmaker(engine, expire_on_commit=False)
        )
        with pytest.raises(IntegrityError):
            await service.create_profile(
                303, "young_user", "Young User", 17, "other"
            )
        await engine.dispose()

    asyncio.run(scenario())


def test_registration_rejects_underage_user_without_saving_profile(monkeypatch):
    registration = {
        "step": "age",
        "gender": "woman",
        "full_name": "Example User",
    }
    monkeypatch.setattr(
        dating_handler.state,
        "get",
        AsyncMock(return_value=json.dumps(registration)),
    )
    delete_state = AsyncMock()
    monkeypatch.setattr(dating_handler.state, "delete", delete_state)
    create_profile = AsyncMock()
    monkeypatch.setattr(
        dating_handler.dating_service, "create_profile", create_profile
    )
    message = SimpleNamespace(
        chat=SimpleNamespace(type=ChatType.PRIVATE),
        from_user=SimpleNamespace(id=303, username="young_user"),
        text="17",
        contact=None,
        answer=AsyncMock(),
    )

    asyncio.run(dating_handler.registration_message(message))

    create_profile.assert_not_awaited()
    delete_state.assert_awaited_once_with("dating:registration:303")
    assert "18 yosh" in message.answer.await_args.args[0]


def test_contact_must_belong_to_registering_user(monkeypatch):
    registration = {
        "step": "contact",
        "gender": "other",
        "full_name": "Example User",
        "age": 25,
    }
    monkeypatch.setattr(
        dating_handler.state,
        "get",
        AsyncMock(return_value=json.dumps(registration)),
    )
    create_profile = AsyncMock()
    monkeypatch.setattr(
        dating_handler.dating_service, "create_profile", create_profile
    )
    message = SimpleNamespace(
        chat=SimpleNamespace(type=ChatType.PRIVATE),
        from_user=SimpleNamespace(id=303, username="example_user"),
        text=None,
        contact=SimpleNamespace(
            user_id=404,
            phone_number="+10000000000",
        ),
        answer=AsyncMock(),
    )

    asyncio.run(dating_handler.registration_message(message))

    create_profile.assert_not_awaited()
    assert "o‘zingizning" in message.answer.await_args.args[0]


def test_own_contact_is_discarded_after_registration(monkeypatch):
    registration = {
        "step": "contact",
        "gender": "woman",
        "full_name": "Example User",
        "age": 25,
    }
    monkeypatch.setattr(
        dating_handler.state,
        "get",
        AsyncMock(return_value=json.dumps(registration)),
    )
    delete_state = AsyncMock()
    monkeypatch.setattr(dating_handler.state, "delete", delete_state)
    create_profile = AsyncMock()
    monkeypatch.setattr(
        dating_handler.dating_service, "create_profile", create_profile
    )
    monkeypatch.setattr(
        dating_handler.dating_service,
        "list_profiles",
        AsyncMock(return_value=([], False)),
    )
    phone_number = "+10000000000"
    message = SimpleNamespace(
        chat=SimpleNamespace(type=ChatType.PRIVATE),
        from_user=SimpleNamespace(id=303, username="example_user"),
        text=None,
        contact=SimpleNamespace(
            user_id=303,
            phone_number=phone_number,
        ),
        answer=AsyncMock(),
    )

    asyncio.run(dating_handler.registration_message(message))

    create_profile.assert_awaited_once_with(
        telegram_id=303,
        username="example_user",
        full_name="Example User",
        age=25,
        gender="woman",
    )
    delete_state.assert_awaited_once_with("dating:registration:303")
    assert phone_number not in repr(message.answer.await_args_list)


def test_registration_edits_existing_prompt_for_next_step(monkeypatch):
    registration = {
        "step": "name",
        "prompt_message_id": 707,
        "gender": "woman",
    }
    monkeypatch.setattr(
        dating_handler.state,
        "get",
        AsyncMock(return_value=json.dumps(registration)),
    )
    save_state = AsyncMock()
    monkeypatch.setattr(dating_handler.state, "set", save_state)
    bot = SimpleNamespace(edit_message_text=AsyncMock())
    message = SimpleNamespace(
        chat=SimpleNamespace(type=ChatType.PRIVATE, id=303),
        from_user=SimpleNamespace(id=303, username="example_user"),
        text="Ali Vali",
        contact=None,
        bot=bot,
        answer=AsyncMock(),
    )

    asyncio.run(dating_handler.registration_message(message))

    bot.edit_message_text.assert_awaited_once()
    assert bot.edit_message_text.await_args.kwargs["message_id"] == 707
    assert "Yoshingizni kiriting" in bot.edit_message_text.await_args.kwargs["text"]
    message.answer.assert_not_awaited()
    saved = json.loads(save_state.await_args.args[1])
    assert saved["step"] == "age"


def test_age_to_contact_transition_closes_old_prompt(monkeypatch):
    registration = {
        "step": "age",
        "prompt_message_id": 707,
        "gender": "woman",
        "full_name": "Ali Vali",
    }
    monkeypatch.setattr(
        dating_handler.state,
        "get",
        AsyncMock(return_value=json.dumps(registration)),
    )
    save_state = AsyncMock()
    monkeypatch.setattr(dating_handler.state, "set", save_state)
    bot = SimpleNamespace(delete_message=AsyncMock())
    contact_prompt = SimpleNamespace(message_id=808)
    message = SimpleNamespace(
        chat=SimpleNamespace(type=ChatType.PRIVATE, id=303),
        from_user=SimpleNamespace(id=303, username="example_user"),
        text="25",
        contact=None,
        bot=bot,
        answer=AsyncMock(return_value=contact_prompt),
    )

    asyncio.run(dating_handler.registration_message(message))

    bot.delete_message.assert_awaited_once_with(303, 707)
    assert message.answer.await_args.kwargs["reply_markup"].keyboard[0][0].request_contact
    saved = json.loads(save_state.await_args.args[1])
    assert saved["step"] == "contact"
    assert saved["prompt_message_id"] == 808


def test_dating_bottom_button_starts_registration(monkeypatch):
    monkeypatch.setattr(
        dating_handler.state, "get", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        dating_handler.state, "set", AsyncMock()
    )
    monkeypatch.setattr(
        dating_handler.dating_service,
        "get_profile",
        AsyncMock(return_value=None),
    )
    prompt = SimpleNamespace(message_id=909)
    message = SimpleNamespace(
        chat=SimpleNamespace(type=ChatType.PRIVATE, id=303),
        from_user=SimpleNamespace(id=303, username="example_user"),
        text="💞 Tanishuv",
        answer=AsyncMock(return_value=prompt),
    )

    asyncio.run(dating_handler.dating_menu_button(message))

    assert "Jinsingizni tanlang" in message.answer.await_args.args[0]
    assert message.answer.await_args.kwargs["reply_markup"].inline_keyboard


def test_profile_button_shows_own_profile(monkeypatch):
    profile = SimpleNamespace(
        full_name="Ali Vali",
        age=25,
        gender="man",
        username="ali_vali",
    )
    monkeypatch.setattr(
        dating_handler.dating_service,
        "get_profile",
        AsyncMock(return_value=profile),
    )
    message = SimpleNamespace(
        chat=SimpleNamespace(type=ChatType.PRIVATE, id=303),
        from_user=SimpleNamespace(id=303, username="ali_vali"),
        text="👤 Profil",
        answer=AsyncMock(),
    )

    asyncio.run(dating_handler.profile_menu_button(message))

    text = message.answer.await_args.args[0]
    assert "Ali Vali" in text
    assert "25" in text
    assert "@ali_vali" in text
    assert "Telefon raqamingiz profilga kiritilmagan" in text
