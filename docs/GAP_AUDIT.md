# IMMS v1.0 原始设定 · 现状审计与待办

> 基线：`docs/ORIGINAL_SPEC.md`（用户 2026-11-26 提供的长文本，逐字归档，未删改）。  
> 代码快照：`c6b107a`，83 个 unittest 全通过（本文件只做审计，不改运行时）。  
> 图例：✅ 已落地｜◐ 部分实现 / 仅登记为纯函数或静态检查｜❌ 未实现｜⚠ 与既有工程决策冲突。



---

## 0. 归档结论

- 原始设定此前**没有**独立落盘；只在对话中出现，散落在 `FORMULAS.md` / `RUNTIME.md` 等派生文档里。
- 现已把全文写入 `docs/ORIGINAL_SPEC.md`（1028 行 / 28673 字符），作为后续所有对照的唯一基线。
- 本文件只做对照与排序，**不修改原始设定的任何文字**。

---

## 1. 逐章对照

| 原始设定章节              | 状态 | 现有实现                                                                                                                        | 主要缺口                                                                                                                   |
| ------------------- | -- | --------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| §1 目标与五条铁律          | ◐  | R1 知识护栏 = `claimed_facts` 白名单 + InfoGuard；R4 剧本锁在 runtime 输出侧硬检查并终幕审计；R5 GM 单一真相写入 + 事件溯源                             | R2 人格余弦闸门无；R3 谎言账本空转；信息不对称有破口（见 C2）                                                                     |
| §2 总体架构             | ◐  | Content（ingest/corpus/registry）、Agent（persona/runtime/llm/memory）、Adjudication（vote/gm/infoguard）、复盘（replay/retro）均有模块      | 无表现层/Web；EventBus 是 SQLite `game_events` 轮询而非推送；无小模型事件分类层                                                              |
| §3 五层人格             | ◐  | L1 Canon 只读 + 证据化打分（F29）；L2/L3 信封与变更审计；L0 以 `session.l0` 字段存在（`difficulty/acting_skill/lying_skill/play_style`）             | L4 表演层不存在；其他角色直接读真实 L3；`assemble` 未从剧本提取 `public_identity/mask/goals/script_locks`（`script_locks=[]`）                  |
| §4 关系场与情绪           | ◐  | F02/F03/F04/F05 进入 `_appraise`；F07 PAD 更新；F08 显式创伤放大、F09 离散情绪、F11 阵营滞回已接 runtime；关系域 clip 与 `attribute_events` 审计 | F06 信任负向不对称只写在配置说明，运行时统一 `λ=0.95`；未声明的 trauma/faction 保持空，不编造               |
| §5 信念图与怀疑           | ◐  | log-odds 怀疑更新、sigmoid、证据权重、投票分布（`vote.py` + `replay.py`）                                                                    | 每个席位开局把**全部** `clue_pool` 写成已知（C2）；`is_true` 分离存储未建立；F17 矛盾事件只登记不生成；`Timeline_Narrated` 与 `alibi_gap` 无                |
| §6 表演与说谎            | ◐  | `src/imms/lie.py`：F20 决策、F21 heat、谎言创建/修订；每个 agent 有 `reasoning_log`，被相信/被揭穿都追加自洽记录；Prompt 与 replay 可见                      | L4 表演投影与 `shown_attitude` 噪声未接；G2 人格余弦护栏仍缺                                                                             |
| §7 世界状态与本体          | ◐  | Ontology 九槽位（facts/clues/clue_pool/timeline_true/truth_graph/evaluation/plot_locks/endings/completions/skeleton）+ solver 门禁 | `truth_graph` 是字符串列表而非 `(s,p,o,t,visibility)` 三元组；线索的 `discover/forgeable/decay_rounds/chain` 未建模；`endings` 只存文本，不参与裁决 |
| §8 回合流程与节奏          | ◐  | `play` 逐轮发言→评价→GM→投票；私聊房间独立 CLI；F22/F23 有纯函数                                                                                | 无幕/阶段状态机；无 AP、搜证、质询 token、时限；GM 只做线索释放 + 休整，且热度目标恒定（C3）；`advance_stage/private_allowed` 未被消费                           |
| §8.2 GM 事件注入库       | ✅  | `src/imms/events.py` 七类事件 + 多变量触发 + `gm inject` / `gm branch --inject-event` + `attribute_events` 审计 + retro 注入审计           | `ap_penalty` / `clue_decay_multiplier` 已入局势层但搜证与衰减流程尚未消费（G06/G14）；默认回合不隐式注入                                            |
| §9 Agent 决策循环       | ◐  | 感知→确定性评价→F26 召回→效用 F24→softmax F25→模板/LLM→护栏→落库                                                                             | `appraise` 是按 `act_type` 的固定映射，不是小模型分类 + 归因；意图集合固定 7 个；反思记忆/语义记忆未实现；人类发言固定 `assert`                                    |
| §10 三重护栏            | ◐  | G1 知识护栏；InfoGuard 四层（含输入传闻化与输出改写）；人类输出不拦                                                                                    | G2 人格一致性余弦无；G3 谎言一致性无（账本空）；G4 只覆盖已知事实泄露，未覆盖玩家提示注入诱导                                                                    |
| §11 原书→剧本自动生成       | ⚠  | 只做"用户给现成剧本 → 解析/归一化/装配/准入"；solver 做可解性校验；`oracle` 做盲测与 5 局平衡冒烟                                                             | 与项目既定边界冲突（C1）：原书抽取、案件模板匹配、UGC 发布均未实现且当前不在范围                                                                  |
| §12 变量总表            | ◐  | SCHEMA.md 已登记 L1/L2/L3 全部键；`formulas.py` 覆盖 F01–F28                                                                         | 局势层（info_entropy/consensus/polarization/alive_count）未计算；L0 `meta_bias`、L4 投影缺失；60+ 参数未做成可编辑配置表                         |
| §13 数据模型/API/Prompt | ◐  | SQLite 事件溯源；LLM Prompt 分块注入且裁剪事实；CLI 替代 HTTP API；记忆段统一写 F26                                                            | 无 HTTP/WebSocket API；`state_snapshots` 只服务分叉；无 `claim/rewrite` 字段级留痕                                                          |
| §14 评分与复盘           | ◐  | `retro` 有 lockedroom 5 指标、转折点、决策时点复核、时间线检查、投票审计、分支对照；`scoring` 提供 F27 AI 评分、玩家六维与条件结局                                                  | 防退化清单多为文档约定，无自动检测；纯文本个人目标无机读判据时记 unscored                                                           |
| §15 路线与扩展           | ⚠  | MVP 的纯文本对局已可跑；V1 的投票/记忆/分叉已补                                                                                                | 三阶段时间表不适用；Web/TTS/赛季化 Meta-Persona/导演模式未实现；末尾三个选项未显式记录（C9）                                                             |

