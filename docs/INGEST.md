# 统一摄入体系（IMMS Script Ingest）

目标：任意来源的本子进来后，都走同一条链，变成项目自持的标准 Ontology；`refer/` 删除后仍可重建。

```
detect → parse → admit → skeleton → complete → repair → canonical JSON → manifest/registry
```

## 1. 支持的来源（适配器）

| `detect.kind` | 来源 | 复用解析器 |
|---|---|---|
| `structured_yaml` | lockedroom 风格 YAML（有 `facts` / `characters`） | `ontology.parse_yaml_script` |
| `rpg_json` | RolePlayingGame 导出 JSON（有 `script_meta` / `roles` / `truth`） | `corpus.admit_rpg` + `corpus.parse_rpg_script` |
| `boxed_dir` / `boxed_md` | 盒装本 `本盒完整内容.md`（单文件先暂存为同名文件） | `boxed.parse_boxed_dir` |
| `section_text` | 旧式 Markdown 分段本（`## 人物` / `## 真相` / `## 线索`） | `ontology.parse_script_text` |
| `imms_ontology` | 已是标准 Ontology 的 JSON | 直接读取 |
| `interactive_scene_game` | `.mjs` / `.js` 场景游戏 | 不解析，直接判 SKIP |

`imms ingest detect --path <路径>` 只探测不改动；输出 `kind`、`confidence`、`evidence`。目录里若只有一个 YAML，按历史行为取该文件。

## 2. 准入门禁（先于补全）

- `requires_mouse_or_image`：源码含 `room` / `spot` / `image` / `map` 等场景依赖 → 放弃。
- RPG 本沿用 `admit_rpg`：无凶手、无证据链、无分幕、无最终裁决、无可玩角色 ≥2 → 放弃。
- 通用本：`too_few_characters` / `duplicate_or_missing_character_ids` / `no_truth` / `no_clues` / `no_endings` / `no_declared_solution`。
- 解析异常记 `parse_error`，不会中断批量摄入。
- 所有放弃原因逐条写进 `manifest.json` 与骨架报告；不猜测、不编造。

## 3. 骨架夹具与补全

详见 `docs/SKELETON.md`。简化顺序：

1. `skeleton.project()` 用固定槽位把解析结果骨架化；
2. 可选 LLM 只在槽内做摘要，越界内容一律丢弃；
3. `skeleton.complete()` 从来源已有证据补槽位；
4. `ingest.repair()` 把骨架投影回 Ontology，再做结构补全。

补全规则带账本，全部记录到 `completions[]`：

| 规则 | 作用 |
|---|---|
| `R1`–`R6` | RPG 来源的死者标注、阶段换算、线索回指、时间线缺口、问询提示、重复 id |
| `S1`–`S4` | 骨架阶段：摘要取自事实、凶手/死者取自有标记角色、线索只给 hint |
| `G1`–`G6` | 规范化阶段：线索投影、权重修复、事实投影、凶手继承、id 冲突 |

## 4. 自持存储

```
corpus/
  manifest.json          # 来源指纹、准入决定、补全条数、状态
  raw/<kind>/<file>      # 来源字节副本（.gitattributes 标记 -text，逐字节保持）
  skeletons/<id>.json    # 固定槽位骨架 + LLM 记录 + 剩余 gaps
  scripts/<id>.json      # 标准 Ontology，sort_keys 确定性序列化
```

- `source_sha256` 同时写入 manifest 与 Ontology 的 `ingest` 块。
- 同一来源重复摄入按哈希/来源路径 upsert，产物字节不变。
- 不同来源的 `script_id` 撞车时追加 `_2`，记 `G6_script_id_collision`。
- `import-corpus` 从 `corpus/` 重建 DB，并自动注册剧本与骨架。

## 5. 命令

```bash
uv run imms ingest detect   --path <文件或目录>
uv run imms ingest skeleton --path <文件> --llm auto --out skeleton.json
uv run imms ingest convert  --path <文件> --llm template --out ontology.json
uv run imms ingest add      --path <文件> --root corpus
uv run imms ingest batch    --path <目录> --root corpus --limit 100
uv run imms ingest report   --root corpus
uv run imms ingest validate --file corpus/scripts/rpg_last_train.json
```

`--llm` 取 `auto` / `template` / `http`；`auto` 在未配置 `IMMS_LLM_URL` 时走 template，完全离线。
`parse` 命令也已收敛到该链路，输出会带 `detected` 与规范化 `checks`。

## 6. 已踩过的坑

- 来源 JSON 里 `"|".join(f"logLR:...")` 会把单字符串逐字符拆开，78/79 条线索权重不可解析；`G2` 现在统一重写并校验。
- 备用域名的场景 JSON 与鼠标搜证 `.mjs` 不能靠"剧情完整"放行；按准入规则直接 SKIP。
- `no_case` 这类叙事片段会在下游解析阶段抛异常，必须在适配器里先做 `admit_rpg`，保留真正的门禁原因。
- 两个不同 SKIP 文件可能哈希相同；manifest 按来源路径分别保留，避免吞掉其中一条原因。
- manifest 的汇总每次写入都重算，否则批量过程里 skip 数会停留在上一次导入的值。
