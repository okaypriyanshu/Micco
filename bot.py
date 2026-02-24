"""
Telegram bot: fresh/used Hotmail credential stock, /next to get one, /check to get OTP.
Uses file-based storage (data/fresh_stock.txt, data/used.txt) like FrostyBot-style flow.
"""
import asyncio
import os
import re
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

from graph_client import get_access_token, get_otp_from_inbox
from storage import (
    get_next,
    add_to_fresh,
    get_by_email,
    stock_counts,
    CRED_LINE_PATTERN,
)

load_dotenv()

# Admin: only these user IDs can /upload, /stock, and use bot in DM (comma-separated)
ADMIN_IDS: set[int] = set()
_raw = os.getenv("ADMIN_IDS", "").strip()
if _raw:
    for part in _raw.split(","):
        part = part.strip()
        if part.isdigit():
            ADMIN_IDS.add(int(part))

# Allowed groups: bot only responds in these group/supergroup chat IDs (comma-separated, can be negative)
ALLOWED_GROUP_IDS: set[int] = set()
_raw = os.getenv("ALLOWED_GROUP_IDS", "").strip()
if _raw:
    for part in _raw.split(","):
        part = part.strip().replace(" ", "")
        try:
            ALLOWED_GROUP_IDS.add(int(part))
        except ValueError:
            pass

# Allowed users: only these user IDs can use the bot in groups (comma-separated). In DM only admins can use.
ALLOWED_USER_IDS: set[int] = set()
_raw = os.getenv("ALLOWED_USER_IDS", "").strip()
if _raw:
    for part in _raw.split(","):
        part = part.strip()
        if part.isdigit():
            ALLOWED_USER_IDS.add(int(part))

# Clean chat: delete user command and OTP-only messages after delay. Email+pass message is never auto-deleted.
CLEAN_CHAT = os.getenv("CLEAN_CHAT", "true").strip().lower() in ("1", "true", "yes")
DELETE_AFTER_SECONDS = int(os.getenv("DELETE_AFTER_SECONDS", "90").strip() or "0")

# Per-chat current credential (assigned by /next or by sending an email we have)
CURRENT: dict[int, dict] = {}
# Per-chat OTP message ids (so "Done" can delete all OTP messages)
LAST_OTP_MESSAGE_IDS: dict[int, list[int]] = {}

# Messages for unauthorised access
MSG_DM_NOT_ALLOWED = (
    "⛔ <b>Not authorised.</b>\n\n"
    "This bot is only available in the allowed group(s). In DM, only admins can use it.\n"
    "Ask an admin if you need access."
)
MSG_GROUP_NOT_ALLOWED = (
    "⛔ <b>Not authorised.</b>\n\n"
    "You are not in the list of users allowed to use this bot in this group.\n"
    "Ask an admin to add your account."
)
MSG_ADMIN_ONLY = "⛔ <b>Not authorised.</b> This command is for admins only."

# Credential line: mail|pass|refresh_token|client_id or with optional client_secret
def parse_credentials(text: str) -> dict | None:
    text = (text or "").strip()
    m = CRED_LINE_PATTERN.match(text)
    if not m:
        return None
    mail, _pass, refresh_token, client_id, client_secret = m.groups()
    return {
        "mail": mail.strip(),
        "pass": (_pass or "").strip(),
        "refresh_token": refresh_token.strip(),
        "client_id": client_id.strip(),
        "client_secret": (client_secret or "").strip() or None,
    }


def _is_admin(user_id: int) -> bool:
    return bool(ADMIN_IDS and user_id in ADMIN_IDS)


async def _delete_after(bot, chat_id: int, message_id: int, after_seconds: int) -> None:
    if after_seconds <= 0:
        return
    await asyncio.sleep(after_seconds)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


def _schedule_delete(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int) -> None:
    if CLEAN_CHAT and DELETE_AFTER_SECONDS > 0:
        asyncio.create_task(_delete_after(context.bot, chat_id, message_id, DELETE_AFTER_SECONDS))


