#!/usr/bin/env python3
"""
Telegram Chat ID Enricher — 从 agent.log 解析 chat_id 并回填到备份消息
使用: python telegram_chat_enricher.py <input_jsonl> <output_jsonl>
"""
import json, re, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

def parse_agent_log(log_path):
    """解析 agent.log，提取 Telegram 事件"""
    events = []
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                entry = json.loads(line)
                raw = entry.get("raw", "")
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

            # Inbound message: user -> agent
            m = re.search(
                r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})[,.\d]*\s+.*"
                r"gateway\.run:\s+inbound message:\s+"
                r"platform=telegram\s+user=(\S+)\s+chat=(-?\d+)",
                raw,
            )
            if m:
                ts_str, user, chat_id = m.groups()
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=timezone(timedelta(hours=8))
                ).timestamp()
                events.append({"ts": ts, "chat_id": chat_id, "type": "inbound"})
                continue

            # Response ready: agent -> user
            m = re.search(
                r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})[,.\d]*\s+.*"
                r"gateway\.run:\s+response ready:\s+"
                r"platform=telegram\s+chat=(-?\d+)",
                raw,
            )
            if m:
                ts_str, chat_id = m.groups()
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=timezone(timedelta(hours=8))
                ).timestamp()
                events.append({"ts": ts, "chat_id": chat_id, "type": "response"})

    events.sort(key=lambda x: x["ts"])
    return events


def enrich_messages(input_jsonl, output_jsonl, events, time_window=30):
    """给消息添加 telegram_chat_id"""
    inbound_events = [e for e in events if e["type"] == "inbound"]
    response_events = [e for e in events if e["type"] == "response"]

    # First pass: load all telegram messages
    telegram_msgs = []
    other_msgs = []
    with open(input_jsonl, "r", encoding="utf-8") as f:
        for line in f:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("source") == "telegram":
                telegram_msgs.append(msg)
            else:
                other_msgs.append(msg)

    # Sort telegram messages by session + timestamp
    telegram_msgs.sort(key=lambda m: (m.get("session_id", ""), m.get("timestamp", 0)))

    # Round 1: time-based matching
    session_chat_map = {}
    for msg in telegram_msgs:
        role = msg.get("role", "")
        ts = msg.get("timestamp", 0)
        session_id = msg.get("session_id", "")
        target_events = inbound_events if role == "user" else response_events
        best = None
        best_diff = float("inf")
        for ev in target_events:
            diff = abs(ev["ts"] - ts)
            if diff <= time_window and diff < best_diff:
                best_diff = diff
                best = ev
        if best:
            msg["telegram_chat_id"] = best["chat_id"]
            session_chat_map[session_id] = best["chat_id"]

    # Round 2: propagate within sessions (forward and backward)
    # Group by session
    by_session = {}
    for msg in telegram_msgs:
        sid = msg.get("session_id", "")
        by_session.setdefault(sid, []).append(msg)

    for sid, msgs in by_session.items():
        msgs.sort(key=lambda m: m.get("timestamp", 0))
        # Forward pass
        last_chat = None
        for msg in msgs:
            if msg.get("telegram_chat_id"):
                last_chat = msg["telegram_chat_id"]
            elif last_chat:
                msg["telegram_chat_id"] = last_chat
        # Backward pass
        last_chat = None
        for msg in reversed(msgs):
            if msg.get("telegram_chat_id"):
                last_chat = msg["telegram_chat_id"]
            elif last_chat:
                msg["telegram_chat_id"] = last_chat

    # Round 3: heuristic for group chat indicators
    for msg in telegram_msgs:
        if msg.get("telegram_chat_id"):
            continue
        content = msg.get("content", "") or ""
        if "@wangna" in content or "@liufei" in content or "[Replying to:" in content:
            msg["telegram_chat_id"] = "-1003970185743"  # Known group ID

    # Write output
    all_msgs = other_msgs + telegram_msgs
    all_msgs.sort(key=lambda m: m.get("timestamp", 0))

    with open(output_jsonl, "w", encoding="utf-8") as f:
        for msg in all_msgs:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    enriched_count = sum(1 for m in telegram_msgs if m.get("telegram_chat_id"))
    telegram_count = len(telegram_msgs)
    return enriched_count, telegram_count


def main():
    if len(sys.argv) < 3:
        print("Usage: python telegram_chat_enricher.py <input_jsonl> <output_jsonl>")
        sys.exit(1)

    input_jsonl = sys.argv[1]
    output_jsonl = sys.argv[2]

    # Find agent log
    log_candidates = [
        Path(r"C:\Users\admin\AppData\Local\hermes\profiles\wangna\log_backups\agent_log.jsonl"),
        Path(r"C:\Users\admin\AppData\Local\hermes\log_backups\agent_log.jsonl"),
        Path(r"C:\Users\admin\AppData\Local\hermes\agent.log"),
    ]
    log_path = None
    for p in log_candidates:
        if p.exists():
            log_path = str(p)
            break

    if not log_path:
        print("[WARN] No agent.log found, skip enrichment")
        # Just copy input to output
        import shutil
        shutil.copy2(input_jsonl, output_jsonl)
        sys.exit(0)

    print(f"[INFO] Parsing agent log: {log_path}")
    events = parse_agent_log(log_path)
    print(f"[INFO] Found {len(events)} telegram events")

    print(f"[INFO] Enriching {input_jsonl} -> {output_jsonl}")
    enriched, total = enrich_messages(input_jsonl, output_jsonl, events)
    print(f"[OK] Enriched {enriched}/{total} telegram messages")


if __name__ == "__main__":
    main()
