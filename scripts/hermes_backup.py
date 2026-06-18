#!/usr/bin/env python3
"""hermes_backup.py — 统一灾备引擎

三种模式：
  --watchdog  WAL checkpoint + 完整性检查 + 安全自修复（每小时，轻量）
  --quick     核心DB快照到D盘（每30min，仅拷贝state.db/mnemosyne.db/agent.log）
  --full      全量灾备（每6h，含JSONL导出+配置文件+SHA256校验+skills清单）

设计原则：
  1. 不丢数据 — quick模式30min一次，full模式6h一次，双层覆盖
  2. 不崩 — 任何单步失败都catch，记录错误继续执行
  3. 可校验 — SHA256校验拷贝完整性，元数据记录所有操作
  4. 可恢复 — watchdog自修复时从D盘最新快照恢复，不暴力删除
"""
import argparse, sqlite3, json, os, shutil, hashlib, time, re
from pathlib import Path
from datetime import datetime
from collections import defaultdict

# === 路径配置（动态获取，不硬编码用户名）===
HOME = Path(os.environ.get("USERPROFILE", os.path.expanduser("~")))
HERMES_HOME = Path(os.environ.get("HERMES_HOME", HOME / "AppData" / "Local" / "hermes"))
DB = HERMES_HOME / "state.db"
MNEMOSYNE_DB = HERMES_HOME / "mnemosyne" / "data" / "mnemosyne.db"
AGENT_LOG = HERMES_HOME / "logs" / "agent.log"
LOG_DIR = HERMES_HOME / "logs"
WATCHDOG_LOG = LOG_DIR / "watchdog.log"
BACKUP_ROOT = Path(r"D:\hermes_backups")
BACKUP_ROOT.mkdir(parents=True, exist_ok=True)


# === 通用工具 ===