---

## 2. 硬冲突（先修口径或先改代码，再谈新功能）

### C1 · 产品边界（已拍板：条件冻结 G16）⚠

原始设定 §11/§15 要求「原书 → 剧本自动生成」和 UGC、Web 前端、语音扩展；项目 README / `SCHEMA.md` 明确「系统不创作剧本、无 Web UI、纯文本、以用户剧本为输入」。  
**当前按项目既定边界实现**。若要启用原书抽取流水线，属于范围变更，需要先确认，不能用"补全"名义默默塞进来。

> **2026-11-26 用户定案**：G16 整组（原书抽取 / UGC / Web / TTS / 跨本元游戏）标记为 **条件冻结**——**若不做客户端运行，则 G16 这一部分整体不做**。冻结范围包含全部五个子项，不单独解冻某一项；解冻的唯一前置是「决定做客户端运行」。在此之前 G16 不进入排期、不写验收、不占开发资源。已在 §4 执行顺序与 README 同步该口径。

### C2 · 信息不对称破口（P0，已修）

`runtime.init_runtime()` 把 `onto.clue_pool` 的**每条**线索写入**每个席位**的 `beliefs`（`belief=0.6, is_true=True`）。这与 §1.3、§5.1「每人只有世界一块碎片」直接矛盾，也让 InfoGuard 的 scope 与信念图失去意义。  
已按 `facts.visibility=public` 与角色 `known_fact_ids` 初始化；线索卡进入 `clue_pool` 不等于知识，只有释放后才把 `reveals_fact_ids` 写入各席位信念图。`is_true` 不再进入角色信念。

