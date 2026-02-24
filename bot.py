"""
Telegram bot: fresh/used Hotmail credential stock, /next to get one, /check to get OTP.
Uses file-based storage (data/fresh_stock.txt, data/used.txt) like FrostyBot-style flow.
"""
import os
import re
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

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

# Per-chat current credential (assigned by /next or by sending an email we have)
CURRENT: dict[int, dict] = {}

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
            await update.message.reply_text("This bot is not available in DM. Use it in the allowed group. (Admins only in DM.)")
            return False
        return True

    if chat.type in ("group", "supergroup"):
        if chat_id not in ALLOWED_GROUP_IDS:
            return False  # silent ignore in non-allowed groups
        if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
            await update.message.reply_text("You are not allowed to use this bot here.")
            return False
        return True

    return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_access(update):
        return
    await update.message.reply_text(
        "Hi. I manage a <b>stock of Hotmail credentials</b> and check inbox for OTP.\n\n"
        "• <b>/next</b> – get the next unused mail from stock (assigned to you; use /check for OTP).\n"
        "• <b>/check</b> – get OTP for your current mail (or /check email@hotmail.com to check that mail if in stock).\n"
        "• <b>/stock</b> – show fresh vs used count (admin).\n"
        "• <b>/upload</b> – add credentials in bulk (admin): paste lines <code>mail|pass|refresh_token|client_id</code> or send a .txt file.",
        parse_mode="HTML",
    )


async def next_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Assign next credential from fresh stock to this chat and move it to used."""
    if not await _check_access(update):
        return
    chat_id = update.effective_chat.id
    cred = get_next()
    if not cred:
        await update.message.reply_text("No fresh stock. Add credentials with /upload (admin).")
        return
    CURRENT[chat_id] = cred
    await update.message.reply_text(
        f"Assigned: <b>{cred['mail']}</b>\nUse /check to get OTP for this inbox.",
        parse_mode="HTML",
    )


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
    await update.message.reply_text("Checking inbox…")
    try:
        token_data = get_access_token(
            client_id=cred["client_id"],
            refresh_token=cred["refresh_token"],
            client_secret=cred.get("client_secret"),
        )
        access_token = token_data.get("access_token")
        if not access_token:
            await update.message.reply_text("Failed to get access token (no access_token in response).")
            return
        otp_entries = get_otp_from_inbox(access_token, max_messages=15)
        if not otp_entries:
            await update.message.reply_text(
                f"Inbox checked for <b>{cred['mail']}</b>. No verification OTP in the last 15 emails.",
                parse_mode="HTML",
            )
            return
        lines = [f"OTP received for <b>{cred['mail']}</b>:\n"]
        for i, e in enumerate(otp_entries, 1):
            codes = ", ".join(e["otp_codes"])
            lines.append(
                f"{i}. From: {e['from']}\n"
                f"   Subject: {e['subject']}\n"
                f"   OTP: <code>{codes}</code>\n"
                f"   Time: {e['received']}"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"Error: {e!s}")


async def stock_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show fresh and used counts (admin only if ADMIN_IDS set)."""
    if not await _check_access(update):
        return
    user_id = (update.effective_user or update.message.from_user).id if update.effective_user else update.message.from_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text("Not allowed.")
        return
    fresh, used = stock_counts()
    await update.message.reply_text(f"📦 Fresh: {fresh} | Used: {used}")


async def upload_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin: add credentials in bulk (paste or file)."""
    if not await _check_access(update):
        return
    user_id = (update.effective_user or update.message.from_user).id if update.effective_user else update.message.from_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text("Not allowed.")
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

    # Multiple lines (bulk) – only for admin
    if is_admin and "\n" in text:
        line_list = [L.strip() for L in text.splitlines() if L.strip()]
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
    app.add_handler(CommandHandler("next", next_command))
    app.add_handler(CommandHandler("check", check_inbox))
    app.add_handler(CommandHandler("stock", stock_command))
    app.add_handler(CommandHandler("upload", upload_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
