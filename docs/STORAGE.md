# IMMS 存储规范（SQLite）v0.1

单一 SQLite 文件保存四类数据与审计。默认路径 `imms.db`，所有 CLI 用 `--db` 指定。

## 1. 表

| 表 | 内容 | 写入方 |
|---|---|---|
| `scripts` | 用户输入的剧本解析产物（Ontology JSON） | `imms parse` |
| `persona_l1` | 人物基础属性，按 `extraction_version` 追加；触发器禁 UPDATE/DELETE | `imms normalize` |
| `persona_l2` | 剧本赋予属性，可改 | GM CLI / 剧本导入 |
| `persona_l3` | 临场属性（有向用 `target`），可改 | 公式引擎 / GM CLI |
| `sessions` | 装配后的席位名册 | `imms assemble` |
| `attribute_events` | L2/L3 每次变更的 before/after/reason | 所有写入方 |
| `state_snapshots` | 轮末状态快照（预留） | 公式引擎 |
| `game_events` | 每轮 SpeechAct，含 utterance / inner_intent；公开与私聊共用全局 `seq` | 对局引擎 / `imms private say` |
| `game_state` | runtime / round / revealed_clues / result / llm_stats | 对局引擎 |
| `private_rooms` | 私聊房间席位、轮次、opened_seq / closed_seq | `imms private open/close` |

## 2. L1 只读

```
CREATE TRIGGER persona_l1_no_update BEFORE UPDATE ON persona_l1
BEGIN SELECT RAISE(ABORT, 'ERR_L1_IMMUTABLE'); END;
CREATE TRIGGER persona_l1_no_delete BEFORE DELETE ON persona_l1
BEGIN SELECT RAISE(ABORT, 'ERR_L1_IMMUTABLE'); END;
```

重新归一化不会产生 UPDATE，而是插入新的 `extraction_version`；旧版本保留，查询只取 MAX(version)。

## 3. 写入审计

所有 L2/L3 变更写 `attribute_events`：

| 字段 | 说明 |
|---|---|
| `seq` | 全局单调（AUTOINCREMENT），解决私聊并发竞态 |
| `session_id` / `round` | 定位到局与轮次 |
| `actor` | `gm_cli:host` / `engine` |
| `layer` / `character_id` / `key` / `target` | 变更目标 |
| `before_json` / `after_json` | 变更前后信封 |
| `reason` | 必填 |

`gm_cli` 若命中 L1 键 → `ERR_L1_IMMUTABLE`；无 reason → `ERR_REASON_REQUIRED`。

## 4. 导出

`imms export --db imms.db --out dump.json` 导出 scripts / L2 / L3 / sessions / events（不含 L1 版本行，L1 用 `imms persona show` 按角色导出）。

## 5. 约定

- 数据库文件不提交（`*.db` / `*.sqlite` 已在 `.gitignore`）。
- 换行由 `.gitattributes` 统一为 LF。
- 派生能力不落库，读取时由 `imms derive` 计算。
