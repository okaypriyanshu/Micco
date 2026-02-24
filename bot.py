"""
Telegram bot: receive Outlook credentials (mail|pass|refresh_token|client_id),
check inbox via Microsoft Graph, report OTP from emails.
"""
import os
import re
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from graph_client import get_access_token, get_otp_from_inbox

load_dotenv()

# Per-chat stored credentials: mail, refresh_token, client_id, client_secret (optional)
# Format: mail|pass|refresh_token|client_id or ...|client_id|client_secret
CREDENTIALS: dict[int, dict] = {}

# Credential line: mail|pass|refresh_token|client_id or with optional client_secret
CRED_PATTERN = re.compile(
    r"^([^|]+)\|([^|]*)\|([^|]+)\|([^|]+)(?:\|([^|]*))?$",
    re.IGNORECASE,
)


def parse_credentials(text: str) -> dict | None:
    """Parse 'mail|pass|refresh_token|client_id' or with optional client_secret. Strips whitespace."""
    text = (text or "").strip()
    m = CRED_PATTERN.match(text)
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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Hi. I can check your Outlook inbox for OTP emails using OAuth and Microsoft Graph.\n\n"
        "Send your credentials in one line:\n"
        "<code>mail|pass|refresh_token|client_id</code>\n\n"
        "Optional 5th field: <code>...|client_secret</code>\n\n"
        "Use the client_id you already have (e.g. from your credential source). If you don't have one, create a single Azure app and use its client_id for all accounts; each account has its own refresh_token.\n\n"
        "Then use /check to scan inbox for OTP. Send a new credential line to switch account.",
        parse_mode="HTML",
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text: if it looks like credentials, save and ack; else hint."""
    text = (update.message.text or "").strip()
    cred = parse_credentials(text)
    if cred:
        await handle_credentials(update, context, cred)
    else:
        await update.message.reply_text(
            "Send credentials as: mail|pass|refresh_token|client_id then use /check to get OTP."
        )


async def handle_credentials(update: Update, context: ContextTypes.DEFAULT_TYPE, cred: dict | None = None) -> None:
    if cred is None:
        cred = parse_credentials((update.message.text or "").strip())
    if not cred:
        return
    chat_id = update.effective_chat.id
    CREDENTIALS[chat_id] = cred
    await update.message.reply_text(
        f"Credentials saved for <b>{cred['mail']}</b>. Use /check to scan inbox for OTP.",
        parse_mode="HTML",
    )


async def check_inbox(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    cred = CREDENTIALS.get(chat_id)
    if not cred:
        await update.message.reply_text(
            "No credentials set. Send a line in format: mail|pass|refresh_token|client_id"
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
                f"Inbox checked for {cred['mail']}. No messages with OTP-like codes in the last 15 emails."
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


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in .env")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("check", check_inbox))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
