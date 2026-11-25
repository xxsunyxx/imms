# IMMS v0.1

用户给一份现成剧本 → 系统解析、归一化、装配 GM + N−1 角色 agent + 1 用户席位并开局。系统不创作剧本。

## 安装

```bash
uv sync
```

## 端到端示例

```bash
uv run imms --db imms.db parse --script examples/sample_script
uv run imms --db imms.db normalize --script-id 雨夜山庄
uv run imms --db imms.db assemble --script-id 雨夜山庄 --human char_01
uv run imms --db imms.db persona show --script-id 雨夜山庄 --char char_01
uv run imms --db imms.db derive --script-id 雨夜山庄 --char char_01 --key observation
uv run imms --db cross.db parse --script examples/cross_source
uv run imms --db cross.db normalize --script-id 跨源等效测试
uv run imms --db cross.db compare --script-id 跨源等效测试 --a char_conan --b char_iron
uv run imms --db imms.db assigned set --session 雨夜山庄-s001 --char char_01 --key public_identity --value '"山庄主人"' --reason '第二幕身份揭示' --round 2
uv run imms --db imms.db emergent set --session 雨夜山庄-s001 --char char_01 --key trust --target char_02 --value -0.2 --reason '谎言被戳穿' --round 3
uv run imms --db imms.db audit --session 雨夜山庄-s001 --char char_01
```

## 剧本格式

见 `examples/sample_script/script.md`。分节：`## World` / `## Characters` / `## TruthGraph` / `## Timeline_True` / `## CluePool` / `## PlotLocks` / `## Endings`。角色用 `### <id> <name>`，证据行为 `- key|锚点值|证据句|定位`。

## 文档

- `docs/ASSEMBLY.md` — 建局装配
- `docs/SCHEMA.md` — 属性信封与标准键
- `docs/NORMALIZATION.md` — 归一化规则
- `docs/FORMULAS.md` — F01–F30 公式登记
- `docs/STORAGE.md` — SQLite 表与只读约束

## 已踩过的坑

- L1 只读不能只靠 CLI 判断；用 SQLite 触发器硬拦 UPDATE/DELETE。
- 归一化得分只能取五档锚点值；自由小数会让审计失去意义。
- 派生值读取时算，不落库；否则同一事实存两份，改基础值不会同步。
- 用户席位不由 LLM 代打；AI 角色与用户席位必须区分 `kind`。
