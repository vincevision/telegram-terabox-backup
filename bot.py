import os
import sys
import asyncio
import logging
import time
from pathlib import Path
from datetime import datetime

from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeFilename

from config import Config
from database import BackupDatabase
from terabox_api import TeraBoxAPI

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log"),
    ],
)
logger = logging.getLogger("BackupBot")

# --- Validate Configuration ---
Config.validate()

# --- Initialize Core Services ---
os.makedirs(Config.TEMP_DOWNLOAD_DIR, exist_ok=True)

db = BackupDatabase(Config.DATABASE_PATH)
terabox = TeraBoxAPI(Config.TERABOX_COOKIE)

client = None
me_id = None

active_backups = {}
cancel_flags = {}


# ==================== HELPER FUNCTIONS ====================

def is_admin(user_id: int) -> bool:
    return user_id == Config.ADMIN_USER_ID or user_id == me_id


def format_size(size_bytes: int) -> str:
    if not size_bytes:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(size_bytes) < 1024:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f} PB"


def get_media_type(message) -> str:
    if message.photo:
        return "photo"
    if message.video:
        return "video"
    if message.audio:
        return "audio"
    if message.voice:
        return "voice"
    if message.video_note:
        return "video_note"
    if message.sticker:
        return "sticker"
    if message.gif:
        return "gif"
    if message.document:
        return "document"
    return "unknown"


def get_file_name(message) -> str:
    if message.document:
        for attr in message.document.attributes:
            if isinstance(attr, DocumentAttributeFilename):
                return attr.file_name

    media_type = get_media_type(message)
    ext_map = {
        "photo": ".jpg", "video": ".mp4", "audio": ".mp3",
        "voice": ".ogg", "video_note": ".mp4", "sticker": ".webp",
        "gif": ".mp4",
    }
    ext = ext_map.get(media_type, ".bin")
    timestamp = message.date.strftime("%Y%m%d_%H%M%S") if message.date else "unknown"
    return f"{media_type}_{message.id}_{timestamp}{ext}"


def get_file_size(message) -> int:
    if message.document:
        return message.document.size or 0
    if message.photo:
        return 500 * 1024
    return 0


async def download_media_file(message, progress_msg=None) -> str:
    file_name = get_file_name(message)
    local_path = os.path.join(Config.TEMP_DOWNLOAD_DIR, file_name)
    last_update = [0]

    async def progress_callback(current, total):
        now = time.time()
        if now - last_update[0] < 3:
            return
        last_update[0] = now
        pct = (current / total * 100) if total else 0
        if progress_msg:
            try:
                await progress_msg.edit(
                    f"⬇️ Downloading: `{file_name}`\n"
                    f"Progress: {pct:.1f}% ({format_size(current)}/{format_size(total)})"
                )
            except Exception:
                pass

    await client.download_media(
        message, file=local_path, progress_callback=progress_callback
    )
    return local_path


def upload_to_terabox(local_path: str, remote_path: str) -> bool:
    parent_folder = os.path.dirname(remote_path)
    terabox.create_folder(parent_folder)
    return terabox.upload_file(local_path, remote_path)


# ==================== BACKUP ENGINE ====================

