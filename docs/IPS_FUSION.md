# IPS v1.0 与 IMMS v1.0 融合审计

> 基线：`docs/IPS_ORIGINAL_SPEC.md`（用户 2026-11-26 提供的 IPS 原文，逐字归档）。
> 代码快照：`db87ae5`，96 个 unittest 全通过。
> 结论：**可融入**——按依赖顺序分五期落地。三处边界已于 2026-11-26 拍板（见 §3），另按用户追加需求新增「越界态」与「跨局长期体验记忆」（见 §4），合计 G17–G24。

---

## 1. 结论先行

| 判断 | 依据 |
|---|---|
| ✅ **方向一致** | IPS 原文 §定位「挂在 §9.2 效用函数与 §6 表演层之间，不新增真相，只新增人味」与 IMMS「数值归公式、语言归模型、真相归引擎」及 C1「系统不创作剧本」完全同向 |
| ✅ **挂载点最小** | 原文 §17.1 自述「只需改动 3 处」：评价函数复用、效用排序处插入覆盖分支、生成时注入 delivery。现有 `runtime._appraise` / `_choose_intent` / `llm.build_prompt` 正好是这三处 |
| ✅ **可复盘可确定性** | IPS 要求 `seed=hash(session, agent, round, event_id)`；IMMS 已有 `game_events`/快照/branch replay，天然承接 |
| ⚠ **有前置依赖** | 面具梯度依赖 L4/mask（G05）；剧本锁护栏 IR4 依赖 G09；AP 前置依赖 G06；保真锚依赖 G2 人格护栏（G05） |
| ✅ **三处边界已拍板** | B1 漂移只审计、不回写 L1；B2 ζ 缺证据即降级；B3 玩家不进 IPS，但玩家与 AI 都可点火他人（见 §3） |
| ✅ **追加两项已纳入** | 越界态（疯批 / 多重人格，可突破常规带通）+ 跨局长期体验记忆（输赢、对 NPC/玩家好恶，约 3 天衰退、再强化复活）|
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

## 3. 三处边界（2026-11-26 已拍板）

### B1 · 基础人格不回写 ✅
用户明确：**基础人格依照原文完成，不写回**。原文 §11.3 的 `Δself_concept` 只写入审计字段 `self_concept_delta` 与 `attribute_events`，**不回写 L1 Canon**。L1 保持只读、人格守恒（R1）。「跨本 canon 演化」继续冻结。用户新增长期体验记忆（§4.1）解决跨局连续感，但不触碰 L1。

### B2 · ζ 缺证据即降级 ✅
按推荐处置：ζ_f 作为新可选属性接入 `skeleton` 九槽位与 `persona_l1`；缺证据写 `value=None + missing_evidence`，此时阴影反转降级为纯面具梯度（无反转）。不编造 ζ 值。若用户要按原著行为证据算 ζ，属于解除 C1 的范围变更。

### B3 · 玩家不即兴，但玩家与 AI 都可点火 ✅
用户澄清：**玩家依赖自己的情绪，无需即兴**——玩家席位不进 IPS 管道（无 accumulator、无带通、无余波结算）。但 **玩家和其他 agent 都可以点火**：点火（Provocation）= 发言作为冲击源抬高目标 `accumulator`，走 §4.2 的 `s_k` 通道，不改玩家席位契约，只在目标身上记账。
- 玩家点火：`runtime._user_act()` 产出的发言经 appraisal 后，可对目标注入正/负 `s_k`（戳创伤、揭短、示好）。
- AI 点火：`_choose_intent` 增加 `provoke` 意图，选中焦点对象注入冲击；AI 之间互燃。
- 防滥用：点火同样受目标不应期 `Θ_refr` 与 budget 约束；玩家点火计入 `game_events` 并可复盘。
- 交付：并入 G18（触发与配额），不单列新任务。

---

## 4. 用户追加需求（2026-11-26）

### 4.1 急性越界态：疯批是「状态」，不是「人格」★
用户要求即兴**不能只有安全库**，要有突破安全的状态。结合用户 2026-11-26 提供的分类学，先明确一个决定：**疯批属于短期状态（state），多重人格/DID 属于长期人格面（facet），二者不是同一机制，必须分开建**。

**设计原则**：急性越界态是**受控、可复盘、有退出条件的临时状态**，不是随机失控（原文 §1「失控行为 = 幻觉」仍禁止）。核心是把「带通上界」从硬闸门改为**状态相关阈值**：

