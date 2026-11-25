# IMMS 对局引擎 v0.1

## 0. 定位

`imms play` 让装配好的 GM + AI 角色 + 用户席位实际跑一局。内核仍是**确定性数值引擎**：意图、目标、事实声明与所有数值都由公式层决定；LLM 只写台词和表现情绪，失败即回退模板，公式层不动。

## 1. 回合循环

```
初始化 runtime（L1 + L2/L3 + 初始关系/情绪/信念）
→ 每轮：
    感知上一轮可见事件（可见性过滤）
    评价 → 关系 F02 / 情绪 F07 / 怀疑 F12
    意图生成 → 效用 F24 → softmax F25 → 选定动作
    三重护栏（知识 / 谎言账本 / 禁造事实）
    落 game_events → GM 节奏 F22/F23（必要时释放线索）
→ 终幕投票阶段（F16 + 规则/账本/审计）→ 与真相比对 → 写 result
```

## 2. GM 与用户席位

- GM：唯一装载 `TruthGraph` / `Timeline_True`；按热度曲线释放线索或插入休整；不生成台词事实。
- AI 角色：由公式 + 模板驱动，扮演独立专家席位。
- 用户席位：**只接受用户输入**，不由 LLM 代打。`--scripted` 提供测试用发言；交互式运行用 stdin。

## 3. 落库

| 表 | 内容 |
|---|---|
| `game_events` | 每轮 SpeechAct（含 `inner_intent`，仅复盘可见）；私聊事件共用全局 `seq` |
| `game_state` | `runtime` / `round` / `revealed_clues` / `user_input_index` / `result` / `vote_state` / `llm_stats` |
| `private_rooms` | 私聊房间的席位、轮次、开关顺序 |

`inner_intent` 与 `utterance` 分离存储，支撑复盘时展示"内心独白"。公式层产生的显著 L3 变化（`|Δ|≥0.05`）也写入 `attribute_events`，供复盘排序转折点。

## 4. 命令

```bash
uv run imms --db imms.db play --session snow-night-villa-s001 --rounds 2 \
  --scripted examples/scripts/user_script.json --seed 42

# 投票阶段可独立运行/审计
uv run imms --db imms.db vote replay --session snow-night-villa-s001
uv run imms --db imms.db vote audit --session snow-night-villa-s001
```

投票规则、多轮改票、AI 选票策略与票据格式见 `docs/VOTE.md`。`scripted.vote` 继续兼容；`scripted.votes` + `vote_config` 可配置复投。

## 5. LLM 台词层（可插拔、不碰数值）

`src/imms/llm.py` 提供 `template`（离线默认）与 `http`（OpenAI-compatible / Ollama）两种 provider。

```bash
# 默认 auto：未设 IMMS_LLM_URL 时用模板，设了则走 HTTP
export IMMS_LLM_URL=http://localhost:11434/v1/chat/completions
export IMMS_LLM_MODEL=qwen2.5:7b
uv run imms --db imms.db play --session snow-night-villa-s001 --rounds 2 \
  --scripted examples/scripts/user_script.json --llm auto
```

- Prompt 分块注入 L1/L2/L3、信念图、谎言账本、本轮意图；`is_true` 等 GM 真值字段不进 Prompt。
- 输出只接受 `utterance` / `emotion_shown` / `claimed_facts`；`claimed_facts` 被裁剪到持有者已知集合，`act_type` 强制等于公式层选定的意图。
- JSON 解析失败、超时、端点不可达 → 回退模板，记入 `llm_stats` 与事件日志；不中断对局。

## 6. 私聊房间

```bash
uv run imms --db imms.db private open --session snow-night-villa-s001 --round 3 \
  --seats human:lin_wanqiu,ai:gu_chengye
uv run imms --db imms.db private say --session snow-night-villa-s001 \
  --room snow-night-villa-s001-p001 --actor human:lin_wanqiu --to ai:gu_chengye \
  --utterance "账房的灯熄得早。"
uv run imms --db imms.db private list --session snow-night-villa-s001
uv run imms --db imms.db private close --session snow-night-villa-s001 --room snow-night-villa-s001-p001
```

