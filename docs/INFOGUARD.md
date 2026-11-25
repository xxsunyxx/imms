# InfoGuard：信息边界引擎

来源：`refer/lockedroom-ai/backend/app/core/infoguard/` 四层设计，已移植为 `src/imms/infoguard.py`，适配 IMMS 的 YAML Ontology。定位：**信息隔离的硬边界**，与 `claimed_facts` 白名单互补——白名单管结构化声明，InfoGuard 管自由文本。

## 1. 四层

| 层 | 实现 | 作用 |
|---|---|---|
| L1 scope | `compile_scopes(onto)` → `InformationScope` | `known = public facts ∪ character.known_fact_ids`；`forbidden = 全集 − known`。编译时确定，可用 `may_know()` 查询 |
| L2 detector | `FactMatcher` + `LeakDetector.check()` | 每个事实最多一次命中；连续 6 字原文（shingle）= `hard`，关键词 = `soft`；shingle 优先 |
| L2b judge | `LeakJudge.adjudicate()` | 有 LLM 时逐条裁决 `confirmed / downgraded / dismissed`；解析失败或缺失一律按 `confirmed`（失败安全） |
| L3 context | `ContextGuard.guard()` | 输入侧**不拒绝**：命中的文本包成「玩家声称：…（注意：你不可能知道…无法证实…）」，让角色当作传闻回应 |

`exempt_fact()` 在 L2 和 L3 同步生效：线索释放后，该线索 `reveals_fact_ids` 里的事实变为可讨论，不再算剧透。映射来自 Ontology 的 `clue_fact_map`（`parse_yaml_script` 生成，key 同时支持线索整行与线索 id）。

## 2. 与对局的接点

- **Prompt（`llm.build_prompt`）**：只注入该角色 scope 内的 `known_fact_ids` 内容；`forbidden` 事实和 `is_true` 字段永不注入。
- **输入（`runtime.run_session`）**：每个席位消费上一轮可见事件时，对他人的 `utterance` 跑 `guard_input()`；命中则改写 payload 为传闻版，并计入 `infoguard.input_wrapped`。
- **输出（`runtime.run_session`）**：AI 发言先过 `claimed_facts` 白名单，再过 `check_output()`。硬命中、或 judge 判 `confirmed` → 台词替换为「这件事我确实不清楚。」，`act_type` 改 `deflect`，`claimed_facts` 清空；软命中且不 confirmed → 保留，记 `flag`。命中记入 `game_events.payload_json.guard` 和 `log`。
- **GM（`_gm_step`）**：释放线索时调用 `exempt_clue()`，事件里带 `facts_now_public`。
- **人类席位**：输出侧**不拦**。玩家说什么都合法（拿 A 的秘密质问 B 正是剧本杀玩法）；只在别的角色感知时做输入侧传闻化。

统计落在 `game_state.infoguard_stats` 与 `play` 返回的 `infoguard` 字段：`input_wrapped / output_rewritten / output_flagged / judge_calls`。

## 3. CLI

```bash
uv run imms --db imms.db infoguard scopes --session snow-night-villa-s001
uv run imms --db imms.db infoguard check --session snow-night-villa-s001 \
  --char gu_chengye --text "死者死于过量胰岛素注射，腹部针孔被领带遮住。"
uv run imms --db imms.db infoguard check --session snow-night-villa-s001 \
  --char lin_wanqiu --text "我听到里面有激烈的争吵声。" --direction in
```

## 4. 测试口径

`tests/test_infoguard.py` 包含 **N×F 对抗矩阵**：全部角色 × 全部私有事实。本人知道的事实逐字复述必须零命中；任何不可知事实逐字复述必须命中且为 `hard`。另有上下文不拒绝、线索豁免双向同步、judge 失败安全、runtime 输入包裹与输出改写测试。

## 5. 边界

- 凶手自曝不在 InfoGuard 范围内：凶手**知道**作案事实，属"自证其罪"的表演质量问题，由 evaluation 单独统计（沿用参考项目的取舍）。
- 关键词命中会误报，所以默认只 `flag` 不改写；`--llm http` 时才启用 judge 做降级/驳回，且失败一律从严。
- 旧 markdown 剧本没有 `facts`，InfoGuard 自动空转；要享受这层保护需用 YAML 剧本并重新 `parse`。

## 6. silent-recipe/utils 的三项参考结论

对 `refer/silent-recipe/src/utils/` 的评估（本轮只评估，未移植代码）：

| 机制 | 文件 | 有价值之处 | 我们的取舍 |
|---|---|---|---|
| 意图路由 | `intentRouter.js` | 关键词打分 + 上下文覆盖 + 人名/时间表达式抽取，`alternatives` 保留次优意图；把用户输入转成结构化 act 而非交给模型猜 | **概念可取**：我们已有公式层选意图，但用户发言固定 `assert`。可后续把 `classifyIntent` 的确定性分类接到人类席位与时间线匹配；人名表需配置化，不能硬编码到剧本 |
| 情绪控制器 | `moodEngine.js` | 可疑度/谎言击破层数/敏感话题 → 离散情绪 + `suspicionDelta` + `shouldEvade`；打字速度随情绪变化 | **部分可取**：离散五级情绪可映射到我们的 PAD 离散化；但它的 `suspicionDelta` 是硬编码 0-100 分制，与我们的 log-odds 怀疑度不同量纲，直接搬会破坏公式一致性。只取"敏感话题触发回避"作为意图层的额外候选 |
| 演出记录分离 | `cutsceneLog.js` | 看过哪些过场独立于存档，`localStorage` 分 key，不进 GameState 快照，保证读档兼容与解锁链稳定 | **可直接取**：对应我们的 `state_snapshots`。分叉重放时"已看过的演出/已揭示的叙事"不应被快照回滚，应像 `cutsceneLog` 那样独立记账。`audio.js` 的三路音量/手势解锁与 `bead/utils/persist.js` 的防抖落盘属前端工程，与当前无 UI 内核无关 |
