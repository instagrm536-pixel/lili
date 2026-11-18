import html
import json
import logging

from aiogram import Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.enums import ChatType
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from app.database.models import DatingProfile
from app.bot.keyboards.main import main_menu_keyboard
from app.services.dating import DatingService
from app.services.state import state

router = Router()
log = logging.getLogger(__name__)
dating_service = DatingService()
MINIMUM_AGE = 18
PROFILE_PAGE_SIZE = 8
REGISTRATION_TTL = 3600
REGISTRATION_KEY = "dating:registration:{}"
GENDER_LABELS = {
    "man": "Erkak",
    "woman": "Ayol",
    "other": "Boshqa",
}


def _registration_key(user_id: int) -> str:
    return REGISTRATION_KEY.format(user_id)


async def _save_registration(user_id: int, data: dict) -> None:
    await state.set(
        _registration_key(user_id),
        json.dumps(data, ensure_ascii=False),
        ex=REGISTRATION_TTL,
    )


async def _load_registration(user_id: int) -> dict | None:
    key = _registration_key(user_id)
    raw = await state.get(key)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        await state.delete(key)
        return None
    if not isinstance(data, dict):
        await state.delete(key)
        return None
    return data


async def _update_registration_prompt(
    message: Message,
    registration: dict,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    prompt_message_id = registration.get("prompt_message_id")
    bot = getattr(message, "bot", None)
    if prompt_message_id is not None and bot is not None:
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=prompt_message_id,
            text=text,
            reply_markup=reply_markup,
        )
        return

    sent = await message.answer(text, reply_markup=reply_markup)
    sent_message_id = getattr(sent, "message_id", None)
    if isinstance(sent_message_id, int):
        registration["prompt_message_id"] = sent_message_id
        await _save_registration(message.from_user.id, registration)


async def _delete_registration_prompt(message: Message, registration: dict) -> None:
    prompt_message_id = registration.get("prompt_message_id")
    bot = getattr(message, "bot", None)
    if prompt_message_id is not None and bot is not None:
        await bot.delete_message(message.chat.id, prompt_message_id)


def _cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="Bekor qilish",
                callback_data="dating:cancel",
                style="danger",
            )
        ]]
    )


def _gender_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Erkak",
                    callback_data="dating:gender:man",
                    style="primary",
                ),
                InlineKeyboardButton(
                    text="Ayol",
                    callback_data="dating:gender:woman",
                    style="primary",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Boshqa",
                    callback_data="dating:gender:other",
                    style="primary",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Bekor qilish",
                    callback_data="dating:cancel",
                    style="danger",
                )
            ],
        ]
    )


def _gender_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Tasdiqlash",
                    callback_data="dating:gender:confirm",
                    style="success",
                ),
                InlineKeyboardButton(
                    text="Qayta tanlash",
                    callback_data="dating:gender:retry",
                    style="primary",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Bekor qilish",
                    callback_data="dating:cancel",
                    style="danger",
                )
            ],
        ]
    )


def _contact_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Telefon raqamimni yuborish", request_contact=True)],
            [KeyboardButton(text="Bekor qilish")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _yes_no_keyboard(yes_data: str, no_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="Ha",
                callback_data=yes_data,
                style="success",
            ),
            InlineKeyboardButton(
                text="Yo‘q",
                callback_data=no_data,
                style="danger",
            ),
        ]]
    )


def _profile_list_keyboard(
    profiles: list[DatingProfile],
    page: int,
    has_more: bool,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=(
                    f"{profile.full_name}, {profile.age} — "
                    f"{GENDER_LABELS.get(profile.gender, 'Boshqa')}"
                )[:64],
                callback_data=f"dating:profile:{profile.telegram_id}",
            )
        ]
        for profile in profiles
    ]
    navigation = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text="Oldingi",
                callback_data=f"dating:page:{page - 1}",
            )
        )
    if has_more:
        navigation.append(
            InlineKeyboardButton(
                text="Keyingi",
                callback_data=f"dating:page:{page + 1}",
            )
        )
    if navigation:
        rows.append(navigation)
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_profiles(
    message: Message,
    telegram_id: int,
    page: int = 0,
) -> None:
    profiles, has_more = await dating_service.list_profiles(
        telegram_id,
        page,
        PROFILE_PAGE_SIZE,
    )
    if not profiles and page == 0:
        await message.answer("Hozircha boshqa ro‘yxatdan o‘tgan odam yo‘q.")
        return
    await message.answer(
        "18+ ro‘yxatdan o‘tganlar. Profilni tanlang:",
        reply_markup=_profile_list_keyboard(profiles, page, has_more),
    )


