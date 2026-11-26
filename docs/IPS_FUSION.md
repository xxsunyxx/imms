# IPS v1.0 与 IMMS v1.0 融合审计

> 基线：`docs/IPS_ORIGINAL_SPEC.md`（用户 2026-11-26 提供的 IPS 原文，逐字归档）。
> 代码快照：`db87ae5`，96 个 unittest 全通过。
> 结论：**可融入**——但需要按依赖顺序分五期落地，且有三处必须先拍板的边界问题。

---

## 1. 结论先行

| 判断 | 依据 |
|---|---|
| ✅ **方向一致** | IPS 原文 §定位「挂在 §9.2 效用函数与 §6 表演层之间，不新增真相，只新增人味」与 IMMS「数值归公式、语言归模型、真相归引擎」及 C1「系统不创作剧本」完全同向 |
| ✅ **挂载点最小** | 原文 §17.1 自述「只需改动 3 处」：评价函数复用、效用排序处插入覆盖分支、生成时注入 delivery。现有 `runtime._appraise` / `_choose_intent` / `llm.build_prompt` 正好是这三处 |
| ✅ **可复盘可确定性** | IPS 要求 `seed=hash(session, agent, round, event_id)`；IMMS 已有 `game_events`/快照/branch replay，天然承接 |
| ⚠ **有前置依赖** | 面具梯度依赖 L4/mask（G05）；剧本锁护栏 IR4 依赖 G09；AP 前置依赖 G06；保真锚依赖 G2 人格护栏（G05） |
| ⚠ **有三处冲突需拍板** | 自我概念漂移 vs L1 Canon 只读；阴影系数 ζ 来源 vs C1 原书抽取冻结；玩家点火/同权即兴 vs 玩家席位边界 |
| ❌ **不可直接照搬的部分** | 200 局自对弈调参（G12 尚未做）、向量嵌入保真度（当前无 embedder，须用规则代理）、TTS 与 Web 看板（C1 冻结） |

**定位修正**：原文末尾问「即兴系统的一部分」——在 IMMS 中 IPS 不是独立系统，而是**行为调制层**，插在 `_choose_intent`（F24/F25）之后、`llm`/模板生成之前，并以 `aftermath` 回写 L3/LieLedger/Memory。它是 G05（L4 表演层）的动态执行面，而非并列模块。

---

## 2. 九段流水线 × 现有实现对照

| IPS 段 | 原文要求 | 现有可复用 | 缺口与依赖 |
|---|---|---|---|
| ① 评价 Appraisal | 事件冲击向量 `s_k`（12 类，含正向暖流） | `runtime._appraise()` 已产出 `changes`、`BETA_MATRIX`（含正/负事件） | `_appraise` 只输出关系/PAD 变化，**没有 `s_k` 冲击通道**；冲击表需新增，与 BETA 并列而非替换 |
| ② 蓄能 Accumulator | 泄漏积分 `A_i`、`ρ_i`、不应期 `Θ_refr` | `emotion.stress/arousal` 已有衰减字段；`formulas.clip` 可复用 | state 无 `accumulator/theta/refractory_until`；纯函数无。属 P0 新增，不依赖他人 |
| ③ 方向 Direction | 面具梯度 `canon-mask` + 阴影反转 `ζ_f` | `formulas.tension()`、`leak_sigma()`、`shown_attitude()`（F01/F18/F19）均为纯函数 | `assemble.py:39` 的 l0 只有 `acting_skill/lying_skill/play_style`，**无 mask、无 ζ_f**；须先完成 G05（L4 投影）并解决 ζ 来源 |
| ④ 候选生成 | 动作库检索 + LLM 提案（`k=3`） | `llm.build_provider()` 已有 provider 抽象；`_choose_intent()` 固定 7 意图 | 动作库 YAML 不存在；LLM 提案复用 provider，但需在 `build_prompt` 加方向段 |
| ⑤ 带通选择 | 意外度 `s∈[1.2,3.2]` ∧ 保真 `F≥0.72` ∧ 方向对齐 | `formulas.utility()`/`softmax()`（F24/F25）已有的排序框架；`BETA`/信念可派生 `freq_session` | 保真锚 `cos(emb(a),anchor_i)` 需要 embedding：当前**无 embedder**；用 OCEAN/values/lexicon 规则代理替代，向量库属 C1 冻结 |
| ⑥ 定档/配额/时机 | 三档 ε、`ImprovBudget`、Kairos `K` | G01 已修 F22 热度曲线 → `Gap(t)=1-H/H*` 可直接算；`actual_heat()` 有 | 无 budget 令牌池；`Insertable` 阶段白名单依赖 G06（幕/阶段状态机）；`Attn` 可从 `suspicion` 焦点近似 |
| ⑦ 护栏 IR1–IR5 | 不造事实/不超知/不破坏可解性/不违反锁/必有后效 | IR2 = 既有 G1 InfoGuard + 信念图（G02）；IR3 可查 `solver.coverage`（`solver.py:280`） | IR1 的 `unverified_claim` 标记字段现有 `act` 无；IR3 缺 `is_unique_path()` 查询；IR4 依赖 G09 运行时剧本锁；IR5 需新增 aftermath 校验 |
| ⑧ 执行 delivery | 句长/敬语/省略号/颤抖等表达参数 | `llm.build_prompt()` 已分块注入；模板 `_utter()` 可拼接 | Prompt 与 act 均无 `delivery` 字段；纯文本系统下参数只能落字符串与 Prompt，不进 TruthGraph |
| ⑨ 余波 Aftermath | 后悔/圆场/加码 + 记忆烙印 + 漂移 + 级联 | **`lie.py.reasoning_log` 已有 `created/believed/exposed/rejustified`；`strategy=maintain/patch/confess` 与 IPS `regret/rationalize/escalate` 可直接映射** | `self_concept` 漂移无字段且违反 L1 只读（见 §3）；级联 `depth≤2` 无实现；`memory.observe` 可承接烙印 |

