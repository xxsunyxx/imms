# IMMS v0.1

用户给一份现成剧本 → 系统解析、归一化、装配 GM + N−1 角色 agent + 1 用户席位并开局。系统不创作剧本。

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
uv run imms --db imms.db replay --session snow-night-villa-s001 --out replay.md
uv run imms --db imms.db branch fork --session snow-night-villa-s001 --at-round 2 --override-round 2 --override-utterance "我承认偷看过遗嘱" --out branch_report.json
```

LLM 台词（可选）：设 `IMMS_LLM_URL` / `IMMS_LLM_MODEL` 后 `play --llm auto`；未配置时用离线模板。LLM 只写台词，不碰数值与事实。详见 `docs/RUNTIME.md`。

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