| 态 | 进入条件 | 带通上界 s_hi | 保真下限 F | 人格锚 | 记忆作用域 | 退出与代价 |
|---|---|---|---|---|---|---|
| 常态 normal | 默认 | 3.2 | 0.72 | 主锚 canon | 完整 | — |
| 临界 charged | A ≥ θ 且未越界 | 3.6 | 0.65 | 主锚 + 阴影临时投影 | 完整 | 令牌未放行者蓄能不清空 |
| **疯批 frenzy** | A ≥ 1.1 ∧ I ≥ 0.85 ∧ 消耗 `breakout_token` | 4.6 | 0.55（仅动作合法性，不降 IR1/IR2 铁律） | 主锚 + 阴影全开 ζ | **完整、连续**（还是同一个人，记得自己说过什么） | 1–2 轮；exposure_risk +0.3、morale −0.2；关系净损失不可回收 |

**不可突破的底线（五条铁律仍硬）**：
1. IR1 即兴不创造事实：`frenzy` 产出一律 `unverified_claim`，禁写 TruthGraph。
2. IR2 不超知：只能操作 `beliefs ∪ secrets ∪ public`。
3. **R4 剧本锁 + R5 真相归引擎**：越界态不得跳过 `script_locks`、不得写真相——越界只破「面具」，不破「世界」。
4. `breakout_token` 全局每局默认 ≤1（导演拨盘 `macro_quota` 可上调），且进入前必须有 ≥1 条 `suppressed_burst` 记录，避免"上来就疯"。
5. 每次越界必须产生 aftermath（IR5），并在 replay 中单列「越界时刻 Top N」。

### 4.3 长期人格面：黑化 / 病娇 / DID 是「面」，要长期持有 ★
用户明确指出「长期的人格面切换，和短期的状态是两个情况」。**决定**：另建 `facet`（人格面）机制，与急性 `breakout_state` 分离。人格面**不是回写 L1**（呼应 B1）：它只是把 `active_facet_id` 指向某个从 L1 派生的 profile，原文基础人格只读不变。

**两族根本区别 = 记忆作用域**，不是行为强度：

| 族 | 对应标签（仅语义参照） | 时长 | 记忆作用域 | 锚 | 退出 |
|---|---|---|---|---|---|
| **连续面 continuous** | 黑化、病娇（执念/转向） | 多轮 → 整局 → 可跨局 | **完整连续**，同一自我；改的是目标权重、态度基线、语言指纹 | 次锚（L1 派生，长期持有） | 剧情节点 / GM 令牌 / 跨局保留 |
| **分离面 dissociative** | 多重人格（临床对应 DID） | 多轮 → 可跨局 | **按面隔离**：各面有独立 `beliefs` / `memory`，互不共享；切换产生记忆断层 | 各面独立次锚 | 显式触发回归；回归后按规则处理断片记忆 |

**`persona_facets` 注册表**（从 L1 派生，不编造）：

| 字段 | 含义 |
|---|---|
| `facet_id` / `kind` | `continuous` 或 `dissociative` |
| `derived_from[]` | 来源 L1 维度 / trauma / 事件 id；缺证据写 `None + missing_evidence` |
| `profile` | 行为维度向量（见 §4.4），**不写诊断名** |
| `memory_scope` | `shared`（连续面）或 `isolated:<facet_id>`（分离面） |
| `entry` / `exit` | 触发与退出条件（阈值 + 令牌 + 剧情节点） |
| `audit[]` | 每次切换写入 `attribute_events`，可复盘 |

**分离面的记忆隔离实现**：`runtime` 的 `beliefs` / `memory` / `lie_ledger` 按 `active_facet_id` 分桶；`_audience_for()` 与 InfoGuard scope 在计算可见性时附加面过滤；切换写 `facet_switch` 事件与 `memory_gap` 标记。**分离面仍不得创造事实**（IR1 照常），只是"这个面不知道那个面的事"。

**与长期记忆（§4.2）衔接**：人格面的当前持有状态写入 `long_term_memory`，跨局保留；"上次那个玩家把她逼到了黑化面"可在下一局作为 `facet_bias` 注入——这解释了跨局连续感，但**不改变 L1**。

### 4.4 诊断名只做语义参照，不进系统数据 ★
用户提供的分类（DID / BPD / ASPD / NPD / 偏执型 / 黑暗三联征 / 疯批 / 病娇 / 黑化）用于**设计侧的语义对齐**，落到系统时做两步转换：

1. **转成行为维度**，不贴标签。系统字段只用维度名：`goal_fixation`（执念）、`possessiveness`（占有）、`abandonment_fear`（被抛弃恐惧）、`empathy_suppression`（共情抑制）、`manipulation`（操控）、`remorse_deficit`（无悔意）、`hostile_attribution`（敌意归因 η）、`suspicion_baseline`、`affect_instability`（情绪不稳）、`attention_seeking`（求关注）、`identity_continuity`（自我连续）、`memory_continuity`（记忆连续）。
2. **三不原则**：角色卡 / Prompt / 面向玩家的复盘文本里**不出现临床诊断名或流行标签**（不写"BPD""疯批""病娇"）；诊断名只在 `docs/` 设计文档里作为参照；不做自动诊断、不给玩家贴标签。