### C3 · F22 热度目标恒定（P0，已修）

`runtime._gm_step()` 调用 `heat_target(round_no, max(1, round_no), 3.0)`。`formulas.heat_target(t, total_rounds, ...)` 在 `total_rounds == t` 时恒返回 `3.0`：

- `heat < target - 0.3` 几乎每轮成立 → 每轮都放线索；
- `heat > target + 0.3` 永不成立 → 休整/私聊分支从未触发。  
  `T_total` 已写入 `game_state` 与 `@global` 快照，策略与 `_gm_step` 共用；旧会话无该字段时回退到已跑轮数。测试覆盖「早期高热度触发休整」。

### C4 · 谎言账本空转（P0，已修）

原状态：`lie_ledger` 初始化为 `[]` 且无写入点。现已补 `src/imms/lie.py`：F20 决策、F21 heat、创建/修订/策略升级、被相信与被揭穿双分支推理记录（`reasoning_log`），LLM Prompt 与 replay 均可读。剩余缺口只在 L4 表演投影与 G2 人格护栏，见 C5。

### C5 · 人格层数口径不一致

原始设定是 L0–L4 五层；项目是 L1–L3 三层 + `session.l0` 字段。L4 表演层缺失，Prompt 注入的是真实 L3，其他角色能"读到真心"，§3.4「看走眼」机制无法发生。

### C6 · 流程状态机口径不一致

原始设定 §8 是「自述→搜证→圆桌→私聊→质询→GM 注入→终幕」；`play` 是单一 round loop。`gm.suggest_decision` 输出的 `advance_stage` / `private_allowed` 没有任何消费者。AP、搜证、challenge token 均无；`SPEECH_BUDGET=120` 只在 LLM 路径截断，不是全局资源。

### C7 · 真相图与时间线被降级为字符串

`truth_graph` / `timeline_true` / 角色 `timeline` 都是字符串列表；`solver.audit_timeline()` 只解析时间戳做顺序检查。§7.1 的结构化三元组、§7.3 双轨时间线与 `alibi_gap` 未建立。

### C8 · 公式编号漂移（低）

早期 `memory.recall()` docstring、`llm.build_prompt()` 的 Prompt 段与 CLI 帮助写「F30」；记忆检索实际是 **F26**（`formulas.memory_score`），F30 是派生能力号（见 `derive.py` / `docs/FORMULAS.md`）。已统一为 F26 并用测试锁定；派生能力文档中的 F30 保持不变，不与检索号混用。

### C9 · 三个选项未显式落账

原始设定末尾要求拍板形态/题材/规模。项目实际选择是：真人 + AI 混编、题材无关（标准键 + 别名）、规模随剧本（2–10 席）。这是合理默认，但此前没有写进文档，现记入本节；如需改为纯 AI 自对弈或只做古典本，需重新确认。

---

## 3. 公式落地矩阵（F01–F30）

