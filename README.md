# IMMS v0.1

用户给一份现成剧本 → 系统解析、归一化、装配 GM + N−1 角色 agent + 1 用户席位并开局。系统不创作剧本。

## 原始设定与差距

用户提供的 IMMS v1.0 全文逐字存于 `docs/ORIGINAL_SPEC.md`；与现有实现的冲突、公式落地情况和待补任务见 `docs/GAP_AUDIT.md`。当前范围冻结为「现成剧本装配 + 纯文本对局」。**G16（原书抽取流水线 / UGC / Web / TTS / 跨本元游戏）已标记为条件冻结：若不做客户端运行，则这一部分整体不做**；解冻的唯一前置是决定做客户端运行。详见 `docs/GAP_AUDIT.md` C1 与 §4 执行顺序。

## 核心规则模块

分工：**guide 提示词驱动变化，IMMS 控制和记忆人物数据**。核心模块把规则固化进 `src/imms/`：人物抽取（`profile`）、提示词生成引擎（`prompt_engine`）、人格→说话风格引擎（`speech_style`，80% 锚点 / 20% 受控漂移，逐轮漂移入账；`style_compiler` 在 LLM 路径把关键词编译成连贯风格指令并逐字/数值校验；`performance_guide` 把本轮意图/怀疑/情绪零 LLM 映射为表演指引，只改措辞）、变量计算（`variables`）、确定性随机源（`random_source`）、guide 提示词库（`guides`）。CLI 见 `imms core --help` / `imms guides --help` / `imms style ledger --help` / `imms style compiled --help`，说明见 `docs/CORE_MODULES.md` 与 `docs/SPEECH_STYLE.md`。

人设有两条来源，互不混淆：带行为证据句的 `canon_evidence` 经 F29 锚点打分进 L1 数值键（`source=canon_extract`）；剧本自述的 `personality` / `speaking_style` / `lexicon` 逐字进 L1 文本槽位（`source=script_text`，不打分、缺则 `None + missing_evidence`）；`public_identity` 属 L2，装配时从剧本写入 `persona_l2` 并回填席位视图。三者共同让 Prompt 身份段不再全为「（未配置）」。

谎言账本严格私有：每个 agent 只看见并维护自己的谎言；`lie_id / inner_intent / recalled / guard` 在可见性层对他人（含玩家）裁剪，发言者与 GM 保留，落库不受影响以便复盘。见 `docs/CORE_MODULES.md` 的私有边界段。

所有会话状态与事件读写统一走 `journal.py`：`game_state` 每次写入落 `state_change_audit`（改前/改后 + reason + actor），事件读取区分席位可见视图与全量审计视图，分叉时审计链一并克隆。可用 `imms core journal --session <id> --round N [--seat X | --full] [--audit]` 复查。见 `docs/MEMORY.md`。

IPS 融合已落 P0/P1（G17、G18、G24）：`ips.py` 用 `s_k` 冲击表与泄漏积分器维护 AI 席位蓄能，按确定性种子产出 `delivery` 表达参数；越阈后经 Kairos 与 `ImprovBudget` 令牌竞争，同轮只放一位席位爆发，未放行者的蓄能不清空并记 `suppressed_burst`，meso/macro 强制带余波。玩家可用 `provoke` / `accuse` / `ally` 点火目标，但玩家席位自身不进 IPS。即兴产出只标 `unverified_claim`，不改真相图。见 `docs/IPS_FUSION.md`。

## 安装

```bash
uv sync
```

## 端到端示例

