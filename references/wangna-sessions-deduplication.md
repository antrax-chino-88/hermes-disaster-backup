# Wangna Profile Sessions 表去重问题

## 现象

2026-06-14 备份测试时，wangna profile 的 `messages` 表有 5579 行，但 `LEFT JOIN sessions` 后输出 172,481 行（膨胀 ~30 倍）。

## 根因

`state.db` 的 `sessions` 表存在大量重复 `id`：

```sql
-- wangna state.db
SELECT COUNT(*) FROM sessions;           -- 1330 行
SELECT COUNT(DISTINCT id) FROM sessions; -- 58 个
```

即 1330 行仅 58 个不同 id，导致 `m.session_id = s.id` 一对多匹配。
Default profile 无此问题（36 行 / 36 个 distinct id）。

## 修复

备份查询使用子查询强制每个 session_id 只匹配一行：

```sql
SELECT m.id, m.session_id, m.role, m.content, m.timestamp,
       m.tool_name, m.tool_calls, m.finish_reason,
       s.source, s.title
FROM messages m
LEFT JOIN (SELECT id, source, title FROM sessions GROUP BY id) s
    ON m.session_id = s.id
ORDER BY m.timestamp
```

注意：SQLite 的 `SELECT DISTINCT id, source, title FROM sessions` 在多列时不一定去重（测试中仍产生膨胀）。必须用 `GROUP BY id` 才能确保每个 id 只返回一行。

## 快速诊断

```python
import sqlite3
conn = sqlite3.connect(r'C:\Users\admin\AppData\Local\hermes\profiles\wangna\state.db')
c = conn.cursor()
c.execute('SELECT COUNT(*) FROM sessions')
total = c.fetchone()[0]
c.execute('SELECT COUNT(DISTINCT id) FROM sessions')
distinct = c.fetchone()[0]
print(f'总行: {total}, 不同 id: {distinct}, 重复率: {total/max(distinct,1):.1f}x')
conn.close()
```

## 影响范围

- 仅影响 wangna profile（sessions 表重复）
- 不影响备份脚本的正确性（已修复）
- 恢复脚本无需修改（它只读 JSONL，不直接读 state.db）
