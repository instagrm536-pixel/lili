from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from app.bot.keyboards.main import main_menu_keyboard

router = Router()

HELP_TEXT = (
    "<b>❓ Yordam</b>\n\n"
    "<b>🎬 Video va media</b>\n"
    "YouTube, Instagram, Pinterest yoki to‘g‘ridan-to‘g‘ri MP4 havolasini "
    "chatga yuboring. 📱 QR Generator’da matn, havola va fayllarni bitta QR "
    "to‘plamiga birlashtirish mumkin.\n\n"
    "<b>💞 Tanishuv</b>\n"
    "Faqat 18+ dan kattalar uchun. Profil yaratish uchun username kerak. So‘rov yuborilgandan "
    "keyin username faqat ikki tomon ham rozilik berganda ochiladi.\n\n"
    "<b>🔒 Maxfiylik</b>\n"
    "Telefon raqami tekshiruv uchun yuboriladi, profilingizda ko‘rsatilmaydi "
    "va bot tomonidan saqlanmaydi.\n\n"
    "<i>Ayrim yopiq, muddati tugagan yoki DRM bilan himoyalangan havolalarni olish "
    "imkoni bo‘lmasligi mumkin.</i>"
)


@router.callback_query(lambda callback: callback.data in {
    "menu:video",
    "menu:help",
})
async def menu_selected(callback: CallbackQuery):
    await callback.answer()
    if callback.data == "menu:video":
        await callback.message.answer(
            "🔗 YouTube, Instagram, Pinterest yoki bevosita MP4 havolasini yuboring.",
            reply_markup=main_menu_keyboard(),
        )
    else:
        await callback.message.answer(HELP_TEXT, reply_markup=main_menu_keyboard())


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=main_menu_keyboard())


@router.callback_query(
    lambda callback: callback.data == "menu:music"
    or (callback.data and callback.data.startswith("music:"))
)
async def removed_music_callback(callback: CallbackQuery):
    await callback.answer("Musiqa bo‘limi olib tashlangan.", show_alert=True)