| ID  | 公式          | 状态 | 说明                                                        |
| --- | ----------- | -- | --------------------------------------------------------- |
| F01 | 戏剧张力        | ◐  | `formulas.tension()` 纯函数，未被 GM/护栏消费                       |
| F02 | 关系更新        | ✅  | `_appraise()` 调用                                          |
| F03 | 关系放大 κ      | ✅  | `_appraise()` 调用                                          |
| F04 | 个体敏感度 ν     | ✅  | `init_runtime()` 调用                                       |
| F05 | β 影响矩阵      | ✅  | `formulas.BETA_MATRIX` 被 `_appraise()` 使用                 |
| F06 | 信任不对称       | ◐  | 仅文档/配置说明；运行时统一 `λ=0.95`，无按符号分档                            |
| F07 | PAD 更新      | ✅  | `_appraise()` 调用                                          |
| F08 | 创伤放大        | ❌  | `trauma[]` 只在 L1，未触发                                      |
| F09 | 离散情绪映射      | ❌  | `emotion_shown` 默认"平静"，无 PAD→情绪映射                         |
| F10 | 阵营对齐        | ◐  | 纯函数，`faction` 恒为 `None`                                   |
| F11 | 滞回与背叛闸门     | ◐  | `faction_decision()` 纯函数，未接运行时                            |
| F12 | 怀疑 log-odds | ✅  | `_update_suspicion()` / GM 线索释放                           |
| F13 | Sigmoid     | ✅  | 同上                                                        |
| F14 | 证据权重        | ◐  | 调用时写死 `cred=0.8, strength=1.0, bias=0.1`                  |
| F15 | logLR 表     | ◐  | 配置样例；剧本权重来自 `clue_pool` 文本                                |
| F16 | 归一化投票分布     | ✅  | `vote.py` softmax；`replay.py` 反事实                         |
| F17 | 矛盾检测        | ◐  | `contradiction_strength()` 纯函数，无 `contradiction_event` 生成 |
| F18 | 泄露强度 σ      | ◐  | `derive.py` 使用；未驱动角色表演噪声                                  |
| F19 | 表现态度        | ❌  | `shown_attitude()` 未接                                     |
| F20 | 说谎收益        | ✅  | `lie.should_lie()` 接入 AI 意图分支                             |
| F21 | 谎言热度        | ✅  | `lie.update_from_events()`，被揭穿/矛盾时升级策略                    |
| F22 | 目标热度曲线      | ⚠  | 函数正确，调用错误（C3）                                             |
| F23 | 实际热度与调控     | ◐  | 只实现「放线索 / 休整」两支                                           |
| F24 | 行为效用        | ✅  | `_choose_intent()`                                        |
| F25 | 温度与 softmax | ✅  | `_choose_intent()` / `vote.py` |
| F26 | 记忆检索        | ✅  | `memory.recall()`（编号漂移见 C8）    |
| F27 | AI 评分       | ✅  | `scoring.score_sessions` 五分量加权，缺证据分量留空            |
| F28 | 可解性         | ✅  | `solver.audit_script()`        |
| F29 | 证据打分        | ✅  | `normalize.py`                 |
| F30 | 派生能力        | ✅  | `derive.py`                    |

---

## 4. 待补任务（按优先级）

