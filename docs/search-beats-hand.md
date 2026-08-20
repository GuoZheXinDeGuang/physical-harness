# 自动搜索 vs 人手：一次可复现的对照

2026-08-19。60 个 dev episode（seed 2000-2059），基线 56.7%。

## EOD 扫描（每个可观测特征）

| 特征 | EOD（首次达到 2σ 的步） | 峰值 \|σ\| |
|---|---|---|
| `observable.finger_gap` | 59 | 8.09 |
| `observable.gripper_effort` | 57 | 3.06 |
| `observable.eef_z` | 88 | 2.18 |
| `observable.joint_speed` | 无 | 1.50 |

## 触发器搜索结果（privilege_budget=0）

| 触发器 | recall | fp | 触发步 | lead | score |
|---|---|---|---|---|---|
| `gripper_effort > 0.0447` dwell=2 @t≥57 | 1.00 | 0.00 | 58 | **42** | 1.105 |
| `finger_gap < 0.0086` dwell=1 @t≥59 | 1.00 | 0.00 | 64 | 36 | 1.090 |
| `gripper_effort < 3.7e-5` @t≥57 | 0.96 | 0.09 | 89 | 11 | 0.883 |

搜索耗时 **0.1 秒**。

## 三个结论

**1. 搜索重现了人手的答案，并且更快。**
我手写时先用 episode 末尾统计量定了 tau=0.005，60 个 episode 只触发 1 次（+1.7%，不显著）；
改用 EOD 扫描后手工收敛到 step 64 附近、tau≈0.025。
自动搜索直接给出 `finger_gap < 0.0086 armed from t=59`，触发步 64 —— 同一个答案。

**2. 搜索找到了比人更好的解。**
`observable.gripper_effort`（夹爪关节速度模）我压根没考虑过。它同样 recall=1.00 / fp=0.00，
但**提前 6 个控制步**触发（lead 42 vs 36），给 recovery 多出 0.3 秒。

**3. 在这个任务上，特权对「检测」毫无帮助。**
放开预算到 1 之后，最佳特权触发器 `privileged.cube_z < 0.8215` 得分 1.095，
**低于**最佳零特权触发器的 1.105。

有意思的例外：`privileged.grasp_error > 0.0244 @t≥26` 在 **step 27** 就触发（lead=73，比零特权早一倍多），
但 recall 只有 0.88、fp 0.06。**特权买到的是「更早」，不是「更准」。**
这个权衡值得单独跟踪：早期预警可能允许更廉价的 recovery，代价是要处理误报。
