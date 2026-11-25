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
→ 终幕投票 F16 → 与真相比对 → 写 result
```

## 2. GM 与用户席位

- GM：唯一装载 `TruthGraph` / `Timeline_True`；按热度曲线释放线索或插入休整；不生成台词事实。
- AI 角色：由公式 + 模板驱动，扮演独立专家席位。
- 用户席位：**只接受用户输入**，不由 LLM 代打。`--scripted` 提供测试用发言；交互式运行用 stdin。

## 3. 落库

| 表 | 内容 |
|---|---|
| `game_events` | 每轮 SpeechAct（含 `inner_intent`，仅复盘可见）；私聊事件共用全局 `seq` |
| `game_state` | `runtime` / `round` / `revealed_clues` / `user_input_index` / `result` / `llm_stats` |
| `private_rooms` | 私聊房间的席位、轮次、开关顺序 |

`inner_intent` 与 `utterance` 分离存储，支撑复盘时展示"内心独白"。公式层产生的显著 L3 变化（`|Δ|≥0.05`）也写入 `attribute_events`，供复盘排序转折点。

## 4. 命令

```bash
uv run imms --db imms.db play --session 雨夜山庄-s001 --rounds 2 \
  --scripted examples/sample_script/user_script.json --seed 42
```

## 5. LLM 台词层（可插拔、不碰数值）

`src/imms/llm.py` 提供 `template`（离线默认）与 `http`（OpenAI-compatible / Ollama）两种 provider。

```bash
# 默认 auto：未设 IMMS_LLM_URL 时用模板，设了则走 HTTP
export IMMS_LLM_URL=http://localhost:11434/v1/chat/completions
export IMMS_LLM_MODEL=qwen2.5:7b
uv run imms --db imms.db play --session 雨夜山庄-s001 --rounds 2 \
  --scripted examples/sample_script/user_script.json --llm auto
```

- Prompt 分块注入 L1/L2/L3、信念图、谎言账本、本轮意图；`is_true` 等 GM 真值字段不进 Prompt。
- 输出只接受 `utterance` / `emotion_shown` / `claimed_facts`；`claimed_facts` 被裁剪到持有者已知集合，`act_type` 强制等于公式层选定的意图。
- JSON 解析失败、超时、端点不可达 → 回退模板，记入 `llm_stats` 与事件日志；不中断对局。

## 6. 私聊房间

```bash
uv run imms --db imms.db private open --session 雨夜山庄-s001 --round 3 \
  --seats human:char_01,ai:char_02
uv run imms --db imms.db private say --session 雨夜山庄-s001 \
  --room 雨夜山庄-s001-p001 --actor human:char_01 --to ai:char_02 \
  --utterance "账房的灯熄得早。"
uv run imms --db imms.db private list --session 雨夜山庄-s001
uv run imms --db imms.db private close --session 雨夜山庄-s001 --room 雨夜山庄-s001-p001
```

- 所有发言（公开/私聊/开房/关房）共用 `game_events.seq`（AUTOINCREMENT），按全局序消费，避免竞态错序。
- 可见性 token `room:<id>` 由 `private_rooms.participants_json` 解析；房间外角色读不到，GM 全见。
- 非成员发言 `ERR_NOT_ROOM_MEMBER`；关闭后发言 `ERR_ROOM_CLOSED`；少于 2 席不开房。

## 7. 复盘

```bash
uv run imms --db imms.db replay --session 雨夜山庄-s001 --out replay.md
```

五段：真相时间线、角色内心独白、关键转折点 Top 5、谎言全景图、反事实推演。反事实用已记录的 `logLR` 重算怀疑度与投票分布，不重放搜证/私聊，故为近似推演。

## 8. GM 分叉回归（核心能力）

**GM 的难点不是把话说漂亮，而是内容分叉后能否回归。** 用户改一轮决策、GM 注入一个事件，系统必须精确恢复当时状态、重跑后续、并校验全局仍然成立。

```bash
uv run imms --db imms.db branch fork --session 雨夜山庄-s001 --at-round 2 \
  --override-round 2 --override-utterance "我相信赵管事，账房的灯熄得早与我无关。" \
  --seed 42 --out branch_report.json
```

- **精确恢复**：每轮末把每个席位的 runtime 与 `@global`（已释放线索、用户输入索引、轮次）写入 `state_snapshots`。`--at-round k` 恢复第 `k-1` 轮末快照，从该边界续跑。
- **不污染原局**：分叉在内存 SQLite 副本中执行；原会话的事件、快照、结果一字不改。
- **真实重放**：与 §7 的"数值近似反事实"不同，这里重新走完整的评价→关系/怀疑更新→效用→护栏→GM 节奏→投票链路。
- **回归校验**（`checks`）：`bounded`（关系/情绪不越界）、`info_isolation`（私聊仍仅房间可见）、`claims_known`（无人声明未知事实）、`solvable`（分叉局仍可解）。
- **差异报告**（`divergence`）：原局/分叉局的赢家、是否破案、结局是否翻转。`verdict` ∈ `pass` / `changed` / `fail`。

典型用途：用户复盘时问"若第 3 轮我选择相信他"，直接分叉跑出另一条时间线，而不是靠模型口述推测。也可作为剧本平衡性回归：批量分叉后统计 `fail` 比例。

## 9. 边界

- 不做语音、不做 Web UI；LLM 端点需用户自行提供。
- RUNTIME 公式见 `docs/FORMULAS.md` F01–F27；纯函数实现见 `src/imms/formulas.py`。
- 投票真相比对从 Ontology 的 `TruthGraph` 文本推断角色名；正式剧本应显式写 `culprit` 字段。
- 分叉重放使用与基础局相同的角色台词 provider；`--llm http` 时端点需可用，否则回退模板。
