# GM 决策核

`src/imms/gm.py` 把 GM 从 `runtime._gm_step` 里的热度节奏器升级成独立决策层。它保留 `branch.py` 的快照分叉与四项回归校验，并补上分支树、策略、合并回滚和叙事 diff。

## 命令

```bash
# 看某轮 GM 会怎么做（纯读，不落事件）
uv run imms --db s.db gm decide --session S --round 2 --policy adaptive

# 分叉 + 用信息增益策略重放 + 回归 + 登记分支
uv run imms --db s.db gm branch --session S --at-round 2 \
  --label "改口" --override-round 2 --override-utterance "我只相信自己。" \
  --policy information --release-clue "c_will ...|logLR:a=1.5"

uv run imms --db s.db gm tree --session S
uv run imms --db s.db gm diff --branch S-gm001
uv run imms --db s.db gm activate --branch S-gm001
uv run imms --db s.db gm merge --branch S-gm001 --rationale "采纳为反事实基准"
uv run imms --db s.db gm rollback --branch S-gm001
```

## 决策策略

`suggest_decision(conn, session_id, round_no, onto, runtime_state, policy, overrides)` 是纯函数，签名兼容 `runtime.run_session(gm_policy=...)`：

| policy | 行为 |
|---|---|
| `heat` | 沿用旧节奏器：热度低于目标时按 `clue_pool` 顺序放线索 |
| `information` | 总是取信息增益最高的线索，忽略热度 |
| `adaptive` | 热度不足时按信息增益选，达标则不放 |

信息增益 = `Σ logLR 权重 + 2 × (该线索揭示的必需事实 / 必需事实总数)`。策略还输出 `advance_stage`（必需事实是否齐备）与 `private_allowed`（是否开放私聊），并给出 `rationale[]`。

运行时每次调用策略都会写一条 `gm_decision` 事件（可见性 `["gm:host"]`，玩家不可见），`_gm_step` 优先释放策略指定的线索，否则回退到原逻辑。

## 分支树

新增 SQLite 表 `gm_branches`：

| 字段 | 含义 |
|---|---|
| `branch_id` / `session_id` | 主键与所属会话 |
| `parent_id` | 父分支，`root` 表示从主线分叉 |
| `fork_round` | 分叉轮次（第 k 轮 = 恢复第 k−1 轮末快照） |
| `role` | `branch` / `canonical` |
| `status` | `active` / `merged` / `rolled_back` |
| `decision_json` | GM 策略与强制线索等决策规格 |
| `report_json` | 回归报告 + 叙事 diff |

**分支状态不落库**：分支是一次决策规格 + 确定性重放的结果，不是世界的第二份拷贝。这保持事件溯源语义，也避免把内存库写回主库时污染历史。

`merge` / `activate` / `rollback` 都写 `gm_merge` / `gm_activate` / `gm_rollback` 事件，不改写既有事件。`rollback` 把 canonical 指回父分支；父分支为 `root` 时清空 canonical 标记。

## 叙事 diff

`narrative_diff()` 对比主线会话与分叉会话：
- `clue_changes`：线索释放集合与顺序差异（added / removed / reordered）
- `utterance_changes`：按 `(round, actor)` 对齐的台词差异
- `suspicion_changes`：按轮次对比每个席位的最高怀疑对象
- `outcome`：赢家、是否破案、是否翻转

回归仍沿用 `branch.regress()` 的四项校验（数值域 / 信息隔离 / 事实已知 / 投票机制合法）。实测：把策略从 `heat` 换成 `information` 并让玩家改口，末局赢家从 `ai:zhou_muchen` 变成 `human:lin_wanqiu`，diff 记录了 2 条线索变化、5 条台词变化、3 条怀疑变化。