```bash
uv run imms --db imms.db parse --script examples/scripts/snow-night-villa.yaml
uv run imms --db imms.db normalize --script-id snow-night-villa
uv run imms --db imms.db assemble --script-id snow-night-villa --human lin_wanqiu
uv run imms --db imms.db persona show --script-id snow-night-villa --char lin_wanqiu
uv run imms --db imms.db derive --script-id snow-night-villa --char lin_wanqiu --key observation
uv run imms --db imms.db compare --script-id snow-night-villa --a lin_wanqiu --b zhou_muchen
uv run imms --db imms.db assigned set --session snow-night-villa-s001 --char lin_wanqiu --key public_identity --value '"山庄管家"' --reason '第二幕身份揭示' --round 2
uv run imms --db imms.db emergent set --session snow-night-villa-s001 --char lin_wanqiu --key trust --target gu_chengye --value -0.2 --reason '谎言被戳穿' --round 3
uv run imms --db imms.db audit --session snow-night-villa-s001 --char lin_wanqiu
uv run imms --db imms.db play --session snow-night-villa-s001 --rounds 2 --scripted examples/scripts/user_script.json --seed 42
uv run imms --db imms.db private open --session snow-night-villa-s001 --round 3 --seats human:lin_wanqiu,ai:gu_chengye
uv run imms --db imms.db private say --session snow-night-villa-s001 --room snow-night-villa-s001-p001 --actor human:lin_wanqiu --to ai:gu_chengye --utterance "我听到书房里有争执。"
uv run imms --db imms.db private close --session snow-night-villa-s001 --room snow-night-villa-s001-p001
uv run imms --db imms.db infoguard scopes --session snow-night-villa-s001
uv run imms --db imms.db infoguard check --session snow-night-villa-s001 --char gu_chengye --text "死者死于过量胰岛素注射"
uv run imms --db imms.db replay --session snow-night-villa-s001 --out replay.md
uv run imms --db imms.db branch fork --session snow-night-villa-s001 --at-round 2 --override-round 2 --override-utterance "我承认偷看过遗嘱" --out branch_report.json

# 剧本准入 / 可解性审计
uv run imms --db imms.db audit-script --script-id snow-night-villa --out audit.json
uv run imms --db imms.db audit-all --out audit_all.json

# GM 决策核：策略 → 分支树 → 叙事 diff → 合并/回滚
uv run imms --db imms.db gm decide --session snow-night-villa-s001 --round 2 --policy adaptive
uv run imms --db imms.db gm branch --session snow-night-villa-s001 --at-round 2 --label "改口" --override-round 2 --override-utterance "我只相信自己" --policy information
uv run imms --db imms.db gm tree --session snow-night-villa-s001
uv run imms --db imms.db gm diff --branch snow-night-villa-s001-gm001
uv run imms --db imms.db gm merge --branch snow-night-villa-s001-gm001 --rationale "反事实基准"

# 复盘回溯评估（5 指标 + 决策时点复核 + 分支对照）
uv run imms --db imms.db retro --session snow-night-villa-s001 --out retro.json --markdown retro.md
uv run imms --db imms.db replay --session snow-night-villa-s001 --with-eval --out full_replay.md

# 盲测 oracle / 作者准入 / 5 局固定 seed 冒烟
uv run imms --db imms.db oracle check --session snow-night-villa-s001 --out oracle.json
uv run imms --db imms.db oracle smoke --script-id snow-night-villa --games 5 --rounds 2 --out smoke.json
uv run imms --db imms.db audit-all --out audit.json

# headless 逐轮驱动（会话可恢复，事件流为 JSONL）
uv run imms --db imms.db drive status --session snow-night-villa-s001
uv run imms --db imms.db drive step --session snow-night-villa-s001 --rounds 3 --utterance "我只说公开信息" --out events.jsonl
uv run imms --db imms.db drive run --session snow-night-villa-s001 --inputs inputs.json --phases --out events.jsonl

# 剧本质量分层（派生清单，不改来源）
uv run imms --db imms.db quality summary
uv run imms --db imms.db audit-all --by-tier
uv run imms --db imms.db quality build --out corpus/quality_manifest.json
```

LLM 台词（可选）：设 `IMMS_LLM_URL` / `IMMS_LLM_MODEL` 后 `play --llm auto`；未配置时用离线模板。LLM 只写台词，不碰数值与事实。

## 记忆与承诺

运行时为每个席位维护听众账本（谁在何时对谁说了什么）、承诺状态机（`stated → revised/confessed/weakened → retracted`）与 F26 召回评分。新供述与旧供述共享事实且出现否定/改口/撤回时，写 GM-only `statement_conflict`（带归因与强度）；双轨时间线只在证词与真相同步倒挂时报警，并用 `alibi_gap` 数值抬高暴露风险。召回结果只进 Prompt 与事件载荷，不生成新事实、不改数值。GM 分叉 diff 会列出记忆与承诺差异。

```bash
uv run imms --db imms.db memory show --session snow-night-villa-s001 --seat ai:gu_chengye --cue "书房 争吵"
uv run imms --db imms.db memory commitments --session snow-night-villa-s001 --seat human:lin_wanqiu
```

