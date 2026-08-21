# L2 预研: governor/ 命名空间的去向盘点(round 62)

L2 的定义(GOAL v2): 删除 governor 命名空间, 插件间零残留依赖。
逐模块盘点(行数为当前值, 去向按依赖方向排序):

| 模块 | 去向 | 依赖难点 |
|---|---|---|
| episode_log.py / audit.py | **已移(round 65, rung C)**: episode_log -> harness/(依赖面干净); audit -> plugins/rsi(依赖 FeatureView+Trigger, 审计的是 RSI 语义) | - |
| features.py / percept.py / invariant.py | **已移(round 69, rung F)**: 契约机制 -> harness, 提取器声明 -> plugins/embodiment_robosuite/features.py(import 时注册, worker 经 provider ref 触发) | - |
| EpisodeSpec | **已上提(round 70, rung G)** -> harness/spec.py 逐字迁移, governor.env 真实 re-export(类同一对象), pickle/字段序不变 | - |
| env 具身部分(TASKS/task_config/object_key/lifted/_default_make_env/CONTROL_FREQ) | **已移(round 71, rung H)** -> plugins/embodiment_robosuite/env.py; EnvProvider 契约扩展 object_key/success, rsi 经契约取语义 | - |
| FrozenPolicy / phase_at / PHASE_HEIGHT + make_env 派发点 | **有据滞留 governor**: 运动词表被 policies(drivers)与 rsi(recovery)共享, 与 proposer 同类; 出口 = 恢复程序携带绝对高度或 spec 携带高度表 | 中 |
| policy.py | **已移(round 66, rung D)**: plugins/policies/drivers.py(驱动器+交还契约+RecoveryActor), 适配器改指兄弟模块 | - |
| governed.py | plugins/rsi(受治理 rollout 是 RSI workload 的核心) | 高: 与 features/percept/policy 三向耦合, 最后动 |
| gate.py / campaign.py | **已移(round 64, rung B)**, governor 留转发壳; 第一方调用点全部改指新家 | - |
| power.py / search.py / screen.py | **已移(round 63, rung A)**, governor 留 PEP 562 转发壳 | - |
| (campaign 已并入上行; CampaignStore 与 harness artifacts 合一另立 rung, 动存档格式前先定兼容策略) | | |
| servo / repertoire / recovery_search / beam / parallel + RecoveryActor | **已移(round 67, rung E)** -> plugins/rsi(干预机构归 workload) | - |
| bc / demos | **已移(round 67, rung E)** -> plugins/policies(策略训练管线) | - |
| proposer.py | **退回 governor(有据)**: 同时被 reasoner 落点与 rsi 校验共享, 直接分家会造成插件互 import; 离开 governor 的前置条件是 brief 自带策略词表的重构 | 中 |

顺序建议(每步 parity): 统计四件套 -> episode_log/audit -> campaign -> policy ->
features 反转(含 registry 上提) -> env/EpisodeSpec 上提 -> governed 收尾 -> 删空壳。
预计 6-8 个 rung。**EpisodeSpec 上提是唯一动"通用货币"的一步, 单独成 rung 并双策略 parity。**
