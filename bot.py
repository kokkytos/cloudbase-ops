import os
import asyncio
import logging
from urllib.parse import quote

import httpx
from dotenv import load_dotenv

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


# ============================================================
# LOAD ENVIRONMENT
# ============================================================

load_dotenv()


# ============================================================
# CONFIGURATION
# ============================================================

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

AIRTABLE_TOKEN = os.environ["AIRTABLE_TOKEN"]
AIRTABLE_BASE_ID = os.environ["AIRTABLE_BASE_ID"]

AIRTABLE_TABLE_NAME = "Participants"

# Airtable fields
AIRTABLE_TELEGRAM_ID_FIELD = "Telegram_ID"
AIRTABLE_TELEGRAM_USERNAME_FIELD = "Telegram_Username"

# Airtable fields containing personalized URLs
CHECKIN_URL_FIELD = "Custom_CheckIn_URL"
CHECKOUT_URL_FIELD = "Custom_Checkout_URL"

# QR code settings
QR_CODE_SIZE = "200x200"

# Temporary group welcome message lifetime
# 5 minutes = 300 seconds
TEMP_MESSAGE_SECONDS = 300


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ============================================================
# USERNAME NORMALIZATION
# ============================================================

def normalize_username(username):
    """
    Normalize a Telegram username.

    Examples:

        @JohnSmith  -> johnsmith
        JohnSmith   -> johnsmith
        @johnsmith  -> johnsmith
        johnsmith   -> johnsmith
    """

    if not username:
        return None

    username = str(username).strip()

    if username.startswith("@"):
        username = username[1:]

    username = username.strip()

    if not username:
        return None

    return username.lower()


# ============================================================
# AIRTABLE LOOKUP
# ============================================================

async def get_participant(
    telegram_user_id,
    telegram_username,
):
    """
    Find a participant in Airtable.

    Lookup priority:

        1. Telegram_Username
        2. Telegram_ID

    Username matching is case-insensitive and ignores @.
    """

    airtable_url = (
        f"https://api.airtable.com/v0/"
        f"{AIRTABLE_BASE_ID}/"
        f"{AIRTABLE_TABLE_NAME}"
    )

    headers = {
        "Authorization": f"Bearer {AIRTABLE_TOKEN}",
    }

    normalized_username = normalize_username(
        telegram_username
    )

    async with httpx.AsyncClient(timeout=15) as client:

        # ====================================================
        # 1. SEARCH BY TELEGRAM USERNAME
        # ====================================================

        if normalized_username:

            username_formula = (
                "LOWER("
                "SUBSTITUTE("
                f"{{{AIRTABLE_TELEGRAM_USERNAME_FIELD}}},"
                "'@',"
                "''"
                ")"
                ")="
                f"'{normalized_username}'"
            )

            logger.info(
                "Searching Airtable by username: %s",
                normalized_username,
            )

            response = await client.get(
                airtable_url,
                headers=headers,
                params={
                    "filterByFormula": username_formula,
                    "maxRecords": 1,
                },
            )

            response.raise_for_status()

            data = response.json()

            records = data.get("records", [])

            if records:

                fields = records[0].get(
                    "fields",
                    {},
                )

                logger.info(
                    "Participant found by username: %s",
                    normalized_username,
                )

                return fields

        # ====================================================
        # 2. FALL BACK TO TELEGRAM ID
        # ====================================================

        logger.info(
            "Username lookup failed. "
            "Searching Airtable by Telegram ID: %s",
            telegram_user_id,
        )

        id_formula = (
            f"{{{AIRTABLE_TELEGRAM_ID_FIELD}}}"
            f"='{telegram_user_id}'"
        )

        response = await client.get(
            airtable_url,
            headers=headers,
            params={
                "filterByFormula": id_formula,
                "maxRecords": 1,
            },
        )

        response.raise_for_status()

        data = response.json()

        records = data.get("records", [])

        if records:

            fields = records[0].get(
                "fields",
                {},
            )

            logger.info(
                "Participant found by Telegram ID: %s",
                telegram_user_id,
            )

            return fields

    logger.warning(
        "No participant found for Telegram ID=%s username=%s",
        telegram_user_id,
        telegram_username,
    )

    return None


# ============================================================
# QR CODE URL
# ============================================================

