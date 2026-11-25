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
uv run imms --db imms.db play --session 雨夜山庄-s001 --rounds 2 --scripted examples/sample_script/user_script.json --seed 42
uv run imms --db imms.db private open --session 雨夜山庄-s001 --round 3 --seats human:char_01,ai:char_02
uv run imms --db imms.db private say --session 雨夜山庄-s001 --room 雨夜山庄-s001-p001 --actor human:char_01 --to ai:char_02 --utterance "账房的灯熄得早。"
uv run imms --db imms.db private close --session 雨夜山庄-s001 --room 雨夜山庄-s001-p001
uv run imms --db imms.db replay --session 雨夜山庄-s001 --out replay.md
uv run imms --db imms.db branch fork --session 雨夜山庄-s001 --at-round 2 --override-round 2 --override-utterance "我相信赵管事" --out branch_report.json
```

LLM 台词（可选）：设 `IMMS_LLM_URL` / `IMMS_LLM_MODEL` 后 `play --llm auto`；未配置时用离线模板。LLM 只写台词，不碰数值与事实。详见 `docs/RUNTIME.md`。

## 单测

```bash
uv run python -m unittest discover -s tests
```

## 剧本格式

见 `examples/sample_script/script.md`。分节：`## World` / `## Characters` / `## TruthGraph` / `## Timeline_True` / `## CluePool` / `## PlotLocks` / `## Endings`。角色用 `### <id> <name>`，证据行为 `- key|锚点值|证据句|定位`。

## 文档

- `docs/ASSEMBLY.md` — 建局装配
- `docs/SCHEMA.md` — 属性信封与标准键
- `docs/NORMALIZATION.md` — 归一化规则
- `docs/FORMULAS.md` — F01–F30 公式登记
- `docs/STORAGE.md` — SQLite 表与只读约束
- `docs/RUNTIME.md` — 对局引擎与回合循环

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
