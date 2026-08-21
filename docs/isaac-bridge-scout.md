# Isaac(go2W_Sim) 桥接成本侦察 — gate 解锁时的决策输入

(2026-08-22, round 77 后侦察。结论支持"robosuite 先行, Isaac gated"的裁定。
仓库: ~/Z-Robotics-Lab/go2W_Sim, 独立 git 仓, Isaac Sim 5.1 Docker + Isaac Lab v2.3.2。)

## 硬约束

- Isaac 只在容器 `go2w-isaac` 内跑(`/isaac-sim/python.sh`, CPython 3.11);
  宿主 .venv(3.12) **无法 import isaaclab** —— robosuite 式"进程内 make_env"不可复刻,
  EnvProvider.make_env 必须返回跨进程代理。
- 两个层级的"episode", 不可混淆:
  - **L-A locomotion RL gym 任务**(`RobotLab-Isaac-Velocity-Flat-Unitree-Go2W-v0` 等):
    标准 gym.make → reset(seed) → step, **可复现**; 但纯速度跟踪, 无臂/相机/仓库。
  - **L-B warehouse_nav.py**(带臂+全传感器+仓库): 长驻进程, ROS2 topic + HTTP 8042 桥,
    **无同步 step API**; `/sim/reset` 无 seed, 只是传送回固定出生位姿, 非 episode 重采样。

## 最省事的可编程路径(gate 解锁时从这开始)

1. 零代码验证: `docker exec -u 0 go2w-isaac bash -c "cd /workspace/go2w/robot_lab && TERM=xterm /isaac-sim/python.sh scripts/reinforcement_learning/rsl_rl/play.py --task RobotLab-Isaac-Velocity-Flat-Unitree-Go2W-v0 --headless --num_envs 1 --seed 0"`
2. plugins/embodiment_isaac: make_env 返回代理, 内部长驻 `docker exec -i` 子进程,
   stdin/stdout JSON-lines(reset/step/obs), 容器内 runner 照抄 play.py:158,217,225-227。
   约 40 行, 无 ROS 无 gRPC。
3. 带臂/仓库任务(第二阶段): warehouse_nav.py 当 sim 服务器, 代理走
   /sim/reset + /cmd_vel + /ground_truth/pose; 阻抗失配: 自由 200Hz 跑 + 0.5s 看门狗,
   "step"只是保持命令一个墙钟片刻, 非严格同步 stepped env。

## 对双轨证据纪律的含义

- L-A 有 env 级 seed, 配对初始条件可行; L-B 当前形态连配对初始条件都做不到(reset 无 seed),
  接进来之前要先给 warehouse_nav.py 加 seeded reset。
- 已知坑(该仓 docs/pitfalls.md): headless+enable_cameras 静默死(坑10);
  fullScan 点云点序非时序(坑34); timeline 冻结根因是外部 X11 注入, 长跑必须带 freeze fix
  应用层四件套(坑39/40)。

## 该仓状态(侦察时)

M1 场景/M2 传感器/M3 导航/M5 vector_os E2E 已验收(README 状态表; M2 的 PiPER ROS2
控制 checkbox 过时, 代码已实现)。抓取 WIP 停在 feat/grasp-wip。部署策略 model_5495。
