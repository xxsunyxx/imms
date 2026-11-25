# IMMS 对局引擎 v0.1

## 0. 定位

`imms play` 让装配好的 GM + AI 角色 + 用户席位实际跑一局。本版是**确定性无模型内核**：台词用模板，意图由效用函数选出，数值全部走 `formula.py`。后续可把模板替换为 LLM 台词生成，公式层不动。

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
| `game_events` | 每轮 SpeechAct（含 `inner_intent`，仅复盘可见） |
| `game_state` | `runtime` / `round` / `revealed_clues` / `user_input_index` / `result` |

`inner_intent` 与 `utterance` 分离存储，支撑复盘时展示"内心独白"。

## 4. 命令

```bash
uv run imms --db imms.db play --session 雨夜山庄-s001 --rounds 2 \
  --scripted examples/sample_script/user_script.json --seed 42
```

## 5. 边界

- 本版不接 LLM、不做语音、不实现私聊并发竞态。
- RUNTIME 公式见 `docs/FORMULAS.md` F01–F27；纯函数实现见 `src/imms/formulas.py`。
- 投票真相比对从 Ontology 的 `TruthGraph` 文本推断角色名；正式剧本应显式写 `culprit` 字段。