async def _try_delete_user_message(update: Update) -> None:
    """Delete the user's message that triggered the command (keeps chat clean)."""
    if not CLEAN_CHAT or not update.message:
        return
    try:
        await update.message.delete()
    except Exception:
        pass


async def _check_access(update: Update) -> bool:
    """
    Enforce: group only for allowed groups + allowed users; DM only for admins.
    Returns True if access allowed; otherwise sends a reply and returns False.
    """
    if not update.message or not update.effective_chat or not update.effective_user:
        return False
    chat = update.effective_chat
    user_id = update.effective_user.id
    chat_id = chat.id

    if chat.type == "private":
        if user_id not in ADMIN_IDS:
            await update.message.reply_text(MSG_DM_NOT_ALLOWED, parse_mode="HTML")
            return False
        return True

    if chat.type in ("group", "supergroup"):
        if chat_id not in ALLOWED_GROUP_IDS:
            return False  # silent ignore in non-allowed groups
        if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
            await update.message.reply_text(MSG_GROUP_NOT_ALLOWED, parse_mode="HTML")
            return False
        return True

    return False


HELP_TEXT = (
    "📋 <b>Micco – Hotmail OTP bot</b>\n\n"
    "• <b>/start</b> – Welcome and short guide\n"
    "• <b>/help</b> – This help\n"
    "• <b>/next</b> – Get the next unused mail from stock (then use /check for OTP)\n"
    "• <b>/check</b> – Get OTP for your current mail\n"
    "• <b>/check email@hotmail.com</b> – Get OTP for that mail if it’s in stock\n"
    "• <b>/stock</b> – Show fresh vs used count (admin only)\n"
    "• <b>/upload</b> – Add credentials in bulk (admin only): paste lines or send a .txt file\n\n"
    "Format per line: <code>mail|pass|refresh_token|client_id</code> (optional 5th: client_secret).\n"
    "You can also send a single email (e.g. <code>user@hotmail.com</code>) to set it as current and then /check."
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_access(update):
        return
    await update.message.reply_text(
        "Hi. I manage a <b>stock of Hotmail credentials</b> and check inbox for OTP.\n\n"
        "• <b>/next</b> – get the next unused mail from stock (assigned to you; use /check for OTP).\n"
        "• <b>/check</b> – get OTP for your current mail (or /check email@hotmail.com to check that mail if in stock).\n"
        "• <b>/stock</b> – show fresh vs used count (admin).\n"
        "• <b>/upload</b> – add credentials in bulk (admin): paste lines <code>mail|pass|refresh_token|client_id</code> or send a .txt file.\n\n"
        "Use <b>/help</b> for full help.",
        parse_mode="HTML",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_access(update):
        return
    await update.message.reply_text(HELP_TEXT, parse_mode="HTML")


# Inline buttons below email+password
KEYBOARD_CHECK_DONE = InlineKeyboardMarkup([
    [InlineKeyboardButton("Check", callback_data="check_otp"), InlineKeyboardButton("Done", callback_data="done_otp")],
])
KEYBOARD_DONE = InlineKeyboardMarkup([[InlineKeyboardButton("Done", callback_data="done_otp_self")]])


async def _send_otp_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, cred: dict) -> int | None:
    """Fetch OTP for cred, send one message with code(s) and [Done] button. Appends message_id to LAST_OTP_MESSAGE_IDS. Returns message_id or None."""
    if chat_id not in LAST_OTP_MESSAGE_IDS:
        LAST_OTP_MESSAGE_IDS[chat_id] = []
    try:
        token_data = get_access_token(
            client_id=cred["client_id"],
            refresh_token=cred["refresh_token"],
            client_secret=cred.get("client_secret"),
        )
        access_token = token_data.get("access_token")
        if not access_token:
            return None
        otp_entries = get_otp_from_inbox(access_token, max_messages=15)
        if not otp_entries:
            msg = await context.bot.send_message(chat_id, "No OTP", reply_markup=KEYBOARD_DONE)
            LAST_OTP_MESSAGE_IDS[chat_id].append(msg.message_id)
            return msg.message_id
        codes_only = [", ".join(e["otp_codes"]) for e in otp_entries]
        text = "<code>" + "  |  ".join(codes_only) + "</code>"
        msg = await context.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=KEYBOARD_DONE)
        LAST_OTP_MESSAGE_IDS[chat_id].append(msg.message_id)
        return msg.message_id
    except Exception:
        return None


