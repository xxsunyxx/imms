# 剧本：雨夜山庄

## World
时代: 现代
地点: 雨夜山庄
封闭性: 是

## Characters
### char_01 林婉
origin_name: 林婉
lineage: 富商之女
social_rank: 6
public_identity: 山庄女主人
assigned_goals: 找出凶手|weight=0.8|deadline=3
canon_evidence:
- OCEAN.O|0.7|她常追问账目之外的细节|ch1:p6
- OCEAN.O|0.9|她提出三种可能|ch4:p6
- OCEAN.C|0.7|她把每一笔账都记两遍|ch1:p1
- OCEAN.C|0.9|她列出三天的用度清单|ch2:p3
- OCEAN.A|0.3|她当场反驳管家的提议|ch1:p2
- OCEAN.A|0.1|她拒绝为仆人求情|ch3:p1
- skill_set.observation|0.7|她注意到烛台的蜡油|ch3:p4
- skill_set.observation|0.7|她发现门闩有划痕|ch4:p2
- health|0.5|她常年服药|ch2:p1
- health|0.3|她爬楼时气喘|ch3:p2

### char_02 赵管事
origin_name: 赵德
lineage: 山庄管家
social_rank: 4
public_identity: 账房管事
assigned_goals: 掩盖挪用|weight=0.9|deadline=2
canon_evidence:
- OCEAN.C|0.7|他每天核对两遍账本|ch1:p4
- OCEAN.C|0.9|他提前锁好账房|ch2:p2
- OCEAN.A|0.7|他主动替众人倒酒|ch1:p5
- OCEAN.A|0.9|他从不与人争执|ch3:p3
- skill_set.observation|0.5|他记得每个人的座位|ch2:p5
- skill_set.observation|0.7|他发现书房灯灭了|ch4:p1
- health|0.9|他常年晨跑|ch2:p4
- health|0.7|他搬运重物不喘|ch3:p5

### char_03 苏医生
origin_name: 苏敏
lineage: 城中医生
social_rank: 5
public_identity: 家庭医生
assigned_goals: 保护病人隐私|weight=0.7|deadline=3
canon_evidence:
- OCEAN.O|0.7|她总追问病因的另一种可能|ch1:p3
- OCEAN.O|0.9|她画出的症状图超出常规|ch3:p2
- OCEAN.N|0.3|她在停电时仍保持冷静|ch2:p1
- OCEAN.N|0.1|她面对尸体没有慌乱|ch4:p3
- skill_set.reasoning|0.9|她从药瓶推断服药时间|ch3:p4
- skill_set.reasoning|0.7|她还原了中毒剂量|ch4:p4
- health|0.7|她体检各项正常|ch2:p3
- health|0.9|她连续值夜无疲态|ch4:p5

## TruthGraph
- f_001 赵管事在22:00进入书房
- f_002 烛台蜡油燃尽约两时辰
- f_003 苏医生调换过药瓶

## Timeline_True
- 21:50 林婉离开客厅
- 22:00 赵管事进入书房
- 22:30 苏医生进入药房

## CluePool
- clue_01 烛台蜡油|logLR:char_02=1.5
- clue_02 账房钥匙划痕|logLR:char_02=1.2
- clue_03 药瓶指纹|logLR:char_03=1.5

## PlotLocks
- must_say:我那晚一直在书房
- must_trigger:断电

## Endings
- 指认赵管事则真凶伏法
- 指认苏医生则冤案