> 这条同时消解了用户提到的风险：把临床诊断当人设标签会失真，而"身份/记忆的整合被破坏"（DID）与"持久僵化的情绪化/操控模式"（B类）本来就是不同机制——系统用 `memory_scope: isolated` 与 `continuous` 两族分开表达，不用一个"多重人格"标签混过去。

### 4.2 跨局长期体验记忆（3 天衰退 + 再强化）★
用户要求：记录**输赢、对 NPC、对玩家的好恶**，约 3 天记忆期，随后衰退，除非再强化。

**定位**：这是 L0 元属性层 / 跨局状态，**不是 L1**（呼应 B1：基础人格不回写）。它与 IPS 的 `memory` 不同——后者是局内情节记忆，前者跨 session 存活。

| 字段 | 含义 | 来源 |
|---|---|---|
| `owner_character_id` | 记忆归属角色 | `persona_l1` |
| `target_kind` / `target_id` | `npc` / `player`，目标标识 | 玩家用稳定 `player_id`（非席位号），NPC 用角色 id |
| `sentiment` | 好恶分 `[-1,1]`，含信任倾向 | 局末从 `relations`/`commitments`/`vote` 结算 |
| `outcome` | 该局对该目标的输赢 `win/loss/draw` | `vote.audit` + retro 评分 |
| `evidence[]` | 来源 `session_id` + event seq | 事件溯源 |
| `last_interacted_at` | 最近交互时间 | 时间戳 |
| `decay_half_life_days` | 半衰期，默认 3 | 导演拨盘可配 |

**衰退公式**：`w(t) = exp(-ln2 · (t − last_interacted_at) / 3d)`；`sentiment` 与 `outcome` 权重同时衰减。低于阈值（如 0.15）时标记 `faded`，不删除（可复盘）。**再强化**：新一局再次交互 → `last_interacted_at` 重置、`sentiment` 按新证据增量更新、`faded` 清除；这就是用户说的「除非再强加」。

**注入**：开局 `init_runtime()` 时按角色读取未 faded 的记录，注入 L3 关系初值（`like/trust` 先验）与 `outcome` 偏置，并在 Prompt 中写明「你上次与这位玩家的关系」。**不写 L1**。需要新表 `long_term_memory`（`db.py`），并补 CLI `memory longterm show/forget`。

---

## 5. 分期计划（原文 P0–P4 → G17–G21，追加 G22–G25）

