# 实测环境事实

全部为本机实际运行输出，非文档抄录。日期 2026-08-19。

## 机器

- macOS arm64（Darwin 25.3.0），18 核（6 性能 + 12 能效），64 GB RAM，无 NVIDIA GPU

## 仿真栈（可用）

| 组件 | 版本 | 状态 |
|---|---|---|
| mujoco | 3.3.7 | 原生 arm64，简单场景 352k steps/s |
| robosuite | 1.5.2 | Lift + Panda 无头跑通 |

pin 原因：robosuite 1.5.2 要求 `mujoco>=3.3.0`，但 mujoco 3.11 把 `MjData.qM` 改名成 `M`，
robosuite 的 OSC 控制器 (`controllers/parts/controller.py:227`) 仍调 `mj_fullM(..., data.qM)`，
在 3.11 上直接 AttributeError。3.3.7 可用。

## 吞吐实测（Lift / Panda / control_freq=20 / horizon=200）

| workers | wall | steps/s | episodes/min |
|---|---|---|---|
| 1 | 2.08s | 96 | 28.8 |
| 4 | 1.87s | 429 | 128.6 |
| 8 | 2.43s | 658 | 197.3 |
| 10 | 2.83s | 708 | **212.3** |

对照：Zetta 论文报告 8×A100 上 35.1 episodes/min（含 VLA 推理）。
我们不在环里跑大模型，所以一台笔记本的 rollout 吞吐反而高一个数量级。
**结论：演化循环的 rollout 预算在这台机器上不是瓶颈。**

## 观测空间（Lift/Panda，`use_camera_obs=False`）

robosuite 自己就把观测分了组，这正好是特权边界：

- `robot0_proprio-state` → **真机可测**：`robot0_joint_pos/vel/acc`, `robot0_eef_pos/quat`,
  `robot0_gripper_qpos/qvel`
- `object-state` → **仅仿真可知**：`cube_pos`, `cube_quat`, `gripper_to_cube_pos`

action_dim = 7（OSC_POSE 6 + gripper 1）

## 不可用（已确认，不要再试）

- LIBERO / RoboCasa / GR00T / pi-0.5：flash-attn 仅 `linux_x86_64` wheel 且无 CPU fallback；
  安装脚本要求 `nvidia-smi` + CUDA 12.6；MuJoCo 渲染走 EGL（NVIDIA 专有路径）。
- Zetta 全仓 `grep sys.platform|darwin` 零命中 —— 代码根本没考虑非 Linux。

## 可复现性（决定门禁能否成立）

配对同种子门禁的前提是：同一个种子重跑两次必须逐比特一致，否则测到的是仿真器噪声不是 critic 效果。

**坑：`np.random.seed(s)` 对 robosuite 无效。** 环境在构造时自己建 RNG
（`environments/base.py:141-142`：`self.seed = seed; self.rng = np.random.default_rng(seed)`），
全局 numpy 种子管不着它。用全局种子时，同种子两次跑出来的 `cube_pos` 和 `robot0_joint_pos` 都不同。

**正确做法：`suite.make(..., seed=N)`。**

验证结果（Lift/Panda，60 步固定控制序列，对轨迹做 sha256）：

```
seed=0  A=83ec7fc33a5da548  B=83ec7fc33a5da548  identical=True
seed=1  A=2a4f152ff103c183  B=2a4f152ff103c183  identical=True
...  5/5 一致，且 5 个种子产生 5 条不同轨迹
```

**这条必须写进 harness 的环境 provider，并配一个回归测试**：任何人以后改环境构造，
如果不小心退回全局种子，配对门禁会静默地退化成掷硬币。