| ID  | 任务                                                                                          | 依据              | 验收口径                                                                         | 旧计划 |
| --- | ------------------------------------------------------------------------------------------- | --------------- | ---------------------------------------------------------------------------- | --- |
| G01 | ~~修 `T_total`~~ **已完成**：入 `game_state` / `@global` 快照，`gm.suggest_decision` 与 `_gm_step` 共用 | C3              | 第 1 轮目标 < 第 T 轮目标；热度过高能触发休整；分叉恢复后目标一致                                        | —   |
| G02 | ~~按可见性初始化信念图~~ **已完成**：取消全场全量线索，释放线索才补 `reveals_fact_ids`                                   | C2 / §5.1       | N×F 断言：角色只持有自己 `known_fact_ids` 与已公开事实；InfoGuard scope 与信念一致                 | —   |
| G03 | ~~谎言账本闭环~~ **已完成**：LieGain、创建/heat/策略、被相信与被揭穿双分支推理记录、Prompt/replay 可见                       | C4 / §6.3       | 一局内出现可追踪谎言；reasoning_log 有 created/believed/exposed/rejustified；replay 谎言图非空 | —   |
| G04 | ~~GM 事件注入库~~ **已完成**：7 类事件 + 多变量触发 + `gm inject` + 分叉注入 + retro 审计                          | §8.2            | 每类事件有触发变量、影响变量、可见性、可回归；分叉能复现；无 LLM 决定数值                                      | —   |
| G05 | ~~L4 表演投影 + G2 人格余弦护栏~~ **已完成**                                                             | C5 / §3.4 / R2  | 感知层只读 L4；L1/L3 真值不进他人 Prompt；越阈重生成并留审计                                       | —   |
| ✅ G06 | ~~幕/阶段状态机：AP、搜证、质询 token、私聊窗口、GM 注入节点、终幕~~ **已完成**：`phases.validate_transition` 禁止跳阶；`investigation` AP 与 `stage_gate` challenge token 事件化；GM `advance_stage/private_allowed` 消费为阶段/私聊窗口状态 | C6 / §8         | 阶段顺序不可跳；AP/token 消耗可审计；`advance_stage/private_allowed` 被消费                   | #31 |
| ✅ G07 | ~~双轨时间线 + `alibi_gap` + 语义级冲突（低误报口径）~~ **已完成**：`timeline.audit` 对齐 `timeline_true` 与角色证词，只在双端有时间且对齐时报倒挂；`alibi_gap` 数值化并抬高 `exposure_risk`；`memory.detect_conflict` 低误报判定改口/撤回/交代 | C7 / §7.3       | 角色证词可对齐真相；空洞有数值；只报真实倒挂                                                       | #31 |
| ✅ G08 | ~~多结局与个人目标评分（F27 + 玩家六维）~~ **已完成**：`scoring.py` 条件结局判定、显式目标判据结算、F27/玩家六维评分；纯文本目标不猜测；runtime 落公共结局 + 席位私有目标事件；replay 第 11 节与 retro 看板可见 | §14             | 结局按脚本条件文本触发；评分可从事件复算；lockedroom 5 指标不变                                         | #32 |
| ✅ G09 | ~~运行时剧本锁 R4~~ **已完成**：`script_locks.py` 只编译显式声明的锁，output 侧拦截 taboo、终幕审计 must_say/must_trigger，分叉继承同一套规则；`solver.is_unique_path()` 使 IR3/IR4 转正 | §1 / §3.2 / IPS IR3/IR4 | 未触发/被违反的锁在 audit 中标红；不可被分叉绕过；IR3 唯一路径降级、IR4 锁/核心目标降级可审计 | #125 |
| ✅ G10 | ~~情绪与阵营运行时接线~~ **已完成**：`emotion.py` 显式创伤触发 1.8 倍放大 + tau 延长 + PAD→离散情绪；`contradiction.py` 归因事件 + arousal+0.3 / suspicion+0.2 + 下一轮优先抛出；`faction.py` 滞回阈值、2 轮冷却与忠诚闸门 | F08/F09/F11/F17 | `contradiction_event` 有见证/对象/强度；阵营变更可审计且不再恒为 `None`；未声明的创伤/阵营保持空 | #126 |
| ✅ G11 | ~~记忆四层补齐~~ **已完成**：`memory.py` 语义记忆只取公开事实/显式规则与目标；每幕/终幕生成 3 条带来源事件、`weight_factor=2` 的反思；F26 检索同时命中情节/语义/反思 | §9.3 | 反思有来源事件、权重加倍；检索可同时命中三层；replay 第 15 节与 retro 看板可见 | #127 |
| ✅ G12 | ~~盲测 oracle + 5 局自对弈平衡冒烟~~ **已完成**：`oracle.py` 公共证据盲推嫌疑人（真相只进 audit_only 对照）、作者准入校验、固定 seed 5 局 scratch DB 冒烟；CLI `oracle check/smoke`，`audit-all` 带准入标记 | §11.3 / §14.3   | 5 局可复现（seed + 版本）；记录每局胜方/冤案/冷场率，抽样看趋势，不设统计区间硬门槛；不编造数据                | #33 |
| ✅ G13 | ~~交互驱动层~~ **已完成**：`driver.py` headless 逐轮 step、输入回调/JSON 文件、按持久化 round/resume、JSONL 事件流；CLI `drive status/step/run`；`run_session(finalize=False)` 只跑单轮 | §13.2 / #34     | 现有 CLI 行为不变；新增驱动可替换输入输出                                                      | #34 |
| ✅ G14 | ~~100 套存量质量分层~~ **已完成**：`script_quality.py` 五档 tier + 来源类型 + 缺口标注；派生 `corpus/quality_manifest.json`；CLI `quality summary/build`，`audit-all --by-tier` 分层汇总 | §15 / #35       | manifest 增 quality tier；不编造数据；只改派生清单不改来源                                     | #35 |
| ✅ G15 | ~~修正 F26 编号漂移~~ **已完成**：`memory.py` / `prompt_engine.py` Prompt 段与 CLI 帮助统一写 F26；F30 只保留在派生能力文档；测试锁定 | C8 | 记忆检索不再出现 F30；F30 派生能力不受影响 | #128 |
| G16 | 原书抽取流水线 / UGC / Web / TTS / 跨本元游戏（条件冻结：以客户端运行为开关）                                       | §11 / §15       | **条件冻结（2026-11-26 用户拍板）**：仅当决定做客户端运行时才启动；**不做客户端运行则不做这一部分**。当前排期只含 G06–G15，G16 不列入待办 | —   |

