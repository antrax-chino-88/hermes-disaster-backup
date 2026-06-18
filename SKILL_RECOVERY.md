---
name: hermes-disaster-recovery
description: Hermes 统一灾备引擎 — 三模式备份（watchdog/quick/full）+ 自修复 + 多profile支持。防 state.db 损坏、防数据丢失、防单点故障。触发：备份、灾备、恢复、state.db坏了、数据丢了、backup
version: 2.0.0
tags: [backup, disaster-recovery, state-db, watchdog, hermes]
---

# Hermes 统一灾备引擎

## 概述

`hermes_backup.py`（400行统一引擎）覆盖三种备份模式，保护默认profile和娜娜(wangna) profile的数据。

### 三模式架构

| 模式 | 频率 | 内容 | 用途 |
|------|------|------|------|
| `--watchdog` | 每小时 | WAL checkpoint + 完整性检查 + 自修复 | 日常健康守护 |
| `--quick` | 每30分钟 | state.db / mnemosyne.db / agent.log 快照 → `D:\hermes_backups\` | 快速恢复 |
| `--full` | 每6小时 | 全量：JSONL导出 + config/SOUL/memories + SHA256校验 + skills清单 | 完整灾备 |

## 路径配置

```
默认 profile:
  state.db:   ~/AppData/Local/hermes/state.db
  SOUL.md:    ~/AppData/Local/hermes/SOUL.md
  config:     ~/AppData/Local/hermes/config.yaml

娜娜 profile:
  state.db:   ~/AppData/Local/hermes/profiles/wangna/state.db
  SOUL.md:    ~/AppData/Local/hermes/profiles/wangna/SOUL.md

备份目标:
  D:\hermes_backups\  ← 独立磁盘，不怕C盘挂
```

## 手动运行

```bash
# 三种模式
python ~/AppData/Local/hermes/scripts/hermes_backup.py --watchdog
python ~/AppData/Local/hermes/scripts/hermes_backup.py --quick
python ~/AppData/Local/hermes/scripts/hermes_backup.py --full
```

## 多Profile扩展

当前脚本只备份默认profile。娜娜的state.db需要单独的备份cron。扩展方案：

```python
# 在脚本末尾添加娜娜profile支持
WANGNA_DB = HERMES_HOME / "profiles" / "wangna" / "state.db"
# ... 镜像 watchdog/quick/full 逻辑
```

## 恢复流程

### state.db 损坏 → watchdog 自动修复

1. 检测到 PRAGMA integrity_check 失败
2. 备份损坏文件 → `state.db.corrupt_YYYYMMDD_HHMMSS`
3. 从 `D:\hermes_backups\` 找最新快照
4. 恢复并验证 → 若也损坏则删除等待 Hermes 重建

### 手动恢复

```bash
# 1. 停止 gateway
hermes gateway stop

# 2. 备份损坏文件
mv state.db state.db.broken

# 3. 从最新快照恢复
cp D:/hermes_backups/quick_*/state.db ~/AppData/Local/hermes/state.db

# 4. 启动 gateway
hermes gateway run &
```

## Cron 配置（已部署）

```
backup-watchdog:  0 * * * *    → 每小时
backup-quick:     */30 * * * *  → 每30分钟
backup-full:      0 */6 * * *   → 每6小时
agent-log-jsonl:  every 30m     → agent.log JSONL导出
```

## 注意事项

- 备份到D盘确保C盘故障时不丢数据
- watchdog 保留最近5个corrupt文件
- quick 保留最近20个快照
- full 保留最近10个快照
- SHA256 校验保证拷贝完整性
- WAL checkpoint 在每次备份前执行，确保数据持久化

