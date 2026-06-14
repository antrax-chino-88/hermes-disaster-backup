#!/usr/bin/env python3
"""
Meridian Flow 灾备备份 — 从 state.db 导出完整聊天记录到 D:盘
使用方式: python disaster_backup.py [--verify]
"""
import sqlite3, json, os, sys, shutil
from datetime import datetime, timezone
from pathlib import Path

STATE_DB = Path(r"C:\Users\admin\AppData\Local\hermes\state.db")
WANGNA_STATE_DB = Path(r"C:\Users\admin\AppData\Local\hermes\profiles\wangna\state.db")
BACKUP_DIR = Path(r"D:\Meridian Flow LLC\chat_backup")
META_FILE = BACKUP_DIR / ".backup_meta.json"

def _backup_profile(profile, db_path):
    if not db_path.exists():
        print(f"[FAIL] state.db not found for {profile}: {db_path}")
        return None

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # 1. 导出消息到 JSONL
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute("SELECT COUNT(*) FROM messages")
    total = c.fetchone()[0]
    if total == 0:
        print(f"[WARN] {profile} state.db has 0 messages, skip")
        conn.close()
        return None

    jsonl_path = BACKUP_DIR / f"messages_{profile}_{ts}.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        c.execute("""
            SELECT m.id, m.session_id, m.role, m.content, m.timestamp,
                   m.tool_name, m.tool_calls, m.finish_reason,
                   s.source, s.title
            FROM messages m
            LEFT JOIN (SELECT id, source, title FROM sessions GROUP BY id) s
                ON m.session_id = s.id
            ORDER BY m.timestamp
        """)
        for row in c:
            entry = {
                "id": row["id"],
                "session_id": row["session_id"],
                "role": row["role"],
                "content": row["content"],
                "timestamp": row["timestamp"],
                "tool_name": row["tool_name"],
                "tool_calls": row["tool_calls"],
                "finish_reason": row["finish_reason"],
                "source": row["source"],
                "title": row["title"],
                "profile": profile,
            }
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # 2. 安全备份原始 state.db（用 sqlite3 backup 确保 WAL 数据合并）
    sessions_count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    conn.close()

    db_backup = BACKUP_DIR / f"state_{profile}_{ts}.db"
    src_conn = sqlite3.connect(str(db_path))
    dst_conn = sqlite3.connect(str(db_backup))
    with dst_conn:
        src_conn.backup(dst_conn)
    dst_conn.close()
    src_conn.close()

    # 3. 调用 enricher 回填 Telegram chat_id
    enriched_path = BACKUP_DIR / f"messages_{profile}_{ts}_enriched.jsonl"
    enricher_script = Path(__file__).parent / "telegram_chat_enricher.py"
    if not enricher_script.exists():
        # fallback to scripts dir (cron copies)
        enricher_script = Path(r"C:\Users\admin\AppData\Local\hermes\profiles\wangna\scripts\telegram_chat_enricher.py")
    if enricher_script.exists():
        import subprocess
        result = subprocess.run(
            [sys.executable, str(enricher_script), str(jsonl_path), str(enriched_path)],
            capture_output=True, text=True
        )
        if result.returncode == 0 and enriched_path.exists():
            shutil.move(str(enriched_path), str(jsonl_path))
            print(f"  ENRICH: OK")
        else:
            print(f"  ENRICH: FAIL — {result.stderr.strip() or result.stdout.strip()}")
    else:
        print(f"  ENRICH: SKIP — enricher not found")

    print(f"[OK] Backup {profile} {ts}: {total} messages, {sessions_count} sessions")
    print(f"  JSONL: {jsonl_path.name}")
    print(f"  DB: {db_backup.name}")
    return {"profile": profile, "ts": ts, "messages_count": total,
            "sessions_count": sessions_count, "jsonl_file": jsonl_path.name,
            "db_file": db_backup.name, "jsonl_path": jsonl_path, "db_path": db_backup}

def _backup():
    profiles = [
        ("default", STATE_DB),
        ("wangna", WANGNA_STATE_DB),
    ]
    results = []
    for profile, db_path in profiles:
        result = _backup_profile(profile, db_path)
        if result:
            results.append(result)

    if not results:
        print("[WARN] All profiles empty, skip backup")
        sys.exit(0)

    ts = results[0]["ts"]
    meta = {
        "last_backup": ts,
        "profiles": {},
        "verified": False,
    }
    for r in results:
        meta["profiles"][r["profile"]] = {
            "messages_count": r["messages_count"],
            "sessions_count": r["sessions_count"],
            "jsonl_file": r["jsonl_file"],
            "db_file": r["db_file"],
        }
    with open(META_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    # 清理：每个 profile 分别保留最近10份
    for profile, _ in profiles:
        for pattern in [f"messages_{profile}_*.jsonl", f"state_{profile}_*.db"]:
            files = sorted(BACKUP_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
            for old in files[10:]:
                old.unlink()
    # 清理旧格式残留（无 profile 前缀的 messages_YYYYMMDD_HHMMSS.jsonl）
    old_fmt_files = sorted(
        [p for p in BACKUP_DIR.glob("messages_2*.jsonl") if not any(p.name.startswith(f"messages_{pr}_") for pr, _ in profiles)],
        key=lambda p: p.stat().st_mtime, reverse=True
    )
    for old in old_fmt_files[10:]:
        old.unlink()

    return results

def _verify():
    if not META_FILE.exists():
        print("[FAIL] No backup meta found")
        sys.exit(1)

    with open(META_FILE, "r", encoding="utf-8") as f:
        meta = json.load(f)

    profiles_meta = meta.get("profiles", {})
    if not profiles_meta:
        # 兼容旧格式（单一 profile）
        profiles_meta = {"default": meta}

    all_ok = True
    for profile, pm in profiles_meta.items():
        jsonl_path = BACKUP_DIR / pm["jsonl_file"]
        db_path = BACKUP_DIR / pm["db_file"]

        # 验证 JSONL 可读
        if not jsonl_path.exists():
            print(f"[FAIL] {profile} JSONL missing: {jsonl_path}")
            all_ok = False
            continue
        jsonl_count = sum(1 for _ in open(jsonl_path, "r", encoding="utf-8"))
        if jsonl_count != pm["messages_count"]:
            print(f"[FAIL] {profile} JSONL count mismatch: {jsonl_count} vs {pm['messages_count']}")
            all_ok = False
            continue

        # 验证 DB 可读
        if not db_path.exists():
            print(f"[FAIL] {profile} DB missing: {db_path}")
            all_ok = False
            continue
        conn = sqlite3.connect(str(db_path))
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM messages")
        db_count = c.fetchone()[0]
        conn.close()
        if db_count != pm["messages_count"]:
            print(f"[FAIL] {profile} DB count mismatch: {db_count} vs {pm['messages_count']}")
            all_ok = False
            continue

        # 验证 JSONL 内容完整
        with open(jsonl_path, "r", encoding="utf-8") as f:
            first = json.loads(f.readline())
            last = None
            for line in f:
                last = line
            if last:
                last = json.loads(last)
        if not first.get("content") or not last.get("content"):
            print(f"[FAIL] {profile} JSONL content incomplete")
            all_ok = False
            continue

        print(f"[OK] Verified {profile}: {jsonl_count} messages, DB={db_count}")
        print(f"  First msg: {first.get('role')} @ {first.get('timestamp')}")
        print(f"  Last msg:  {last.get('role')} @ {last.get('timestamp')}")

    if not all_ok:
        sys.exit(1)

    # 更新元数据
    meta["verified"] = True
    meta["verified_at"] = datetime.now(timezone.utc).isoformat()
    with open(META_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

if __name__ == "__main__":
    if "--verify" in sys.argv:
        _verify()
    else:
        _backup()
        _verify()
