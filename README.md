# Hermes Disaster Backup

A battle-tested backup system for [Hermes Agent](https://github.com/NousResearch/hermes-agent) state.db — because state.db corruption, data loss on close, and runaway WAL growth are known, unpatched bugs.

Built for real-world production use. Supports dual-profile backups, chat_id enrichment, searchable recovery, and automated integrity verification.

## Why This Exists

Hermes Desktop has **three known, unresolved data-loss vulnerabilities** that make external backup essential:

| Issue | Title | Status |
|-------|-------|--------|
| [#5563](https://github.com/NousResearch/hermes-agent/issues/5563) | state.db Corruption Kills session_search | P1, open since 2026-04 |
| [#226](https://github.com/fathah/hermes-desktop/issues/226) | Session data loss on close (not flushing) | Open since 2026-05 |
| [#35201](https://github.com/NousResearch/hermes-agent/issues/35201) | WAL grows to 90+ MB | Open |

**Root cause:** `hermes_state.py` hardcodes `timeout=1.0`. WAL mode + multiple processes (desktop, gateway, cron) competing to write the same `state.db` = lock failure → B-tree corruption. Known crash pattern: file size locked at **909,312 bytes**.

This system doesn't wait for upstream fixes. It backs up daily, verifies immediately, and makes every historical message searchable.

## What It Does

- **Dual-profile export** — backs up both `default` and `wangna` profiles in one run
- **Safe state.db copy** — uses SQLite `backup()` API (merges WAL data, avoids raw copy)
- **Telegram chat_id enrichment** — parses `agent.log` to recover `chat_id` (missing from state.db sessions table)
- **Integrity verification** — every backup: row counts match, JSONL head/tail valid, metadata indexed
- **Searchable recovery** — keyword search, session search, chat_id search, or restore to SQLite with FTS

## Quick Start

```bash
# Run backup now
python scripts/disaster_backup.py

# Verify latest backup
python scripts/disaster_backup.py --verify

# Search backups
python scripts/backup_search.py "your keyword" 10
python scripts/backup_search.py --session "session_id" 50
python scripts/backup_search.py --chat "-1003970185743" 50

# Restore to SQLite
python scripts/restore_from_backup.py
python scripts/restore_from_backup.py --profile wangna
```

## Scripts

| Script | Purpose |
|--------|---------|
| `disaster_backup.py` | Main backup — exports messages + state.db, enriches chat_id, verifies |
| `backup_search.py` | Search backup JSONL by keyword, session, or Telegram chat_id |
| `restore_from_backup.py` | Restore from JSONL to a queryable SQLite DB with FTS |
| `telegram_chat_enricher.py` | Parse agent.log to recover missing `chat_id` |
| `backup_state_db.sh` | Lightweight shell backup using sqlite3 CLI |

## Directory Layout

```
hermes-disaster-backup/
├── scripts/
│   ├── disaster_backup.py
│   ├── backup_search.py
│   ├── restore_from_backup.py
│   ├── telegram_chat_enricher.py
│   └── backup_state_db.sh
├── references/
│   ├── github-issue-motivation.md    # The 3 bugs that motivated this
│   ├── telegram-source-behavior.md   # Why chat_id isn't in state.db
│   ├── dual-profile-backup-notes.md  # Architecture decisions
│   ├── v2.1.1-fixes.md               # Audit fixes
│   └── wangna-sessions-deduplication.md  # JOIN explosion fix
├── SKILL.md                           # Hermes skill definition
├── LICENSE                            # MIT
└── README.md                          # This file
```

## Requirements

- Python 3.10+
- `sqlite3` (stdlib)
- `pathlib`, `json`, `gzip` (stdlib)
- Agent log access for chat_id enrichment

## Troubleshooting

### Second profile sidebar shows no sessions

If the left sidebar of a secondary profile (e.g. `wangna`) appears empty — no sessions listed — **your data is not lost**. This is a known Hermes Desktop UI frontend bug, not a disaster-recovery scenario.

**Symptom:** Switch to the second profile → sidebar blank → panic.

**Reality check:**
```bash
# Verify sessions still exist in the DB
sqlite3 ~/AppData/Local/hermes/profiles/wangna/state.db "SELECT COUNT(*) FROM sessions;"
```

If the count is non-zero, your sessions are intact. The UI simply fails to render them for non-default profiles. Use `session_search` or query the DB directly — or switch back to the default profile and confirm the backup ran correctly.

Do not trigger a full disaster-recovery workflow for a UI rendering glitch.

## License

MIT — see [LICENSE](LICENSE).

## Credits

- Author: [wangna](https://github.com/antrax-chino-88) (Meridian Flow)
