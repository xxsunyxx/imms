# 剧本：跨源等效测试

## World
题材: 跨作品对照
用途: 归一化公式验证

## Characters
### char_conan 江户川柯南
origin_name: 江户川柯南
canon_evidence:
- OCEAN.O|0.7|他对每个异常细节都追问到底|ch1:p1
- OCEAN.O|0.9|他主动重构案发过程|ch1:p2
- OCEAN.C|0.7|他先记录再行动|ch2:p1
- OCEAN.C|0.9|他提前布置好证词陷阱|ch2:p2
- OCEAN.E|0.5|他多数时候冷眼观察|ch3:p1
- OCEAN.E|0.7|他在同伴前主动组织讨论|ch3:p2
- OCEAN.A|0.5|他保持礼貌距离|ch4:p1
- OCEAN.A|0.7|他愿意替同伴承担风险|ch4:p2
- OCEAN.N|0.3|他面对枪口仍冷静|ch5:p1
- OCEAN.N|0.5|他事后才感到后怕|ch5:p2
- skill_set.observation|0.9|他注意到鞋印的深度差|ch1:p3
- skill_set.observation|0.9|他从杯沿推断人数|ch2:p3
- skill_set.reasoning|0.9|他一推理出完整链条|ch3:p3
- skill_set.reasoning|0.9|他用足球还原轨迹|ch4:p3
- skill_set.craft|0.3|他偶尔借用现成道具|ch5:p3
- skill_set.craft|0.5|他改装过领结变声器|ch5:p4
- skill_set.combat|0.5|他靠足球击中目标|ch6:p1
- skill_set.combat|0.5|他躲过一次突袭|ch6:p2
- skill_set.eloquence|0.5|他用童声掩饰推理|ch7:p1
- skill_set.eloquence|0.7|他模仿毛利小五郎的口吻|ch7:p2
- physique|0.5|他身体是小学生|ch8:p1
- physique|0.5|他奔跑容易气喘|ch8:p2
- values.loyalty|0.9|他始终不暴露身份|ch9:p1
- values.loyalty|0.9|他坚持回京都找毛利兰|ch9:p2
- values.honesty|0.7|他对朋友尽量坦白|ch10:p1
- values.honesty|0.7|他隐瞒身份但不出卖同伴|ch10:p2
- values.ambition|0.5|他只想恢复原状|ch11:p1
- values.ambition|0.5|他不追求名利|ch11:p2

### char_iron 托尼·斯塔克
origin_name: 托尼·斯塔克
canon_evidence:
- OCEAN.O|0.9|他不断试错新方案|ch1:p4
- OCEAN.O|0.9|他把科幻想法直接造出来|ch1:p5
- OCEAN.C|0.5|他常临时改计划|ch2:p4
- OCEAN.C|0.7|他给战甲设定严格自检|ch2:p5
- OCEAN.E|0.7|他在发布会上掌控全场|ch3:p4
- OCEAN.E|0.9|他主动拉拢盟友|ch3:p5
- OCEAN.A|0.3|他当众羞辱对手|ch4:p4
- OCEAN.A|0.5|他保护核心团队|ch4:p5
- OCEAN.N|0.5|他在危机中嘴硬|ch5:p4
- OCEAN.N|0.7|他事后反复复盘失败|ch5:p5
- skill_set.observation|0.7|他从残骸读出攻击角度|ch1:p6
- skill_set.observation|0.7|他 monitors 全楼传感器|ch2:p6
- skill_set.reasoning|0.9|他当场推导能源方案|ch3:p6
- skill_set.reasoning|0.9|他预判对手三步|ch4:p6
- skill_set.craft|0.9|他手工组装原型机|ch5:p6
- skill_set.craft|0.9|他迭代出新一代战甲|ch6:p3
- skill_set.combat|0.7|他亲自驾驶战甲作战|ch6:p4
- skill_set.combat|0.9|他在空战中反杀|ch6:p5
- skill_set.eloquence|0.9|他用演讲扭转舆论|ch7:p3
- skill_set.eloquence|0.9|他用玩笑化解质询|ch7:p4
- physique|0.7|他常年健身|ch8:p3
- physique|0.9|他靠战甲弥补体能|ch8:p4
- values.loyalty|0.7|他最终选择保护队友|ch9:p3
- values.loyalty|0.7|他不抛弃责任|ch9:p4
- values.honesty|0.5|他常用玩笑回避实话|ch10:p3
- values.honesty|0.7|他对队友坦承失误|ch10:p4
- values.ambition|0.9|他追求技术极限|ch11:p3
- values.ambition|0.9|他想把公司变成能源帝国|ch11:p4

### char_mori 毛利兰
origin_name: 毛利兰
canon_evidence:
- OCEAN.E|0.7|她主动照顾身边人|ch1:p7
- OCEAN.E|0.9|她在人群中总能稳住场面|ch2:p7
- OCEAN.A|0.7|她总是先为别人着想|ch3:p7
- OCEAN.A|0.9|她愿意原谅犯错的人|ch4:p7
- skill_set.combat|0.7|她空手阻止过歹徒|ch5:p7
- skill_set.combat|0.9|她在危急时保护同伴|ch6:p7

## TruthGraph
- f_001 两人被放在同一标准向量下比较
## Timeline_True
- 00:00 归一化完成
## CluePool
- clue_01 标准向量|logLR:char_conan=0.0
## PlotLocks
- must_trigger:compare
## Endings
- 输出逐维并列数值
