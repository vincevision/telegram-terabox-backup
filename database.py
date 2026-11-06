import sqlite3
import datetime
from typing import Optional, List


class BackupDatabase:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS channels (
                channel_id INTEGER PRIMARY KEY,
                channel_title TEXT,
                terabox_folder TEXT NOT NULL,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS backed_up_media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                file_name TEXT,
                file_size INTEGER,
                file_type TEXT,
                terabox_path TEXT,
                backed_up_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'completed',
                error_message TEXT,
                UNIQUE(channel_id, message_id),
                FOREIGN KEY (channel_id) REFERENCES channels(channel_id)
            );

            CREATE TABLE IF NOT EXISTS backup_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER NOT NULL,
                status TEXT DEFAULT 'pending',
                total_media INTEGER DEFAULT 0,
                processed_media INTEGER DEFAULT 0,
                successful INTEGER DEFAULT 0,
                failed INTEGER DEFAULT 0,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                FOREIGN KEY (channel_id) REFERENCES channels(channel_id)
            );

            CREATE INDEX IF NOT EXISTS idx_backed_up_media_channel 
                ON backed_up_media(channel_id, message_id);
        """)

        conn.commit()
        conn.close()

    def add_channel(self, channel_id: int, channel_title: str, terabox_folder: str):
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO channels (channel_id, channel_title, terabox_folder, is_active)
               VALUES (?, ?, ?, 1)""",
            (channel_id, channel_title, terabox_folder)
        )
        conn.commit()
        conn.close()

    def get_channel(self, channel_id: int) -> Optional[dict]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM channels WHERE channel_id = ? AND is_active = 1",
            (channel_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_all_channels(self) -> List[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM channels WHERE is_active = 1"
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def remove_channel(self, channel_id: int):
        conn = self._get_conn()
        conn.execute(
            "UPDATE channels SET is_active = 0 WHERE channel_id = ?",
            (channel_id,)
        )
        conn.commit()
        conn.close()

    def is_media_backed_up(self, channel_id: int, message_id: int) -> bool:
        conn = self._get_conn()
        row = conn.execute(
            """SELECT 1 FROM backed_up_media 
               WHERE channel_id = ? AND message_id = ? AND status = 'completed'""",
            (channel_id, message_id)
        ).fetchone()
        conn.close()
        return row is not None

    def record_backup(self, channel_id: int, message_id: int, file_name: str,
                      file_size: int, file_type: str, terabox_path: str,
                      status: str = "completed", error_message: str = None):
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO backed_up_media 
               (channel_id, message_id, file_name, file_size, file_type, 
                terabox_path, status, error_message, backed_up_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (channel_id, message_id, file_name, file_size, file_type,
             terabox_path, status, error_message,
             datetime.datetime.now().isoformat())
        )
        conn.commit()
        conn.close()

    def get_backup_stats(self, channel_id: int) -> dict:
        conn = self._get_conn()
        row = conn.execute(
            """SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as successful,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
                SUM(CASE WHEN status = 'completed' THEN file_size ELSE 0 END) as total_size
               FROM backed_up_media WHERE channel_id = ?""",
            (channel_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else {"total": 0, "successful": 0, "failed": 0, "total_size": 0}

    def create_job(self, channel_id: int, total_media: int) -> int:
        conn = self._get_conn()
        cursor = conn.execute(
            """INSERT INTO backup_jobs (channel_id, status, total_media)
               VALUES (?, 'running', ?)""",
            (channel_id, total_media)
        )
        job_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return job_id

    def update_job(self, job_id: int, processed: int, successful: int, failed: int,
                   status: str = "running"):
        conn = self._get_conn()
        completed_at = datetime.datetime.now().isoformat() if status != "running" else None
        conn.execute(
            """UPDATE backup_jobs 
               SET processed_media = ?, successful = ?, failed = ?, status = ?,
                   completed_at = COALESCE(?, completed_at)
               WHERE id = ?""",
            (processed, successful, failed, status, completed_at, job_id)
        )
        conn.commit()
        conn.close()

    def get_active_job(self, channel_id: int) -> Optional[dict]:
        conn = self._get_conn()
        row = conn.execute(
            """SELECT * FROM backup_jobs 
               WHERE channel_id = ? AND status = 'running'
               ORDER BY id DESC LIMIT 1""",
            (channel_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None