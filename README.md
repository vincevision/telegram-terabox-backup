# Telegram to TeraBox Backup Userbot

This project is a Telegram userbot that watches selected chats and automatically backs up media to your TeraBox account.

It supports:
- backing up existing media history from any monitored chat or channel
- automatically uploading new media as it appears
- storing upload state in SQLite so files are not re-uploaded
- tracking backup progress and quotas through Telegram commands

It uses Telethon in userbot mode, which allows it to act like a real Telegram user rather than a restricted bot account.

## Features
- Full historical media backup for a group/channel
- Real-time backup for newly uploaded media
- Per-chat TeraBox folder mapping
- Duplicate prevention via SQLite database
- Backup status and quota reporting from Telegram
- Simple command-based control from Saved Messages or admin chats

## Requirements
- Python 3.9+
- Telegram API ID and API hash from https://my.telegram.org
- A TeraBox account
- A valid TeraBox cookie and jsToken

## Installation

1. Clone the repository

```bash
git clone <your-repo-url>
cd telegram-terabox-backup
```

2. Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

On Windows:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

3. Install dependencies

```bash
pip install -r requirements.txt
```

## Configuration

Create a `.env` file in the project root with the following values:

```env
API_ID=your_api_id
API_HASH=your_api_hash
ADMIN_USER_ID=your_telegram_user_id
USE_USERBOT=true
TERABOX_COOKIE=your_terabox_cookie
TERABOX_JSTOKEN=your_terabox_jstoken
DEFAULT_TERABOX_FOLDER=/TelegramBackup
TEMP_DOWNLOAD_DIR=./downloads
DATABASE_PATH=./backup_tracker.db
```

### Getting your Telegram API credentials
- Go to https://my.telegram.org
- Create or open an app
- Copy the API ID and API hash

### Getting your TeraBox cookie and jsToken
Open TeraBox in a browser and follow these steps:

1. Open Developer Tools (`F12`)
2. Go to the Network tab and refresh the page
3. Select the first request to `www.terabox.com`
4. Copy the full `Cookie` header and paste it into `TERABOX_COOKIE`
5. In the Console tab, run:

```javascript
window.jsToken || (typeof fn_jsToken === 'function' ? fn_jsToken() : 'Not Found')
```

6. Copy the returned value into `TERABOX_JSTOKEN`

This is required for the script to authenticate to TeraBox and avoid common API rejection errors.

## Running the bot

```bash
python bot.py
```

On first launch, Telegram will ask for your phone number and login code. The session is saved locally, so you usually only need to do this once.

## Telegram commands

Once the bot is running, send commands from your Saved Messages, or from the admin chat where you want to manage backups.

| Command | Description |
| --- | --- |
| `/start` | Shows available commands |
| `/add <folder_name>` | Register the current group/channel and map it to a TeraBox folder |
| `/add <chat_id> <folder_name>` | Register a target chat by ID from Saved Messages |
| `/remove <chat_id>` | Stop monitoring a chat |
| `/backup <chat_id>` | Back up the chat's existing media history |
| `/cancel <chat_id>` | Cancel a running backup job |
| `/status` | Show all monitored chats and their states |
| `/stats <chat_id>` | Show success/failure totals and current progress |
| `/quota` | Show current TeraBox storage usage |
| `/test` | Check whether the TeraBox session is still active |

### Example workflow

```text
/add /MySecretVideos
/backup -1001234567890
```

This configures the chat as a monitored source and starts a full historical media backup into the `/MySecretVideos` folder on TeraBox.

## How it works

- The script logs into Telegram with Telethon
- It listens for new media in configured chats
- It stores metadata in `backup_tracker.db` to avoid re-uploading the same message
- It downloads media to the temporary download folder
- It uploads the file to the configured TeraBox directory
- It records success or failure details for reporting and retries

## Notes and limitations

- This project is intended for personal or private backup use.
- TeraBox authentication can expire, so your cookie and jsToken may need refreshing.
- Large backups may take a long time depending on file size, number of messages, and TeraBox API limits.
- Telegram media can include many formats; the script attempts to handle common file types, but some edge cases may need manual review.

## Troubleshooting

### TeraBox error `4000023` or `need verify`
This usually means TeraBox has flagged the session.

Fix:
- sign in to TeraBox in your browser
- manually upload a small file from the browser
- complete any verification challenge if prompted
- restart the bot

### Uploads fail or quota check returns empty
This usually means the TeraBox cookie is expired or invalid.

Fix:
- re-login to TeraBox in the browser
- refresh the `TERABOX_COOKIE` and `TERABOX_JSTOKEN` values
- restart the script

## Disclaimer

This project is not affiliated with, endorsed, or sponsored by Telegram or TeraBox. It is provided for educational and personal backup purposes only. Please comply with the relevant terms of service and local laws.


