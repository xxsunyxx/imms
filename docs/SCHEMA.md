# IMMS 建局装配后端 · Schema v0.1

状态：草案。输入是用户提供的现成剧本；系统不写剧本，只做解析、归一化，并装配 GM + AI 角色 + 用户席位后开局。无 Web UI。装配流程见 `docs/ASSEMBLY.md`。

已确认决策：

- L1 一次采集归一化后只读入库。
- L2/L3 由主持人通过 CLI 变更，每次变更留审计事件。
- 题材无关：标准键 + 可配置别名/锚点/派生公式，不绑具体语料。
- 剧本由用户提供；系统只解析，不生成。
- GM 是唯一真相写入方；用户席位不被 AI 代打。

## 1. 分层与写权限

| 层 | 语义 | 写入方 | 可变 | 存储约束 |
|---|---|---|---|---|
| L1 Canon | 用户剧本抽取的客观属性 | 归一化管线 | 否 | 只读视图 `persona_l1_ro`；无 UPDATE/DELETE 授权；变更=新版本行 |
| L2 Assigned | 剧本赋予的公开身份、面具、任务、脚本锁 | 剧本导入 + GM CLI | 是 | 每次变更写 `attribute_events` |
| L3 Emergent | 对局中形成的态度与自身状态 | 公式引擎 + GM CLI | 是 | 轮末快照；GM 可 `--lock` 暂停公式覆盖 |

派生能力不入库为独立事实，读取时由 `derivation` 计算。

## 2. 统一属性信封

每个属性都是同一信封，层差异只体现在 `layer / scope / source / derivation`。