async def next_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Assign next credential; send email + password with [Check] [Done] buttons. Message is not auto-deleted."""
    if not await _check_access(update):
        return
    chat_id = update.effective_chat.id
    cred = get_next()
    if not cred:
        await update.message.reply_text("No fresh stock. Add credentials with /upload (admin).")
        return
    CURRENT[chat_id] = cred
    pass_str = cred.get("pass") or ""
    text = f"<code>{cred['mail']}</code>\n<code>{pass_str}</code>" if pass_str else f"<code>{cred['mail']}</code>"
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=KEYBOARD_CHECK_DONE)
    await _try_delete_user_message(update)


async def check_inbox(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check inbox for OTP. Uses current assigned mail or /check email@... if provided."""
    if not await _check_access(update):
        return
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()
    parts = text.split(maxsplit=1)
    cred = None
    if len(parts) > 1 and parts[1].strip() and "@" in parts[1]:
        email = parts[1].strip()
        cred = get_by_email(email)
        if not cred:
            await update.message.reply_text(f"Mail <b>{email}</b> not found in stock (fresh or used).", parse_mode="HTML")
            return
    else:
        cred = CURRENT.get(chat_id)
    if not cred:
        await update.message.reply_text(
            "No mail assigned. Use /next to get one from stock, or /check email@hotmail.com to check a mail in stock."
        )
        return
    checking_msg = await update.message.reply_text("Checking inbox…")
    await _try_delete_user_message(update)
    mid = await _send_otp_message(context, chat_id, cred)
    try:
        await checking_msg.delete()
    except Exception:
        pass
    if mid is None:
        await context.bot.send_message(chat_id, "Error")


async def callback_check_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle [Check] and [Done] buttons. Check = fetch OTP and send with [Done]. Done = delete OTP message(s)."""
    q = update.callback_query
    await q.answer()
    if not q.message or not q.from_user or not q.message.chat:
        return
    chat_id = q.message.chat.id
    user_id = q.from_user.id
    if q.message.chat.type == "private":
        if user_id not in ADMIN_IDS:
            return
    else:
        if chat_id not in ALLOWED_GROUP_IDS:
            return
        if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
            return

    if q.data == "check_otp":
        cred = CURRENT.get(chat_id)
        if not cred:
            await context.bot.send_message(chat_id, "No mail assigned. Use /next first.")
            return
        await _send_otp_message(context, chat_id, cred)
        return
    if q.data == "done_otp":
        msg_ids = LAST_OTP_MESSAGE_IDS.pop(chat_id, [])
        for msg_id in msg_ids:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except Exception:
                pass
        return
    if q.data == "done_otp_self":
        try:
            await q.message.delete()
        except Exception:
            pass
        if chat_id in LAST_OTP_MESSAGE_IDS and q.message and q.message.message_id in LAST_OTP_MESSAGE_IDS[chat_id]:
            LAST_OTP_MESSAGE_IDS[chat_id] = [i for i in LAST_OTP_MESSAGE_IDS[chat_id] if i != q.message.message_id]
        return


async def stock_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show fresh and used counts (admin only if ADMIN_IDS set)."""
    if not await _check_access(update):
        return
    user_id = (update.effective_user or update.message.from_user).id if update.effective_user else update.message.from_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text(MSG_ADMIN_ONLY, parse_mode="HTML")
        return
    fresh, used = stock_counts()
    await update.message.reply_text(f"📦 Fresh: {fresh} | Used: {used}")


