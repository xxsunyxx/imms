# IMMS 基础数据归一化规则 v0.1

范围：题材无关、配置驱动。用户提供的剧本 → L1 只读卡；L2/L3 变更走 CLI。规则表存 `config_aliases` / `config_anchors` / `config_derivations`，样例见 `config/`。公式全集见 `docs/FORMULAS.md`。

## 1. 管线五步

| 步 | 动作 | 输出 | 失败处理 |
|---|---|---|---|
| 1 切分 | 章节切块 + 指代消解 | 带 `locator` 的句库 | 无法定位的句子丢弃并计数 |
| 2 证据抽取 | 抽行为/外貌/能力/关系证据句 | `evidence[]` = `{quote, locator}` | 无证据 → 该维度 `null` |
| 3 语义对齐 | alias 表映射到标准键 + 极性 | 标准化维度候选 | 未命中 → 落 `unmapped` 报告，不造键 |
| 4 锚点打分 | 证据句归入 5 档锚点 → `0.1/0.3/0.5/0.7/0.9` | `raw_e` | 每维 < 2 条证据 → `null` |
| 5 冲突消解 + 入库 | 多源加权 → `value/confidence` | L1 信封，`locked=true` | 冲突 Δ>0.35 标 `needs_review` |

## 2. 别名表（节选，`config_aliases`）

| 别名 | 标准键 | 极性 | 说明 |
|---|---|---|---|
| 心机深 / 城府重 / calculating | `OCEAN.C`, `OCEAN.A` | + / − | 计划性↑、宜人性↓ |
| 冲动 / 急躁 / impulsive | `OCEAN.C`, `OCEAN.N` | − / + | 计划性↓、神经质↑ |
| 重情义 / 护短 / loyal | `values.loyalty` | + | |
| 胆小 / 怯懦 / timid | `OCEAN.N`, `pad_dominance` | + / − | |
| 口齿伶俐 / 善辩 / eloquent | `skill_set.eloquence` | + | |
| 体弱 / 多病 / frail | `health` | − | |

规则：别名命中即写入 evidence，不覆盖原文。同句多命中全部记录。未命中别名不强行映射。

## 3. 锚点表（`config_anchors`）

### OCEAN 五档

| 分 | O | C | E | A | N |
|---|---|---|---|---|---|
| 0.1 | 只谈眼前实务，拒绝假设 | 无计划、随口承诺 | 独处、回避主动发言 | 处处挑衅、拒绝协作 | 小事即慌 |
| 0.3 | 极少联想，需提示才发散 | 偶有计划，常拖延 | 熟人前才话多 | 合作但明显护短 | 压力下有紧张反应 |
| 0.5 | 偶尔提出新解 | 大体守约，偶有例外 | 熟人圈活跃 | 愿合作，底线清晰 | 被激怒后需时间平复 |
| 0.7 | 主动联想、好设问 | 事前列计划并跟进 | 主动组织讨论 | 愿让步、能调解 | 易被刺激，反应明显 |
| 0.9 | 抽象推演成习惯 | 记账式核对、零失约 | 全场主导发言 | 过度迁就、几乎不拒绝 | 微小刺激即爆发 |

### values 通用档

| 分 | 行为判据 |
|---|---|
| 0.1 | 只在直接利益冲突时才体现，常反悔 |
| 0.5 | 多数场合遵守，压力下有例外 |
| 0.9 | 稳定一致，可作为决策首要依据 |

### skill_set

0 = 完全不会；5 = 能应付日常；10 = 领域顶尖。归一化 `skill_norm = skill / 10`。

## 4. 证据打分公式

```
raw_e ∈ {0.1, 0.3, 0.5, 0.7, 0.9}     # 由锚点归类，禁止自由小数
score       = Σ(w_e · raw_e) / Σ w_e
consistency = 1 - min(1, std(raw_e) / 0.3)
confidence  = (n / (n + 2)) · consistency
if n < 2: score = null, reason = "insufficient_evidence"
```

`w_e` = `locator` 精度 × 语义直接度：直接引述 1.0，转述 0.6，推测 0.3。

## 5. 多源冲突消解

优先级：`canon_extract 1.0 > script 0.8 > player_input 0.6 > history_infer 0.4`

1. 取最高优先级非空 `score` 为 base。
2. 低优先级若 `|s − base| ≤ 0.2` 且 `confidence ≥ 0.5`，按权重混合；否则记入 evidence 的 `conflict` 字段，不改 base。
3. 任一 `|Δ| > 0.35`：`confidence *= 0.7`，`needs_review = true`。
4. 消解只发生在入库前。L1 入库后不可重算；要改就新版本。

## 6. 派生能力（读取时计算，不入库）

