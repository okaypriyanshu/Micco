# Outlook OTP Telegram Bot (Micco)

Manages a **stock of Hotmail credentials** (fresh vs used) and checks inbox for OTP via OAuth and Microsoft Graph. Flow is similar to [FrostyBot](https://github.com/okaypriyanshu/FrostyBot): admins feed a list of credentials; users get the **next** one and use **/check** to receive OTP.

## How it works

1. **Admin** adds credentials in bulk (e.g. ~100 lines) via **/upload**: paste lines or send a `.txt` file. Each line: `mail|pass|refresh_token|client_id` (optional 5th: `client_secret`). These go into **fresh stock** (`data/fresh_stock.txt`).
2. **User** runs **/next**: the bot assigns one credential from fresh stock to that chat and moves it to **used** (`data/used.txt`).
3. **User** runs **/check**: the bot uses the **current** assigned mail for that chat, calls Microsoft Graph, and replies with any OTP found in the inbox.
4. **Check by email**: user can send **/check email@hotmail.com** to check that specific mail if it exists in stock (fresh or used). Or send a single message that is just an email address (e.g. `user@hotmail.com`); if that mail is in stock, the bot sets it as current and says to use **/check**.

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
# Edit .env: TELEGRAM_BOT_TOKEN and optionally ADMIN_IDS=123456789,987654321
python bot.py
```

## Usage

- **/start** – Short help.
- **/next** – Get the next unused mail from stock (assigned to your chat; then use **/check** for OTP).
- **/check** – Get OTP for your current mail. Or **/check email@hotmail.com** to check that mail if it’s in stock.
- **/stock** – Show fresh vs used count (admin only if `ADMIN_IDS` is set).
- **/upload** – Admin only: add credentials in bulk (paste lines or send a `.txt` file).

**Auto-detect by email:** Send a message that is only an email address (e.g. `user@hotmail.com`). If that mail is in fresh or used stock, the bot sets it as your current mail and tells you to use **/check**.

---

## Admin: how to feed emails

Only users in **ADMIN_IDS** can do this (in DM or in the allowed group).

1. **Open DM with the bot** (or use the bot in your allowed group).
2. Send **/upload**. The bot will ask for credentials.
3. **Option A – Paste in chat**  
   Paste multiple lines, one credential per line:
   ```text
   user1@hotmail.com|password1|refresh_token_1|client_id
   user2@hotmail.com|password2|refresh_token_2|client_id
   ```
   The bot will reply with how many were added to **fresh stock**.
4. **Option B – Send a file**  
   Send a **.txt** file where each line is one credential in the same format. The bot will add them to fresh stock and report the count.

Each line format: **mail|pass|refresh_token|client_id** (optional 5th: **client_secret**).  
Use **/stock** anytime to see **Fresh** vs **Used** counts.

---

## Users: how to access and get OTP

Only in the **allowed group(s)** and (if set) only for users in **ALLOWED_USER_IDS**. Users cannot feed data; only admins can **/upload**.

1. **Get credentials from stock**  
   Send **/next**. The bot assigns you one unused mail from stock and moves it to “used”. You get the **email + password** so you can use the account; then use **/check** for OTP.

2. **Get the OTP**  
   When you expect a verification email (e.g. Zoom code, sign-up code):
   - Send **/check**.  
   The bot checks the inbox of the mail currently assigned to that chat and replies with any OTP found (e.g. *OTP received for user@hotmail.com: 469421*).

3. **Check a specific mail**  
   If you already know the address (e.g. from a previous /next):
   - Send **/check user@hotmail.com**  
   or send a single message with just the email: **user@hotmail.com**  
   Then send **/check**. The bot will use that mail (if it’s in stock) and return the OTP.

**Summary:** **/next** → get email + password for one account; **/check** → get OTP for that mail (or **/check email@...** for a specific mail in stock).

**Clean chat:** If **CLEAN_CHAT** is enabled (default), the bot deletes your command message and deletes its own reply (credentials or OTP) after **DELETE_AFTER_SECONDS** (default 90), so the chat stays clean. Set `CLEAN_CHAT=false` in `.env` to disable.

---

## Data files (file-based storage)

- **`data/fresh_stock.txt`** – One credential per line (unused). Populated by **/upload**.
- **`data/used.txt`** – One credential per line (already assigned via **/next**). Used mails stay here so you can still **/check email@...** later.
- The **`data/`** directory is in `.gitignore`; credentials are not committed.

## Access control (limited people, group only, DM = admin only)

Configure in `.env`:

- **ADMIN_IDS** – Comma-separated user IDs. Only these users can use the bot **in DM** and run **/upload** and **/stock**.
- **ALLOWED_GROUP_IDS** – Comma-separated group/supergroup chat IDs. The bot **only responds in these groups** (and in DMs for admins). In other groups it ignores messages. Get group ID: add [@RawDataBot](https://t.me/RawDataBot) to the group and read `chat.id` (e.g. `-1001234567890`).
- **ALLOWED_USER_IDS** – Comma-separated user IDs who can use the bot **in the allowed groups**. If empty, everyone in those groups can use it.

**Summary:** DM = admin only. Groups = only allowed group IDs, and (if set) only allowed user IDs.

## Security

- Credentials are stored in **files** under `data/` and in **memory** per chat for “current” assignment.
- **Access control:** Set **ADMIN_IDS** (DM + /upload, /stock), **ALLOWED_GROUP_IDS** (only these groups), and **ALLOWED_USER_IDS** (who can use in groups). DM = admin only; groups = allowed groups + (if set) allowed users.
- Do not share refresh tokens or client secrets; anyone with them can read mail.

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
