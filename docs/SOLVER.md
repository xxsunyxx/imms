# 可解性求解器与时间线系统

`src/imms/solver.py` 是剧本的静态准入/回归门禁。它只读 Ontology，不跑会话、不调 LLM、不改任何状态。目的不是"剧情好不好"，而是回答：**这本子能否被证明可玩**。

## 命令

```bash
uv run imms --db corpus.db audit-script --script-id rpg_last_train --out audit.json
uv run imms --db corpus.db audit-all --out audit_all.json
```

`audit-script` stdout 只打印 verdict/checks/solution/summary；`--out` 落完整报告（含时间线、门控、补全回归、难度曲线）。

## 判定项

继承 lockedroom-ai `validate_references()` 的四条真值模型规则：

| 规则 | 含义 |
|---|---|
| `has_culprit` / `culprit_is_role` | 标注了凶手且凶手在角色表中 |
| `single_declared_killer` | `is_killer` 至多一人且与 `evaluation.killer_id` 一致 |
| `killer_knows_motive` | 动机事实由凶手知晓（或公开） |
| `victim_no_secrets` | 死者不持有私有秘密 |
| `private_facts_reachable` | 每个私有事实至少被某角色知晓或被某线索揭示 |

IMMS 扩展：

- `required_facts_covered`：必需事实必须能被线索、公开可见性、私密知识或证据链合成覆盖。完整真相 `type=truth` 视为由 `evidence_chain` 合成，不要求单条线索逐字揭示。
- `clue_weights_parseable`：线索权重必须能被 `logLR:<id>=<w>` 正则解析。**这项直接抓出 corpus 构建时的 `"|".join(str)` 缺陷**（权重被拆成 `|l|o|g|L|R|`，GM 释放线索后怀疑度不更新）。
- `unique_solution`：把线索的 logLR 累加到被指向席位，最高分必须唯一；没有显式权重时用线索/事实文本中的角色名回退推断。
- `solution_matches_truth`：唯一指向必须与 `evaluation.killer_id` 一致。
- `stage_gates_consistent`：`plot_locks` 中"第 N 幕才能……"的门控不得被更早阶段的线索提前揭示。
- `completions_effective`：R3/R5 补全必须接入证据链事实；只回指完整真相的算 `hint_only`，记入 `missing_links`。

## 时间线系统

`timeline_true` 与角色 `timeline` 原本只是字符串列表。`audit_timeline()` 把它们解析成结构化事件：

```json
{"source": "truth", "text": "21:42 任瑞借H-7双槽验车锤", "minute": 1302}
```

支持 `HH:MM`、`N点`、`第N幕`；`source` 区分真相与角色证词。只有两类结果：

- `order_conflicts`：真相时间线时间倒挂 —— 判为矛盾，阻断 verdict。
- `warnings`：无时间标记的事件 —— 只提示，不阻断。

这里**不做**语义级的"谁在何时何地"推理：那需要地点抽取和常识库，误报率高于收益。时间线的作用是为复盘提供结构化对齐锚点，并抓真实存在的顺序冲突。

## verdict

`solvable_unique` / `solvable_with_contradictions` / `ambiguous` / `misdirected` / `unsolvable` / `incomplete_facts` / `insufficient_data`。

盒装本的 `evaluation.killer_id` 来源于真相文本人名频次（`culprit_basis=truth_text_name_frequency`），没有显式 logLR，通常判 `ambiguous` —— 这是数据质量的真实反映，不是求解器故障。
