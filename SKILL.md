---
name: disaster-backup
title: Meridian Flow 灾备备份系统
version: 2.1.1
description: 每天将 default + wangna 两个 profile 的 state.db 完整聊天记录备份到 D盘，双向验证，可搜索恢复
author: wangna
tags: [backup, disaster-recovery, state-db, chat-history]
---

# Meridian Flow 灾备备份系统

## 背景

Hermes 的 state.db 会被压缩/清理，旧 session 可能永久消失。agent.log 只是系统日志，**不是对话记录**。

本系统确保：
1. 每天自动导出完整消息到 D:盘
2. 备份后立即验证可读性
3. 可从备份中搜索任何历史对话

## 文件位置

| 文件 | 位置 | 作用 |
|---|---|---|
| 备份脚本 | `~/.hermes/profiles/wangna/scripts/disaster_backup.py` | 导出 default + wangna 两个 profile 的消息 |
| 搜索脚本 | `~/.hermes/profiles/wangna/scripts/backup_search.py` | 关键词/session/chat_id 搜索 |
| Telegram enricher | `~/.hermes/profiles/wangna/scripts/telegram_chat_enricher.py` | 从 agent.log 解析 chat_id 回填 |
| 恢复脚本 | `~/.hermes/profiles/wangna/scripts/restore_from_backup.py` | 从备份恢复到 SQLite（支持 `--profile`） |
| default state.db | `C:\Users\admin\AppData\Local\hermes\state.db` | 刘非的会话数据 |
| wangna state.db | `C:\Users\admin\AppData\Local\hermes\profiles\wangna\state.db` | 娜娜的会话数据 |
| 备份目录 | `D:\Meridian Flow LLC\chat_backup` | 所有备份文件 |
| 元数据 | `D:\Meridian Flow LLC\chat_backup\.backup_meta.json` | 备份索引（含两个 profile 的统计） |
| 恢复数据库 | `D:\Meridian Flow LLC\chat_backup\restored_chat.db` | 可查询的 SQLite（含 `profile` 列） |

## Cron Job

- **ID**: `disaster-backup-daily`
- **时间**: 每天凌晨 3:00
- **模式**: no_agent (纯脚本，不消耗 LLM 额度)

## 手动操作

### 立即备份
```bash
python ~/.hermes/profiles/wangna/scripts/disaster_backup.py
```

### 验证最新备份
```bash
python ~/.hermes/profiles/wangna/scripts/disaster_backup.py --verify
```

### 从备份搜索
```bash
# 关键词搜索
python ~/.hermes/profiles/wangna/scripts/backup_search.py "关键词" 10

# 按 session 搜索
python ~/.hermes/profiles/wangna/scripts/backup_search.py --session "session_id" 50

# 按 Telegram chat_id 搜索（群聊 vs 私聊）
python ~/.hermes/profiles/wangna/scripts/backup_search.py --chat "-1003970185743" 50
python ~/.hermes/profiles/wangna/scripts/backup_search.py --chat "5222593199" 50
```

### 从备份恢复到 SQLite
```bash
# 恢复最新备份（默认 default profile）
python ~/.hermes/profiles/wangna/scripts/restore_from_backup.py

# 恢复指定 profile 最新备份
python ~/.hermes/profiles/wangna/scripts/restore_from_backup.py --profile wangna

# 恢复指定备份文件
python ~/.hermes/profiles/wangna/scripts/restore_from_backup.py --file "D:\Meridian Flow LLC\chat_backup\messages_wangna_YYYYMMDD_HHMMSS.jsonl"
```

恢复后可用 sqlite3 直接查询 `D:\Meridian Flow LLC\chat_backup
estored_chat.db`：
```sql
-- 按 profile 区分来源
SELECT profile, COUNT(*) FROM messages GROUP BY profile;

-- 按 Telegram chat_id 查询
SELECT * FROM messages WHERE telegram_chat_id = '-1003970185743';

-- 全文搜索
SELECT * FROM messages_fts WHERE content MATCH '关键词';
```

