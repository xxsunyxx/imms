# IMMS 公式登记表 v0.1

来源：IMMS v1.0 §3–§14。本文是登记与校验清单；公式由装配后的 GM / 角色 agent 消费，不写进基础数据 CLI。

标记：`[DATA]` 基础数据处理（本后台）｜`[CONFIG]` 参数表｜`[RUNTIME]` 对局引擎｜`[CONTENT]` 剧本生成。

## 0. 覆盖状态

| 项 | 之前 | 现在 |
|---|---|---|
| 证据打分 / 置信度（F29） | 已在 `NORMALIZATION.md` §4 | 登记 |
| 派生能力 / 泄露温度（F30、F18 部分） | 已在 §6 | 登记 |
| 关系、情绪、怀疑、说谎、节奏、效用、评分 | 缺失 | F01–F28 全部登记 |
| β 矩阵、logLR 表、滞回阈值等参数 | 缺失 | 列为 `[CONFIG]`，附样例 |

结论：此前**未完成全部公式**。本文件补齐 F01–F30。

## 1. 人格与关系

### F01 戏剧张力 `[RUNTIME]` §3.2

```
Tension_i = Σ_{f∈F} ω_f · |v^canon_f − v^mask_f| · Exposure_f(t)
```

`v^canon, v^mask ∈ [0,1]`；`Exposure ∈ [0,1]`。Tension 越高 → 泄露噪声越大、决策温度越高。

### F02 关系更新 `[RUNTIME]` §4.1

```
R^c_{i→j}(t+1) = clip_{[c_min,c_max]}(
    b_c + λ_c·(R^c_{i→j}(t) − b_c)
    + Σ_{k∈E_t} β_c(k)·η_i(k)·κ_{ij}(k)·ν_i
)
```

`b_c` 回归基线（多疑者 `b_trust=-0.2`）；`λ_c` 衰减；`β_c(k)` 事件系数；`η_i(k)∈[0,1.5]` 归因折扣；`ν_i` 个体敏感度。

### F03 关系放大 `[RUNTIME]` §4.1

```
κ_ij(k) = 1 + 0.6 · intimacy_ij(t)
```

### F04 个体敏感度 `[RUNTIME]` §4.1

```
ν_i = 0.5 + N_i        N_i = OCEAN.N ∈ [0,1]  →  ν_i ∈ [0.5,1.5]
```

### F05 事件影响矩阵 β `[CONFIG]` §4.2

| 事件 k | like | trust | respect | suspicion | grudge | debt |
|---|---|---|---|---|---|---|
| 公开为我辩护 | +.18 | +.22 | +.08 | −.10 | −.05 | +.20 |
| 私下分享关键线索 | +.12 | +.25 | +.05 | −.08 | 0 | +.15 |
| 公开指控我（无据） | −.20 | −.25 | −.05 | +.05 | +.22 | −.10 |
| 指控我且证据成立 | −.10 | −.15 | +.10 | 0 | +.10 | 0 |
| 谎言被当场戳穿 | −.25 | −.45 | −.20 | +.30 | +.15 | 0 |
| 被我抓到说谎 | −.15 | −.40 | −.10 | +.35 | +.05 | 0 |
| 替我隐瞒 | +.25 | +.30 | 0 | −.05 | 0 | +.35 |
| 落井下石 | −.35 | −.30 | −.15 | +.10 | +.40 | −.20 |
| 救我于危难 | +.40 | +.35 | +.20 | −.15 | −.20 | +.50 |
| 抢我功劳 | −.22 | −.18 | +.05 | +.05 | +.25 | −.15 |

### F06 信任不对称 `[CONFIG]` §4.2

```
β⁻_trust ≈ 2.5 · β⁺_trust
λ⁻_trust > λ⁺_trust        # 跌得快、回得慢
```

## 2. 情绪与阵营

### F07 PAD 更新 `[RUNTIME]` §4.3

```
e_i(t+1) = e^(−Δt/τ_e) · e_i(t) + Σ_k A_i(k) · 1[trigger_k]
```

`τ_e`：愤怒 ≈2 轮，悲伤 ≈6 轮，恐惧 ≈4 轮。