def log(msg):
    ts = datetime.now().isoformat()
    line = f"{ts} {msg}"
    print(line)
    try:
        with open(WATCHDOG_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def sha256_file(filepath):
    h = hashlib.sha256()
    try:
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                h.update(chunk)
        return h.hexdigest()[:16]
    except Exception:
        return None

def safe_copy(src, dst, label, results, errors, checksums=None):
    src, dst = Path(src), Path(dst)
    if not src.exists():
        results[label] = "MISSING"
        return
    try:
        shutil.copy2(src, dst)
        src_hash = sha256_file(src)
        dst_hash = sha256_file(dst)
        if src_hash != dst_hash:
            errors.append(f"{label}: SHA256 mismatch!")
            results[label] = "CORRUPTED"
            return
        if checksums is not None:
            checksums[label] = src_hash
        results[label] = f"{src.stat().st_size:,}B"
    except Exception as e:
        errors.append(f"{label}: {e}")
        results[label] = f"ERR:{e}"


# === 模式1: --watchdog ===

def checkpoint_db(db_path, label):
    conn = None
    for attempt in range(3):
        try:
            conn = sqlite3.connect(str(db_path), timeout=10)
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.commit()
            log(f"{label} WAL checkpoint: OK")
            return True
        except Exception as e:
            log(f"{label} checkpoint attempt {attempt+1}: {e}")
            time.sleep(2 ** attempt)
        finally:
            if conn:
                try: conn.close()
                except: pass
    log(f"{label} WAL checkpoint: FAILED after 3 attempts")
    return False

def integrity_check(db_path, label):
    conn = None
    try:
        conn = sqlite3.connect(str(db_path), timeout=10)
        conn.execute("PRAGMA query_only = ON")
        result = conn.execute("PRAGMA integrity_check").fetchone()
        ok = result[0] == "ok"
        log(f"{label} integrity: {'OK' if ok else 'FAILED - ' + result[0][:200]}")
        return ok
    except Exception as e:
        log(f"{label} integrity error: {e}")
        return False
    finally:
        if conn:
            try: conn.close()
            except: pass

def find_latest_backup(filename="state.db"):
    if not BACKUP_ROOT.exists():
        return None
    for snap in sorted(
        [x for x in BACKUP_ROOT.iterdir()
         if x.is_dir() and (x / ".backup_meta.json").exists()],
        key=lambda x: x.name, reverse=True
    ):
        f = snap / filename
        if f.exists() and f.stat().st_size > 0:
            return f
    return None

def safe_repair(db_path, label):
    log(f"{label} auto-repair triggered")
    corrupt_bak = db_path.with_suffix(
        f".corrupt_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    try:
        shutil.copy2(db_path, corrupt_bak)
        log(f"{label} corrupted DB backed up: {corrupt_bak.name}")
    except Exception as e:
        log(f"{label} corrupt backup failed: {e}")

    backup_name = "state.db" if "state" in label.lower() else "mnemosyne.db"
    latest = find_latest_backup(backup_name)
    if latest:
        try:
            for suffix in ['', '-wal', '-shm']:
                f = db_path.with_name(db_path.name + suffix)
                if f.exists(): f.unlink()
            shutil.copy2(latest, db_path)
            if integrity_check(db_path, f"{label} restored"):
                log(f"{label} restored from: {latest}")
                return {"status": "restored", "source": str(latest)}
            else:
                log(f"{label} restored DB also corrupt")
                return {"status": "error", "action": "restore_failed"}
        except Exception as e:
            log(f"{label} restore failed: {e}")
            return {"status": "error", "error": str(e)[:200]}
    else:
        log(f"{label} no backup available, removing for auto-recreate")
        try:
            for suffix in ['', '-wal', '-shm']:
                f = db_path.with_name(db_path.name + suffix)
                if f.exists(): f.unlink()
            return {"status": "removed", "action": "no_backup"}
        except Exception as e:
            return {"status": "error", "error": str(e)[:200]}

def run_watchdog():
    """WAL checkpoint + 完整性检查 + 安全自修复。每小时执行。"""
    result = {"mode": "watchdog", "status": "ok", "actions": []}

    # state.db
    if DB.exists():
        checkpoint_db(DB, "state.db")
        if not integrity_check(DB, "state.db"):
            r = safe_repair(DB, "state.db")
            result["actions"].append({"target": "state.db", **r})
            result["status"] = r["status"]
        wal = DB.with_name(DB.name + "-wal")
        if wal.exists() and wal.stat().st_size > 50 * 1024 * 1024:
            log(f"WAL oversized ({wal.stat().st_size:,}B), forcing checkpoint")
            checkpoint_db(DB, "state.db")
    else:
        result["actions"].append({"target": "state.db", "status": "missing"})

    # Mnemosyne DB
    if MNEMOSYNE_DB.exists():
        if not integrity_check(MNEMOSYNE_DB, "mnemosyne.db"):
            r = safe_repair(MNEMOSYNE_DB, "mnemosyne.db")
            result["actions"].append({"target": "mnemosyne.db", **r})
            result["status"] = r["status"]
    else:
        log("mnemosyne.db not found (may not be initialized yet)")

    # 清理旧corrupt文件（保留最近5个）
    for old in sorted(DB.parent.glob("*.corrupt_*"),
                      key=lambda x: x.stat().st_mtime, reverse=True)[5:]:
        old.unlink()

    print(json.dumps(result, ensure_ascii=False))


# === 模式2: --quick ===

def run_quick():
    """核心DB快照到D盘。每30min执行，仅拷贝3个核心文件。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    d = BACKUP_ROOT / f"quick_{ts}"
    d.mkdir(parents=True, exist_ok=True)
    results, errors, checksums = {}, [], {}

    # 先做WAL checkpoint确保持久化
    checkpoint_db(DB, "state.db")

    safe_copy(DB, d / "state.db", "state.db", results, errors, checksums)
    if MNEMOSYNE_DB.exists():
        safe_copy(MNEMOSYNE_DB, d / "mnemosyne.db", "mnemosyne.db",
                  results, errors, checksums)
    if AGENT_LOG.exists():
        safe_copy(AGENT_LOG, d / "agent.log", "agent.log",
                  results, errors, checksums)

    meta = {"mode": "quick", "timestamp": ts, "results": results,
            "errors": errors, "checksums": checksums}
    with open(d / ".backup_meta.json", 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # 保留最近20个quick快照
    quicks = sorted(
        [x for x in BACKUP_ROOT.iterdir()
         if x.is_dir() and x.name.startswith("quick_")],
        key=lambda x: x.name
    )
    for old in quicks[:-20]:
        shutil.rmtree(old)

    print(json.dumps({
        "mode": "quick", "status": "OK" if not errors else "WARN",
        "timestamp": ts, "results": results, "errors": errors[:3],
        "kept": min(len(quicks), 20)
    }, ensure_ascii=False))


# === 模式3: --full ===

def export_jsonl(db_path, jsonl_path, label, results, errors, checksums):
    """从SQLite导出全量记录为JSONL。"""
    try:
        conn = sqlite3.connect(str(db_path), timeout=10)
        conn.execute("PRAGMA query_only = ON")
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]

        if "messages" in tables:
            # state.db: 导出消息
            rows = conn.execute(
                "SELECT m.id, m.session_id, m.role, m.content, m.timestamp, "
                "m.tool_name, s.source, s.title FROM messages m "
                "LEFT JOIN sessions s ON m.session_id=s.id ORDER BY m.timestamp"
            ).fetchall()
            with open(jsonl_path, 'w', encoding='utf-8') as f:
                for r in rows:
                    f.write(json.dumps({
                        "id": r[0], "session_id": r[1], "role": r[2],
                        "content": r[3], "timestamp": r[4],
                        "tool_name": r[5], "source": r[6], "title": r[7]
                    }, ensure_ascii=False) + '\n')
            results[f"{label}_jsonl"] = f"{len(rows)} msgs"
            checksums[f"{label}_jsonl"] = sha256_file(jsonl_path)
        else:
            # Mnemosyne: 通用导出
            memories = []
            for table in tables:
                if any(kw in table.lower() for kw in ['memor', 'item', 'chunk']):
                    try:
                        cols = [d[0] for d in conn.execute(
                            f"SELECT * FROM {table} LIMIT 0").description]
                        for r in conn.execute(f"SELECT * FROM {table}").fetchall():
                            memories.append(dict(zip(cols, r)))
                    except Exception:
                        pass
            if memories:
                with open(jsonl_path, 'w', encoding='utf-8') as f:
                    for m in memories:
                        f.write(json.dumps(m, ensure_ascii=False,
                                default=str) + '\n')
                results[f"{label}_jsonl"] = f"{len(memories)} items"
                checksums[f"{label}_jsonl"] = sha256_file(jsonl_path)
        conn.close()
    except Exception as e:
        errors.append(f"{label} jsonl: {e}")
        results[f"{label}_jsonl"] = f"ERR:{e}"

def run_full():
    """全量灾备快照。每6h执行，含JSONL导出+配置文件+skills清单。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    d = BACKUP_ROOT / ts
    d.mkdir(parents=True, exist_ok=True)
    results, errors, checksums = {}, [], {}
    start = time.time()

    # 先checkpoint
    checkpoint_db(DB, "state.db")

    # 核心DB
    safe_copy(DB, d / "state.db", "state.db", results, errors, checksums)
    if MNEMOSYNE_DB.exists():
        safe_copy(MNEMOSYNE_DB, d / "mnemosyne.db", "mnemosyne.db",
                  results, errors, checksums)
    if AGENT_LOG.exists():
        safe_copy(AGENT_LOG, d / "agent.log", "agent.log",
                  results, errors, checksums)

    # 配置文件
    for fname, label in [
        ("SOUL.md", "SOUL.md"),
        ("config.yaml", "config.yaml"),
        ("memories/MEMORY.md", "MEMORY.md"),
        ("memories/USER.md", "USER.md"),
        ("auth.json", "auth.json"),
        ("cron/jobs.json", "cron_jobs.json"),
    ]:
        safe_copy(HERMES_HOME / fname, d / label, label,
                  results, errors, checksums)

    # kanban.db
    kanban = HERMES_HOME / "kanban.db"
    if kanban.exists():
        safe_copy(kanban, d / "kanban.db", "kanban.db",
                  results, errors, checksums)

    # JSONL导出
    if DB.exists():
        export_jsonl(DB, d / f"messages_{ts}.jsonl", "messages",
                     results, errors, checksums)
    if MNEMOSYNE_DB.exists():
        export_jsonl(MNEMOSYNE_DB, d / "mnemosyne_export.jsonl",
                     "mnemosyne", results, errors, checksums)

    # skills清单
    skills_dir = HERMES_HOME / "skills"
    if skills_dir.exists():
        skill_list = []
        for skill_md in skills_dir.rglob("SKILL.md"):
            rel = skill_md.relative_to(skills_dir).as_posix()
            skill_list.append({
                "path": rel,
                "size": skill_md.stat().st_size,
                "modified": datetime.fromtimestamp(
                    skill_md.stat().st_mtime).isoformat()
            })
        with open(d / "skills_manifest.json", 'w', encoding='utf-8') as f:
            json.dump(skill_list, f, ensure_ascii=False, indent=2)
        results["skills_manifest"] = f"{len(skill_list)} skills"

    # 元数据
    elapsed = int((time.time() - start) * 1000)
    meta = {
        "mode": "full", "timestamp": ts,
        "hermes_home": str(HERMES_HOME),
        "results": results, "errors": errors,
        "checksums": checksums, "duration_ms": elapsed
    }
    with open(d / ".backup_meta.json", 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # 保留最近10个full快照
    fulls = sorted(
        [x for x in BACKUP_ROOT.iterdir()
         if x.is_dir() and (x / ".backup_meta.json").exists()
         and not x.name.startswith("quick_")],
        key=lambda x: x.name
    )
    for old in fulls[:-10]:
        shutil.rmtree(old)

    print(json.dumps({
        "mode": "full", "status": "OK" if not errors else "WARN",
        "timestamp": ts, "results": results, "errors": errors[:5],
        "duration_ms": elapsed, "snapshots_kept": min(len(fulls), 10)
    }, ensure_ascii=False))


# === 入口 ===

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hermes统一灾备引擎")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--watchdog", action="store_true",
                       help="WAL checkpoint + 完整性检查 + 自修复")
    group.add_argument("--quick", action="store_true",
                       help="核心DB快照（state.db/mnemosyne.db/agent.log）")
    group.add_argument("--full", action="store_true",
                       help="全量灾备（JSONL+配置+SHA256校验）")
    args = parser.parse_args()

    if args.watchdog:
        run_watchdog()
    elif args.quick:
        run_quick()
    elif args.full:
        run_full()