## 备份流程

1. 同时从 default 和 wangna 两个 profile 的 state.db 导出消息到 JSONL
2. 用 sqlite3 `backup()` API 安全备份原始 state.db（合并 WAL 数据）
3. 自动从 agent.log 解析 Telegram chat_id 并回填
4. 分别验证每个 profile 的 JSONL 行数 = DB 消息数 = 元数据计数
5. 验证 JSONL 首尾内容完整
6. 写入 `.backup_meta.json`（含两个 profile 的统计）
7. 每个 profile 分别清理，保留最近 10 份；旧格式残留文件同样保留最近10份

## 备份内容

每次备份产生：
1. `messages_default_YYYYMMDD_HHMMSS.jsonl` — default profile 消息列表
2. `messages_wangna_YYYYMMDD_HHMMSS.jsonl` — wangna profile 消息列表
3. `state_default_YYYYMMDD_HHMMSS.db` — default 原始 state.db 安全拷贝
4. `state_wangna_YYYYMMDD_HHMMSS.db` — wangna 原始 state.db 安全拷贝

每个 profile 分别保留最近 10 份。

JSONL 每行包含 `profile` 字段，恢复后可按 profile 区分数据来源。

## References

- [`references/telegram-source-behavior.md`](references/telegram-source-behavior.md) — How Telegram `chat_id` is (not) stored in state.db and where to find it.
- [`references/dual-profile-backup-notes.md`](references/dual-profile-backup-notes.md) — Architecture of the dual-profile backup, the `sessions` table duplication trap, and the fix.
- [`references/v2.1.1-fixes.md`](references/v2.1.1-fixes.md) — 2026-06-14 audit fixes: enricher integration, tool_calls column, WAL backup, old-format cleanup.

### Sessions 表重复导致 JOIN 膨胀
wangna profile 的 `sessions` 表曾出现 1330 行仅 58 个 distinct id 的情况，`LEFT JOIN sessions` 会产生瓦级别的行数膨胀（5579 条 message 输出 17 万+行）。
**修复**：备份查询使用子查询去重 `LEFT JOIN (SELECT id, source, title FROM sessions GROUP BY id) s`，确保每个 session_id 只匹配一行。
详情见 `references/wangna-sessions-deduplication.md`。

### Enrich 输出文件名与原始 JSONL 冲突
`disaster_backup.py` 的 subprocess enrich 调用中，`enriched_path` 不能与原始 `jsonl_path` 重名。否则 enricher 读写同一文件会导致覆盖失败。
**修复**：`enriched_path = BACKUP_DIR / f"messages_{ts}_enriched.jsonl"`，move 替换原文件。

### Enricher 脚本路径
`disaster_backup.py` 通过 `Path(__file__).parent` 查找 enricher。若在 cron job 或其他上下文中运行，`__file__` 可能指向非 profile 目录的副本。必须确保 `telegram_chat_enricher.py` 同时存在于：
- `~/.hermes/profiles/wangna/scripts/telegram_chat_enricher.py` — 给 cron/no_agent 调用
- `~/.hermes/scripts/telegram_chat_enricher.py` — 给手动调用

### Hermes state.db 不存 Telegram chat_id
state.db 的 `sessions` 表只有 `user_id` (发送者个人 ID)，没有 `chat_id` (群聊/私聊频道 ID)。agent.log 的 `gateway.run: inbound message` 行才包含 `chat=YYY`。
详情见 `references/telegram-source-behavior.md`。

### 验证 chat_id 覆盖率
备份后应立即验证：
```python
import json
from collections import Counter

with open("messages_*.jsonl") as f:
    chat_counts = Counter(msg.get("telegram_chat_id", "MISSING") 
                          for msg in (json.loads(l) for l in f) 
                          if msg.get("source") == "telegram")
print(chat_counts)  # 应无 MISSING
```

