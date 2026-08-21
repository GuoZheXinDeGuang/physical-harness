# L2 预研: governor/ 命名空间的去向盘点(round 62)

L2 的定义(GOAL v2): 删除 governor 命名空间, 插件间零残留依赖。
逐模块盘点(行数为当前值, 去向按依赖方向排序):

| 模块 | 去向 | 依赖难点 |
|---|---|---|
| episode_log.py / audit.py | **已移(round 65, rung C)**: episode_log -> harness/(依赖面干净); audit -> plugins/rsi(依赖 FeatureView+Trigger, 审计的是 RSI 语义) | - |
| features.py / percept.py / invariant.py | harness/(特征契约+隔离+不变量是内核职能), 提取器注册反转进 embodiment 插件 | **中: import 时注册的 spawn 语义必须保住**(见 ARCHITECTURE 推迟记录) |
| env.py | plugins/embodiment_robosuite(env 构建+FrozenPolicy+任务表), EpisodeSpec 上提进 harness | **高: EpisodeSpec 是全系统的通用货币**, 上提时字段序/默认值/pickle 形态都不能动 |
| policy.py | plugins/policies(驱动器+交还契约) | 中: RecoveryActor 与 governed 的耦合 |
| governed.py | plugins/rsi(受治理 rollout 是 RSI workload 的核心) | 高: 与 features/percept/policy 三向耦合, 最后动 |
| gate.py / campaign.py | **已移(round 64, rung B)**, governor 留转发壳; 第一方调用点全部改指新家 | - |
| power.py / search.py / screen.py | **已移(round 63, rung A)**, governor 留 PEP 562 转发壳 | - |
| (campaign 已并入上行; CampaignStore 与 harness artifacts 合一另立 rung, 动存档格式前先定兼容策略) | | |
| beam.py / recovery_search.py / repertoire.py / servo.py / bc.py / demos.py / proposer.py / parallel.py | plugins/rsi 或 plugins/policies 按归属 | 低 |

顺序建议(每步 parity): 统计四件套 -> episode_log/audit -> campaign -> policy ->
features 反转(含 registry 上提) -> env/EpisodeSpec 上提 -> governed 收尾 -> 删空壳。
预计 6-8 个 rung。**EpisodeSpec 上提是唯一动"通用货币"的一步, 单独成 rung 并双策略 parity。**