```json
{
  "key": "trust",
  "value": 0.35,
  "layer": "L3",
  "scope": "dyadic",
  "target": "char_05",
  "domain": [-1, 1],
  "visibility": "self",
  "confidence": 0.7,
  "source": "runtime_formula",
  "evidence": [
    {"source_id": "ev_014", "quote": "...", "locator": "ch3:p2", "weight": 0.8, "anchor_bin": 0.5}
  ],
  "derivation": {
    "rule": "R_trust_v1",
    "inputs": {"before": 0.30, "beta": 0.25, "eta": 1.0, "kappa": 1.24, "nu": 1.16}
  },
  "volatility": 0.25,
  "decay": {"lambda": 0.95, "baseline": 0.0, "tau_rounds": 8},
  "updated_at_round": 4,
  "updated_by": "gm_cli:host",
  "locked": false,
  "reason": "主持人裁定 char_05 本轮未兑现承诺"
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `key` | 是 | 标准键，小写下划线；见 §3 |
| `value` | 是 | 数值/枚举/对象；须落在 `domain` 内 |
| `layer` | 是 | `L1` / `L2` / `L3` |
| `scope` | 否 | `self` / `dyadic` / `global`，默认 `self` |
| `target` | 条件 | `scope=dyadic` 时必填，指向对方 agent id |
| `domain` | 是 | `[min,max]` 或 `enum` / `list` / `object` / `text` |
| `visibility` | 是 | 见 §4 |
| `confidence` | 是 | `[0,1]`，持有者确信度；可与真值不符 |
| `source` | 是 | `canon_extract` / `script` / `player_input` / `history_infer` / `gm_cli` / `runtime_formula` |
| `evidence` | 是 | 证据链；无证据时为空数组并带 `reason` |
| `derivation` | 否 | 计算规则 + 输入快照，供审计与失效判定 |
| `volatility` | 否 | 易变性，影响更新步长 |
| `decay` | 否 | `lambda` / `baseline` / `tau_rounds` |
| `updated_at_round` | 是 | 最后变更轮次 |
| `updated_by` | 是 | `pipeline:vN` / `gm_cli:<host>` / `engine` |
| `locked` | 是 | L1 恒为 `true`；L2/L3 由 GM 锁定 |
| `reason` | 条件 | `source=gm_cli` 时必填 |

完整机器校验见 `schemas/attribute.schema.json`。公式全集见 `docs/FORMULAS.md`；可编辑规则表见 `config/`。

## 3. 标准键字典（题材无关）

### 3.1 L1 Canon

| 组 | 键 | 域 | 备注 |
|---|---|---|---|
| 身份 | `origin_name` / `lineage` / `social_rank` | text / text / 0–10 | 客观出身 |
| 生理 | `age` / `health` / `physique` / `marks[]` | int / [0,1] / [0,1] / list | `marks` 可作证据 |
| 能力 | `skill_set.*` | 0–10 | 标准子键：`observation` `reasoning` `eloquence` `craft` `combat` |
| 心理 | `OCEAN.O/C/E/A/N` | [0,1] | 五维，证据化打分 |
| 价值 | `values.loyalty/justice/ambition/honesty/prudence/empathy` | [0,1] | 效用权重来源 |
| 创伤 | `trauma[]` | list of `{trigger, effect}` | 情绪雷区 |
| 秘密 | `secrets[]` | list of `{fact_id, exposure_cost}` | 核心动机 |
| 语言 | `lexicon` | object | `catchphrases[]` / `rhythm` / `syntax` / `taboo_words[]` |
| 关系史 | `history_relations[]` | list of `{target, like, trust, dominance}` | 初始先验 |

### 3.2 L2 Assigned

| 键 | 域 | 可见性 |
|---|---|---|
| `public_identity` | enum/text | public |
| `mask` | object（OCEAN/values 同构） | 表现可见 |
| `assigned_goals[]` | list of `{goal_id, weight, deadline_round}` | self |
| `initial_relations` | map target → 关系子键 | 部分 |
| `script_locks[]` | list of `{type, content}`，type ∈ must_say / must_trigger / taboo | self + gm |
| `alibi_template` | object `{time_window, location, witnesses}` | self |
| `arc` | object `{from, to, progress}` | gm |
| `faction_init` | enum | faction |

### 3.3 L3 Emergent

有向关系（11，`scope=dyadic`）：

`like` `trust` `respect` `intimacy` `suspicion` `fear` `envy` `grudge` `debt` `dominance` `commitment`

自身状态（8，`scope=self`）：

`valence` `arousal` `pad_dominance` `stress` `cognitive_load` `intoxication` `morale` `exposure_risk`

> 注意：`dominance` 是关系维度，`pad_dominance` 是 PAD 自身维度，不可混用。

## 4. 可见性

| 值 | 含义 | 谁可读 |
|---|---|---|
| `public` | 全场可知 | 所有角色 + GM |
| `faction` | 阵营内 | 同阵营 + GM |
| `self` | 仅自己 | 持有者 + GM |
| `gm` | 仅主持人 | GM |
| `truth` | 真相 | GM 写入，仅复盘开放 |

其他角色的感知只读 L4 投影；L1/L3 真值不可跨角色直读。

## 5. L1 只读与变更审计

- DB：角色 `imms_writer` 对 `persona_l1` 无 UPDATE/DELETE；应用只通过 `persona_l1_ro` 读。
- CLI：对 L1 键执行 `assigned set` / `emergent set` → `ERR_L1_IMMUTABLE`。
- 重新抽取产生新 `extraction_version`；旧版本行保留；生效指针仅由迁移任务更新。
- 每次 L2/L3 变更追加事件：

```json
{
  "event_id": "evt_0091",
  "session_id": "sess_01",
  "round": 3,
  "seq": 910,
  "actor": "gm_cli:host",
  "layer": "L2",
  "key": "public_identity",
  "target": null,
  "before": "账房先生",
  "after": "贾府管事",
  "reason": "第二幕身份揭示"
}
```

## 6. 最小存储集合

| 集合 | 用途 | 写权限 |
|---|---|---|
| `persona_l1` | L1 信封，append-only 版本行 | 归一化管线 |
| `persona_l2_l3` | 当前生效的 L2/L3 信封 | GM CLI / 公式引擎 |
| `attribute_events` | 全部变更审计 | 所有写入方 |
| `state_snapshots` | 轮末物化快照 | 公式引擎 |
| `config_aliases` / `config_anchors` / `config_derivations` | 归一化规则 | 管理员 |

物理实现为单一 SQLite 文件，表结构与 L1 只读触发器见 `docs/STORAGE.md`。

## 7. 验收清单

- [ ] 每个 L1 属性有 evidence，或为 `null` 且带 `reason`
- [ ] 所有 `value` 落在 `domain` 内
- [ ] 每个信封都有 `visibility`
- [ ] 派生能力不作为独立 L1 属性落库
- [ ] CLI 对 L1 写入被拒绝
- [ ] 每次 L2/L3 变更都有 `reason` 且生成 `attribute_events`
