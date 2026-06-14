# Hermes Telegram 存储设计与 Chat ID 回填

## 框架限制

Hermes state.db 的 `sessions` 表只存 `user_id` (发送者个人 ID)，不存 Telegram 的 `chat_id` (群聊/私聊频道 ID)。

验证：
```sql
SELECT DISTINCT user_id FROM sessions WHERE source = 'telegram';
-- 只返回 5222593199 和 8460135595（两个发送者）
-- 群聊 ID -1003970185743 不在表中
```

`messages` 表的 `platform_message_id` 字段对 Telegram 为 NULL，无法用来区分群聊/私聊。

## 解决方案：解析 agent.log

agent.log 中的 `gateway.run` 日志包含完整的 chat_id：

```
gateway.run: inbound message: platform=telegram user=Leo chat=-1003970185743 msg='...'
gateway.run: inbound message: platform=telegram user=Leo chat=5222593199 msg='...'
gateway.run: response ready: platform=telegram chat=-1003970185743 time=...s
gateway.run: response ready: platform=telegram chat=5222593199 time=...s
```

时间戳格式：agent.log 使用 UTC+8（中国时间），state.db 使用 UTC。解析时需做时区转换。

## 回填策略（三轮）

1. **时间窗匹配**：将 agent.log 中的 `inbound message` / `response ready` 事件与 state.db 消息按时间戳匹配
   - User 消息对应 `inbound message`
   - Assistant 消息对应 `response ready`
   - 时间窗一般取 30 秒

2. **Session 传播**：同一 session 内已知 chat_id 的消息向前/向后传播
   - 正向传播 + 反向传播各一次
   - 解决中间消息无法时间匹配的问题

3. **启发式标签**：含 `@wangna` / `@liufei` / `[Replying to:` 的消息标记为已知群聊 ID
   - 作为最后一道防线

## 覆盖率

实测结果：726/726 Telegram 消息 = **100%**

| chat_id | 消息数 | 类型 |
|---|---|---|
| -1003970185743 | 570 | 群聊 |
| 5222593199 | 156 | 私聊 |

## 维护注意

- agent.log 是追加写日志，不会被清理，但它不是永久存储，仍需定期移动到备份目录
- 若移动到新群聊，需更新 `telegram_chat_enricher.py` 中的启发式标签逻辑
