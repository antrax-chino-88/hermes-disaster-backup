#!/usr/bin/env python3
"""
从 D:盘灾备备份恢复到可查询的 SQLite
使用: python restore_from_backup.py [--latest | --file <jsonl_path>]
"""
import json, sqlite3, sys, os
from pathlib import Path
from datetime import datetime, timezone

BACKUP_DIR = Path(r"D:\Meridian Flow LLC\chat_backup")
META_FILE = BACKUP_DIR / ".backup_meta.json"
RESTORE_DB = Path(r"D:\Meridian Flow LLC\chat_backup\restored_chat.db")


def get_latest_jsonl(profile=None):
    if profile:
        pattern = f"messages_{profile}_*.jsonl"
        files = sorted(BACKUP_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        return files[0] if files else None
    if not META_FILE.exists():
        files = sorted(BACKUP_DIR.glob("messages_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        return files[0] if files else None
    with open(META_FILE, "r", encoding="utf-8") as f:
        meta = json.load(f)
    # 尝试从新格式 meta 读取 default profile
    profiles = meta.get("profiles", {})
    if profiles:
        pm = profiles.get("default", list(profiles.values())[0])
        return BACKUP_DIR / pm["jsonl_file"]
    return BACKUP_DIR / meta.get("jsonl_file", "")


def restore(jsonl_path, output_db):
    if not jsonl_path.exists():
        print(f"[FAIL] Backup not found: {jsonl_path}")
        sys.exit(1)

    output_db.parent.mkdir(parents=True, exist_ok=True)
    if output_db.exists():
        os.remove(str(output_db))

    conn = sqlite3.connect(str(output_db))
    c = conn.cursor()

    c.execute("""
        CREATE TABLE messages (
            id TEXT,
            session_id TEXT,
            role TEXT,
            content TEXT,
            timestamp REAL,
            source TEXT,
            title TEXT,
            telegram_chat_id TEXT,
            tool_name TEXT,
            tool_calls TEXT,
            profile TEXT
        )
    """)
    c.execute("CREATE INDEX idx_session ON messages(session_id)")
    c.execute("CREATE INDEX idx_timestamp ON messages(timestamp)")
    c.execute("CREATE INDEX idx_source ON messages(source)")
    c.execute("CREATE INDEX idx_chat ON messages(telegram_chat_id)")
    c.execute("CREATE INDEX idx_profile ON messages(profile)")
    c.execute("CREATE VIRTUAL TABLE messages_fts USING fts5(content)")

    total = 0
    with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            c.execute(
                "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    msg.get("id"),
                    msg.get("session_id"),
                    msg.get("role"),
                    msg.get("content"),
                    msg.get("timestamp"),
                    msg.get("source"),
                    msg.get("title"),
                    msg.get("telegram_chat_id"),
                    msg.get("tool_name"),
                    msg.get("tool_calls"),
                    msg.get("profile"),
                ),
            )
            if msg.get("content"):
                c.execute("INSERT INTO messages_fts VALUES (?)", (msg["content"],))
            total += 1

    conn.commit()
    conn.close()
    print(f"[OK] Restored {total} messages to {output_db}")
    print(f"  Tables: messages, messages_fts")
    print(f"  Indexes: session, timestamp, source, chat, profile")
    return output_db


def verify_restored(db_path):
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()

    c.execute("SELECT COUNT(*) FROM messages")
    total = c.fetchone()[0]

    c.execute("SELECT source, COUNT(*) FROM messages GROUP BY source")
    by_source = dict(c.fetchall())

    c.execute("SELECT profile, COUNT(*) FROM messages GROUP BY profile")
    by_profile = dict(c.fetchall())

    c.execute("SELECT telegram_chat_id, COUNT(*) FROM messages WHERE telegram_chat_id IS NOT NULL GROUP BY telegram_chat_id")
    by_chat = dict(c.fetchall())

    conn.close()

    print(f"\n[OK] Verification:")
    print(f"  Total messages: {total}")
    print(f"  By source: {by_source}")
    print(f"  By profile: {by_profile}")
    print(f"  By Telegram chat: {by_chat}")


if __name__ == "__main__":
    profile = None
    if "--profile" in sys.argv:
        idx = sys.argv.index("--profile")
        profile = sys.argv[idx + 1]
    if "--file" in sys.argv:
        idx = sys.argv.index("--file")
        jsonl_path = Path(sys.argv[idx + 1])
    else:
        jsonl_path = get_latest_jsonl(profile)

    if not jsonl_path:
        print("[FAIL] No backup found")
        sys.exit(1)

    print(f"[INFO] Restoring from: {jsonl_path}")
    db_path = restore(jsonl_path, RESTORE_DB)
    verify_restored(db_path)
