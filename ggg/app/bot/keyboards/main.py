from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="💞 Tanishuv"),
                KeyboardButton(text="👤 Profil"),
            ],
            [KeyboardButton(text="📱 QR Generator")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Bo‘limni tanlang",
    )
