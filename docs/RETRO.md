# 复盘回溯评估

`src/imms/retro.py` 把复盘从"五段式叙事文本"升级为"叙事 + 可计算的回溯评估"。量化指标沿用 lockedroom-ai `replayeval/metrics.py`；锁仓库没有的决策时点复核、转折点归因、时间线检查与分支对照由 IMMS 补上。

## 命令

```bash
uv run imms --db s.db retro --session S --out retro.json --markdown retro.md
uv run imms --db s.db replay --session S --with-eval --out full_replay.md
```

`replay --with-eval` 在原有五段后追加 `## 6. 回溯评估`。

## 指标（沿用 lockedroom-ai 公式）

| 字段 | 计算 |
|---|---|
| `leak_rate` | `(hard × 1.0 + soft × 0.3) / AI 发言数` |
| `clue_coverage` | `已解锁线索揭示的 facts ∩ required_fact_ids / |required|` |
| `killer_correct` | 本局投票是否命中 `evaluation.killer_id` |
| `motive_correct` | 指认的 `motive_fact_id` 是否等于剧本动机事实 |
| `score` | `max(0, 100 × (0.45×coverage + 0.35×killer + 0.20×motive) − 20×leak_rate)` |
| `grade` | `≥90 S / ≥75 A / ≥60 B / 其他 C` |
| `difficulty` | `min(10, |required| + |可扮演角色|×0.5 + |private facts|×0.2)` |

`motive_correct` 需要投票时提供动机事实。运行时 `_vote` 会把 `scripted.motive_fact_id` 记入 `result`；CLI 的 `retro --session` 也支持在脚本里带该字段。没有动机指认时该项为 `false` 并如实影响评分，不替玩家补判。

## IMMS 扩展的回溯维度

- **决策时点信息边界复核**：按事件顺序重建 InfoGuard 引擎，逐轮 `exempt_clue`，对每条 AI 台词离线重检；同时核对 `claimed_facts` 在该角色当时是否可获知。这与运行时护栏相互独立，可作为回归基线。
- **转折点归因**：从 `attribute_events` 的 L3 变化取 Top 5，给出轮次、角色、维度、before/after 与原因。
- **可解性**：内嵌 `solver.audit_script()` 的 verdict 与 checks。
- **证据指向强度**：最高 logLR 指向与次高之差；`≥0.5` 且唯一才判 `decisive`。这是静态估计，不是重放。
- **真实分支对照**：读 `gm_branches` 里 `active`/`merged` 分支的 diff，列出线索变化数、台词变化数、结局是否翻转及赢家迁移。静态强度与真实分支并列，不混为一谈。
- **时间线检查**：来自 solver 的 `order_conflicts` 与 `warnings`。
- **投票审计**：`vote.audit()` 汇总合法票率、弃票率、自我投票拦截、非法目标、证据支持率、平票裁决与动机记录；挂在 `retrospective.vote_audit`，不改变 lockedroom 5 指标评分公式。

## 边界

- 指标只从落盘事件计算，不重放台词生成，因此不受 LLM 随机性影响。
- `leak_rate` 的分母只数 AI 公开回合发言；私聊与 GM 事件不计入，与 lockedroom-ai 的口径一致。
- 离线信息复核使用当前 Ontology 的静态 scope；如果剧本在事后被重新归一化，复核结果可能不同于当时运行时，报告里应结合事件中的 `guard` 字段一起读。
- 不移植 lockedroom-ai 的 LLM 推理链（`chain.py`）：它产出的 `suspect_trajectory` / `key_insights` 是定性文本，不参与评分，且会引入不可复现性。转折点归因用数值审计替代。
