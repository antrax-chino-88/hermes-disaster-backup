#!/bin/bash
# Hermes backup script — FIXED VERSION (uses Python sqlite3, no external sqlite3 binary needed)
# Python's sqlite3 module supports .backup() natively via the backup() API.

set -euo pipefail

HERMES="/c/Users/admin/AppData/Local/hermes"
BACKUP_DIR="${HERMES}/backups"
STATE_DB="${HERMES}/state.db"
AGENT_LOG="${HERMES}/logs/agent.log"

mkdir -p "$BACKUP_DIR"
TS=$(date +%Y%m%d_%H%M%S)

# 1. Backup agent.log (safe to cp)
if [ -f "$AGENT_LOG" ]; then
    cp "$AGENT_LOG" "$BACKUP_DIR/agent_$TS.log" 2>/dev/null || true
fi

# 2. Backup state.db using Python sqlite3 (WAL-safe, no external binary needed)
if [ -f "$STATE_DB" ]; then
    python -c "
import sqlite3, shutil, os
src = r'C:\Users\admin\AppData\Local\hermes\state.db'
dst = r'C:\Users\admin\AppData\Local\hermes\backups\state_${TS}.db'
conn = sqlite3.connect(src)
with sqlite3.connect(dst) as backup_conn:
    conn.backup(backup_conn)
print(f'state.db backed up to {dst}')
"
fi

# 3. Prune: keep last 20 of each
cd "$BACKUP_DIR"
ls -t agent_*.log 2>/dev/null | tail -n +21 | xargs rm -f 2>/dev/null || true
ls -t state_*.db 2>/dev/null | tail -n +21 | xargs rm -f 2>/dev/null || true

# 4. Health check
if [ -f "$STATE_DB" ]; then
    if python -c "import sqlite3; sqlite3.connect(r'C:\Users\admin\AppData\Local\hermes\state.db').execute('SELECT 1')" 2>/dev/null; then
        echo "[OK] Backup completed at $TS"
        echo "  agent.log -> $BACKUP_DIR/agent_$TS.log"
        echo "  state.db  -> $BACKUP_DIR/state_$TS.db"
        exit 0
    fi
fi

echo "[WARN] state.db health check failed or file missing at $(date)"
exit 0
