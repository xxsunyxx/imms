# IMMS 核心规则模块

> 分工契约：**guide 提示词驱动变化，IMMS 控制和记忆人物数据**。
> 这些模块把此前散落在文档和单一函数里的规则固化进 `src/imms/`。数据来自已落库的表，公式来自 `formulas.py`；模块本身不编造事实、不做数值裁决、不写库。

## 核心模块

| 模块 | 职责 | 关键约束 |
|---|---|---|
| `profile.py` | 人物抽取：从 `persona_l1` + Ontology 编排成结构化人物档案（canon / abilities / evidence / gaps） | 缺证据 `value=None + missing_evidence`；L1 只读；无 LLM、无随机 |
| `prompt_engine.py` | 提示词生成引擎：命名块注册表 + guide 注入 | guide 只改措辞/语气；事实 id 与 `key=value` 注入被清洗掉；块可注册扩展 |
| `variables.py` | 情绪与变量计算编排：从席位状态算出 PAD、泄露 σ、决策温度、怀疑分布、关系焦点、张力 | 纯函数、无副作用、同输入同输出；缺失值回退中性并记账 |
| `random_source.py` | 确定性随机源：从 `(session, seat, round, event_seq, purpose, base_seed)` 派生种子 | 禁止全局未种子 RNG；分叉可复现；模块只给种子不做决定 |
| `guides.py` | guide 提示词库：`config/guides.yaml` 导入 `guide_rules` 表，按 purpose/tag 确定性选取 | 导入与渲染双重闸门拦事实 id / `key=value`；只注入措辞，不改 claimed_facts/intent/数值 |

## guide 库（G27）

- 源文件 `config/guides.yaml` 可版本化、可读；五类 purpose：`tone / delivery / pacing / improv / aftermath`。
- 表 `guide_rules(guide_id, version, ...)` 按版本取当前生效集；`guides import` 可重新导入，拒绝的规则带 reason 返回、不静默丢弃。
- `guides.select()` 用 `random_source` 派生种子，同参数必得同一组 guide（复盘与分叉可复现）。
- `core prompt --guide-purpose tone` 从库取 guide 交给引擎；事实块与意图块前后完全一致。

## 数据流

```
scripts / persona_l1 表 ──> profile.py（抽取，缺证据留空）
                                  │
                                  v
runtime game_state ──> variables.py（算变量）──> prompt_engine.py（渲染数据块）
                                                        +
                                              guide 提示词（仅措辞，经清洗）
                                                        │
                                                        v
                                                  LLM / 模板生成
                                                        │
                                                        v
                                     Guard + 事件溯源 ──> game_state / attribute_events
```

## CLI

```bash
uv run imms --db imms.db core profile --script 雨夜山庄
uv run imms --db imms.db core profile --script 雨夜山庄 --character char_01
uv run imms --db imms.db core variables --session S --seat ai:char_02
uv run imms --db imms.db core prompt --session S --seat ai:char_02 --intent assert \
    --guide "语气更冷，句子短些"
uv run imms --db imms.db core seed --session S --seat ai:char_02 --round 2 --purpose intent

uv run imms --db imms.db guides import
uv run imms --db imms.db guides list --purpose improv
uv run imms --db imms.db guides select --purpose aftermath --session S --seat ai:char_02 --round 2
uv run imms --db imms.db core prompt --session S --seat ai:char_02 --guide-purpose tone
```

## 谎言账本的私有边界（G26）

**约束：lie 只能 agent 自己看见自己的，也只由自己维护。**

| 层 | 谁能读 | 实现 |
|---|---|---|
| 账本 `state["lie_ledger"]` | 仅该席位；创建/heat/策略/推理日志只写自己的 state | `lie.py` 全部函数以 `state` 为单位，无跨席位写入 |
| 事件里的私有字段 | 发言者本人 + GM；其他席位（含玩家）不可见 | `db.visible_game_events()` 在可见性层裁掉 `lie_id / inner_intent / recalled / guard / memory_gap` |
| 落库原始 payload | 复盘/回溯评估（全量审计） | `replay._events()` 直读原表，不经裁剪，谎言全景图不受影响 |

裁剪只在**读取视图**进行，不修改落库数据——因此「说的给全场看、想的和谎言只给自己与 GM 看」同时成立，且分叉重放一致。

## 边界

- 本轮**未改运行时主链路**（`runtime.py` 的 `_appraise` / `_choose_intent` 行为不变）；`llm.build_prompt` 委托给引擎，签名与输出兼容。
- 编号漂移 C8/G15 已修：`memory.py` 与 Prompt 的记忆段统一为 **F26**。
- guide 清洗测试证明：注入 `f_fake=1.0`、`arousal=1.0` 不会进入 Prompt 的数据段。
- 回归基线：113 个 unittest 全通过（新增 `test_core_modules.py` 8 项、`test_lie_privacy.py` 4 项、`test_guides.py` 5 项）。