### IPS v1.0 行为调制层（用户 2026-11-26 追加）

原文逐字归档于 `docs/IPS_ORIGINAL_SPEC.md`；融合审计与逐段对照见 `docs/IPS_FUSION.md`。结论：**可融入**，作为行为调制层插入 `_choose_intent` 与生成之间，不新增第二套账本。下列任务与 G01–G16 共用编号序列。


| ID | 任务 | 依据 | 验收口径 | 前置 |
|---|---|---|---|---|
| ✅ G17 | ~~蓄能器 + 常态微扰 + 安全即兴库（P0）~~ **已完成**：`ips.py` 落 `accumulator`（ρ/θ/不应期/冲击明细）与 7 项安全库，`micro_perturb` 的 `delivery` 进事件但不进真相；玩家席位按 B3 例外 | IPS §4/§9/§7.5 | AI 席位每轮产出 delivery；派生 seed 可复现；126 项测试通过 | 无 |
| ✅ G18 | ~~越阈触发 + 三档幅度 + ImprovBudget + IR1/IR2/IR5（P1）~~ **已完成**：`resolve_bursts` 令牌竞争 + `suppressed_burst` + 三策略 aftermath；IR3/IR4 仍待 G09/solver | IPS §6/§8/§10 | 同轮多人越阈只放最高者；未放行者蓄能不清空；meso/macro 必有 ≥1 后效 | G17；IR4 需 G09、IR3 需 solver 唯一路径查询 |
| ✅ G19 | ~~面具梯度 + 阴影反转 + 双目标带通（P2）~~ **已完成**：`direction.py` 三候选带通，保真/对齐用 centered OCEAN 代理，ζ 缺失降级不编造 | IPS §5/§6 | 候选被拒记原因；ζ=None 时不反转；人格锚守恒 | G05 + B2 定案 |
| ✅ G20 | ~~余波三策略 + 记忆烙印 + 级联阻尼（P3）~~ **已完成**：`aftermath.py` 三策略后果复用 lie/memory 账本，imp 烙印 τ×3，漂移 L1 范数保险丝 0.08，级联 d≤2 降档 + 配额 | IPS §11/§12 | 策略∈{regret,rationalize,escalate}；级联 d≤2 且降档；漂移只审计 | G18 + B1 定案 |
| ✅ G21 | ~~导演拨盘 5 旋钮 + retro 即兴指标看板（P4）~~ **已完成**：`director.py` 五旋钮+五预设，retro 输出 10 项指标与目标区间 | IPS §14/§15 | 拨盘可复现；有效即兴率 30–60%、余波覆盖 100%、漂移 ≤0.08 | G17–G20；指标依赖 G08/G12 |
| ✅ G22 ★ | ~~急性越界态（疯批）~~ **已完成**：`breakout.py` 三态机 + 每局 token + `suppressed_burst` 前置 + 状态带通 + 退出代价；replay 单列 | 用户 2026-11-26 追加 | `s>3.2` 仅在 frenzy 态放行且记 `state`；记忆不隔离；IR1/IR2/R4/R5 不破；token 每局 ≤1；IR4 仍待 G09 | G17–G19；IR4 需 G09 |
| ✅ G23 ★ | ~~跨局长期体验记忆~~ **已完成**：`long_term_memory` 表 + `longterm.py` 3 天半衰期/再强化/局末结算/开局 L3 先验注入 + CLI `memory longterm` | 用户 2026-11-26 追加 | 跨局可查；衰退可标 `faded`；再交互复活；**L1 不变**；证据溯源 | 需稳定 `player_id`；可与 G17 并行 |
| ✅ G24 | ~~点火 Provocation~~ **已完成（并入 G18）**：`provoke` 意图 + 玩家 accuse/ally/显式 impact 注入目标 `s_k` + AI 互燃 | 用户 2026-11-26 追加（B3） | 玩家点火抬高目标 accumulator 且落库；玩家席位无 accumulator/delivery | G17 + G04 冲击通道 |
| ✅ G25 ★★ | ~~长期人格面（facet）~~ **已完成**：`facets.py` 连续面（记忆共享、行为权重重排）vs 分离面（beliefs/memory/lie_ledger 分桶 + 记忆断层）；CLI `facets register/switch/list`；Prompt 只见行为维度 | 用户 2026-11-26 追加（状态 ≠ 面） | 两族 `memory_scope` 可验证；分离面间不共享信念；切换有 `facet_switch`/`memory_gap`；L1 不变；玩家文本无诊断名 | G17/G19/G23；分离面需 InfoGuard 面过滤 |