详见 `docs/MEMORY.md`。

## 谎言推理闭环与事件注入

每个 agent 维护 `lie_ledger` + `reasoning_log`：说谎由 F20 决定，被相信或被揭穿都会写入一次修订，heat>0.8 时发言前强制重新自圆其说。Prompt 与复盘均可追溯「何时说谎、谁相信、谁戳穿、如何改口」。

GM 事件注入库支持 7 类外部扰动（第二具尸体/停电/信件/匿名举报/旧情复燃/酒后失言/天气），由局势变量确定性触发：

```bash
uv run imms --db imms.db gm inject --session S --round 2 --dry-run
uv run imms --db imms.db gm inject --session S --round 2 --event blackout
uv run imms --db imms.db gm branch --session S --at-round 2 --inject-event blackout
```

注入写入 `game_events` / `attribute_events`，复盘追加「GM 事件注入审计」。详见 `docs/EVENTS.md`。

## 投票体系

```bash
uv run imms --db imms.db vote run --session snow-night-villa-s001 --scripted vote_script.json
uv run imms --db imms.db vote cast --session snow-night-villa-s001 --voter human:lin_wanqiu --target zhou_muchen
uv run imms --db imms.db vote replay --session snow-night-villa-s001
uv run imms --db imms.db vote audit --session snow-night-villa-s001
uv run imms --db imms.db vote config --session snow-night-villa-s001
```

投票支持弃票/自我投票拦截、非法目标处理、证据平票裁决、1–5 轮复投改票、AI `argmax`/`persona` 策略与完整票据账本。`play` 自动投票并保持旧的 `scripted.vote` 兼容；`scripted.votes` + `vote_config` 可指定多轮票型。复盘会附加投票审计。详见 `docs/VOTE.md`。

## 结局与个人目标评分

每局投票结束后由 `scoring.py` 从事件溯源复算：条件结局（脚本 `endings` 里的 `condition`，支持 `solved` / `tie` / `winner` / `motive_correct` / `min_clue_coverage` / `min_goal_completion` / `max_violations` 与 `all`/`any`/`not`，无 `eval`）、真凶策略行为记录、F27 五项 AI 评分与玩家六维评分。公共事件 `ending_settled` 只带角色姓名和评分，不含内部席位 id；个人目标逐席写 `goal_settled`（可见性仅本人 + GM）。只有脚本给出 `check` 判据（secret / identify / escape / frame / vote_for）的目标才判完成，纯文本目标记 `unscored`，不按措辞猜测。复盘第 11 节与 `retro` 看板都会显示结算；旧会话没有落库评分时按需离线复算。

## 盲测 oracle 与准入

`oracle.py` 的盲测只从公开线索推导嫌疑人；真相仅在同一份报告的 `audit_only` 段用于事后对照，不参与推导。`oracle check --session` 出单局报告，`oracle check --script-id` 出静态作者准入；`audit-all` 在每条 solvability 摘要上附 `authoring` / `admitted` 门禁。`oracle smoke` 在独立临时库用固定 seed 跑 5 局（默认 2 轮、模板 LLM、轮换固定票型），记录每局胜方、冤案、冷场率和指纹；小样本只报告观察值，不做统计区间硬门槛。

## Headless 交互驱动

`driver.py` 不另造引擎：每次 `step` 调一次现有 runtime，`finalize=False` 只跑当前讨论轮，最后一轮才投票、结算结局与个人目标。`drive status` 从持久化 `round` / `game_events` 复原游标（进程重启后可续），`drive step` 逐轮送用户发言，`drive run` 从 JSON 数组读每轮输入，均可把新增事件追加成 JSONL 事件流。事件是宿主侧全量审计视图，带 `visibility` 字段，消费方需自行按席位过滤；不提供 Web UI 或 WebSocket 协议。

## 剧本质量分层

`script_quality.py` 按已存字段把剧本分五档（`explicit_full` / `explicit_partial` / `explicit_minimal` / `narrative_only` / `insufficient`），并标来源类型（盒装本 md、显式 yaml、RPG JSON、配置、未知）与具体缺口（无真凶、无线索、无结构化事实、无时间线等）。当前存量：100 套盒装本为 `explicit_partial`（有真凶与线索但无 `timeline_true`），6 套显式真凶本为 `explicit_full`。`quality build` 只写派生 `corpus/quality_manifest.json`，不改 `manifest.json` 或来源字节；`audit-all --by-tier` 出分层汇总。