@router.callback_query(lambda callback: callback.data == "menu:dating")
async def dating_start(callback: CallbackQuery) -> None:
    if callback.message is None or callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer(
            "Tanishuv bo‘limi faqat shaxsiy chatda ishlaydi.",
            show_alert=True,
        )
        return
    await callback.answer()
    await _start_dating(callback.message, callback.from_user.id, callback.from_user.username)


async def _start_dating(message: Message, user_id: int, username: str | None) -> None:
    if not username:
        await message.answer(
            "Davom etish uchun Telegram sozlamalarida username o‘rnating."
        )
        return

    try:
        if await _load_registration(user_id) is not None:
            await message.answer(
                "Ro‘yxatdan o‘tish davom etmoqda. Oldingi savolga javob bering "
                "yoki bekor qiling."
            )
            return
        profile = await dating_service.get_profile(user_id)
        if profile is not None:
            await dating_service.update_username(user_id, username)
            await _show_profiles(message, user_id)
            return
    except Exception:
        log.exception("failed to load dating profile")
        await message.answer(
            "❌ Tanishuv bo‘limi hozir ishlamayapti. Keyinroq urinib ko‘ring."
        )
        return

    prompt = await message.answer(
        "1/4 · Tanishuv bo‘limi faqat 18 yoshdan kattalar uchun.\n"
        "Jinsingizni tanlang:",
        reply_markup=_gender_keyboard(),
    )
    await _save_registration(
        user_id,
        {
            "step": "gender",
            "prompt_message_id": prompt.message_id,
        },
    )


@router.message(
    lambda message: message.text
    and message.text.casefold() == "💞 tanishuv"
)
async def dating_menu_button(message: Message) -> None:
    if message.chat.type != ChatType.PRIVATE or message.from_user is None:
        await message.answer("Tanishuv bo‘limi faqat shaxsiy chatda ishlaydi.")
        return
    await _start_dating(
        message, message.from_user.id, message.from_user.username
    )


@router.message(
    lambda message: message.text and message.text.casefold() == "👤 profil"
)
async def profile_menu_button(message: Message) -> None:
    if message.chat.type != ChatType.PRIVATE or message.from_user is None:
        await message.answer("Profil bo‘limi faqat shaxsiy chatda ishlaydi.")
        return
    try:
        profile = await dating_service.get_profile(message.from_user.id)
    except Exception:
        log.exception("failed to load own dating profile")
        await message.answer("❌ Profilni yuklab bo‘lmadi. Keyinroq urinib ko‘ring.")
        return

    if profile is None:
        await message.answer(
            "Profilingiz hali yaratilmagan. Ro‘yxatdan o‘tish uchun "
            "pastdagi «💞 Tanishuv» tugmasini bosing.",
            reply_markup=main_menu_keyboard(),
        )
        return

    await message.answer(
        "<b>👤 Profilingiz</b>\n\n"
        f"<b>Ism:</b> {html.escape(profile.full_name)}\n"
        f"<b>Yosh:</b> {profile.age}\n"
        f"<b>Jins:</b> "
        f"{html.escape(GENDER_LABELS.get(profile.gender, 'Boshqa'))}\n"
        f"<b>Username:</b> @{html.escape(profile.username)}\n\n"
        "Telefon raqamingiz profilga kiritilmagan.",
        reply_markup=main_menu_keyboard(),
    )


