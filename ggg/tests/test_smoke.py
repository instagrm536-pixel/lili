import asyncio
from unittest.mock import AsyncMock

from app.bot.handlers.start import start
from aiogram.types import ReplyKeyboardMarkup


def test_project_smoke():
    assert True


def test_start_menu_uses_bottom_dating_profile_and_qr_keyboard():
    message = type("MessageStub", (), {"answer": AsyncMock()})()

    asyncio.run(start(message))

    text = message.answer.await_args.args[0]
    keyboard = message.answer.await_args.kwargs["reply_markup"]
    assert "Yordam: /help" in text
    assert isinstance(keyboard, ReplyKeyboardMarkup)
    assert [[button.text for button in row] for row in keyboard.keyboard] == [
        ["💞 Tanishuv", "👤 Profil"],
        ["📱 QR Generator"],
    ]


def test_removed_music_menu_callback_is_acknowledged():
    from app.bot.handlers.callbacks import removed_music_callback

    callback = type("CallbackStub", (), {"answer": AsyncMock()})()
    callback.data = "menu:music"

    asyncio.run(removed_music_callback(callback))

    callback.answer.assert_awaited_once_with(
        "Musiqa bo‘limi olib tashlangan.", show_alert=True
    )


def test_help_menu_shows_formatted_guidance_and_navigation():
    from app.bot.handlers.callbacks import menu_selected

    message = type("MessageStub", (), {"answer": AsyncMock()})()
    callback = type(
        "CallbackStub",
        (),
        {
            "data": "menu:help",
            "message": message,
            "answer": AsyncMock(),
        },
    )()

    asyncio.run(menu_selected(callback))

    text = message.answer.await_args.args[0]
    keyboard = message.answer.await_args.kwargs["reply_markup"]
    assert "<b>❓ Yordam</b>" in text
    assert "Maxfiylik" in text
    assert isinstance(keyboard, ReplyKeyboardMarkup)
    labels = [button.text for row in keyboard.keyboard for button in row]
    assert labels == ["💞 Tanishuv", "👤 Profil", "📱 QR Generator"]
    assert "Yordam" not in labels
    assert "Video" not in labels