```
observation     = 0.6*O + 0.4*skill_norm('observation')
reasoning       = 0.5*O + 0.3*C + 0.2*skill_norm('reasoning')
deception       = 0.5*(1-A) + 0.3*C + 0.2*acting_skill
impulse_control = 0.6*C + 0.4*(1-N)
nu              = 0.5 + N
leak_sigma      = sigma0*(1+γ1*arousal)*(1+γ2*stress)*(1-acting_skill)
T_v             = T0*(1+κ_stress*stress)/(1+κ_C*C)
```

缓存必须带 `derivation`；输入变化即失效。L1 只读，实际会变的只有 L0/L3。

## 7. L2/L3 规范化

- L2：枚举/范围校验；`visibility` 必填；`source ∈ {script, gm_cli}`。
- L3：公式更新沿用 IMMS §4.1；GM 覆盖必须带 `reason`。`--lock` 置 `locked=true` 阻止公式覆盖，`unlock` 后恢复。
- 所有变更写 `attribute_events`，含 `before/after/reason`。

## 8. CLI 契约

| 命令 | 作用 | 约束 |
|---|---|---|
| `imms normalize --input <dir> --out persona.l1.json --report report.json` | 抽取归一化 | 只产出 L1，不写 L2/L3 |
| `imms persona show <char> [--layer L1\|L2\|L3] [--target <id>]` | 查看 | 默认按 visibility 裁剪 |
| `imms assigned set <char> <key> <value> --reason "..." --round N` | 改 L2 | L1 键报错 |
| `imms emergent set <char> <key> [--target <id>] <value> --reason "..." --round N [--lock]` | 改 L3 | 必带 reason |
| `imms emergent unlock <char> <key> [--target <id>]` | 解除锁定 | 写审计 |
| `imms derive <char> --key observation` | 算派生值 | 不落库 |
| `imms audit <char> --since N` | 查变更历史 | 只读 |

错误码：`ERR_L1_IMMUTABLE` / `ERR_DOMAIN` / `ERR_VISIBILITY_MISSING` / `ERR_REASON_REQUIRED` / `ERR_UNMAPPED_ALIAS`。

## 9. 验收

- [ ] 未命中别名的句子出现在 `unmapped` 报告，不产生标准键
- [ ] 每维得分只取 5 档锚点值，无自由小数
- [ ] 每维证据数 ≥ 2，否则为 `null`
- [ ] 冲突消解结果可回溯到 `evidence[].conflict`
- [ ] 派生值不出现在 `persona_l1`
- [ ] 所有 `gm_cli` 变更可在 `attribute_events` 中定位到 `before/after/reason`

## 10. 跨来源等效（柯南 + 钢铁侠）

归一化的目标不是"把某本书读对"，而是**把任意来源的角色放到同一等效框架里**。标准向量固定为：
`O/C/E/A/N`、`values.*`、`skill_set.*`、`observation`、`reasoning`、`deception`、`impulse_control`、`nu`、`tech_craft`、`combat_effective`、`social_leverage`。

标准公式（与题材无关，全部读取时计算，不落库）：

```
skill_norm(s)     = skill_set[s] / 10                 # 统一到 [0,1]
observation       = 0.6·O + 0.4·skill_norm('observation')
reasoning         = 0.5·O + 0.3·C + 0.2·skill_norm('reasoning')
deception         = 0.5·(1−A) + 0.3·C + 0.2·acting_skill
impulse_control   = 0.6·C + 0.4·(1−N)
nu                = 0.5 + N
tech_craft        = 0.7·skill_norm('craft') + 0.3·O
combat_effective  = 0.6·skill_norm('combat') + 0.2·physique + 0.2·(1−N)
social_leverage   = 0.4·E + 0.3·skill_norm('eloquence') + 0.3·(1−A)
```

跨来源三条硬规则：

1. **同一锚点表**：侦探、超级英雄、古典人物都用 §3 的同一份锚点，不为任何题材另设量表。
2. **同一派生式**：上面的公式对所有角色一致；差值只来自输入值，不来自公式切换。
3. **同一比较命令**：`imms compare --script-id <id> --a <char> --b <char>` 输出逐维并列向量。

真实剧本示例（`examples/scripts/snow-night-villa.yaml`）：

```bash
uv run imms --db imms.db compare --script-id snow-night-villa --a lin_wanqiu --b zhou_muchen
```

该剧本不含行为证据句，归一化报告列出 `missing_evidence`，标准键产出 `value=null`，派生值走中性默认。**没有证据就不给分**——跨来源等效的前提是差异来自可比数值，而不是题材标签或臆测。补入 `canon_evidence` 后重跑 `normalize` 生成新 `extraction_version`，再比较才会出现角色差异。