async def upload_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin: add credentials in bulk (paste or file)."""
    if not await _check_access(update):
        return
    user_id = (update.effective_user or update.message.from_user).id if update.effective_user else update.message.from_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text(MSG_ADMIN_ONLY, parse_mode="HTML")
        return
    await update.message.reply_text(
        "Send a <b>.txt</b> file with one credential per line, or paste lines in chat.\n"
        "Format: <code>mail|pass|refresh_token|client_id</code> (optional 5th: client_secret).",
        parse_mode="HTML",
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle: bulk credential lines (admin), single credential line, or single email (set current and hint /check)."""
    if not await _check_access(update):
        return
    chat_id = update.effective_chat.id
    user_id = (update.effective_user or update.message.from_user).id if update.effective_user else update.message.from_user.id
    text = (update.message.text or "").strip()
    is_admin = _is_admin(user_id)

    # Single email: if we have it in stock, set as current and tell user to /check
    if text and "@" in text and "|" not in text and len(text) < 120:
        maybe_email = text.strip().lower()
        if re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", maybe_email):
            cred = get_by_email(maybe_email)
            if cred:
                CURRENT[chat_id] = cred
                await update.message.reply_text(
                    f"Set to <b>{cred['mail']}</b>. Use /check to get OTP.",
                    parse_mode="HTML",
                )
                return

    # Bulk credentials – admin only. Treat as bulk if newlines OR one long line that looks like credentials (e.g. pasted with spaces instead of newlines)
    if is_admin and text and ("\n" in text or (text.count("|") >= 3 and "@" in text)):
        line_list = [L.strip() for L in text.splitlines() if L.strip()]
        if not line_list:
            line_list = [text.strip()]
        added, errors = add_to_fresh(line_list)
        msg = f"Added <b>{added}</b> to fresh stock."
        if errors:
            msg += "\n" + "\n".join(errors[:15])
            if len(errors) > 15:
                msg += f"\n... and {len(errors) - 15} more"
        await update.message.reply_text(msg, parse_mode="HTML")
        return

    # Single credential line (still supported: set as current for this chat)
    cred = parse_credentials(text)
    if cred:
        CURRENT[chat_id] = cred
        await update.message.reply_text(
            f"Set to <b>{cred['mail']}</b>. Use /check to get OTP.",
            parse_mode="HTML",
        )
        return

    await update.message.reply_text("Use /next to get a mail from stock, or /check to get OTP for your current mail.")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin: process uploaded .txt file as bulk credentials."""
    if not await _check_access(update):
        return
    user_id = (update.effective_user or update.message.from_user).id if update.effective_user else update.message.from_user.id
    if not _is_admin(user_id):
        return
    doc = update.message.document
    if not doc or not doc.file_name:
        return
    if not doc.file_name.lower().endswith(".txt"):
        await update.message.reply_text("Send a .txt file with one credential per line.")
        return
    file = await context.bot.get_file(doc.file_id)
    buf = await file.download_as_bytearray()
    text = buf.decode("utf-8", errors="replace")
    lines = [L.strip() for L in text.splitlines() if L.strip()]
    added, errors = add_to_fresh(lines)
    msg = f"From file: added <b>{added}</b> to fresh stock."
    if errors:
        msg += "\n" + "\n".join(errors[:15])
        if len(errors) > 15:
            msg += f"\n... and {len(errors) - 15} more"
    await update.message.reply_text(msg, parse_mode="HTML")


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in .env")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("next", next_command))
    app.add_handler(CommandHandler("check", check_inbox))
    app.add_handler(CommandHandler("stock", stock_command))
    app.add_handler(CommandHandler("upload", upload_command))
    app.add_handler(CallbackQueryHandler(callback_check_done))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