@router.callback_query(
    lambda callback: callback.data
    and callback.data.startswith("dating:gender:")
)
async def gender_selected(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    registration = await _load_registration(user_id)
    if registration is None:
        await callback.answer("Ro‘yxatdan o‘tishni qaytadan boshlang.", show_alert=True)
        return

    action = callback.data.rsplit(":", 1)[1]
    if action == "retry":
        registration["step"] = "gender"
        registration.pop("gender", None)
        await _save_registration(user_id, registration)
        await callback.answer()
        await _update_registration_prompt(
            callback.message,
            registration,
            "Jinsingizni tanlang:",
            _gender_keyboard(),
        )
        return
    if action == "confirm":
        if registration.get("step") != "gender_confirm":
            await callback.answer("Avval jinsni tanlang.", show_alert=True)
            return
        registration["step"] = "name"
        await _save_registration(user_id, registration)
        await callback.answer()
        await _update_registration_prompt(
            callback.message,
            registration,
            "2/4 · Ism va familiyangizni kiriting (18+ tanishuv profili uchun):",
            _cancel_keyboard(),
        )
        return

    if registration.get("step") != "gender" or action not in GENDER_LABELS:
        await callback.answer("Bu tanlov endi amal qilmaydi.", show_alert=True)
        return
    registration["gender"] = action
    registration["step"] = "gender_confirm"
    await _save_registration(user_id, registration)
    await callback.answer()
    await _update_registration_prompt(
        callback.message,
        registration,
        f"Jins: <b>{GENDER_LABELS[action]}</b>. Tasdiqlaysizmi?",
        _gender_confirmation_keyboard(),
    )


@router.callback_query(lambda callback: callback.data == "dating:cancel")
async def registration_cancelled(callback: CallbackQuery) -> None:
    await state.delete(_registration_key(callback.from_user.id))
    await callback.answer("Ro‘yxatdan o‘tish bekor qilindi.")
    if callback.message is not None:
        await callback.message.delete()
        await callback.message.answer(
            "Ro‘yxatdan o‘tish bekor qilindi.",
            reply_markup=ReplyKeyboardRemove(),
        )


@router.message()
async def registration_message(message: Message) -> None:
    if (
        message.chat.type != ChatType.PRIVATE
        or message.from_user is None
    ):
        raise SkipHandler

    registration = await _load_registration(message.from_user.id)
    if registration is None:
        raise SkipHandler

    step = registration.get("step")
    if message.text and message.text.casefold() in {"/cancel", "bekor qilish"}:
        await state.delete(_registration_key(message.from_user.id))
        await _delete_registration_prompt(message, registration)
        await message.answer(
            "Ro‘yxatdan o‘tish bekor qilindi.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    if step == "name":
        full_name = " ".join((message.text or "").split())
        if (
            len(full_name) < 3
            or len(full_name) > 100
            or len(full_name.split()) < 2
        ):
            await _update_registration_prompt(
                message,
                registration,
                "❌ Ism noto‘g‘ri.\n"
                "2/4 · Ism va familiyangizni kiriting (3–100 belgi):",
                _cancel_keyboard(),
            )
            return
        registration["full_name"] = full_name
        registration["step"] = "age"
        await _save_registration(message.from_user.id, registration)
        await _update_registration_prompt(
            message,
            registration,
            "3/4 · Yoshingizni kiriting (18–100):",
            _cancel_keyboard(),
        )
        return

    if step == "age":
        try:
            age = int((message.text or "").strip())
        except ValueError:
            await _update_registration_prompt(
                message,
                registration,
                "❌ Yosh raqam bo‘lishi kerak.\n"
                "3/4 · Yoshingizni kiriting (18–100):",
                _cancel_keyboard(),
            )
            return
        if age < MINIMUM_AGE:
            await state.delete(_registration_key(message.from_user.id))
            await _delete_registration_prompt(message, registration)
            await message.answer(
                "Tanishuv bo‘limidan faqat 18 yoshdan kattalar foydalanishi mumkin.",
                reply_markup=ReplyKeyboardRemove(),
            )
            return
        if age > 100:
            await _update_registration_prompt(
                message,
                registration,
                "3/4 · Yoshni 18–100 oralig‘ida kiriting:",
                _cancel_keyboard(),
            )
            return
        registration["age"] = age
        registration["step"] = "contact"
        await _delete_registration_prompt(message, registration)
        contact_prompt = await message.answer(
            "4/4 · Telefon raqamingizni Telegram tugmasi orqali yuboring. "
            "Raqam profilingizda ko‘rsatilmaydi va saqlanmaydi.",
            reply_markup=_contact_keyboard(),
        )
        registration["prompt_message_id"] = contact_prompt.message_id
        await _save_registration(message.from_user.id, registration)
        return

    if step == "contact":
        contact = message.contact
        if (
            contact is None
            or contact.user_id != message.from_user.id
        ):
            await _update_registration_prompt(
                message,
                registration,
                "❌ Faqat o‘zingizning Telegram kontaktingizni yuboring.\n"
                "4/4 · Telefon raqamingiz saqlanmaydi.",
            )
            return
        if not message.from_user.username:
            await state.delete(_registration_key(message.from_user.id))
            await _delete_registration_prompt(message, registration)
            await message.answer(
                "Telegram username topilmadi. Sozlamalarda username o‘rnating "
                "va ro‘yxatdan o‘tishni qayta boshlang.",
                reply_markup=ReplyKeyboardRemove(),
            )
            return
        try:
            await dating_service.create_profile(
                telegram_id=message.from_user.id,
                username=message.from_user.username,
                full_name=registration["full_name"],
                age=registration["age"],
                gender=registration["gender"],
            )
        except Exception:
            log.exception("failed to register dating profile")
            await _update_registration_prompt(
                message,
                registration,
                "❌ Profilni saqlab bo‘lmadi. Qayta yuboring yoki keyinroq urinib ko‘ring.",
            )
            return

        await state.delete(_registration_key(message.from_user.id))
        await _delete_registration_prompt(message, registration)
        await message.answer(
            "✅ Profilingiz yaratildi. Telefon raqamingiz saqlanmadi.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await _show_profiles(message, message.from_user.id)
        return

    raise SkipHandler


@router.callback_query(
    lambda callback: callback.data
    and callback.data.startswith("dating:page:")
)
async def profile_page_selected(callback: CallbackQuery) -> None:
    try:
        page = int(callback.data.rsplit(":", 1)[1])
        if page < 0:
            raise ValueError("page must be non-negative")
        await _show_profiles(
            callback.message,
            callback.from_user.id,
            page,
        )
        await callback.answer()
    except (TypeError, ValueError):
        await callback.answer("Sahifa noto‘g‘ri.", show_alert=True)
    except Exception:
        log.exception("failed to show dating profiles")
        await callback.answer(
            "Ro‘yxatni ochib bo‘lmadi. Keyinroq urinib ko‘ring.",
            show_alert=True,
        )


@router.callback_query(
    lambda callback: callback.data
    and callback.data.startswith("dating:profile:")
)
async def profile_selected(callback: CallbackQuery) -> None:
    try:
        target_id = int(callback.data.rsplit(":", 1)[1])
        if target_id == callback.from_user.id:
            raise ValueError("cannot select own profile")
        target = await dating_service.get_profile(target_id)
        own_profile = await dating_service.get_profile(callback.from_user.id)
        if target is None or own_profile is None:
            await callback.answer("Profil topilmadi.", show_alert=True)
            return
        await callback.message.answer(
            "Ushbu profilga tanishuv so‘rovi yuborilsinmi?\n\n"
            f"<b>{html.escape(target.full_name)}</b>\n"
            f"Yoshi: {target.age}\n"
            f"Jinsi: {html.escape(GENDER_LABELS.get(target.gender, 'Boshqa'))}\n\n"
            "Username faqat u ham rozilik bildirsa ochiladi.",
            reply_markup=_yes_no_keyboard(
                f"dating:decision:{target_id}:yes",
                f"dating:decision:{target_id}:no",
            ),
        )
        await callback.answer()
    except (TypeError, ValueError):
        await callback.answer("Profil tanlovi noto‘g‘ri.", show_alert=True)
    except Exception:
        log.exception("failed to load selected dating profile")
        await callback.answer("Profilni ochib bo‘lmadi.", show_alert=True)


@router.callback_query(
    lambda callback: callback.data
    and callback.data.startswith("dating:decision:")
)
async def profile_decision(callback: CallbackQuery) -> None:
    try:
        _, _, target_raw, decision = callback.data.split(":")
        target_id = int(target_raw)
        if decision not in {"yes", "no"}:
            raise ValueError("invalid decision")
        if decision == "no":
            await callback.answer("Tanlov bekor qilindi.")
            return

        request, result = await dating_service.create_request(
            callback.from_user.id,
            target_id,
        )
        if result == "unavailable":
            await callback.answer("Profil endi mavjud emas.", show_alert=True)
            return
        if result == "already_contacted":
            await callback.answer(
                "Bu profilga avval so‘rov yuborilgan.",
                show_alert=True,
            )
            return
        sender_profile = await dating_service.get_profile(
            callback.from_user.id
        )
        if sender_profile is None:
            await callback.answer("Profil topilmadi.", show_alert=True)
            return
        await callback.bot.send_message(
            target_id,
            "Sizga tanishuv so‘rovi keldi.\n\n"
            f"<b>{html.escape(sender_profile.full_name)}</b>\n"
            f"Yoshi: {sender_profile.age}\n"
            f"Jinsi: "
            f"{html.escape(GENDER_LABELS.get(sender_profile.gender, 'Boshqa'))}\n\n"
            "Qabul qilsangiz, ikkala Telegram username’i ochiladi. "
            "Telefon raqamlari ulashilmaydi.",
            reply_markup=_yes_no_keyboard(
                f"dating:reply:{request.id}:yes",
                f"dating:reply:{request.id}:no",
            ),
        )
        await callback.message.answer(
            "✅ So‘rov yuborildi. Ikkinchi tomon javob berguncha kuting."
        )
        await callback.answer("So‘rov yuborildi.")
    except (TypeError, ValueError):
        await callback.answer("So‘rov noto‘g‘ri.", show_alert=True)
    except Exception:
        log.exception("failed to send dating request")
        await callback.answer(
            "So‘rov yuborilmadi. Keyinroq urinib ko‘ring.",
            show_alert=True,
        )


@router.callback_query(
    lambda callback: callback.data
    and callback.data.startswith("dating:reply:")
)
async def request_response(callback: CallbackQuery) -> None:
    try:
        _, _, request_raw, decision = callback.data.split(":")
        request_id = int(request_raw)
        if decision not in {"yes", "no"}:
            raise ValueError("invalid decision")
        result = await dating_service.respond_to_request(
            request_id,
            callback.from_user.id,
            accept=decision == "yes",
        )
        if result is None:
            await callback.answer(
                "So‘rov eskirgan yoki sizga yuborilmagan.",
                show_alert=True,
            )
            return

        request, sender, recipient = result
        if decision == "no":
            await callback.bot.send_message(
                sender.telegram_id,
                "Tanishuv so‘rovingiz qabul qilinmadi.",
            )
            await callback.answer("So‘rov rad etildi.")
            return

        await callback.bot.send_message(
            sender.telegram_id,
            "✅ Ikkalangiz ham rozilik berdingiz.\n"
            f"Telegram username: @{html.escape(recipient.username)}",
        )
        await callback.bot.send_message(
            recipient.telegram_id,
            "✅ Ikkalangiz ham rozilik berdingiz.\n"
            f"Telegram username: @{html.escape(sender.username)}",
        )
        await callback.answer("Rozilik tasdiqlandi.")
    except (TypeError, ValueError):
        await callback.answer("Javob noto‘g‘ri.", show_alert=True)
    except Exception:
        log.exception("failed to process dating response")
        await callback.answer(
            "Javobni saqlab bo‘lmadi. Keyinroq urinib ko‘ring.",
            show_alert=True,
        )
