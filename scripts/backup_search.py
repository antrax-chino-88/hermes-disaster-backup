#!/usr/bin/env python3
"""
灾备备份搜索 — 从 D:盘备份中搜索历史聊天记录
使用方式: python backup_search.py <keyword> [limit]
"""
import json, sys, re
from pathlib import Path

BACKUP_DIR = Path(r"D:\Meridian Flow LLC\chat_backup")
META_FILE = BACKUP_DIR / ".backup_meta.json"

def search_keyword(keyword, limit=20):
    if not META_FILE.exists():
        print("[FAIL] No backup found. Run disaster_backup.py first.")
        return []

    with open(META_FILE, "r", encoding="utf-8") as f:
        meta = json.load(f)
    jsonl_path = BACKUP_DIR / meta["jsonl_file"]

    if not jsonl_path.exists():
        print(f"[FAIL] Backup file missing: {jsonl_path}")
        return []

    results = []
    with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            content = entry.get("content", "") or ""
            if keyword.lower() in content.lower():
                results.append(entry)
                if len(results) >= limit:
                    break
    return results

def search_session(session_id, limit=100):
    with open(META_FILE, "r", encoding="utf-8") as f:
        meta = json.load(f)
    jsonl_path = BACKUP_DIR / meta["jsonl_file"]

    results = []
    with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                entry = json.loads(line)
            except:
                continue
            if session_id in entry.get("session_id", ""):
                results.append(entry)
                if len(results) >= limit:
                    break
    return results

def format_entry(entry):
    role = entry.get("role", "?")
    ts = entry.get("timestamp", "?")
    sess = entry.get("session_id", "")[:16]
    content = entry.get("content", "") or ""
    # Truncate
    if len(content) > 300:
        content = content[:300] + " [...]"
    return f"[{role}] [{sess}] {ts}\n{content}\n"

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    if sys.argv[1] == "--session":
        results = search_session(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 100)
        print(f"Found {len(results)} entries for session {sys.argv[2]}\n")
    else:
        results = search_keyword(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 20)
        print(f"Found {len(results)} entries for '{sys.argv[1]}'\n")

    for r in results:
        print(format_entry(r))
        print("-" * 50)
