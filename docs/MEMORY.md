# 记忆账本与承诺状态机

`src/imms/memory.py` 把原先只写 `{round, act, importance}` 的 `state["memory"]` 升级为**听众感知账本**，并让 F26 记忆检索公式（`formulas.memory_score`，见 `docs/FORMULAS.md` §F26）第一次真正进入运行链路。

> 注：早期任务清单里把记忆检索称作 F30，实际公式登记表中 F30 是派生能力、F26 才是记忆检索。实现以 `formulas.memory_score` 为准。

## 数据结构

每条记忆记录：

| 字段 | 含义 |
|---|---|
| `seq` | 事件全局序号，用于去重（运行时每轮都会重读旧事件） |
| `round` | 发生轮次 |
| `speaker` | 发言席位 id |
| `audience` | 实际听到该话的席位 id；公开事件为其余全体，私聊为房间成员 |
| `act_type` / `utterance` | 事件类型与最终台词（InfoGuard 改写后的版本） |
| `claimed_facts` | 最终被保留的事实主张 |
| `status` | `stated` / `revised` / `weakened` / `retracted` |
| `importance` | 由动作类型、主张数、是否指人、是否本人发言决定，范围 0–1 |
| `emotion_snapshot` | 记录当时的 PAD，用于情绪一致性 |
| `self` | 是否是本人自己的发言 |

承诺账本 `state["commitments"]` 按事实 id 记录 `status` + `history[]`，每次状态变化都留下轮次与事件序号。

## 状态机

```
stated ─→ revised ─→ retracted
  │         ↑           │
  └──→ weakened ────────┘
```

- `revise` 类措辞（「更正」「其实」「我刚才说错」）把已有主张改为 `revised`。
- `retract` 类措辞（「我撤回」「收回刚才」「那句作废」）改为终态 `retracted`；没有显式 `claimed_facts` 时，撤回该发言人最近一条仍有效的主张。
- `deflect` / 「记不清」/「不确定」改为 `weakened`，仍可再改口或撤回。
- `retracted` 是不可逆终态，后续 `revise` 被忽略。

补全只依赖事件日志里已经出现的台词和主张，不生成新事实；本模块不判定台词真假，那是 InfoGuard 与 solver 的职责。

## F26 召回如何被消费

`memory.recall(state, current_round, cue, limit, tau_m)` 对每条记忆计算：

```
score = exp(-age/tau_m) + importance + cosine(cue, utterance+claims) + emotion_congruence
```

四项分别对应 `formulas.memory_score` 的 α/β/γ/δ 权重（默认均为 1）。`cosine` 是确定性的中英文 token 重合率，`emotion_congruence` 是当时 PAD 与当前 PAD 的接近度；都不调用 LLM。

消费点：

1. **Prompt**：`llm.build_prompt(..., recalled_lines=...)` 新增「记忆召回（F26）」段，只注入已记录的旧话并标注状态；Prompt 同时强调不得据此新增事实。
2. **事件载荷**：每条 public 发言的 `payload["recalled"]` 记录本轮召回 Top3，离线可审计。
3. **GM 分支 diff**：`gm.narrative_diff` 比较逐席位快照的 `memory` 与 `commitments`，输出 `memory_changes`（added / removed / changed / commitment / recall_top）并计入 `summary.memory_changes`。

## CLI

```bash
imms --db imms.db memory show --session S --seat ai:gu_chengye --round 1
imms --db imms.db memory show --session S --seat ai:gu_chengye --cue "书房 争吵" --limit 3
imms --db imms.db memory commitments --session S --seat human:lin_wanqiu
```

## 边界

- 私聊只在房间成员的内存里留下记录；`private_say` 事件本身仍按 `room:` 可见性隔离。
- 人类玩家可以声称任何事；`claimed_facts` 只在通过 `_guard` 后才进入记忆与承诺账本。
- 记忆会随快照进入分叉回归，因此分支的记忆差异是重放结果，不是主会话被改写。
- 召回影响 Prompt 与票型审计的输入，不改变意图选择或任何数值公式的确定性输出。
