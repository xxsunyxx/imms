# GM 事件注入库

`src/imms/events.py` 实现原始设定 §8.2 的「多变量外部扰动源」。它是确定性规则层：LLM 不决定触发、不决定数值、不决定真相。

## 七类事件

| event_id | 事件 | 触发变量 | 影响变量 |
|---|---|---|---|
| `second_body` | 第二具尸体 | `case_progress < 0.4` 且 `round_progress > 0.5` | 全体 `fear+0.3`、`stress+0.25`、`alive_count-1` |
| `blackout` | 停电/封锁 | `heat < heat_target - 0.3` | `ap_penalty=-2`、全体 `arousal+0.2` |
| `npcs_letter` | NPC 送来信件 | 关键结论缺证据且有未释放线索 | 释放最高 `logLR` 线索，相关事实写入各席位 `beliefs` |
| `anonymous_tip` | 匿名举报 | 某席位 `exposure_risk > 0.6` | 该席位 `stress+0.4`、`exposure_risk+0.1` |
| `old_flame` | 旧情复燃 | 某对 `intimacy > 0.7` | 双向 `commitment+0.3` |
| `drunken_slip` | 酒后失言 | 某席位 `intoxication > 0.6` | 从该角色**已有 canon 知识**中泄露一条，写入他人 `beliefs` |
| `weather` | 天气/时辰 | 每 3 轮定时 | `clue_decay_multiplier=2` |

`case_progress` = 已释放线索 / 线索池；`round_progress` = 当前轮 / 总轮数。`heat` 与 `heat_target` 复用 F23/F22，并与 GM 决策核读取同一个 `total_rounds`。

## 命令

```bash
# 只看当前局有哪些候选，不落库
uv run imms --db imms.db gm inject --session S --round 2 --dry-run

# 按触发强度自动选一个最高分事件并落库
uv run imms --db imms.db gm inject --session S --round 2

# 强制指定事件（仍需通过该事件的触发条件）
uv run imms --db imms.db gm inject --session S --round 2 --event blackout

# 分叉重放时强制注入
uv run imms --db imms.db gm branch --session S --at-round 2 \
  --inject-event blackout --policy adaptive
```

`runtime._gm_step` 只在 `gm_decision.inject_event` 存在时消费注入；不会被默认回合循环隐式触发，因此旧会话行为不变。

## 落库与审计

- 每次注入写一条 `game_events`，`act_type=gm_event_injection`，payload 含 `event_id / score / effects / applied / changes / variables`。
- L3 数值变更同步写 `attribute_events`，`actor=gm:host`，`source=gm_event`。
- 线索类注入同步 InfoGuard 双侧豁免，并把 `reveals_fact_ids` 写入各席位信念图。
- `retro` 新增 `retrospective.event_injections`：注入次数、按事件统计、每轮强度与变量变更数；Markdown 复盘追加「GM 事件注入审计」。

## 边界

- 没有 `alive_count` 的旧会话按席位人数初始化；死亡只改计数，不删除席位。
- `ap_penalty` / `clue_decay_multiplier` 是局势层变量，写入 `game_state`；现有搜证/线索衰减流程尚未消费它们（属后续 G06/G14）。
- 注入不改写既有事件；分叉中的注入通过快照确定性重放。