def create_qr_code_url(data):
    """
    Create a QR Server API URL.

    The QR code contains the supplied URL.
    """

    if not data:
        return None

    encoded_data = quote(
        str(data),
        safe="",
    )

    return (
        "https://api.qrserver.com/v1/create-qr-code/"
        f"?size={QR_CODE_SIZE}"
        f"&data={encoded_data}"
    )


# ============================================================
# PRIVATE WELCOME MESSAGE
# ============================================================

async def send_private_welcome(
    bot,
    user,
):
    """
    Send the initial private welcome message.
    """

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🎟 My Codes",
                callback_data="codes",
            )
        ]
    ])

    first_name = user.first_name or "there"

    await bot.send_message(
        chat_id=user.id,
        text=(
            f"👋 <b>Welcome, {first_name}!</b>\n\n"
            "We're happy to have you in our paragliding community. 🎉\n\n"
            "You can use the button below to retrieve "
            "your personal Check-In and Check-Out codes."
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


# ============================================================
# /START
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Handle /start.

    Supported:

        /start

        /start codes

        /start welcome_GROUP_ID_MESSAGE_ID
    """

    user = update.effective_user

    if not user:
        return

    if user.is_bot:
        return

    parameter = None

    if context.args:
        parameter = context.args[0]

    # ========================================================
    # /start codes
    # ========================================================

    if parameter == "codes":

        try:

            await send_codes_to_user(
                bot=context.bot,
                user=user,
            )

        except Exception as e:

            logger.exception(
                "Could not send codes from /start codes: %s",
                e,
            )

            await context.bot.send_message(
                chat_id=user.id,
                text=(
                    "⚠️ I couldn't retrieve your codes "
                    "right now.\n\n"
                    "Please try again."
                ),
            )

        return

    # ========================================================
    # WELCOME DEEP LINK
    # ========================================================

    if parameter and parameter.startswith("welcome_"):

        try:

            parts = parameter.split("_")

            # Format:
            #
            # welcome_GROUP_ID_MESSAGE_ID
            #

            if len(parts) >= 3:

                group_id = int(parts[1])
                message_id = int(parts[2])

                try:

                    await context.bot.delete_message(
                        chat_id=group_id,
                        message_id=message_id,
                    )

                    logger.info(
                        "Deleted temporary welcome "
                        "message %s from group %s",
                        message_id,
                        group_id,
                    )

                except Exception as e:

                    logger.info(
                        "Could not delete temporary "
                        "welcome message: %s",
                        e,
                    )

        except (
            ValueError,
            IndexError,
        ) as e:

            logger.warning(
                "Invalid welcome deep link: %s",
                e,
            )

    # ========================================================
    # NORMAL /start
    # ========================================================

    await send_private_welcome(
        bot=context.bot,
        user=user,
    )


# ============================================================
# SEND CODES PRIVATELY
# ============================================================

async def send_codes_to_user(
    bot,
    user,
):
    """
    Look up the user in Airtable and send their
    personalized Check-In / Check-Out information privately.

    Telegram errors are intentionally allowed to propagate
    so /codes can detect when the user has not started
    the bot yet.
    """

    # ========================================================
    # AIRTABLE LOOKUP
    # ========================================================

    try:

        participant = await get_participant(
            telegram_user_id=user.id,
            telegram_username=user.username,
        )

    except httpx.HTTPStatusError as e:

        logger.exception(
            "Airtable HTTP error: %s",
            e,
        )

        await bot.send_message(
            chat_id=user.id,
            text=(
                "⚠️ <b>Temporary problem</b>\n\n"
                "I couldn't access the participant database "
                "right now.\n\n"
                "Please try again in a moment."
            ),
            parse_mode=ParseMode.HTML,
        )

        return

    except Exception as e:

        logger.exception(
            "Airtable lookup failed: %s",
            e,
        )

        await bot.send_message(
            chat_id=user.id,
            text=(
                "⚠️ Something went wrong while retrieving "
                "your codes.\n\n"
                "Please try again later."
            ),
        )

        return

    # ========================================================
    # NO AIRTABLE RECORD
    # ========================================================

    if not participant:

        await bot.send_message(
            chat_id=user.id,
            text=(
                "❌ <b>Participant not found</b>\n\n"
                "I couldn't find a participant record matching "
                "your Telegram username or Telegram ID.\n\n"
                "Please contact an administrator if you believe "
                "this is an error."
            ),
            parse_mode=ParseMode.HTML,
        )

        return

    # ========================================================
    # EXTRACT URLS
    # ========================================================

    checkin_url = participant.get(
        CHECKIN_URL_FIELD
    )

    checkout_url = participant.get(
        CHECKOUT_URL_FIELD
    )

    # ========================================================
    # GENERATE QR URLS
    # ========================================================

    checkin_qr_url = create_qr_code_url(
        checkin_url
    )

    checkout_qr_url = create_qr_code_url(
        checkout_url
    )

    first_name = user.first_name or "there"

    # ========================================================
    # HEADER
    # ========================================================

    await bot.send_message(
        chat_id=user.id,
        text=(
            f"🎟 <b>Your Personal Codes</b>\n\n"
            f"Hello {first_name}!\n\n"
            "Here are your personal Check-In and "
            "Check-Out codes.\n\n"
            "⚠️ Please keep these codes private."
        ),
        parse_mode=ParseMode.HTML,
    )

    # ========================================================
    # CHECK-IN
    # ========================================================

    if checkin_url:

        checkin_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🟢 Chick here to Open Check-In Form",
                    url=str(checkin_url),
                )
            ]
        ])

        await bot.send_message(
            chat_id=user.id,
            text="🟢 <b>CHECK-IN</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=checkin_keyboard,
        )

        if checkin_qr_url:

            try:

                await bot.send_photo(
                    chat_id=user.id,
                    photo=checkin_qr_url,
                    caption="📱 Check-In QR Code",
                )

            except Exception as e:

                logger.exception(
                    "Could not send Check-In QR: %s",
                    e,
                )

                await bot.send_message(
                    chat_id=user.id,
                    text=(
                        "⚠️ I couldn't load the Check-In "
                        "QR image. Please use the button above."
                    ),
                )

    else:

        await bot.send_message(
            chat_id=user.id,
            text=(
                "🟢 <b>CHECK-IN</b>\n\n"
                "No Check-In code is currently available."
            ),
            parse_mode=ParseMode.HTML,
        )

    # ========================================================
    # CHECK-OUT
    # ========================================================

    if checkout_url:

        checkout_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔴 Click here to Open Check-Out Form",
                    url=str(checkout_url),
                )
            ]
        ])

        await bot.send_message(
            chat_id=user.id,
            text="🔴 <b>CHECK-OUT</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=checkout_keyboard,
        )

        if checkout_qr_url:

            try:

                await bot.send_photo(
                    chat_id=user.id,
                    photo=checkout_qr_url,
                    caption="📱 Check-Out QR Code",
                )

            except Exception as e:

                logger.exception(
                    "Could not send Check-Out QR: %s",
                    e,
                )

                await bot.send_message(
                    chat_id=user.id,
                    text=(
                        "⚠️ I couldn't load the Check-Out "
                        "QR image. Please use the button above."
                    ),
                )

    else:

        await bot.send_message(
            chat_id=user.id,
            text=(
                "🔴 <b>CHECK-OUT</b>\n\n"
                "No Check-Out code is currently available."
            ),
            parse_mode=ParseMode.HTML,
        )


# ============================================================
# /CODES COMMAND
# ============================================================

async def codes_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    /codes can be entered in the group.

    The codes are NEVER sent into the group.

    If the user has not started the bot privately yet,
    provide a button that opens the private bot.
    """

    user = update.effective_user

    if not user:
        return

    if user.is_bot:
        return

    # ========================================================
    # IF /CODES IS USED IN PRIVATE CHAT
    # ========================================================

    if update.effective_chat.type == "private":

        await send_codes_to_user(
            bot=context.bot,
            user=user,
        )

        return

    # ========================================================
    # /CODES WAS USED IN GROUP
    # ========================================================

    try:

        await send_codes_to_user(
            bot=context.bot,
            user=user,
        )

        # Successful:
        #
        # Nothing is posted into the group.

    except Exception as e:

        logger.exception(
            "Could not send private /codes message: %s",
            e,
        )

        bot_username = context.bot.username

        if not bot_username:

            await update.message.reply_text(
                "🔐 Please open the bot privately first."
            )

            return

        # ====================================================
        # PRIVATE BOT LINK
        # ====================================================

        start_link = (
            f"https://t.me/{bot_username}?start=codes"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔐 Open private bot",
                    url=start_link,
                )
            ]
        ])

        await update.message.reply_text(
            "🔐 <b>Open the bot privately</b>\n\n"
            "Tap the button below. I'll then send "
            "your personal codes privately.",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )


# ============================================================
# BUTTON HANDLER
# ============================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Handles private inline buttons.
    """

    query = update.callback_query

    if not query:
        return

    await query.answer()

    user = query.from_user

    # ========================================================
    # MY CODES BUTTON
    # ========================================================

    if query.data == "codes":

        try:

            await send_codes_to_user(
                bot=context.bot,
                user=user,
            )

        except Exception as e:

            logger.exception(
                "Could not send codes from button: %s",
                e,
            )

            await context.bot.send_message(
                chat_id=user.id,
                text=(
                    "⚠️ I couldn't retrieve your codes "
                    "right now. Please try again."
                ),
            )


# ============================================================
# NEW MEMBER HANDLER
# ============================================================

async def new_member(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Detect new members joining the Telegram group.
    """

    message = update.message

    if not message:
        return

    if not message.new_chat_members:
        return

    bot_username = context.bot.username

    if not bot_username:

        logger.error(
            "Could not determine bot username."
        )

        return

    for user in message.new_chat_members:

        # ----------------------------------------------------
        # Ignore bots
        # ----------------------------------------------------

        if user.is_bot:
            continue

        # ----------------------------------------------------
        # First send the temporary group message.
        #
        # We need the message ID before creating the deep link.
        # ----------------------------------------------------

        temporary_message = await message.reply_text(
            f"👋 Welcome {user.mention_html()}!\n\n"
            "We're glad to have you in our paragliding community. 🎉\n\n"
            "Tap the button below to receive your "
            "private welcome message.",
            parse_mode=ParseMode.HTML,
        )

        # ----------------------------------------------------
        # Create deep link containing:
        #
        # group ID
        # message ID
        # ----------------------------------------------------

        start_parameter = (
            f"welcome_"
            f"{message.chat.id}_"
            f"{temporary_message.message_id}"
        )

        start_link = (
            f"https://t.me/{bot_username}"
            f"?start={start_parameter}"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "👋 Get my private welcome",
                    url=start_link,
                )
            ]
        ])

        # ----------------------------------------------------
        # Add button to the existing message.
        # ----------------------------------------------------

        try:

            await temporary_message.edit_reply_markup(
                reply_markup=keyboard,
            )

        except Exception as e:

            logger.exception(
                "Could not add welcome button: %s",
                e,
            )

        # ----------------------------------------------------
        # Automatically delete after timeout.
        # ----------------------------------------------------

        asyncio.create_task(
            delete_later(
                bot=context.bot,
                chat_id=message.chat.id,
                message_id=temporary_message.message_id,
                seconds=TEMP_MESSAGE_SECONDS,
            )
        )