> 边界已拍板（2026-11-26，详见 `IPS_FUSION.md` §3）：**B1** 基础人格依原文只读、不回写；漂移只进审计。**B2** ζ 缺证据即写 `None + missing_evidence` 并降级。**B3** 玩家靠自身情绪、不进 IPS；玩家与 AI 均可点火他人（→ G24）。
> 追加需求见 `IPS_FUSION.md` §4：越界态（G22，即兴不止于安全）与跨局长期记忆（G23，约 3 天记忆期 + 再强化）。

### 核心规则模块与私有边界（用户 2026-11-26 追加）

按「guide 提示词驱动变化、IMMS 控制并记忆人物数据」的分工，已固化四个核心模块（`profile` / `prompt_engine` / `variables` / `random_source`，见 `docs/CORE_MODULES.md`）。下列任务沿用同一编号序列。

| ID | 任务 | 依据 | 验收口径 | 前置 |
|---|---|---|---|---|
| G26 | ~~lie 私有边界~~ **已完成**：`lie_ledger` 每席位独立存储与维护；公开事件在可见性层裁剪 `lie_id/inner_intent/recalled/guard`，发言者与 GM 保留全量，落库不动以便复盘 | 用户 2026-11-26「lie 只能 agent 自己看见自己、自己维护」 | 他人席位读不到私有字段；自己的谎言与推理日志仍可自洽；replay/retro 全量审计不回归 | G03 |
| G27 | ~~guide 提示词库~~ **已完成**：`guides.py` + 版本化 `guide_rules` 表，`prompt_engine` 按 purpose 注入措辞；事实 id 与 `key=value` 双重清洗 | 用户分工「guide 驱动变化」 | guide 只改措辞不改事实/数值；有 CLI 管理入口；注入前后 claimed_facts 不变 | 核心模块已落地 |
| G28 | ~~日志/记忆统一读写入口~~ **已完成**：`journal.py` 收拢 state/event 读写；状态写入落 `state_change_audit`（before/after + reason + actor），事件读分「席位可见视图」与「全量审计视图」，分支克隆同步审计链 | 用户 2026-11-26「等等吧」 | 现有 CLI 行为不变；新入口可追溯、可复现 | G26 |
| G29 | ~~真凶专用栽赃系统~~ **已完成**：`frame.py` 只为真凶席位从公开事件/已揭示线索/自身怀疑计算他人破绽候选与可引用片段，agent 按私有 prompt 段自行措辞；引擎不编造事实 | 用户 2026-11-28「真凶专用栽赃系统…提示词驱动的伪代码也可以」 | 仅真凶席位可见；候选有依据；无内部 id/真相文本进 prompt；replay/retro 可审计 | G08/G10 |

