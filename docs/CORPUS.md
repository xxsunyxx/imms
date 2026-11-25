# 自持语料库、准入与补全机制

`refer/` 目录是可丢弃的外部参考。本项目不依赖它运行：可用剧本先被转换成项目内的标准 Ontology JSON，再导入 SQLite。这样即使 `refer/` 被删除，`corpus/` + 数据库仍能重建与复核。

## 1. 存储布局

```text
corpus/
├── manifest.json          # 准入决定、来源指纹、补全记录
├── raw/rpg/*.json         # 来源文件逐字节副本（溯源用，不参与运行）
└── scripts/*.json         # 项目自持的标准 Ontology（运行导入用）
```

- `manifest.json` 记录 `source_sha256`、决定（import/skip）、放弃原因和补全记录。
- `scripts/*.json` 是唯一导入源；其中 `source_files` 指向 `raw/rpg/...`，不含 `refer/` 路径。
- 数据库仍只保存解析产物：`scripts.ontology_json` + `persona_l1`。删除数据库后可用 `import-corpus` 从 `corpus/` 重建。

## 2. 命令

```bash
# 只在 refer/ 还存在时执行；生成 corpus/ 后可删除 refer/
uv run imms --db corpus.db build-corpus --report output/corpus_manifest.json
uv run imms --db corpus.db corpus-report
uv run imms --db corpus.db import-corpus --dir corpus --report output/corpus_import.json
uv run imms --db corpus.db scripts
```

如需继续导入盒装本，仍用 `imms import-boxed`；那条链路与本语料库互不影响。

## 3. 准入规则（该放弃就放弃）

`admit_rpg()` 先做硬门禁，再谈补全：

| 规则 | 判定 | 处置 |
|---|---|---|
| 需要鼠标/地图/图片认物 | 出现 `room`/`spot`/`zone`/图片或地图字段，或场景物件必须靠图辨认 | SKIP |
| 无明确凶手 | `truth.culprit_role_id` 缺失 | SKIP |
| 无证据链 | `truth.evidence_chain` 为空 | SKIP |
| 无分幕/结局裁决 | `stage_flow` 或末幕 `final_decision` 缺失 | SKIP |
| 可玩角色不足 | `human_playable` 少于 2 | SKIP |
| 凶手不在角色表 | `culprit_role_id` 无对应角色 | SKIP |
| 纯文本可推理 | 线索为自包含文字，搜证只是文本菜单 | IMPORT |

本次结果：

| 来源 | 决定 | 原因 |
|---|---|---|
| 《深海回声》 | IMPORT | 4 人 / 12 线索 / 30 条分钟级时间线 |
| 《雾港档案·钟楼夜宴》 | IMPORT（带补全） | 4 人 / 6 线索；关键材料在私有记忆，补一条不泄底问询线索 |
| 《末班列车》 | IMPORT | 5 人 / 16 线索 / 30 条时间线，逻辑最完整 |
| 《旧宅回廊》 | IMPORT | 5 人 / 13 线索 |
| 《雨夜来信》 | IMPORT | 3 人 / 13 线索，2 幕 |
| 《无声剧院》 | IMPORT | 6 人 / 18 线索 |
| 《西游记·火焰山》 | SKIP | 无凶手、无证据链、无 stage_flow；是叙事片段不是推理本 |
| 《雾灯未眠》（mossvale） | SKIP | 51 条 `clues` 带 `room`/`spot`，依赖房间地图与手绘物件图标 |
| 《第7号嫌疑人》（mossvale） | SKIP | 45 项 `evidence` + 34 份证词按章节/房间解锁，依赖鼠标搜证 |

被放弃的本子只写 manifest 决定，不复制正文；它们不会进入数据库。

## 4. 补全机制

原则：**只用来源中已有的证据补全，绝不编造剧情、时间或事实。** 无法补的字段就放弃该本。

| 规则 | 触发 | 补全动作 | 记录 |
|---|---|---|---|
| R1 | 死者不是角色表字段 | 从 `story_background` / `truth.full_story` 标注死者名，写 `evaluation.victim_name` + 公开事实 | `basis=source_text_annotation` |
| R2 | `unlock_stage` 是 1-based | 转成 Ontology 的 0-based `stage` | `stage_source` 写回线索 |
| R3 | 线索未被 `evidence_chain` 点名 | 只连到完整真相事实，不猜具体结论 | `clue_truth_fallback` |
| R4 | 无结构化时间线 | 保留空数组并记缺口，不补事件 | `timeline_placeholder` |
| R5 | 证据链明确要求“角色提供的账本照片/药柜行踪”等私有材料 | 生成一条问询线索，只提示“需要核对什么”，不泄露私有内容 | `inquiry_gap` |
| R6 | 线索 id 重复 | 追加序号保证引用稳定 | `duplicate_clue_id` |

所有补全写入 Ontology 的 `completions[]`，并汇总进 manifest。L1 人格数值仍按既有口径：没有行为证据句就 `value=null` + `missing_evidence`，不因补全剧情而编分。

## 5. 验证

- 37 个 unittest 全通过（新增 `tests/test_corpus.py` 3 项：准入、补全结构、落库与投票）。
- 从 9 个候选中 IMPORT 6、SKIP 3；6 个本全部落库并生成 L1。
- `rpg_last_train` 完成 `assemble → play → vote` 冒烟，用户席位按角色 id 正确映射。
- 测试夹具在 `tests/fixtures/corpus_mini/`，不依赖 `refer/`；删除 `refer/` 后仍可跑全量测试。