# ============================================================
# DELETE TEMPORARY MESSAGE
# ============================================================

async def delete_later(
    bot,
    chat_id,
    message_id,
    seconds,
):
    """
    Delete a temporary group message after a delay.
    """

    await asyncio.sleep(seconds)

    try:

        await bot.delete_message(
            chat_id=chat_id,
            message_id=message_id,
        )

        logger.info(
            "Deleted expired temporary message %s",
            message_id,
        )

    except Exception as e:

        # Usually means the message was already deleted
        # because the user clicked the welcome button.

        logger.info(
            "Temporary message %s already deleted: %s",
            message_id,
            e,
        )


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Global Telegram error handler.
    """

    logger.error(
        "Telegram update error",
        exc_info=context.error,
    )


# ============================================================
# MAIN
# ============================================================

def main():

    application = (
        Application
        .builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    # ========================================================
    # /START
    # ========================================================

    application.add_handler(
        CommandHandler(
            "start",
            start_command,
        )
    )

    # ========================================================
    # /CODES
    # ========================================================

    application.add_handler(
        CommandHandler(
            "codes",
            codes_command,
        )
    )

    # ========================================================
    # PRIVATE INLINE BUTTONS
    # ========================================================

    application.add_handler(
        CallbackQueryHandler(
            button_handler,
        )
    )

    # ========================================================
    # NEW GROUP MEMBERS
    # ========================================================

    application.add_handler(
        MessageHandler(
            filters.StatusUpdate.NEW_CHAT_MEMBERS,
            new_member,
        )
    )

    # ========================================================
    # ERROR HANDLER
    # ========================================================

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "🤖 Telegram Welcome + Airtable Codes Bot is running..."
    )

    # ========================================================
    # START POLLING
    # ========================================================

    application.run_polling()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
