# 剧本管理与骨架夹具

## 1. 为什么要骨架

规范化解决"数值/属性怎么存"，但输入端的混乱是另一类问题：同一条真相散在角色独白、GM 手册、线索卡和线索池里。直接把原文塞进 Ontology 会出现三种漂移：

1. LLM 顺手编一个人物或时间；
2. 线索指向一个来源里不存在的"事实 id"；
3. 本来就缺时间线，却被补成"看起来完整"的剧情。

所以输入先过**固定槽位夹具**，再归一化。

## 2. 槽位定义

`skeleton_version = 1.0`，固定九个槽位（`src/imms/skeleton.py` 的 `SLOTS`）：

| 槽位 | 内容 | 来源 |
|---|---|---|
| `identity` | script_id / title / brief / source_kind | 确定性 |
| `world` | mode / stages / player_selectable | 确定性 |
| `participants` | id / name / public_identity / is_victim / is_suspect | 确定性 |
| `truth` | summary / facts[] | LLM 可摘要 summary |
| `timeline` | events[] | 确定性；缺失留 gap |
| `clues` | id / content / stage / reveals[] | 确定性 |
| `locks` | items[] | 确定性 |
| `endings` | correct / incorrect | LLM 可摘要，必须源自原文 |
| `evaluation` | killer / victim / required_facts / role_kind | 确定性补全 |

额外字段 `gaps[]`（未补齐的槽位）、`completions[]`（用了哪条规则）、`llm`（provider / accepted / rejected）。

## 3. LLM 的边界

`ingest skeleton/convert/add/batch --llm auto|template|http`：

- `template`：不调用网络，保留确定性骨架；
- `http`：OpenAI 兼容 / Ollama 端点，沿用 `llm.HttpProvider`；
- `auto`：配置了 `IMMS_LLM_URL` 才走 http，否则 template。

LLM 只允许写 `identity.brief`、`truth.summary`、`endings.*`，并且每一句都必须能在原文里逐字找到（去空白后子串匹配）。以下情况直接丢弃并写入 `llm.rejected`：

- 未声明的槽位 / 人物 id / 线索 id / 事实 id；
- 原文中不存在的句子；
- 返回的不是 JSON。

**LLM 不生成数值、关系、嫌疑、时间、真相 id。** 这些仍由确定性引擎和公式层负责。

## 4. 剧本管理

SQLite 新增三张表（见 `docs/STORAGE.md`）：

- `script_registry`：一个剧本一行，记录来源指纹、骨架版本、Ontology 版本、生命周期状态；
- `script_skeletons`：骨架按版本追加，不覆盖旧版；
- `script_events`：注册、骨架保存、状态迁移的事件日志。

生命周期：

```
staged → normalized → ready → archived
   └──────→ rejected → staged（重新摄入）
```

非法跃迁（例如 `ready → staged`）抛 `ERR_STATUS_TRANSITION`。

## 5. 命令

```bash
uv run imms script list --status ready
uv run imms script show --script-id rpg_last_train
uv run imms script skeleton --script-id rpg_last_train --version 1
uv run imms script history --script-id rpg_last_train
uv run imms script promote --script-id rpg_last_train --status ready --notes "audit passed"
```

`imms parse` 和 `imms import-corpus` 会自动注册剧本并保存骨架；后者生成 L1 后把状态推进到 `normalized`。

## 6. 与下游的关系

```
乱输入 → 夹具骨架（LLM 可摘要）→ 证据补全 → Ontology → normalize(L1) → 对局 → solver / retro
```

骨架不替代求解器：`gaps[]` 只是"来源没有"，`audit-script` 才判断"是否真能唯一解"。两者一起构成准入和可玩性证明。