> 关键复用：⑨ 余波不必另起炉灶。IPS 的三策略等价于刚落地的谎言策略家族；`aftermath` 可作为 `lie.reasoning_log` 的新事件类型接入，避免两套账本。

---

## 3. 必须先拍板的三处边界

### B1 · 自我概念漂移 vs L1 Canon 只读（阻塞 G20）
原文 §11.3 允许 `Δself_concept` 跨局累积 cap 0.08/局、0.20 多局，并称「允许 canon 极缓慢演化」。
IMMS 铁律 R1 与 `SCHEMA.md` 明确 L1 Canon 只读、人格守恒。**默认处置**：漂移只写入审计字段 `self_concept_delta` 与 `attribute_events`，**不回写 L1**；跨本继承属于元游戏（C1 冻结）。若要真让 canon 演化，需用户显式解除 R1。

### B2 · 阴影系数 ζ_f 来源 vs C1 原书抽取冻结（阻塞 G19）
原文要求 ζ_f 在「原书抽取阶段」产出，例 `尤二姐 ζ_A=0.95`。项目 C1 已冻结原书抽取（只处理用户现成剧本）。
**默认处置**：ζ_f 作为新可选属性接入 `skeleton` 九槽位与 `persona_l1`；缺证据写 `value=None + missing_evidence`，此时阴影反转降级为纯面具梯度（无反转）。不编造 ζ 值。若用户要按原著行为证据算 ζ，属于解除 C1 的范围变更。

### B3 · 玩家点火 / 玩家同权即兴（扩展项，不进 P0–P4）
原文 §17.3 的 Provocation 与「玩家提交即兴意图」需要玩家侧输入 `intent` 而不只是 `utterance`。现有 `runtime._user_act()` 只返回固定脚本发言；G13 交互驱动层尚未做。
**默认处置**：列入 G21 之后的扩展，不改现有玩家席位契约；若要开放，需同时定义防滥用与余波结算口径。

---

## 4. 分期计划（映射原文 P0–P4 → G17–G21）

| 新 ID | 原文阶段 | 内容 | 前置 | 验收口径 |
|---|---|---|---|---|
| **G17** | P0 活人感 | `Accumulator`（ρ/θ/不应期/令牌字段）+ always-on `micro_perturb` + 安全即兴库（7 项）；只改 `delivery`，不改行为 | 无（l0 已有 acting_skill；emotion 已有 arousal/stress） | 每轮每席位产出 `delivery` 且进 `game_events`；`delivery` 不写 TruthGraph；96 项测试不回归；`seed` 可复现 |
| **G18** | P1 会爆发 | 越阈触发 + 三档幅度 + `ImprovBudget`（macro 1/幕、meso 2/人、d≤2）+ IR1/IR2/IR5；`suppressed_burst` 日志 | G17；IR4 部分需 G09，IR3 需 `is_unique_path()` | 同轮多人越阈只放最高者；未放行者 A 不清空且记 `suppressed_burst`；每次 meso/macro 有 ≥1 条 aftermath |
| **G19** | P2 有方向 | 面具梯度 + 阴影反转 + 带通选择（意外度/保真度/方向对齐）；保真用规则代理，不引入向量库 | G05（L4/mask、G2 护栏）+ B2 定案 | `direction.mask_gradient` 可复盘；`s>3.2` 或 `F<0.72` 的候选被拒并记原因；ζ 缺失时降级不编造 |
| **G20** | P3 有后果 | 余波三策略接 `lie.reasoning_log` + 记忆烙印 + 级联传染阻尼；漂移只审计不回写 | G18 + lie.py + B1 定案 | `aftermath.strategy∈{regret,rationalize,escalate}`；级联 `d≤2` 且强制降档；`self_concept_delta` 不进 L1 |
| **G21** | P4 可调可评 | 导演拨盘 5 旋钮入 `session.l0`（improv_gain/chaos/shadow_sensitivity/leak_rate/macro_quota）+ retro 指标看板 + 预设档位 | G17–G20；指标基线依赖 G08/G12 | 拨盘改 θ/quota/T 且可复现；retro 输出有效即兴率 30–60%、余波覆盖 100%、漂移 ≤0.08；玩家点火仍冻结（B3） |

**依赖链**：`G05 → G19`，`G09 → G18(IR4)`，`G06 → G18(Insertable)`，`G08/G12 → G21`，`G17 → G18 → G20 → G21`。
G17 可立即开工（无阻塞）；G18 的 IR3/IR4 可先以「降级为 claim-only / 跳过锁检查并标红」过渡，待 G09 完成后转正。

---

## 5. 执行约束（沿用 GAP_AUDIT §5， IPS 特有补充）

1. **即兴只破面具，不破世界**：`delivery` 与 `improv` 字段不得写入 `truth_graph` / `facts` / `timeline_true`。
2. **不新增第二套账本**：余波复用 `lie.reasoning_log` 与 `memory`，令牌/意外度入 `@global` 快照与 `game_events`。
3. **缺证据不编造**：ζ、mask、动作库未覆盖的维度一律 `None + missing_evidence`，即兴降级为安全库。
4. **确定性**：每次即兴记录 `seed=hash(session_id, seat_id, round, triggering_event_seq)`；分叉注入须能复现同一动作。
5. **人格守恒保险丝**：阴影反转仅 1–2 轮窗口；`self_concept_delta` 只审计，跨局累积 cap 0.08 待 B1 定案。
6. **回归基线**：每期补对应 unittest，96 项既有测试不得回归。