### 执行顺序（2026-11-26 定；G17–G28 全部完成）

1. ~~**G27 guide 库**~~ — 已完成，四个核心模块可用。
2. ~~**G28 日志/记忆统一入口**~~ — 已完成，IPS 接入前先收拢读写边界。
3. ~~**G17 活人感**~~ — 已完成，蓄能器与 delivery 已入运行链路。
4. ~~**G18 → G24**~~ — 已完成越阈爆发、令牌池、余波与点火。
5. ~~**G05 L4 表演层**~~、~~**G19 方向引擎**~~、~~**G22 越界态**~~、~~**G20 余波闭环**~~ — 已完成投影护栏、带通选择、受控越界与后果结算。
6. ~~**G21 拨盘与指标看板**~~ — 已完成五旋钮、五预设与 10 项 retro 指标。
7. ~~**G23 跨局长期记忆**~~ — 已完成半衰期、再强化、先验注入与 CLI。
8. ~~**G25 长期人格面**~~ — 已完成连续面/分离面、记忆隔离与切换审计；IPS 融合链 G17–G25 全部完成。
9. ~~**G06/G07 时序门控与双轨时间线**~~ — 已完成阶段跳阶拒绝、AP/质询 token 审计、GM 阶段开关消费、真倒挂与 `alibi_gap`。
10. ~~**G08 多结局与个人目标评分**~~ — 已完成条件结局、显式目标判据、F27 与玩家六维评分；未给判据的纯文本目标记 `unscored`，不按措辞猜。
11. ~~**G12 盲测 oracle 与自动验收**~~ — 已完成公共证据盲推、作者准入与 5 局可复现冒烟，`audit-all` 带准入标记。
12. ~~**G13 交互驱动层**~~ — 已完成 headless 逐轮驱动、resume 与事件流，不引入 Web UI。
13. ~~**G14 剧本质量分层**~~ — 已完成五档质量标签、来源类型、缺口标注与派生清单；100 套盒装本标 `explicit_partial`、6 套显式真凶本标 `explicit_full`，不补造缺失字段。
14. ~~**G09 剧本锁 R4**~~ — 已完成显式锁编译、输出侧硬拦截、终幕红标与分叉继承；IR3/IR4 由此转正。
15. ~~**G10 情绪与阵营接线**~~ — 已完成创伤触发器、矛盾事件归因与数值、PAD→离散情绪、阵营滞回/冷却/忠诚闸门。
16. ~~**G29 真凶栽赃系统**~~ — 已完成破绽候选评分、可引用事实片段、真凶私有 prompt 块与 GM 审计；自然语言栽赃由 agent 生成，引擎不写假事实。
17. ~~**G11 语义与反思记忆**~~、~~**G15 F26 编号修正**~~ — 已完成四层记忆补齐与编号锁定。**G16 条件冻结**——若不做客户端运行则不做这一部分（用户 2026-11-26 定案，见 C1）。解冻条件只有一个：决定做客户端运行。

> 旧计划 #31–#35 已完成（G06/G07/G08/G12/G13/G14）；本表把它们映射到具体可执行项，不新增第二套编号。

---

## 5. 执行约束（不可退让）

1. **数值归公式，语言归模型，真相归引擎**：任何新事件只能由规则/阈值触发，LLM 不做数值裁决。
2. **缺证据不编造**：`value=None + missing_evidence`；补全只用来源已有证据。
3. **可回放**：所有新状态入 `game_events` / `attribute_events` / 快照；分叉能确定性复原。
4. **信息隔离**：新增线索/事件必须同步 InfoGuard 的 detector 与 context guard 两侧豁免。
5. **不依赖 `refer/`**：语料自持在 `corpus/`。
6. **回归基线**：每加一项补对应 unittest；83 项既有测试不得回归。