## 统一摄入：先骨架，再归一化

```bash
uv run imms ingest detect --path <任意来源>
uv run imms ingest skeleton --path <来源> --llm auto --out skeleton.json
uv run imms ingest convert --path <来源> --llm template --out ontology.json
uv run imms ingest add --path <来源> --root corpus
uv run imms ingest batch --path <目录> --root corpus
uv run imms ingest report && uv run imms ingest validate --file corpus/scripts/rpg_last_train.json
```

YAML / 盒装本 Markdown / RolePlayingGame JSON / 旧式分段文本 / 标准 Ontology 走同一条 `detect → admit → skeleton → 补全 → canonical` 管线；需要鼠标搜证或图片认物的本子直接 SKIP 并留原因。LLM 只在固定槽位里摘要，越界内容丢弃，不生成人物、时间、事实或数值。详见 `docs/INGEST.md`、`docs/REGISTRY.md`。

## 剧本管理

```bash
uv run imms script list --status normalized
uv run imms script show --script-id rpg_last_train
uv run imms script skeleton --script-id rpg_last_train --version 1
uv run imms script history --script-id rpg_last_train
uv run imms script promote --script-id rpg_last_train --status ready --notes "audit passed"
```

每个本子有生命周期（staged → normalized → ready → archived，或 rejected）、按版本追加的骨架和事件日志。`parse` / `import-corpus` 会自动登记；详见 `docs/REGISTRY.md`。

## 单测

```bash
uv run python -m unittest discover -s tests
```

## 剧本格式

真实剧本见 `examples/scripts/*.yaml`（取自 lockedroom-ai）。结构：`facts`（`id/visibility/core/content/keywords`）、`characters`（含 `known_fact_ids/goals/timeline/speaking_style`）、`clues`（`reveals_fact_ids/stage`）、`evaluation`（`killer_id/motive_fact_id/required_fact_ids`）。旧 markdown 分节格式仍兼容，新剧本优先 yaml。

真实剧本通常不含行为证据句。归一化对无 `canon_evidence` 的角色产出 `value=null` 并在报告的 `missing_evidence` 中列名，不编造分值；对局用中性默认值，属性补齐后重跑 `normalize` 生成新版本。

## 文档

- `docs/ASSEMBLY.md` — 建局装配
- `docs/SCHEMA.md` — 属性信封与标准键
- `docs/NORMALIZATION.md` — 归一化规则
- `docs/FORMULAS.md` — F01–F30 公式登记
- `docs/STORAGE.md` — SQLite 表与只读约束
- `docs/RUNTIME.md` — 对局引擎与回合循环
- `docs/INFOGUARD.md` — 信息边界四层引擎与参考取舍
- `docs/INGEST.md` — 统一摄入管线、格式适配与自持存储
- `docs/REGISTRY.md` — 骨架夹具、LLM 边界与剧本生命周期管理
- `docs/SOLVER.md` — 可解性求解器、真值模型规则与时间线系统
- `docs/GM.md` — GM 决策策略、分支树、合并回滚与叙事 diff
- `docs/RETRO.md` — 复盘回溯评估指标与决策时点复核
- `docs/VOTE.md` — 投票规则、多轮复投、AI 策略与票据审计
- `docs/CORE_MODULES.md` — 核心规则模块：人物抽取、提示词引擎、变量计算、确定性随机源、guide 库、谎言私有边界
- `docs/MEMORY.md` — 听众账本、承诺状态机与 F26 召回
- `docs/ORIGINAL_SPEC.md` — 用户提供的 IMMS v1.0 原始设定（逐字归档）
- `docs/GAP_AUDIT.md` — 原始设定 vs 现状：冲突、公式落地矩阵与待补任务（G06/G07/G08 已完成；IPS G17–G25、G26–G28 已完成；G09–G15 待办）
- `docs/IPS_ORIGINAL_SPEC.md` — 用户提供的 IPS v1.0 即兴扰动系统原文（逐字归档）
- `docs/IPS_FUSION.md` — IPS 九段流水线对照、可融入性结论与已定案边界；区分急性越界态（疯批）与长期人格面（黑化/病娇/DID，按记忆作用域分族），含跨局体验记忆，分期 G17–G25
- `docs/EVENTS.md` — GM 事件注入库：七类外部扰动与多变量触发
- `docs/JUBENSHA_RULES_AND_GM_PLAN.md` — 剧本杀通用玩法/DM 职责调研（带来源）与 IMMS GM 工作安排（已确认：阶段化 + 搜证私聊第一轮 + replay 内 DM 复盘）