async def backup_single_message(message, channel_id: int, terabox_folder: str,
                                 status_msg=None, job_context: dict = None):
    if not message.media:
        return False

    media_type = get_media_type(message)
    if media_type == "unknown":
        return False

    if db.is_media_backed_up(channel_id, message.id):
        return True

    file_name = get_file_name(message)
    file_size = get_file_size(message)
    remote_path = f"{terabox_folder}/{file_name}"
    local_path = None

    try:
        if status_msg:
            try:
                ctx = f"[{job_context['current']}/{job_context['total']}] " if job_context else ""
                await status_msg.edit(
                    f"⬇️ {ctx}Downloading: `{file_name}`\n"
                    f"Type: {media_type} | Size: {format_size(file_size)}"
                )
            except Exception:
                pass

        local_path = await download_media_file(message)

        if not local_path or not os.path.exists(local_path):
            raise Exception("Download failed - local file missing")

        actual_size = os.path.getsize(local_path)

        if status_msg:
            try:
                ctx = f"[{job_context['current']}/{job_context['total']}] " if job_context else ""
                await status_msg.edit(
                    f"⬆️ {ctx}Uploading to TeraBox: `{file_name}`\n"
                    f"Size: {format_size(actual_size)}"
                )
            except Exception:
                pass

        success = upload_to_terabox(local_path, remote_path)

        if success:
            db.record_backup(
                channel_id, message.id, file_name,
                actual_size, media_type, remote_path, "completed"
            )
            logger.info(f"✅ Backed up: {file_name} -> {remote_path}")
            return True
        else:
            db.record_backup(
                channel_id, message.id, file_name,
                actual_size, media_type, remote_path, "failed",
                "TeraBox upload failed"
            )
            logger.error(f"❌ Upload failed: {file_name}")
            return False

    except Exception as e:
        logger.error(f"❌ Backup error for msg {message.id}: {e}")
        db.record_backup(
            channel_id, message.id, file_name,
            file_size, media_type, remote_path, "failed", str(e)
        )
        return False

    finally:
        if local_path and os.path.exists(local_path):
            try:
                os.remove(local_path)
            except Exception:
                pass


