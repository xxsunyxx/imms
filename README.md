# IMMS v0.1

用户给一份现成剧本 → 系统解析、归一化、装配 GM + N−1 角色 agent + 1 用户席位并开局。系统不创作剧本。

## 原始设定与差距

用户提供的 IMMS v1.0 全文逐字存于 `docs/ORIGINAL_SPEC.md`；与现有实现的冲突、公式落地情况和待补任务见 `docs/GAP_AUDIT.md`。当前范围冻结为「现成剧本装配 + 纯文本对局」，原书自动生成 / Web / TTS 未纳入。

## 核心规则模块

分工：**guide 提示词驱动变化，IMMS 控制和记忆人物数据**。核心模块把规则固化进 `src/imms/`：人物抽取（`profile`）、提示词生成引擎（`prompt_engine`）、变量计算（`variables`）、确定性随机源（`random_source`）、guide 提示词库（`guides`）。CLI 见 `imms core --help` / `imms guides --help`，说明见 `docs/CORE_MODULES.md`。

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
```

LLM 台词（可选）：设 `IMMS_LLM_URL` / `IMMS_LLM_MODEL` 后 `play --llm auto`；未配置时用离线模板。LLM 只写台词，不碰数值与事实。

## 记忆与承诺

运行时为每个席位维护听众账本（谁在何时对谁说了什么）、承诺状态机（`stated → revised/weakened → retracted`）与 F26 召回评分。召回结果只进 Prompt 与事件载荷，不生成新事实、不改数值。GM 分叉 diff 会列出记忆与承诺差异。

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

## 批量导入 100 套盒装本

```bash
uv run imms --db imms.db import-boxed --dir refer/mystery-scripts-100/盒装本
uv run imms --db imms.db scripts
uv run imms --db imms.db assemble --script-id C01 --human c01_r02
uv run imms --db imms.db play --session C01-s001 --rounds 2 \
  --scripted examples/scripts/boxed_user_script.json --llm template
```

四类各 25 套（搞笑/恐怖/职场/反转），2–4 人本均可装配。线索文本取自清单；抽验 C01/H01 图片与清单一致，故不启用 OCR。映射与注意事项见 `docs/BOXED.md`。详见 `docs/RUNTIME.md`。

## 自持语料库（外部 refer 可删除）

```bash
uv run imms --db corpus.db build-corpus
uv run imms --db corpus.db corpus-report
uv run imms --db corpus.db import-corpus --dir corpus
```

`corpus/` 保存来源副本、标准 Ontology 与准入清单；`refer/` 删除后仍可重建数据库。本次从 9 个候选中收 6 个纯文本推理本，放弃 3 个：mossvale 两个故事依赖鼠标实景搜证，《西游记·火焰山》没有凶手/证据链/结局裁决。缺失字段的补全只使用来源已有证据，全部记在 `completions[]` 与 manifest 中；规则见 `docs/CORPUS.md`。

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
- `docs/BOXED.md` — 100 套盒装本批量导入与映射
- `docs/CORPUS.md` — 自持语料库、准入门禁与缺陷补全
- `docs/INGEST.md` — 统一摄入管线、格式适配与自持存储
- `docs/REGISTRY.md` — 骨架夹具、LLM 边界与剧本生命周期管理
- `docs/SOLVER.md` — 可解性求解器、真值模型规则与时间线系统
- `docs/GM.md` — GM 决策策略、分支树、合并回滚与叙事 diff
- `docs/RETRO.md` — 复盘回溯评估指标与决策时点复核
- `docs/VOTE.md` — 投票规则、多轮复投、AI 策略与票据审计
- `docs/CORE_MODULES.md` — 核心规则模块：人物抽取、提示词引擎、变量计算、确定性随机源、guide 库、谎言私有边界
- `docs/MEMORY.md` — 听众账本、承诺状态机与 F26 召回
- `docs/ORIGINAL_SPEC.md` — 用户提供的 IMMS v1.0 原始设定（逐字归档）
- `docs/GAP_AUDIT.md` — 原始设定 vs 现状：冲突、公式落地矩阵与待补任务（含 IPS G17–G25、G26–G28 与下一批执行顺序）
- `docs/IPS_ORIGINAL_SPEC.md` — 用户提供的 IPS v1.0 即兴扰动系统原文（逐字归档）
- `docs/IPS_FUSION.md` — IPS 九段流水线对照、可融入性结论与已定案边界；区分急性越界态（疯批）与长期人格面（黑化/病娇/DID，按记忆作用域分族），含跨局体验记忆，分期 G17–G25
- `docs/EVENTS.md` — GM 事件注入库：七类外部扰动与多变量触发

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