## 已踩过的坑

- L1 只读不能只靠 CLI 判断；用 SQLite 触发器硬拦 UPDATE/DELETE。
- 归一化得分只能取五档锚点值；自由小数会让审计失去意义。
- 派生值读取时算，不落库；否则同一事实存两份，改基础值不会同步。
- 用户席位不由 LLM 代打；AI 角色与用户席位必须区分 `kind`。
- LLM 输出必须裁剪 `claimed_facts` 并强制 `act_type`；让模型决定"说什么"可以，决定"知道什么"不行。
- 私聊与圆桌必须共用一个自增 `seq`；分表计数会在并发时错序。
- 被线索指向的角色应更新 `exposure_risk` 而非对自身的 `suspicion`；否则复盘会出现"自己怀疑自己"。
- 分叉必须落在轮末快照边界（第 k 轮开分叉 = 恢复第 k−1 轮末），不能从事件日志现推状态；重复施加评价会双重更新关系。
- GM 的核心能力是分叉回归，不是叙事润色：每次分叉都要校验数值域、信息隔离、事实已知与可解性。
- InfoGuard 只管 AI 生成的台词；人类玩家说什么都合法，只在别人感知时把超知内容包成传闻。
- 关键词命中默认只标记不改写；judge 解析失败必须按 `confirmed` 处理，不能"失败即放行"。
- 线索释放后必须在 detector 与 context guard 两侧同步 `exempt_fact`，否则已公开线索会被误判为剧透。
- 线索指向与投票目标都必须经席位表解析角色 id；硬编码 `ai:` 前缀会在人类角色被指向时产生幽灵席位。
- 分叉回归的 `solvable` 只能校验投票机制合法，不能拿「玩家是否破案」当完整性指标。
- 外部参考目录会被删除：可用剧本必须在 `refer/` 还在时转成项目自持的 `corpus/`，运行链路不得引用 `refer/` 路径。
- 需要鼠标地图、实景搜查或靠图片认物的本子不是纯文本剧本杀，直接 SKIP；不要为了“装进去”而改写玩法。
- 剧情逻辑缺口只能从来源已有证据补全（死者标注、线索回指、问询提示）；补不出来的就放弃，绝不编造时间与事实。
- `"|".join(单个字符串)` 会把字符串逐字符拆开：corpus 的线索权重曾变成 `|l|o|g|L|R|`，运行时正则解析不到，GM 放线索后怀疑度不更新。权重必须直接 f-string 拼接；求解器的 `clue_weights_parseable` 会抓这种退化。
- 完整真相是证据链合成的结果，不该要求单条线索逐字揭示；但"只回指完整真相"的 R3/R5 补全是提示而不是证据链接，必须单独标记。
- 时间线没有时间戳只是 warning，不是矛盾；只有真实的时间倒挂才阻断可解性判定。
- 复盘评分里的动机指认必须显式提供（`scripted.motive_fact_id`）；不能替玩家把"投中凶手"自动算成"动机也正确"。
- 骨架必须先于归一化：LLM 只做槽内摘要，任何未在原文逐字出现的句子都丢弃；否则"补全"会变成编剧情。
- 结构化本子在 `admit` 之后才调用下游 parser；否则 `no_case` 这类叙事片段会用解析异常盖掉真正的门禁原因。
- manifest 汇总必须在每次 upsert 后重算；两个不同 SKIP 文件可能哈希相同，不能只按哈希去重。
- 平票不能用 `max(tally, key=tally.get)`：它依赖字典插入顺序；投票平局必须由证据分数或显式规则裁决。
- 多轮投票的 `majority_stop=false` 表示继续投满指定轮次，不能写成 `if not majority_stop: break`，否则第二轮永远不会发生。
- 运行时每轮都会重读此前事件，记忆账本必须按事件 `seq` 去重，否则同一句话会被反复评价与召回。
- 记忆检索是 F26（`formulas.memory_score`），F30 是派生能力；任务编号与公式编号不要混用。

