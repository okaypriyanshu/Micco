# Outlook OTP Telegram Bot

A Telegram bot that signs in to your Outlook account using OAuth (Microsoft Graph) and checks your inbox for OTP/verification emails, then reports the codes back to you.

## How it works

1. You send the bot a single line with your data: **mail|pass|refresh_token|client_id** (and optionally **|client_secret**).
2. The bot stores these for your chat and uses the **refresh_token** + **client_id** to get an access token from Microsoft.
3. You run **/check** and the bot reads your Outlook inbox via Microsoft Graph, finds messages that look like OTP/verification codes, and sends you the codes.
4. You can send a **new** credential line anytime to switch to another account; the next **/check** will use that account.

## Setup

### 1. Telegram bot token

- Open [@BotFather](https://t.me/BotFather) on Telegram, create a bot, copy the token.

### 2. Azure app (for client_id and refresh_token)

- Go to [Azure Portal](https://portal.azure.com) → **Microsoft Entra ID** → **App registrations** → **New registration**.
- Name it, choose **Accounts in any organizational directory and personal Microsoft accounts**.
- Under **Authentication**: add a **Mobile and desktop application** redirect URI, e.g. `http://localhost`.
- Under **API permissions**: add **Microsoft Graph** → **Delegated** → `Mail.Read`, `User.Read`, `offline_access`.
- Copy the **Application (client) ID** → this is your **client_id**.
- If you use a **client secret** (optional): **Certificates & secrets** → New client secret → copy the value → use as 5th field in the credential line.

### 3. Get a refresh token

You need one refresh token per account (and optionally client_secret if the app is “confidential”):

- Use the [OAuth 2.0 authorization code flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow) or a tool (e.g. [oauth2-proxy](https://github.com/oauth2-proxy/oauth2-proxy), or a small script with `msal`) to sign in as the user and get a **refresh_token**.
- Scopes must include: `Mail.Read`, `User.Read`, `offline_access`.

### 4. Install and run

```bash
cd /Users/senor/Micco
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env and set TELEGRAM_BOT_TOKEN=your_bot_token
python bot.py
```

## Usage

- **/start** – Instructions and credential format.
- Send one line: **mail|pass|refresh_token|client_id** or **mail|pass|refresh_token|client_id|client_secret**  
  The bot saves it for your chat and confirms.
- **/check** – Fetches the last 15 inbox messages, finds likely OTP codes, and replies with “OTP received” and the codes.
- Send another **mail|pass|refresh_token|client_id** line to switch account; then **/check** uses that account.

## Security

- Credentials are kept **in memory** only (per chat). Restarting the bot clears them.
- Do not share your refresh token or client secret; anyone with them can read your mail.
- Prefer running the bot in a private chat and on a machine you control.

## Do I need to generate client_id myself?

- **If you already have credential lines** (e.g. from another tool) that include `client_id`: use them as-is. You don’t generate anything; each line can have its own client_id.
- **If you’re setting up from scratch**: you create **one** Azure app → you get **one** client_id. That same client_id is used for every account. Each account has its **own** refresh_token (and mail). So you only generate the client_id once; the part that changes per account is `mail` and `refresh_token`.

## Credential format

| Field           | Required | Description                                      |
|----------------|----------|--------------------------------------------------|
| mail           | Yes      | Your Outlook email (used for display only).      |
| pass           | Yes*     | Can be empty; OAuth uses refresh_token, not pass. |
| refresh_token  | Yes      | OAuth refresh token from Microsoft (per account).|
| client_id      | Yes      | Azure app (client) ID — one per app, can be same for all accounts. |
| client_secret  | No       | Optional; use if your app has a client secret.   |

Example (fake values):

```
you@outlook.com||long_refresh_token_here|abc123-client-id
```

With secret:

```
you@outlook.com||long_refresh_token_here|abc123-client-id|your_client_secret
```
