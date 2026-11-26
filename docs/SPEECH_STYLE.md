# 人格 → 说话风格 Prompt 引擎（80% 锚点 / 20% 漂移）

`src/imms/speech_style.py`。参考设计给出的是**字段清单**，但 IMMS 的字段名和数据粒度不同。本引擎按**现有数据重写算法**：凡是 IMMS 已存的参数，一律派生出对应措辞关键词；只有真正不存在的字段才在 `missing` 报告，不编造人物事实。

## 1. 参数 → 措辞词库（config/speech_style_lexicon.json）

词库是**措辞规则（怎么说）**，不是人物事实（说什么）；任何词条都不得产生 claimed_fact / intent / 数值状态。`lexicon()` 只读缓存，文件缺失返回空映射（不编造默认词）。

| 类别 | 维度 | 映射档位 |
|---|---|---|
| OCEAN | O/C/E/A/N | 每维 5 档（0.2/0.4/0.6/0.8/1.0），每档含 `terms` + `syntax` |
| relations | like/trust/suspicion/respect/intimacy/fear/envy/grudge/debt/dominance/commitment（11 维） | positive / negative |
| emotion | arousal/valence/stress/cognitive_load/morale/intoxication/exposure_risk（7 维） | high/low（valence 用 positive/negative） |
| L0 | acting_skill/lying_skill/difficulty/play_style | high/low + 风格枚举 |
| IPS | accumulator（near_theta/overshoot/refractory）+ breakout（normal/charged/frenzy） | 状态枚举 |
| delivery.params | 10 项表演系数 | high/low（离散项 on/off） |

派生函数：`derive_tone` / `derive_syntax` / `derive_rhythm` 走 OCEAN 5 档；`relation_terms` / `emotion_terms` / `l0_terms` / `ips_terms` / `delivery_param_terms` 从真实 state 触发措辞，全部带参数值前缀（如 `grudge(+1.00)→句末带刺`），漂移可解释。

## 2. 五步流水线

| 步骤 | 函数 | 数据来源（IMMS 真实字段） |
|---|---|---|
| 指纹提取 | `extract_fingerprint` | L1 五维 + honesty + personality/speaking_style；口癖从原文引号抽取；syntax/rhythm 由 OCEAN + play_style 经词库派生；sample_lines 取 `canon_evidence` 逐字引用句 |
| 预算分配 | `allocate_drift` | 真实 `emotion.dominance`（不再用 1−stress 代理）；11 维关系；honorific_step + breakout.state + facets；exposure×load + A−theta；IPS 已采样的 delivery.probability |
| 变体生成 | `generate_variants` | 直接编译 IPS `delivery.params` 的 10 项系数为措辞指令（句长/省略/疑问/敬语/口癖/停顿/修正/口语/倒装/修饰） |
| 三块组装 | `assemble` | `[ANCHOR]`（人格/口癖/句式/节奏/表演底色/证据句）/ `[DRIFT]`（5 类漂移 + 关系/情绪/IPS 措辞）/ `[BOUNDARY]` |
| 一致性审计 | `audit` | 字符 bigram 余弦代理 + 禁用词 + 预算检查 |

## 3. 漂移预算（硬顶 0.20）

| 类型 | 权重 | 上限 | 触发 |
|---|---|---|---|
| emotion | 0.30 | 0.06 | arousal/valence/真实 dominance |
| relation | 0.25 | 0.05 | 11 维关系绝对值（含 grudge/debt） |
| mask | 0.20 | 0.04 | honorific_step + breakout charged/frenzy + 分离面 + L2 mask |
| stress | 0.15 | 0.03 | stress + 0.4×exposure×load + A−theta |
| noise | 0.10 | 0.02 | IPS delivery.probability（无采样时回退 seed） |

`scale = min(0.20 / raw, 1.0)`；每项再按 `caps` 二次裁剪。

## 4. 三道闸

- **预闸**：`[DRIFT]` 每项写死上下界，LLM 只能在区间内移动。
- **中闸**：仅 LLM 生成文本做 `audit()`；`cos < 0.72` 写 `style_regenerate_hint`。模板 provider 余弦留空（`skipped_template`），避免零重叠噪声。
- **后闸**：连续 3 轮 `drift_total > 0.18` → 下一轮强制回落基线，`DriftRecord` 全零并记 `post_gate:forced_baseline`。

## 5. 80/20 结构比的实测口径

`structure_ratio = len(ANCHOR) / (len(ANCHOR)+len(DRIFT)+len(BOUNDARY))`（中文字符代理）。

O09 实测约 **0.50**，未达 0.78–0.82。根因是剧本 `canon_evidence` 只有 5–29 字的短引用句、没有 3–5 条逐字台词样本；按「缺证据不编造」不自造长台词凑数。词库补全后锚点信息量显著增加（8 行人格/表演映射），但字符比仍受限于短证据句。补齐真实台词样本后比例上升（单测验证）。引擎如实输出 `structure_ok=false`，`missing` 只剩 `taboo_words`（剧本真没有禁用词表）。

## 6. 余弦是代理，不是语义相似度

项目无 embedder、无 numpy。`cosine_proxy` 用字符 bigram 多重集余弦，字段 `cosine_kind="character_bigram_proxy"`。它只用于 R2 一致性护栏的粗筛，**不代表文本语义**；模板路径跳过。

## 7. 账本与复盘

- 表 `drift_ledger`：每 AI 席位每轮一行，含 5 类漂移、总漂移、结构比、触发原因、是否强制回落。
- CLI：`imms style ledger --session S [--seat X] [--out F]`。
- replay 第 7 节「说话风格漂移账」逐轮列出可解释触发。

## 8. 边界

- 风格引擎**只改措辞**，不改意图、目标、事实白名单、谎言账本或任何数值（IR1）。
- 词库只映射风格，不产生人物事实；公开身份走 L2，L1 只读不变。
- 关系漂移不能让角色违背已出口的谎言；`[BOUNDARY]` 前置该约束，InfoGuard 后置兜底。

## 9. 验证

- `tests/test_speech_style.py` 25 项：预算封顶、词库覆盖（11 维关系/7 维情绪/5 档 OCEAN）、真实 dominance、delivery 采样噪声、措辞关键词、三块结构、三道闸、账本读写。
- 全量 **211 项** unittest 通过。
- O09 端到端：新词库下三块 prompt 完整渲染，锚点含 8 行派生映射，漂移块含 11 维关系 + 7 维情绪 + IPS 措辞。