L4 表演层已落 G05：`l4.py` 把他人的 L1 真值 + L2 面具 + L3 状态投影成每席位专属的可见表现，泄露噪声按派生种子抽取；R2 以 OCEAN/values 中立基线的余弦代理做护栏，越阈重生成或回收并写 `l4_guard` 审计事件。其他角色 Prompt 只读该 L4 投影，读不到他人 L1/L3 真值。可用 `imms core prompt --session S --seat X --perceived` 检查。

方向引擎已落 G19：`direction.py` 从 L2 面具生成 canon→mask 梯度，ζ_f 只在数据存在时启用阴影反转；三候选经 s∈[1.2,3.2]、F≥0.72、方向对齐带通筛选，被拒原因随 `improv_burst.direction` 落库。保真用 centered OCEAN 规则代理而非向量库，L1 全程只读。

急性越界态已落 G22：`breakout.py` 用 `normal/charged/frenzy` 三态机管理 1–2 轮疯批窗口，入界消耗全局 token（默认每局 1 次）且必须已有 `suppressed_burst`；带通上界按态放宽到 3.6/4.6，但 IR1/IR2/R4/R5 与真相图不变，记忆始终连续。退出一次性结算 exposure_risk +0.3、morale −0.2 与不可回收的关系损失，并在 replay 第 6 节单列「越界时刻」。

匿名行为原型库：`config/archetypes.json` 收录九张只含特质与习惯用语的人物卡，已用 `imms import-archetypes` 注入 `imms.db`（`trait-archetypes-001`）。来源角色名与作品名不入库，姓名由固定种子随机生成；`archetypes.validate_cards()` 用摘要黑名单拦截原名回潮。可用 `imms persona --script-id trait-archetypes-001 --char arch_01 show` 查看特质、证据与习惯用语。见 `docs/MEMORY.md`。

余波闭环已落 G20：`aftermath.py` 把后悔/圆场/加码三种策略落到既有的关系、情绪、谎言与记忆账本（不新建第二套）。即兴记忆按 `0.6+0.4I` 烙印、τ×3 并带情绪一致标签；自我概念漂移只审计、L1 范数保险丝 0.08；级联传染限 2 跳、逐跳降档并受全局令牌配额约束。

导演拨盘已落 G21：`imms director --session S --preset shakaiha [--set improv_gain=1.5]` 把 5 个旋钮（戏浓度/混乱度/阴影敏感/泄露率/爆点配额）写入 `session.l0`，运行时按派生值改 θ、策略温度、级联概率、ε 与阴影系数；无拨盘的旧会话保持中性。`retro` 现在输出 10 项 IPS 指标看板（有效即兴率、密度、意外度/保真度均值、OOC 拒绝率、兜底占比、级联深度分布、余波覆盖、人格漂移、可解性保持）。见 `docs/IPS_FUSION.md`。

跨局长期记忆已落 G23：`long_term_memory` 表记录角色对稳定 `player_id` / NPC 的好恶与输赢，`longterm.py` 按 3 天半衰期衰减，低于 0.15 标 `faded`（不删除），再交互会重置时钟并增量强化；局末自动结算，开局把未衰退的好恶注入 L3 关系先验（带证据溯源），L1 不变。可用 `imms memory longterm [--owner X] [--as-of ISO]`、`--settle --session S`、`--forget` 管理。见 `docs/IPS_FUSION.md`。

长期人格面已落 G25：`facets.py` 把「面」作为 L2 派生档案（不回写 L1）。连续面记忆共享、只重排行为权重；分离面切换时真正交换 `beliefs/memory/lie_ledger` 桶并写 `facet_switch` + `memory_gap`，InfoGuard 作用域按面过滤。只存 12 个行为维度，不存临床/流行标签。用 `imms facets register --session S --seat X --facet F --kind continuous|dissociative --dim KEY=VALUE --derived NOTE` 注册，`imms facets switch --facet F`（省略则回基面）切换。至此 IPS 融合链 G17–G25 全部完成。

GM 叙事/导演层：`gm_narrator` 按轮把已公开前情与本轮事件讲成连续场景，并按焦点角色个性调视角；`gm_direction` 让 GM 读到全本真相，在 GM prompt 的「导演视角」段掌握真凶、手法与私怨，同时给各席位注入自己的私人目标、暗线与真凶的隐藏/搅浑水/栽赃策略。导演信息只进各自私有 prompt，公开叙述由 `parse_narration` 拦截事实编号、数值、未公开线索关键词与自造台词；席位 `claimed_facts` 仍由 InfoGuard 裁剪。

