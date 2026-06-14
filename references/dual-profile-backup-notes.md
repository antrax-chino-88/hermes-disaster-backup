# Dual-Profile Backup Notes

## Context

Hermes on this machine runs two active profiles under the same Windows user:

| Profile | Agent | state.db path | Sessions | Messages |
|---|---|---|---|---|
| `default` | 刘非 (小非) | `~\AppData\Local\hermes\state.db` | 36 | ~7,900 |
| `wangna` | 王娜 (娜娜) | `~\AppData\Local\hermes\profiles\wangna\state.db` | 58 unique / 1,330 rows | ~5,600 |

The backup script (`disaster_backup.py`) originally only read the `default` profile. This was fixed in v2.1.0 to back up **both** profiles in a single run.

## File Naming Convention

Each backup produces four files:

```
messages_default_YYYYMMDD_HHMMSS.jsonl
messages_wangna_YYYYMMDD_HHMMSS.jsonl
state_default_YYYYMMDD_HHMMSS.db
state_wangna_YYYYMMDD_HHMMSS.db
```

Each profile is cleaned independently (keep last 10).

## The `sessions` Table Duplication Trap

### Symptom
During testing, wangna's backup produced **172,481 lines** from a table that only had **5,579 messages**. The JSONL was ~17× larger than it should be.

### Root Cause
The wangna `sessions` table had duplicate rows for the same `id`:

```
Total sessions rows:  1,330
Distinct session ids:    58
```

A plain `LEFT JOIN sessions s ON m.session_id = s.id` therefore produced a Cartesian product — every message row multiplied by however many duplicate session rows matched.

### Why Only wangna?
The `default` profile has clean sessions (36 rows, 36 distinct ids). The duplication in `wangna` is likely from Hermes handoff/crash-recovery logic creating extra session rows without deduplication.

### Fix in Backup Query

Replace:
```sql
LEFT JOIN sessions s ON m.session_id = s.id
```

With:
```sql
LEFT JOIN (SELECT id, source, title FROM sessions GROUP BY id) s
    ON m.session_id = s.id
```

This forces one session row per `id`, eliminating the cartesian product.

> SQLite 3.53.1 (bundled with Python on this Windows host) does **not** support `SELECT DISTINCT` in subqueries for `LEFT JOIN`. `GROUP BY id` works and is semantically equivalent here.

### Verification After Fix

```
[OK] Backup wangna: 5579 messages, 1338 sessions
[OK] Verified wangna: 5579 messages, DB=5579
```

## Restore Script Changes

The restore script (`restore_from_backup.py`) was updated to:

1. Add `profile TEXT` column to the `messages` table
2. Add `idx_profile` index
3. Support `--profile wangna` to pick the latest wangna backup automatically
4. Print `By profile` in verification output

## Meta JSON Format (v2.1.0)

```json
{
  "last_backup": "20260614_045134",
  "profiles": {
    "default": {
      "messages_count": 7951,
      "sessions_count": 36,
      "jsonl_file": "messages_default_20260614_045134.jsonl",
      "db_file": "state_default_20260614_045134.db"
    },
    "wangna": {
      "messages_count": 5579,
      "sessions_count": 1338,
      "jsonl_file": "messages_wangna_20260614_045135.jsonl",
      "db_file": "state_wangna_20260614_045135.db"
    }
  },
  "verified": true,
  "verified_at": "2026-06-14T04:51:35.354920+00:00"
}
```

The old single-profile format is still handled by `_verify()` for backward compatibility.