| 新 ID | 原文阶段 | 内容 | 前置 | 验收口径 |
|---|---|---|---|---|
| ✅ **G17**（已完成） | P0 活人感 | `ips.py` 落地泄漏积分蓄能器（ρ/θ/不应期/冲击明细）、always-on `micro_perturb` 与 7 项安全库；只写 `delivery`；玩家席位按 B3 不进 IPS | 无（l0 已有 acting_skill；emotion 已有 arousal/stress） | 每轮 AI 席位产出 `delivery` 且进 `game_events`（玩家席位按 B3 例外）；`delivery` 不写 TruthGraph；旧测试不回归；`seed` 可复现 |
| ✅ **G18**（已完成） | P1 会爆发 | `ips.resolve_bursts` 落 I/ε/Kairos 门槛、`ImprovBudget`、同轮令牌竞争、`suppressed_burst`、IR1/IR2/IR5 与三策略 aftermath；IR3/IR4 仍按计划待 G09/solver 唯一路径 | G17；IR4 部分需 G09，IR3 需 `is_unique_path()` | 同轮多人越阈只放最高者；未放行者 A 不清空且记 `suppressed_burst`；每次 meso/macro 有 ≥1 条 aftermath |
| **G19** | P2 有方向 | 面具梯度 + 阴影反转 + 带通选择（意外度/保真度/方向对齐）；保真用规则代理，不引入向量库 | G05（L4/mask、G2 护栏）+ B2 定案 | `direction.mask_gradient` 可复盘；`s>3.2` 或 `F<0.72` 的候选被拒并记原因；ζ 缺失时降级不编造 |
| **G20** | P3 有后果 | 余波三策略接 `lie.reasoning_log` + 记忆烙印 + 级联传染阻尼；漂移只审计不回写 | G18 + lie.py + B1 定案 | `aftermath.strategy∈{regret,rationalize,escalate}`；级联 `d≤2` 且强制降档；`self_concept_delta` 不进 L1 |
| **G21** | P4 可调可评 | 导演拨盘 5 旋钮入 `session.l0`（improv_gain/chaos/shadow_sensitivity/leak_rate/macro_quota）+ retro 指标看板 + 预设档位 | G17–G20；指标基线依赖 G08/G12 | 拨盘改 θ/quota/T 且可复现；retro 输出有效即兴率 30–60%、余波覆盖 100%、漂移 ≤0.08 |
| **G22** ★ | 追加：**急性越界态（疯批）** | `breakout_token` + 状态机 `normal/charged/frenzy` + 状态相关带通阈值（s_hi 3.2→4.6）+ after-effect；记忆连续；越界单列复盘 | G17–G19；IR4 需 G09 | `s>3.2` 仅在 frenzy 态放行且记 `state`；记忆不隔离；IR1/IR2/R4/R5 不被突破；每局 token 默认 ≤1 |
| **G23** ★ | 追加：长期体验记忆 | 新表 `long_term_memory` + 3 天半衰期 + 再强化 + `init_runtime` 关系先验注入 + CLI `memory longterm` | 需稳定 `player_id`（`session.l0` 或参会元数据） | 跨局好恶/输赢可查；超过半衰期权重衰减且可 `faded`；再交互复活；**L1 不变**；证据可溯源 |
| ✅ **G24**（已完成，并入 G18） | 追加：点火 Provocation | `provoke` 意图 + 玩家 accuse/ally/显式 impact 注入 `s_k` + AI 互燃；点火只写目标 accumulator，玩家席位自身不进 IPS | G17 + §2①/G04 冲击通道 | 玩家点火抬高目标 accumulator 且落库；玩家无 accumulator/delivery；受不应期与 budget 约束 |
| **G25** ★★ | 追加：**长期人格面（facet）** | `persona_facets` 注册表；连续面（黑化/病娇，记忆共享、权重重排）与分离面（DID，`beliefs/memory/lie_ledger` 按面分桶 + 记忆断层）；`active_facet_id` 切换审计；诊断名不进数据 | G17/G19/G23；分离面需 InfoGuard scope 附加面过滤 | 两族 `memory_scope` 不同且可验证；分离面之间不共享信念；切换有 `facet_switch` + `memory_gap`；L1 不变；复盘无诊断名 |

**依赖链**：`G05 → G19 → G22`，`G09 → G18(IR4)`，`G06 → G18(Insertable)`，`G08/G12 → G21`，`G17 → G18 → G20 → G21`；`G17 → G24`；`G23` 独立（需 `player_id`），可与 G17 并行；`G23 + G19 → G25`。

**建议执行顺序**：G17（无阻塞）→ G18（含 G24 点火）→ G19（待 G05）→ G20 → G22 → G21 → G23 → G25。G18 的 IR3/IR4 可先以「降级为 claim-only / 跳过锁检查并标红」过渡，待 G09 完成后转正。**G25 单独排最后**：它需要长期记忆的跨局承载与 G19 的方向引擎，且记忆隔离会动到 InfoGuard 的可见性口径，风险最高。

---

## 6. 执行约束（沿用 GAP_AUDIT §5，IPS 特有补充）

1. **即兴只破面具，不破世界**：`delivery` 与 `improv` 字段不得写入 `truth_graph` / `facts` / `timeline_true`；越界态同样受此约束（§4.1）。
2. **不新增第二套账本**：余波复用 `lie.reasoning_log` 与 `memory`，令牌/意外度入 `@global` 快照与 `game_events`。
3. **缺证据不编造**：ζ、mask、次锚、动作库未覆盖的维度一律 `None + missing_evidence`，即兴降级为安全库。
4. **确定性**：每次即兴记录 `seed=hash(session_id, seat_id, round, triggering_event_seq)`；分叉注入须能复现同一动作。
5. **人格守恒保险丝**：阴影反转与越界态仅 1–2 轮窗口；基础人格 L1 永不回写（B1 已拍板）；`self_concept_delta` 仅审计。
6. **玩家不即兴，但可点火**：玩家席位不进 IPS 管道；玩家发言只作为冲击源影响目标角色（B3 已拍板）。
7. **长期记忆不碰 L1**：跨局好恶/输赢只写 `long_term_memory` 与 L3 初值，不改 canon（B1）。
8. **状态与面分离**：`breakout_state`（急性、记忆连续、1–2 轮）与 `active_facet`（长期、可跨局、连续面共享记忆 / 分离面隔离记忆）是两套字段，不得混用或互相降级。
9. **诊断名不入数据**：临床诊断名与流行标签只作 `docs/` 语义参照；角色卡 / Prompt / 玩家复盘只写行为维度名（§4.4 三不原则）。
10. **回归基线**：每期补对应 unittest，96 项既有测试不得回归。