### F08 创伤放大 `[CONFIG]` §4.3

```
命中 trauma[] → A_i(k) × 1.8，τ_e 延长（倍数可配置，建议 1.5）
```

### F09 离散情绪映射 `[RUNTIME]` §4.3

```
高唤醒 + 负效价 + 高 pad_dominance → 愤怒
高唤醒 + 负效价 + 低 pad_dominance → 恐惧
```

### F10 阵营对齐 `[RUNTIME]` §4.4

```
Align_{i→F} = w1·R̄_{i→F} + w2·GoalFit + w3·Safety − w4·BetrayCost
```

### F11 滞回与背叛闸门 `[CONFIG]` §4.4

```
θ_on = 0.70    θ_off = 0.50        # 滞回区间防抖动
loyalty 高 → w4 极大；仅 morale < 0.2 时可能倒戈
阵营变更冷却期 = 3 轮
```

## 3. 认知与推理

### F12 怀疑对数几率 `[RUNTIME]` §5.2

```
ℓ_{i→j}(t) = ℓ₀^(i) + Σ_{e∈E_i(t)} ω_i(e) · log( P(e|H_j) / P(e|¬H_j) )
```

`ℓ₀^(i)` 由 Canon 多疑度与初始关系决定。伪证：真实 `logLR < 1`，角色误以为 `> 1`，两者分离存储。

### F13 Sigmoid `[RUNTIME]` §5.2

```
S_{i→j} = σ(ℓ_{i→j}) = 1 / (1 + e^(−ℓ_{i→j}))
```

### F14 证据权重 `[RUNTIME]` §5.2

```
ω_i(e) = cred(src) · strength(e) · (1 − bias_i)
```

### F15 证据 logLR 表 `[CONFIG]` §5.2

| 证据类型 | logLR |
|---|---|
| 指纹 / 物证直接匹配 | +2.2 |
| 目击证词（来源信任 > 0.6） | +1.2 |
| 时间线矛盾 | +1.5 |
| 动机存在 | +0.5 |
| 神色慌张 | +0.3 |
| 有效不在场证明 | −2.5 |
| 他人担保（信任高） | −0.8 |

### F16 归一化投票分布 `[RUNTIME]` §5.3

```
P_i(culprit=j) = exp(ℓ_{i→j} / T_v) / Σ_{j'∈S} exp(ℓ_{i→j'} / T_v)
```

### F17 矛盾检测 `[RUNTIME]` §5.4

```
mutex(a.prop, b.prop) ∧ a.belief > 0.5 ∧ b.belief > 0.5
s = min(a.belief, b.belief) · strength(a,b)
s > 0.35 → contradiction_event
触发：arousal += 0.3，suspicion(相关方) += 0.2
```

## 4. 表演与谎言

### F18 泄露强度 `[RUNTIME，本后台已提供派生式]` §6.1

```
σ_leak = σ0 · (1 + γ1·arousal_i) · (1 + γ2·stress_i) · (1 − acting_skill_i)
ε ~ N(0, σ_leak²)
```

### F19 表现态度 `[RUNTIME]` §6.1

```
R^show_{i→j} = clip( R^true_{i→j} + Δ^mask_{ij} + ε )
```

### F20 说谎收益 `[RUNTIME]` §6.2

```
LieGain = V_protect + V_frame − P_exposed·C_exposed − μ_i·HonestyCost
μ_i = values.honesty
LieGain > 0 → 说谎；否则说真话或沉默 / 转移
P_exposed ≈ f(在场者掌握的反证数量, cognitive_load)
```

### F21 谎言热度 `[RUNTIME]` §6.3

```
heat(t+1) = 0.85·heat(t) + 0.4·n_challenge + 0.6·n_contradiction
heat > 0.3 → 注入 Prompt；heat > 0.8 → 圆谎或崩盘
```

## 5. 节奏与决策

### F22 目标热度曲线 `[RUNTIME]` §8.1

```
H*(t) = H_max · (t/T)^1.6
```

### F23 实际热度与调控 `[RUNTIME]` §8.1

