# 投票体系

`src/imms/vote.py` 把投票从 `runtime.py` 的内联计票升级成独立阶段：**规则配置 → 合法票校验 → 多轮投票/改票 → 票型账本 → 审计**。

设计边界：

- 系统不替玩家做选择；`scripted.vote` / `scripted.votes` 只用于 headless 测试和 CLI。
- LLM 不决定投票。AI 选票来自存储中的 `ell` 对数 odds，叠加关系、情绪与证据指向。
- 旧接口保留：`result` 仍有 `tally` / `winner` / `solved` / `motive_fact_id`，`scripted.vote` 继续有效。

## 1. 规则配置

优先级从低到高：`Ontology.evaluation.vote_rules` → `scripted.vote_config` → 调用方显式 `rules`。

```json
{
  "utterances": ["...", "..."],
  "vote_config": {
    "rounds": 2,
    "strategy": "persona",
    "temperature": 0.8,
    "allow_self": false,
    "allow_abstain": true,
    "tie_break": "evidence",
    "majority_stop": false,
    "open_ballot": true
  },
  "votes": [
    {"target": "lin_wanqiu", "reason": "先观察票型"},
    {"target": "zhou_muchen", "reason": "第二轮改投"}
  ],
  "motive_fact_id": "t_drug"
}
```

| 字段 | 默认 | 含义 |
|---|---|---|
| `rounds` | 1 | 最多 1–5 次投票；`votes` 提供多轮改票 |
| `strategy` | `argmax` | `argmax` 保持旧行为；`persona` 叠加人格/关系/情绪 |
| `temperature` | 1.0 | `persona` 策略的 softmax 温度 |
| `allow_self` | false | 是否允许投自己 |
| `allow_abstain` | true | 空票/弃票是否合法 |
| `tie_break` | `evidence` | `evidence` / `deterministic` / `abstain` |
| `majority_stop` | true | 首轮过半即结束，否则投满 `rounds` |
| `open_ballot` | false | 是否把个人票型写为 public；默认仅 GM 可见 |
| `bandwagon` | 0.25 | 复投前对当前票王的怀疑度增益 |
| `ally_weight` | 0.35 | 高 like/trust 降低该目标的投票分 |
| `grudge_weight` | 0.50 | 高 grudge 提高该目标的投票分 |
| `fear_weight` | 0.30 | 高 fear 提高该目标的投票分 |
| `exposure_deflect` | 0.40 | 暴露风险高时向当前证据指向者转移 |

## 2. 票据解析与合法性

`to_seat()` 依次接受：席位 id（`ai:x` / `human:x`）、角色 id、角色中文名、补前缀的裸 id。空值、`abstain`、`弃票` 归一为 `ABSTAIN`。

校验规则：

- 目标不在席位表 → `unknown_target`，按 `invalid_as` 处理（默认弃票）
- 目标等于投票者本人且 `allow_self=false` → `self_vote_blocked`
- 弃票且 `allow_abstain=false` → `abstain_blocked`
- 非法票不进入计数，但保留在账本中供审计

平票不使用字典插入顺序：`evidence` 用已释放线索的 `logLR` 求和裁决，仍有并列时按席位 id 排序；`abstain` 则没有赢家。

## 3. 多轮与复投

每轮顺序：AI 生成票据 → 人类读取 `votes[round-1]` → 校验 → 计票 → 写 `vote_cast` / `vote_tally` 事件。若未过半且还有轮次，写 `vote_deliberation`，并按 `bandwagon` 调整各席位 `ell` / `suspicion`，再进入下一轮。

复投中的人类改票不是覆盖旧票，而是新增一张票；审计看全部票据，复盘计票只看最后一轮。

## 4. 命令

```bash
# 正常 play 会自动执行终幕投票
uv run imms --db imms.db play --session S --rounds 2 --scripted script.json

# 单独运行投票阶段（用于已经装配但未结束的会话）
uv run imms --db imms.db vote run --session S --scripted vote_script.json

# 手工补票、重算票型、审计、查看规则
uv run imms --db imms.db vote cast --session S --voter human:lin_wanqiu --target zhou_muchen --reason "证据指向"
uv run imms --db imms.db vote replay --session S --out vote_replay.json
uv run imms --db imms.db vote audit --session S --out vote_audit.json
uv run imms --db imms.db vote config --session S
```

`vote run` 会更新 `game_state.result`；`play` 的返回值也包含完整投票结果。

## 5. 账本与事件

`game_state.vote_state`：

```json
{
  "rules": {},
  "rounds": [
    {"ballot_no": 1, "round": 2, "ballots": [], "tally": {}, "evidence_scores": {}}
  ],
  "manual_ballots": [],
  "result": {"winner": "ai:zhou_muchen", "solved": true, "tie": false}
}
```

`game_events` 新增四类事件：

| act_type | 内容 | 默认可见性 |
|---|---|---|
| `vote_cast` | 本轮全部票据 | `gm:host`（`open_ballot=true` 时 public） |
| `vote_tally` | 计票、有效票、弃票、平票裁决 | public |
| `vote_deliberation` | 复投前的票型影响 | public |
| `vote_result` | 赢家、胜负、动机与规则摘要 | public |

## 6. 审计

`vote.audit()` 输出：

| 字段 | 含义 |
|---|---|
| `legal_rate` | 合法票 / 全部票 |
| `abstention_rate` | 弃票 / 全部票 |
| `self_votes_blocked` | 自我投票被拦截次数 |
| `unknown_targets` | 指向不存在席位的次数 |
| `evidence_supported` / `evidence_judged` | 投给当前证据最高指向者的票数 / 可判定的有效票数 |
| `vote_reasonableness` | 证据支持率；无线索时为 `null`，不猜 |
| `winner_legal` / `tie_resolved_by` / `majority` | 结果合法性、平票裁决方式、是否过半 |
| `killer_correct` / `motive_recorded` | 是否命中真相、是否记录动机 |

复盘 `retro` 会把该报告挂在 `retrospective.vote_audit`，并在 Markdown 中追加「投票审计」小节；GM 分支 diff 仍比较 `winner` / `solved`，但两局都各自带完整票据账本。

## 7. 边界

- `evidence_supported` 只说明票据是否符合**已释放线索**的 logLR 指向，不等于真相正确。
- `persona` 策略使用当前 runtime 的关系与情绪；如果剧本尚未归一化，L1 为空，行为接近默认基线。
- 盒装本的 `killer_id` 是投票目标而非谋杀真凶；`solved` 只能解释为「投中标注目标」。
- 票据不写入真值事实，也不会被 InfoGuard 当作台词泄露扫描。
