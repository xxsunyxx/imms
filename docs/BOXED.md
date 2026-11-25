# 盒装本批量导入（100 套）

来源：`refer/mystery-scripts-100/盒装本/`（外部参考仓库，不纳入版本控制）。
100 套，四类各 25 套：搞笑 C01–C25、恐怖 H01–H25、职场 O01–O25、反转 T01–T25；角色数 2/3/4。

## 导入命令

```bash
uv run imms --db imms.db import-boxed --dir refer/mystery-scripts-100/盒装本 --report output/boxed_import.json
uv run imms --db imms.db scripts
```

解析后可直接装配：

```bash
uv run imms --db imms.db assemble --script-id C01 --human c01_r02
uv run imms --db imms.db play --session C01-s001 --rounds 2 \
  --scripted examples/scripts/boxed_user_script.json --llm template
```

## 解析映射（`src/imms/boxed.py`）

| 剧本文本 | Ontology 字段 |
|---|---|
| `> 编号 … ｜ 题材 ｜ … ｜ N 人 ｜ 时长 ｜ 难度` | `world` / `brief` |
| `## ③ 角色剧本 · <name>` | `characters[]`，id `<sid>_r01`… |
| 角色「你是谁 / 人前主张 / 秘密 / 任务 / 分幕指引 / 自我介绍」 | `public_identity` / `stance` / `secret` / `goals` / `acts` / `intro` |
| 公开背景 | `f_<sid>_pub`，`visibility=public` |
| 每人的秘密 | `f_<sid>_sNN`，`visibility=private`，写入该角色 `known_fact_ids` |
| 主持人手册「完整真相 / 反转与高潮」 | `f_<sid>_truth` / `f_<sid>_twist`，`visibility=truth`（无人可合法知道） |
| 线索卡清单逐条文本 | `clues[]` + 旧格式 `clue_pool`，按关键词/人名/6 字 shingles 命中事实 → `reveals_fact_ids` |
| 建议揭示时机 | `clues[].stage`（第一幕 0 / 第二幕 1 / 第三幕 2） |
| 结局卡 | `endings[]` |
| 学生评审名片 | 解析前整块剔除，不进入事实与 Prompt |

## 两个刻意的设计决定

1. **不编造 L1 数值。** 这些剧本没有行为证据句，归一化产出 `value=null` + `missing_evidence`，与 YAML 剧本同一口径。角色差异来自文本字段和事实可见性，不来自虚构的 OCEAN 分值。
2. **「核心真相角色」不等于凶手。** 这批本多为闹剧/职场/叙诡，未必有谋杀。`evaluation.killer_id` 用真相段落的角色名出现次数推断，并显式记 `culprit_basis=truth_text_name_frequency`、`has_murder=false`；它只是投票目标，不代表剧本有唯一凶手。正式本应显式给出 `evaluation.killer_id` 覆盖。

## 线索卡图片

抽验 C01、H01 两张 `线索卡.png`：图片逐条文字与 `线索卡清单.md` 完全一致（含编号与措辞），因此**不启用 OCR**，以清单为线索文本唯一来源；图片路径写入每条线索的 `source_images` 作为溯源。若以后引入图片与清单不一致的剧本，应显式补 OCR 校验，不要在导入时静默覆盖清单。

## 兼容改动

- `parse_script_dir` 遇到 `本盒完整内容.md` 自动走盒装解析器；单文件 YAML 与旧 Markdown 分节格式不变。
- 2 人本可被 `assemble` 接受（下限从 3 放到 2）。
- 投票与 GM 线索释放按席位表解析角色 id，不再硬编码 `ai:` 前缀；人类席位被指向时不会映射到不存在的席位。
- 分叉回归的 `solvable` 校验改为「投票产出合法席位」，玩家没破案不再被误判为回归失败。

## 验证记录

- 100/100 套：角色 2–4 人、线索>0、事实>0、凶手目标在角色集合内、已知事实⊆事实集、每条线索保留图片溯源；0 个结构异常。
- 34 个单测（含 `test_boxed.py` 四项）全通过。
- C01 干净库端到端：`parse → normalize → assemble → play → private → replay → branch`，分叉四项校验 `pass`。