```
H(t) = α·n_accuse + β·n_contradiction + γ·mean_arousal
H(t) < H*(t) − δ → 注入事件 / 释放线索
H(t) > H*(t) + δ → 插入休整 / 私聊 / 幽默 NPC
```

### F24 行为效用 `[RUNTIME]` §9.2

```
U(a) = w_p·Fit(a,Persona) + w_g·Gain(a,Goal) + w_e·Impulse(a,e)
     + w_s·Social(a,R) − w_r·Risk(a) − w_c·Cost(a)
Social(a,R) = Σ_j R_{i→j} · impact(a,j)
```

### F25 动作概率与温度 `[RUNTIME，本后台已提供 T_v 式]` §9.2 / §5.3

```
P(a) = exp(U(a)/T) / Σ_{a'} exp(U(a')/T)
T    = T0 · (1 + γ·arousal + γ'·intoxication)
T_v  = T0 · (1 + κ_stress·stress) / (1 + κ_C·C)
```

### F26 记忆检索 `[RUNTIME]` §9.3

```
score(m) = α·e^(−Δt/τ_m) + β·imp(m) + γ·cos(q,m) + δ·emo_congruence(m,e_t)
```

## 6. 评分与可解性

### F27 AI 评分 `[RUNTIME]` §14.1

```
Score_AI = w1·GoalCompletion + w2·PersonaFidelity
         + w3·DramaContribution + w4·Consistency − w5·Violations
```

### F28 可解性 `[CONTENT]` §11.3

```
∀ c ∈ KeyConclusions: |IndependentPaths(c)| ≥ 2
checks = 唯一解 / 证据冗余 / 无矛盾 / 凶手可达 / 无辜可洗 / 锁可触发 / 信息闭合
```

## 7. 基础数据处理（本后台范围，已实现）

### F29 证据打分 `[DATA]`

```
raw_e ∈ {0.1, 0.3, 0.5, 0.7, 0.9}
score       = Σ(w_e · raw_e) / Σ w_e
consistency = 1 − min(1, std(raw_e) / 0.3)
confidence  = (n / (n + 2)) · consistency
n < 2 → score = null, reason = "insufficient_evidence"
```

### F30 派生能力 `[DATA]`

```
observation     = 0.6·O + 0.4·skill_norm('observation')
reasoning       = 0.5·O + 0.3·C + 0.2·skill_norm('reasoning')
deception       = 0.5·(1−A) + 0.3·C + 0.2·acting_skill
impulse_control = 0.6·C + 0.4·(1−N)
nu              = 0.5 + N
leak_sigma      = F18
T_v             = F25
tech_craft      = 0.7·skill_norm('craft') + 0.3·O
combat_effective = 0.6·skill_norm('combat') + 0.2·physique + 0.2·(1−N)
social_leverage = 0.4·E + 0.3·skill_norm('eloquence') + 0.3·(1−A)
```

## 8. 实现边界

| 类别 | ID | 本轮 CLI |
|---|---|---|
| DATA | F29 F30 | 已实现（规则 + `config/*.json`） |
| CONFIG | F05 F06 F08 F11 F15 | 样例配置已提供 |
| RUNTIME | F01–F04, F07, F09, F10, F12–F14, F16–F27 | 由装配后的 GM / 角色 agent 消费；不进数据 CLI |
| CONTENT | F28 | 仅登记；解析用户剧本时用于可玩性校验，不生成剧本 |

## 9. 数值走查（IMMS 附录）

设 `trust0=0.30, like0=0.20, intimacy=0.40, N=0.66`。

```
κ = 1 + 0.6·0.40 = 1.24
ν = 0.5 + 0.66   = 1.16
R2 Δtrust = +0.25·1.0·1.24·1.16 = +0.3596  → trust 0.30 → 0.6596 ≈ 0.66
R3 ℓ       = 0 + 1.5·0.8 = 1.2               → S = σ(1.2) = 0.7685 ≈ 0.77
R3 Δtrust = −0.45·1.2·1.24·1.16 = −0.7747   → trust 0.66 → −0.1147 ≈ −0.12
R3 arousal += 0.5·1.8 = 0.9                 → σ_leak 放大 ≈ 2.1×
R4 Align = 0.73 > θ_on 0.70                 → 阵营反转
```
