# 人格 → 说话风格 Prompt 引擎（80% 锚点 / 20% 漂移）

`src/imms/speech_style.py`。按参考设计实现，但**只读取 IMMS 已有的 L0–L4 数据**，缺失字段一律留空，不编造台词、句式或禁用词。

## 1. 五步流水线

| 步骤 | 函数 | 数据 |
|---|---|---|
| 指纹提取 | `extract_fingerprint` | L1 数值 + 文本槽位 + L2 公开身份；`syntax/rhythm/catchphrase/taboo_words/sample_lines` 当前无字段 → `None`/`[]` |
| 预算分配 | `allocate_drift` | L3 情绪/关系、L2 mask、IPS 蓄能、确定性噪声 |
| 变体生成 | `generate_variants` | 语气/称呼/句长/词汇丰富度/口癖密度/应激修饰 |
| 三块组装 | `assemble` | `[ANCHOR]` / `[DRIFT]` / `[BOUNDARY]` |
| 一致性审计 | `audit` | 字符 bigram 余弦代理 + 禁用词 + 预算检查 |

## 2. 漂移预算（硬顶 0.20）

| 类型 | 权重 | 上限 | 触发 |
|---|---|---|---|
| emotion | 0.30 | 0.06 | L3 arousal/valence/stress（PAD 的 dominance 用 1−stress 代理） |
| relation | 0.25 | 0.05 | 对目标席位 like/trust/suspicion |
| mask | 0.20 | 0.04 | L2 mask 或 G25 分离面 |
| stress | 0.15 | 0.03 | stress + IPS 蓄能 A |
| noise | 0.10 | 0.02 | seed 派生的确定性随机（代替 temperature） |

`scale = min(0.20 / raw, 1.0)`；每项再按 `caps` 二次裁剪。同一 seed 同一轮结果确定。

## 3. 三道闸

- **预闸**：`[DRIFT]` 每项写死上下界，LLM 只能在区间内移动。
- **中闸**：仅 LLM 生成文本做 `audit()`；`cos < 0.72` 写 `style_regenerate_hint`。模板 provider 不是人格尝试，余弦留空（`skipped_template`），避免零重叠噪声。
- **后闸**：连续 3 轮 `drift_total > 0.18` → 下一轮强制回落基线，`DriftRecord` 全零并记 `post_gate:forced_baseline`。

## 4. 80/20 结构比的实测口径

`structure_ratio = len(ANCHOR) / (len(ANCHOR)+len(DRIFT)+len(BOUNDARY))`（中文字符代理）。

**当前 O09 实测 0.50–0.54，未达 0.78–0.82。** 根因：剧本没有 3–5 条逐字台词样本（`sample_lines`），也没有 syntax/rhythm/catchphrase 字段。按「缺证据不编造」不能自造台词凑数，引擎如实输出 `structure_ok=false` 并在 `missing` 列出缺口。补齐真实台词样本后比例会上升（单测验证）。

## 5. 余弦是代理，不是语义相似度

项目无 embedder、无 numpy。`cosine_proxy` 用字符 bigram 多重集余弦，字段 `cosine_kind="character_bigram_proxy"`。它只用于 R2 一致性护栏的粗筛，**不代表文本语义**；与整段锚点模板对比时零重叠属正常，故模板路径跳过。

## 6. 账本与复盘

- 表 `drift_ledger`：每 AI 席位每轮一行，含 5 类漂移、总漂移、结构比、触发原因、是否强制回落。
- CLI：`imms style ledger --session S [--seat X] [--out F]`。
- replay 第 7 节「说话风格漂移账」逐轮列出可解释触发，例如 `relation:ai:o09_r02 like=-0.22,trust=-0.23` → 说明语气变冷是关系漂移而非随机。

## 7. 边界

- 风格引擎**只改措辞**，不改意图、目标、事实白名单、谎言账本或任何数值（IR1）。
- `public_identity` 走 L2，装配时写入、runtime 回填；L1 只读触发器不变。
- 关系漂移不能让角色违背已出口的谎言；`[BOUNDARY]` 前置该约束，InfoGuard 后置兜底。

## 8. 验证

- `tests/test_speech_style.py` 17 项（预算封顶、确定性噪声、缺字段不编造、三块结构、三道闸、账本读写）。
- 全量 203 项 unittest 通过。
- O09 10 轮端到端：30 条漂移账本，均值 0.131，无强制回落；四人锚点各不相同。