async def full_channel_backup(channel_id: int, admin_chat_id: int):
    channel_info = db.get_channel(channel_id)
    if not channel_info:
        await client.send_message(admin_chat_id, "❌ Chat/Group not registered in database.")
        return

    terabox_folder = channel_info["terabox_folder"]
    terabox.create_folder(terabox_folder)

    status_msg = await client.send_message(
        admin_chat_id,
        f"🔍 Scanning media history for `{channel_info['channel_title']}`...\n"
        f"📁 TeraBox Target: `{terabox_folder}`"
    )

    media_messages = []
    try:
        async for message in client.iter_messages(channel_id):
            if cancel_flags.get(channel_id, False):
                break
            if message.media:
                media_type = get_media_type(message)
                if media_type != "unknown":
                    media_messages.append(message)
    except Exception as e:
        await status_msg.edit(f"❌ Error scanning history: {e}")
        return

    if cancel_flags.get(channel_id, False):
        await status_msg.edit("🛑 Backup cancelled during history scan.")
        cancel_flags.pop(channel_id, None)
        return

    total = len(media_messages)
    pending = [m for m in media_messages if not db.is_media_backed_up(channel_id, m.id)]

    await status_msg.edit(
        f"📊 Found {total} total media files\n"
        f"⏭️ Previously Backed Up: {total - len(pending)}\n"
        f"📥 Pending Backup: {len(pending)}\n\n"
        f"🚀 Starting process..."
    )

    if not pending:
        await status_msg.edit(
            f"✅ All {total} media items are already backed up!\n"
            f"📁 Target Folder: `{terabox_folder}`"
        )
        return

    job_id = db.create_job(channel_id, len(pending))
    successful = 0
    failed = 0
    processed = 0

    for i, message in enumerate(pending, 1):
        if cancel_flags.get(channel_id, False):
            break

        processed = i
        job_context = {"current": i, "total": len(pending)}
        result = await backup_single_message(
            message, channel_id, terabox_folder, status_msg, job_context
        )

        if result:
            successful += 1
        else:
            failed += 1

        db.update_job(job_id, i, successful, failed)

        if i % 5 == 0 or i == len(pending):
            try:
                await status_msg.edit(
                    f"📦 Backup Status: **{channel_info['channel_title']}**\n\n"
                    f"✅ Successful: {successful}\n"
                    f"❌ Failed: {failed}\n"
                    f"📊 Progress: {i}/{len(pending)} ({i/len(pending)*100:.1f}%)\n"
                    f"📁 Folder: `{terabox_folder}`"
                )
            except Exception:
                pass

        await asyncio.sleep(1)

    cancelled = cancel_flags.pop(channel_id, False)
    status = "cancelled" if cancelled else "completed"
    db.update_job(job_id, processed, successful, failed, status)

    final_emoji = "🛑" if cancelled else "🎉"
    await client.send_message(
        admin_chat_id,
        f"{final_emoji} **Backup {'Cancelled' if cancelled else 'Complete'}!**\n\n"
        f"👥 Group/Channel: `{channel_info['channel_title']}`\n"
        f"📁 TeraBox Folder: `{terabox_folder}`\n"
        f"✅ Successful: {successful}\n"
        f"❌ Failed: {failed}\n"
        f"📊 Total processed: {processed}/{len(pending)}\n"
        f"⏰ Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    active_backups.pop(channel_id, None)


# ==================== BOT COMMAND HANDLERS ====================

async def cmd_start(event):
    if not is_admin(event.sender_id):
        return

    await event.reply(
        "🤖 **Telegram → TeraBox Auto Backup Bot**\n\n"
        "**Available Commands:**\n"
        "• `/add <terabox_folder>` (inside group)\n"
        "• `/add <group_id/username> <terabox_folder>` (in private chat or Saved Messages)\n"
        "• `/remove <group_id>` - Remove chat from monitoring\n"
        "• `/backup <group_id>` - Backup past history\n"
        "• `/cancel <group_id>` - Stop active backup task\n"
        "• `/status` - View configured backup chats\n"
        "• `/stats <group_id>` - Detailed group transfer statistics\n"
        "• `/quota` - Check TeraBox account space\n"
        "• `/test` - Validate TeraBox connection"
    )


async def cmd_add(event):
    if not is_admin(event.sender_id):
        return

    parts = event.raw_text.split()

    if len(parts) == 2 and not event.is_private:
        terabox_folder = parts[1]
        channel_id = event.chat_id
        chat = await event.get_chat()
        channel_title = getattr(chat, 'title', f"Group_{channel_id}")

    elif len(parts) >= 3:
        channel_input = parts[1]
        terabox_folder = parts[2]
        try:
            entity = await client.get_entity(channel_input)
            channel_id = entity.id

            if hasattr(entity, 'megagroup') or hasattr(entity, 'broadcast'):
                if not str(channel_id).startswith("-100"):
                    try:
                        channel_id = int(f"-100{entity.id}")
                    except Exception:
                        pass

            channel_title = getattr(entity, 'title', str(channel_id))
        except Exception as e:
            await event.reply(f"❌ Error locating target chat: {e}")
            return
    else:
        await event.reply(
            "❌ **Invalid Command Usage**\n\n"
            "• **Inside a Group:** `/add <terabox_folder>`\n"
            "• **In Private/Saved Messages:** `/add <group_username_or_id> <terabox_folder>`"
        )
        return

    if not terabox_folder.startswith("/"):
        terabox_folder = "/" + terabox_folder

    status_msg = await event.reply("🔄 Configuring backup destination...")

    if terabox.create_folder(terabox_folder):
        db.add_channel(channel_id, channel_title, terabox_folder)
        await status_msg.edit(
            f"✅ **Group Monitored Successfully!**\n\n"
            f"👥 Target: **{channel_title}**\n"
            f"🆔 Chat ID: `{channel_id}`\n"
            f"📁 TeraBox Folder: `{terabox_folder}`\n\n"
            f"• **Live Monitor:** New media uploaded automatically.\n"
            f"• **Past History:** Run `/backup {channel_id}` to transfer older media."
        )
    else:
        await status_msg.edit("❌ Failed creating directory on TeraBox. Verify authentication cookie.")


async def cmd_remove(event):
    if not is_admin(event.sender_id):
        return

    parts = event.raw_text.split()
    if len(parts) < 2:
        await event.reply("❌ Usage: `/remove <group_id>`")
        return

    try:
        channel_id = int(parts[1])
    except ValueError:
        await event.reply("❌ Invalid Chat ID.")
        return

    channel = db.get_channel(channel_id)
    if not channel:
        await event.reply("❌ Target not registered.")
        return

    if channel_id in active_backups:
        cancel_flags[channel_id] = True

    db.remove_channel(channel_id)
    await event.reply(f"✅ Removed monitoring for: {channel['channel_title']}")


async def cmd_backup(event):
    if not is_admin(event.sender_id):
        return

    parts = event.raw_text.split()
    if len(parts) < 2:
        await event.reply("❌ Usage: `/backup <group_id>`")
        return

    try:
        channel_id = int(parts[1])
    except ValueError:
        await event.reply("❌ Invalid Chat ID.")
        return

    channel = db.get_channel(channel_id)
    if not channel:
        await event.reply("❌ Target not registered. Add it first using `/add`.")
        return

    if channel_id in active_backups:
        await event.reply("⚠️ Backup task already running for this chat.")
        return

    if not terabox.check_connection():
        await event.reply("❌ TeraBox authentication failed. Check your cookie.")
        return

    cancel_flags.pop(channel_id, None)

    task = asyncio.create_task(
        full_channel_backup(channel_id, event.chat_id)
    )
    active_backups[channel_id] = task

    await event.reply(
        f"🚀 Backup initiated for: **{channel['channel_title']}**\n"
        f"📁 TeraBox Destination: `{channel['terabox_folder']}`\n\n"
        f"Stop anytime using `/cancel {channel_id}`"
    )


async def cmd_cancel(event):
    if not is_admin(event.sender_id):
        return

    parts = event.raw_text.split()
    if len(parts) < 2:
        await event.reply("❌ Usage: `/cancel <group_id>`")
        return

    try:
        channel_id = int(parts[1])
    except ValueError:
        await event.reply("❌ Invalid Chat ID.")
        return

    if channel_id not in active_backups:
        await event.reply("ℹ️ No running task found for this chat ID.")
        return

    cancel_flags[channel_id] = True
    await event.reply("🛑 Task cancellation requested (stopping after current file completes)...")


async def cmd_status(event):
    if not is_admin(event.sender_id):
        return

    channels = db.get_all_channels()
    if not channels:
        await event.reply("📭 No active chats monitored.")
        return

    text = "📊 **Monitored Backup Chats**\n\n"
    for ch in channels:
        stats = db.get_backup_stats(ch["channel_id"])
        is_running = ch["channel_id"] in active_backups
        status_emoji = "🟢 RUNNING" if is_running else "⚪ IDLE"

        text += (
            f"**{ch['channel_title']}** ({status_emoji})\n"
            f"   ID: `{ch['channel_id']}`\n"
            f"   Folder: `{ch['terabox_folder']}`\n"
            f"   Transferred: ✅ {stats['successful'] or 0} | ❌ {stats['failed'] or 0}\n"
            f"   Volume: {format_size(stats['total_size'] or 0)}\n\n"
        )

    await event.reply(text)


async def cmd_stats(event):
    if not is_admin(event.sender_id):
        return

    parts = event.raw_text.split()
    if len(parts) < 2:
        await event.reply("❌ Usage: `/stats <group_id>`")
        return

    try:
        channel_id = int(parts[1])
    except ValueError:
        await event.reply("❌ Invalid Chat ID.")
        return

    channel = db.get_channel(channel_id)
    if not channel:
        await event.reply("❌ Group not found.")
        return

    stats = db.get_backup_stats(channel_id)
    job = db.get_active_job(channel_id)

    text = (
        f"📊 **Backup Stats for {channel['channel_title']}**\n\n"
        f"📁 Path: `{channel['terabox_folder']}`\n"
        f"✅ Total Uploaded: {stats['successful'] or 0}\n"
        f"❌ Total Failures: {stats['failed'] or 0}\n"
        f"💾 Storage Size: {format_size(stats['total_size'] or 0)}\n"
    )

    if job:
        text += (
            f"\n🔄 **Active Progress:**\n"
            f"   Transfer: {job['processed_media']}/{job['total_media']}\n"
            f"   Completed: ✅ {job['successful']} | ❌ {job['failed']}\n"
        )

    await event.reply(text)


async def cmd_quota(event):
    if not is_admin(event.sender_id):
        return

    quota = terabox.get_quota()
    if quota:
        used_pct = (quota["used"] / quota["total"] * 100) if quota["total"] else 0
        await event.reply(
            f"💾 **TeraBox Storage Quota**\n\n"
            f"Total: {format_size(quota['total'])}\n"
            f"Used: {format_size(quota['used'])} ({used_pct:.1f}%)\n"
            f"Available: {format_size(quota['free'])}"
        )
    else:
        await event.reply("❌ Unable to fetch quota. Check login cookie.")


async def cmd_test(event):
    if not is_admin(event.sender_id):
        return

    msg = await event.reply("🔄 Checking TeraBox server authorization...")

    if terabox.check_connection():
        quota = terabox.get_quota()
        await msg.edit(
            f"✅ **TeraBox Connection Valid**\n"
            f"💾 Free Space: {format_size(quota['free']) if quota else 'Unknown'}"
        )
    else:
        await msg.edit("❌ **TeraBox Connection Failed!** Check your `.env` cookie string.")


# ==================== LIVE AUTO-BACKUP HANDLER ====================

async def auto_backup_new_media(event):
    if not event.media:
        return

    chat_id = event.chat_id
    channel = db.get_channel(chat_id)
    if not channel:
        return

    media_type = get_media_type(event.message)
    if media_type == "unknown":
        return

    if db.is_media_backed_up(chat_id, event.message.id):
        return

    logger.info(f"📥 New file detected in {channel['channel_title']}: {get_file_name(event.message)}")

    try:
        success = await backup_single_message(
            event.message, chat_id, channel["terabox_folder"]
        )

        file_name = get_file_name(event.message)
        if success:
            await client.send_message(
                Config.ADMIN_USER_ID,
                f"✅ **Auto Backup Uploaded**\n\n"
                f"👥 Group: {channel['channel_title']}\n"
                f"📄 File: `{file_name}`\n"
                f"📁 Location: `{channel['terabox_folder']}/{file_name}`"
            )
        else:
            await client.send_message(
                Config.ADMIN_USER_ID,
                f"❌ **Auto Backup Failed**\n\n"
                f"👥 Group: {channel['channel_title']}\n"
                f"📄 File: `{file_name}`"
            )
    except Exception as e:
        logger.error(f"Live backup failure: {e}")


# ==================== ENTRY POINT ====================

async def main():
    global client, me_id

    logger.info("🚀 Starting Telegram → TeraBox Userbot Engine...")

    client = TelegramClient("userbot_session", Config.API_ID, Config.API_HASH)

    if Config.USE_USERBOT or not Config.BOT_TOKEN:
        logger.info("📱 Starting as Userbot (User Account)...")
        await client.start()
    else:
        logger.info("🤖 Starting as Bot Token...")
        await client.start(bot_token=Config.BOT_TOKEN)

    me = await client.get_me()
    me_id = me.id
    name = getattr(me, 'first_name', '') or getattr(me, 'username', 'User')
    logger.info(f"✅ Connected as User: {name} (ID: {me_id})")

    # Register Event Handlers
    client.add_event_handler(cmd_start, events.NewMessage(pattern=r"^/start$"))
    client.add_event_handler(cmd_add, events.NewMessage(pattern=r"^/add(\s+|$)"))
    client.add_event_handler(cmd_remove, events.NewMessage(pattern=r"^/remove(\s+|$)"))
    client.add_event_handler(cmd_backup, events.NewMessage(pattern=r"^/backup(\s+|$)"))
    client.add_event_handler(cmd_cancel, events.NewMessage(pattern=r"^/cancel(\s+|$)"))
    client.add_event_handler(cmd_status, events.NewMessage(pattern=r"^/status$"))
    client.add_event_handler(cmd_stats, events.NewMessage(pattern=r"^/stats(\s+|$)"))
    client.add_event_handler(cmd_quota, events.NewMessage(pattern=r"^/quota$"))
    client.add_event_handler(cmd_test, events.NewMessage(pattern=r"^/test$"))
    client.add_event_handler(auto_backup_new_media, events.NewMessage())

    if terabox.check_connection():
        logger.info("✅ TeraBox Session Active")
    else:
        logger.warning("⚠️ TeraBox Session Inactive - Check Cookie")

    try:
        await client.send_message(
            Config.ADMIN_USER_ID,
            "🤖 **Userbot engine online and operational!**"
        )
    except Exception:
        pass

    logger.info("System Ready. Listening for requests...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())