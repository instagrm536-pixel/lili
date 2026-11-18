from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from app.bot.keyboards.main import main_menu_keyboard

router = Router()

@router.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "<b>AEwolf</b>\n\n"
        "🎬 Media havolasini yuboring.\n"
        "💞 Tanishuv, 👤 Profil va 📱 QR Generator pastdagi menyuda.\n"
        "Yordam: /help",
        reply_markup=main_menu_keyboard(),
    )