每轮 GM 只输出一次场景叙述；「跑偏拉回」是干预而非常规输出，仅在人类玩家把话题带离公开记录且至少两名 AI 席位跟随时触发。选角只展示公开人设与角色姓名，绝不标注真凶；席位 agent 与 GM agent 在协议和报告里一律用剧本里的角色姓名（如「郑可欣」「主持人」），不使用 `ai:<id>`、`actor-xxx` 等内部标识。

复盘首先输出「对局实录（按轮）」：每轮按 GM 叙述 / 跑偏拉回、公开线索、各席位发言（含意图与对象）的顺序展开，随后单列投票段（谁投谁、票数、裁定、GM 揭晓）。原先按角色分组的「内心独白」等内容保留为后续审计与指标明细，不再充当主线叙事。

GM 场景叙述必须带信息：prompt 的「案情事实」段由剧本 brief、public facts、已公开线索及这些线索证实的私有事实组成（数字转成中文措辞），叙述校验会拒绝不含任何案情锚点的空泛文字；回退叙述也至少带一条客观事实。未揭示的真相仍只留在 GM 独知的导演段，线索一旦公开，其证实的事实才进入可讲述范围。

跑偏拉回不再单独生成：每轮只有一次 GM 叙述。检测到「人类把话题带离公开记录且至少两名席位跟随」时，拉回作为「本轮拉回指引」写进同一份叙述 prompt，靠叙事中的具体转场完成，不另起主持词。历史库里的独立 `gm_redirect` 事件在复盘中会折叠回该轮 GM 叙述行。

第一轮剧本杀骨架已落地（P0–P3）：`phases.py` 定义 `intro → self_intro → investigate → discuss → vote → reveal` 六阶段状态机，`runtime.run_phased_session` 编排全程，旧 `run_session` 保持原行为；CLI 用 `imms play --session S --rounds 3 --phased` 跑完整流程。`investigation.py` 提供行动点、线索卡持有与公开/隐藏决定：公开线索进 `revealed_clues` 并由 InfoGuard 解锁事实，隐藏线索只对发现者可见；私聊复用房间机制（`room:<id>` 可见性），GM 不决定线索内容。席位 prompt 新增玩家规则块（第一人称、角色姓名互称、不自曝、不贴脸、不跳车），GM prompt 新增 DM 职责与禁忌。

`replay` 现在是「对局实录（含开场/读本/搜证）→ 主持复盘 → 审计明细」三段式。`gm_debrief.py` 确定性生成 DM 讲述式复盘（铺垫 → 手法/时间线 → 动机/关系 → 互动提问 → 结束语），投对先讲人物关系与动机、投错先讲手法与时间线；真相只在复盘段公开，不调用 LLM。端到端自检 `output/phase_e2e_check.py` 产出 `phase_e2e.json` 与两种分支复盘样本（仅验接线，不论文风）。

P5 真 agent 验收（4 个独立 agent 读 prompt 写回复，无桩）：原生 4 人本 midnight-meeting，人类坐沈巍，5 轮完整阶段流程。结果：六阶段齐备、15 次席位生成 0 回退、GM 9 次调用（3 阶段串场 + 5 场景叙述 + 1 投票揭晓）全部合规、投票 3:1 命中真凶、破解成功。真 GM 叙述按轮累积案情（反锁/无搏斗 → 发绀对心衰 → 奶泡剂 → 抽屉文件 → 平票收束），按角色个性调视角，无数值/真相/票数泄露。产物在 `output/phased_replay.md`、`phased_summary.json`、`phased_protocol/prompts|replies/`。

真跑暴露并修复一个信息边界 bug：席位 prompt 把线索展示串里的 `|logLR:han_ziang=1.5` 直接送给 agent，导致台词念出「一点五」。新增 `infoguard.display_text()` 统一清洗 prompt 展示文本（id 前缀 + logLR 权重），不改变 `claimed_facts` 白名单与数值计算；修复后端到端自检席位 prompt 中 logLR 出现次数为 0。`phased_replay.md` 是修复前真跑的原始记录（`seat_hygiene_driver.py` 可用于复验修复后的席位台词）。