- 所有发言（公开/私聊/开房/关房）共用 `game_events.seq`（AUTOINCREMENT），按全局序消费，避免竞态错序。
- 可见性 token `room:<id>` 由 `private_rooms.participants_json` 解析；房间外角色读不到，GM 全见。
- 非成员发言 `ERR_NOT_ROOM_MEMBER`；关闭后发言 `ERR_ROOM_CLOSED`；少于 2 席不开房。

## 7. 复盘

```bash
uv run imms --db imms.db replay --session snow-night-villa-s001 --out replay.md
```

五段：真相时间线、角色内心独白、关键转折点 Top 5、谎言全景图、反事实推演。反事实用已记录的 `logLR` 重算怀疑度与投票分布，不重放搜证/私聊，故为近似推演。

## 8. GM 分叉回归（核心能力）

**GM 的难点不是把话说漂亮，而是内容分叉后能否回归。** 用户改一轮决策、GM 注入一个事件，系统必须精确恢复当时状态、重跑后续、并校验全局仍然成立。

```bash
uv run imms --db imms.db branch fork --session snow-night-villa-s001 --at-round 2 \
  --override-round 2 --override-utterance "我承认，我偷看过遗嘱草稿。" \
  --seed 42 --out branch_report.json
```

- **精确恢复**：每轮末把每个席位的 runtime 与 `@global`（已释放线索、用户输入索引、轮次）写入 `state_snapshots`。`--at-round k` 恢复第 `k-1` 轮末快照，从该边界续跑。
- **不污染原局**：分叉在内存 SQLite 副本中执行；原会话的事件、快照、结果一字不改。
- **真实重放**：与 §7 的"数值近似反事实"不同，这里重新走完整的评价→关系/怀疑更新→效用→护栏→GM 节奏→投票链路。
- **回归校验**（`checks`）：`bounded`（关系/情绪不越界）、`info_isolation`（私聊仍仅房间可见）、`claims_known`（无人声明未知事实）、`solvable`（分叉局仍可解）。
- **差异报告**（`divergence`）：原局/分叉局的赢家、是否破案、结局是否翻转。`verdict` ∈ `pass` / `changed` / `fail`。

典型用途：用户复盘时问"若第 3 轮我选择相信他"，直接分叉跑出另一条时间线，而不是靠模型口述推测。也可作为剧本平衡性回归：批量分叉后统计 `fail` 比例。

## 9. InfoGuard 信息边界

从 lockedroom-ai 移植的四层信息隔离引擎（`src/imms/infoguard.py`，详见 `docs/INFOGUARD.md`）：

- L1 `compile_scopes`：每个角色的 `known = public ∪ known_fact_ids`，`forbidden` 为全集补集。
- L2 `LeakDetector`：AI 台词里连续 6 字原文 = 硬泄露，关键词 = 软命中。
- L2b `LeakJudge`：仅 `--llm http` 时启用；裁决失败按 `confirmed`（从严改写）。
- L3 `ContextGuard`：输入侧不拒绝，把不可知事实包成「玩家声称…无法证实」的传闻。

```bash
uv run imms --db imms.db infoguard scopes --session snow-night-villa-s001
uv run imms --db imms.db infoguard check --session snow-night-villa-s001 \
  --char gu_chengye --text "死者死于过量胰岛素注射"
```

`play` 返回 `infoguard` 统计（输入包裹/输出改写/标记/judge 调用）；命中写进 `game_events.payload_json.guard`。GM 释放线索时同步豁免 `reveals_fact_ids`，避免把已公开事实当剧透。人类席位不受输出护栏约束。

## 10. 边界

- 不做语音、不做 Web UI；LLM 端点需用户自行提供。
- RUNTIME 公式见 `docs/FORMULAS.md` F01–F27；纯函数实现见 `src/imms/formulas.py`。
- 投票目标经席位表解析（角色 id / 中文名 / `ai:` / `human:` 均可）；真相比对优先用 `evaluation.killer_id`，正式剧本应显式给出，不要依赖 `TruthGraph` 文本回退。
- 分叉重放使用与基础局相同的角色台词 provider；`--llm http` 时端点需可用，否则回退模板。
