# STATUS

**Goal:** 见 GOAL.md — 建一个 Mac 上真跑仿真的具身 harness，冻结策略 + 演化 critic/recovery + 特权预算。
**Mode:** convergent（先打到 GOAL.md 的 5 条验收），之后转 evolving。
**Round:** 1 (setup + analysis)
**Updated:** 2026-08-19

## 现在在哪

- [x] 环境可行性实测完成（见 docs/verified-environment.md）
- [x] GOAL.md 定稿
- [ ] 架构设计（workflow wf_585c9df5-4ac 运行中：7 map agent + 5 设计问题 × 对抗 critic）
- [ ] 骨架实现
- [ ] 第一次真实 campaign

## 下一步

等 workflow 返回 → 按设计落骨架 → 先打通「单 episode 跑通并落日志」这条最短真实链路。

## 阻塞

无。

## 不要重做的事

- 不要试图跑 LIBERO / RoboCasa：flash-attn 只有 linux_x86_64 wheel，需 nvidia-smi + CUDA 12.6 + EGL。已确认不可行。
- 不要用 mujoco 3.11 配 robosuite 1.5.2：`MjData.qM` 已改名 `M`，必须 pin mujoco==3.3.7。
- 不要在 mac 上设 MUJOCO_GL=osmesa：非法值，直接抛。无头就不设。
