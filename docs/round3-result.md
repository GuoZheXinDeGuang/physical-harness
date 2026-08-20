# Round 3：端到端链路跑通，五条验收全部达成

2026-08-19。搜索在 dev 种子（2000-2059），门禁在 **held-out 种子（1000-1059，从未参与搜索）**。

## 自动选出的触发器

```
observable.gripper_effort > 0.044661 for 2 steps, armed from t=57  (privilege=0)
```

## 迁移消融曲线（held-out，n=60）

| recovery 感知 sd | 基线 | 受治理 | 提升 | 修复 | 破坏 | 触发 | 精确 McNemar p | 声明特权 |
|---|---|---|---|---|---|---|---|---|
| 0.000 真值 | 50.0% | 90.0% | +40.0% | 24 | 0 | 34 | <1e-5 | **1** |
| 0.010 | 50.0% | 83.3% | +33.3% | 21 | 1 | 34 | 1e-5 | 0 |
| **0.020 真实水平** | 50.0% | **75.0%** | **+25.0%** | 17 | 2 | 34 | **0.00073** | **0** |
| 0.030 | 50.0% | 53.3% | +3.3% | 6 | 4 | 34 | 0.754 | 0 |

## 对比 Round 1 的手写实验

| | 触发器 | 触发步 | sd=0.020 时的提升 | p |
|---|---|---|---|---|
| 人手（round 1） | `finger_gap < 0.025` | 64 | +13.3% | 0.057 **不显著** |
| harness 自动（round 3） | `gripper_effort > 0.0447` | 58 | **+25.0%** | **0.00073 显著** |

差别的来源是**提前 6 个控制步**（0.3 秒）检测，recovery 多出的时间把结果推过了显著性线。
搜索目标函数里的 earliness 权重不是装饰，它在 held-out 上兑现了。

**结论修正：** round 1 得出的「零特权只剩 +13.3% 且不显著」是**那一条手写规则**的上限，
不是这个任务的上限。更好的规则在零特权下依然显著。特权买到的是 +40% 对 +25% 的差距，
不是「有没有效果」的差距。

## 五条验收对照（GOAL.md）

1. **端到端真实链路** — `rollout → search → governed_rollout → paired_gate → ablation_curve`
   全部跑真仿真，42 秒完成 60 dev + 4×120 held-out episode。
2. **held-out 显著提升** — +25.0%，p=0.00073，零特权。
3. **每条技能自带特权预算与消融** — `Bundle.declared_privilege()` 自动计算；
   `sensor_sd=0` 被机制标记为 privilege=1，不依赖人去声明。
4. **重建不变量真的会炸** — `tests/test_invariants.py::test_injection_is_caught`
   故意在 view 被记录后注入一个特权键，断言抓住。
   配套 `test_cached_digest_would_not_catch_injection` 记录了为什么 digest 必须是方法而非缓存值。
5. **测试全绿，无 mock 验证** — 19 passed，全部走真 MuJoCo。

## 三个来自 critique agent 的关键修正（它们真的跑了代码去证伪）

1. **缓存 digest 的断言是循环的**（`f(x)==f(x)`），永远不会失败。改成对 live contents 求值。
2. **`FeatureView` 必须继承 `Mapping` 而非 `dict`**：dict 子类的 `.get()` / `{**v}` / `dict(v)`
   由 C 实现服务，绕过 `__getitem__`，特权读取完全不被记账。已用参数化测试锁死五种访问器。
3. **子进程会复活特权特征**：worker 重新 import `governor.features`，模块级 `register()` 重跑，
   父进程卸载无效。隔离设计中和了它 —— 边界是 **view** 不是 registry，
   `test_containment_holds_in_a_fresh_worker_process` 同时断言「registry 里确实有」和「view 里确实没有」。
