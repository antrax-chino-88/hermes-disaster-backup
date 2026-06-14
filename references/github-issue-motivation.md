# 三个 GitHub Issue 背景（备份系统设计动机）

Hermes Desktop 存在三个已知未修复的 state.db 崩溃漏洞，构成了这套灾备系统的核心设计动机。

| Issue | 标题 | 状态 | 影响 |
|-------|------|------|-------|
| [#5563](https://github.com/NousResearch/hermes-agent/issues/5563) | state.db Corruption Kills session_search | P1，2026-04-06 至今无回复 | SQLite WAL 写入时进程异常退出，state.db 比项目崩溃，整库不可用 |
| [#226](https://github.com/fathah/hermes-desktop/issues/226) | Session data loss on close (not flushing) | 2026-05-17 至今无回复 | 桌面端正常关闭也不 flush 到磁盘，sessions.json 的 messageCount 严重偏低 |
| [#35201](https://github.com/NousResearch/hermes-agent/issues/35201) | WAL grows to 90+ MB | 未修 | WAL 文件胀膨至 90MB+，长时读事务阻止 auto-checkpoint |

## 根本原因

- `hermes_state.py:715/718` 硬编码 `timeout=1.0`
- WAL 模式 + CLI/gateway/subagent 多进程并发写同一个 state.db
- 抢锁失败即导致 B-tree 损坏
- 已知崩溃模式：文件大小固定 909,312 字节

## 对备份系统的设计启发

1. **不信任单一恢复源** — agent.log 、sqlite3 .recover 、二进制字符串提取全上
2. **多层被动防护** — 30min 会话备份 + 定期 WAL checkpoint + integrity_check
3. **分离存储到 D:盘** — 避免与 state.db 同一磁盘的单点故障
