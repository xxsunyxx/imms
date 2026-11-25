# IMMS 建局装配规范 v0.1

## 0. 目的

用户给一份**现成剧本杀剧本** → 系统凑齐并开局：

- 1 个 GM 主持人 agent（唯一全知、唯一真相写入方）
- N−1 个角色 agent（每个角色一个专家席位，只持有自己的信息与人格）
- 1 个用户席位（用户自己演，不被 AI 代打）

系统**不创作剧本**。剧本由用户提供，系统只做解析、归一化、装配、驱动。

## 1. 输入

`script/` 目录，格式可为 Markdown / 纯文本 / JSON：

- 剧本文本（人物介绍、分幕、对话、结局）
- 角色卡（可选，缺失则从正文抽取）
- 线索表 / 真相 / 真实时间线（GM 专用）
- 必经桥段 `PlotLocks`、结局条件

## 2. 解析产物：Script Ontology

```
Script
├── World            时代 / 地点 / 封闭性 / 规则
├── TruthGraph       真相事实（仅 GM）
├── Timeline_True    客观时间线（仅 GM）
├── Characters[]     → L1 + L2 草案
├── CluePool[]       线索及 logLR
├── PlotLocks[]      必经桥段
└── Endings[]        结局条件
```

解析校验：角色唯一 id、角色数 ≥3、真相与线索一致、每个关键结论有 ≥2 条独立证据路径（F28）。**校验失败只报告，不改剧本**。

## 3. 装配六步

1. **抽取归一化**：每角色 → L1 只读卡（`imms normalize`）。
2. **L2 注入**：公开身份 / 面具 / 任务 / 脚本锁 / 初始关系写入角色 agent。
3. **用户选角**：指定 `human_character_id`；该席位归用户，其余 N−1 生成 AI 角色 agent。
4. **GM 实例化**：装载 TruthGraph / Timeline_True，持有裁决权与节奏调控权。
5. **生成 session.json**：席位名单 + 可见性矩阵 + 初始 L3 状态。
6. **开局**：进入 IMMS §8 状态机（自述 → 搜证 → 圆桌 → 私聊 → 投票 → 复盘）。

## 4. 席位结构（专家团）

| 席位 | kind | 持有信息 | 行为驱动 |
|---|---|---|---|
| GM | `gm` | 全部真相、时间线、线索状态 | 裁决 + 节奏 + 事件注入 |
| AI 角色 | `ai_character` | 自己的 L1/L2/L3、信念图、谎言账本 | 公式引擎 + LLM 台词 |
| 用户角色 | `human_character` | 自己角色的 L1/L2/L3 | 用户输入；系统只做可见性裁剪与护栏 |

每个 AI 角色是一个独立专家 agent，人格由 L1 卡锚定，临场态度由 L3 演化。**角色之间只能读 L4 投影**，读不到彼此 L1/L3 真值。

## 5. Participant 字段

| 字段 | 说明 |
|---|---|
| `id` | `gm:<session>` / `ai:<char_id>` / `human:<char_id>` |
| `kind` | `gm` / `ai_character` / `human_character` |
| `script_character_id` | 对应剧本角色 id |
| `persona_ref` | L1 卡引用（GM 可为 null） |
| `l0` | `difficulty` / `acting_skill` / `lying_skill` / `play_style` |
| `initial_l3` | 初始有向关系与自身状态 |
| `script_locks` | 该席位的脚本锁 |
| `visibility` | 可见性范围；GM 为全量 |

完整校验见 `schemas/session.schema.json`。

## 6. CLI 契约

所有命令用 `--db <file>` 指向 SQLite 数据库；物理表结构见 `docs/STORAGE.md`。

| 命令 | 作用 |
|---|---|
| `imms parse --script <dir> [--out ontology.json]` | 解析用户剧本，Ontology 入库；不改剧本原文 |
| `imms normalize --script-id <id> [--report report.json]` | 抽取 L1 只读卡入库 |
| `imms assemble --script-id <id> --human <char_id>` | 装配 GM + N−1 AI + 1 用户席位 |
| `imms persona show --script-id <id> --char <char_id>` | 查看 L1 卡 |
| `imms seats --session <session_id>` | 查看席位与可见性 |
| `imms audit --session <session_id> --char <char_id> --since N` | 查 L2/L3 变更历史 |

`assemble` 失败码：`ERR_NO_HUMAN_SEAT` / `ERR_CHAR_COUNT` / `ERR_TRUTH_INCONSISTENT` / `ERR_UNSOLVABLE`。

## 7. 写权限（更新）

| 数据 | 写入方 |
|---|---|
| L1 Canon | 归一化管线，一次入库后只读 |
| L2 Assigned | 剧本解析 + GM CLI |
| L3 Emergent | 公式引擎（AI 席位）+ 用户输入（用户席位）+ GM CLI 覆盖 |
| TruthGraph / Timeline_True | 仅 GM，单一写入口 |

## 8. 验收

- [ ] N 个剧本角色 → 1 GM + (N−1) AI + 1 human
- [ ] 每个 AI 席位有 L1 卡 + L2 注入 + L0 参数
- [ ] 用户席位不被 LLM 代打
- [ ] 非 GM 席位读不到 `truth` 可见性数据
- [ ] `session.json` 通过 `schemas/session.schema.json` 校验
- [ ] 剧本原文未被修改（解析只读）
