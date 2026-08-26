# 多仿真适配 — venv-per-sim + 卡片 per-sim + 常驻 runtime per-sim

首要事实（调查报告：`local-archive/robocasa-install-report.md`、
`local-archive/sim-survey.md`，2026-08-26）。目标：RoboCasa 做开发主力（长程厨房
mission），RoboTwin/RoboDojo 走 XPolicyLab 契约做外部 benchmark。**不为任何一个
sim hardcode harness**——每个 sim 是一张 embodiment 卡 + 一个解释器，底座零 sim 知识。

## 0. 地形（已核实）

* 三套引擎不可共 venv：robocasa 钉 robosuite master（自称 1.5.2，含 pip 1.5.2 没有的
  `load_model_on_init`）+ mujoco 3.3.1 + numpy 2.2.5；harness .venv 钉 robosuite
  1.5.2 release + mujoco 3.3.7 + numpy 1.26.4。numpy 1.x/2.x ABI 是隔离的最大理由。
* 底座依赖是松的（`numpy>=1.26` + zstandard），base lane 在 robosuite 不可导入的机器
  上 522 绿——**底座本来就是 sim-agnostic 的**，这是本设计的全部凭据。
* RoboCasa venv 已就位：`sims/robocasa-venv`（py3.12，robocasa 1.0.1@a07e365 +
  robosuite master@5ce6643 editable-compat + 23G 资产），EGL 无头冒烟通过，同 seed
  双 rollout 逐元素一致（确定性成立），`get_ep_meta()/set_ep_meta()` 可做场景指纹。
* **sys.path 遮蔽陷阱**：cwd 能看见 `sims/robocasa/`（repo 根目录名 == 包名）时
  `import robocasa` 命中 namespace package，374 个 kitchen env 静默不注册。
  规矩：robocasa runtime 一律 cwd=physical-harness repo（那里没有 robocasa 目录）。
* RoboTwin(SAPIEN)/RoboDojo(Isaac 5.1) 与我们隔 websocket：XPolicyLab 契约 = 每
  policy 一个目录，`model.py` 四方法（`__init__/update_obs/get_action/reset`）+
  `deploy.yml` server 配置。policy 与仿真器异环境运行是官方设计——两个 benchmark
  只需一次契约实现。RoboDojo 前置风险：驱动 570/580 + CUDA 12.8（改驱动先盘点），
  license 非商业限定。

## 1. 架构 — 三条既有轴各自延长，零新概念

```
sims/robocasa-venv  ──解释器──▶  常驻 runtime #2 (runs/session-robocasa, MUJOCO_GL=egl,
                                  cwd=$REPO, PYTHONPATH=$REPO)
plugins/embodiment_robocasa/  ── 卡片：env provider + percept provider + PREDICATES 原语
board submit_brief(session=…) ── 路由：写哪个 session 的 inbox（默认 session-main 不变）
```

* **runtime 就是"看一个 inbox 的进程"**（`harness_runtime.py`：claim=原子 rename）。
  多 sim = 多常驻 runtime，各自 session 目录、各自解释器，互不知晓。cockpit 的
  adopt-or-spawn 逐 session 应用；`--stop` 精确 pidfile 不变。
* **board 面加一个 `session` 参数**（默认 `session-main`）：storecli / board fn /
  mcp tool 三脸同改（双脸铁律）。UI 会话选择器已存在，天然显示第二 runtime。
* **测试 marker 镜像 robosuite 模式**：`robocasa` marker + conftest 同款 find_spec
  自动跳过。base lane（`-m "not robosuite"`）会多出 robocasa 跳过项——base-gate
  快照 + README 计数**同 commit 刷**（纪律照旧）。robocasa venv 里只跑
  `pytest -m robocasa`（那里的 robosuite 是 master，不许跑 robosuite lane）。

## 2. 卡片 — `plugins/embodiment_robocasa/`

manifest：`actuation="sim"`、`needs_sim=true`、
`third_party=["robocasa","robosuite","mujoco"]`（边界测试读它；任何其他卡 import
robocasa = embodiment 泄漏）。

* **env provider**：包 `robocasa create_env`；TASKS 注册表把 mission 任务名映射到
  robocasa env 名 + kwargs（seed 透传给场景生成 rng）。每次 make 归档
  `get_ep_meta()`（layout/style/object_cfgs 场景指纹）进 episode 封存。
* **percept provider**：privileged obs（`{name}_pos/_quat` 全物体 + fixture 状态）
  加噪走现行 oracle/percept 双轨——robocasa 不自带噪声模型，wrapper 里加，与
  robosuite 卡同构。
* **PREDICATES 原语层**：`robocasa.utils.object_utils`（~30 个谓词：
  `obj_inside_of/check_obj_grasped/gripper_obj_far/…`）+ fixture 状态 API
  （`microwave.is_closed/get_state()['turned_on']`、`fridge.is_open`、
  `sink.get_handle_state` 等）就是免费 oracle。卡片把它们包成 mission 可引用的
  谓词表；时序 flag（"水开着时菜必须在槽里"）抄 MultistepSteaming 的累积模式，
  每步采样、wrapper 持有，不动 robocasa 源码。
* **驱动**：PandaOmron 12 维（arm OSC 6 + gripper 1 + torso 1 + base vx/vy/wyaw 3 +
  base_mode 1）。两类脚本化 driver，均为冻结策略、可被治理：
  - `navigate`：privileged fixture 位姿做目标的速度闭环（base_mode=+1），无路径
    规划（robocasa 没有），直线 + fixture 停靠位；撞不动就是诚实失败面。
  - `arm 阶段 driver`：现行 robosuite 卡的分阶段脚本模式平移（grasp/place/开关门/
    按钮各一个 stage 表）。底座前进轴 master 改过向——**以本 venv 实测为准**。

## 3. 首发 mission — `kitchen_thaw`（MicrowaveThawingFridge）

一个持久 episode（M7 `EpisodeContext`，`episodic: true`），≥14 节点：
survey(perceive) → plan(decide) → nav-fridge(segment) → verify-at →
grasp-item(segment) → verify-grasped → nav-microwave(segment) → verify-at →
place-in(segment) → verify-inside(`obj_inside_of`) → close-door(segment) →
verify-closed → press-start(segment) → verify-on(`turned_on`) → report(decide)。
每个 verify 读活状态；失败 → 回合内 replan（同一世界重试，M7 语义）；horizon
1000（v1.0.1 已放大）。备选升级：MultistepSteaming（自带跨步时序 flag，5 阶段）、
PackFoodByTemp（导航面最大）。

证据纪律照旧：scratch 种子（<542k）冒烟 → 标定块另 alloc → prereg 先于烧块 →
held-out 一次。首发里程碑是**架构 E2E**（一个 episode 里图执行 + 逐节点活状态
verify + 回合内 replan 在 UI 图谱实时可见），不是成功率——成功率是 RSI 后续的活。

## 4. 落地顺序（每步有机械验收）

1. **接线**：robocasa venv 里 `pip install -e $REPO`（底座）+ `robocasa` marker +
   conftest 钩子 + base-gate 快照刷新。验收：venv 里 kernel 可导入，base lane 绿。
2. **卡片**：env/percept provider + doctor 绿（robocasa venv 里 `-m robocasa`）。
3. **驱动**：navigate + 各 arm 阶段 driver，逐阶段独立冒烟（从 reset 驱动到该阶段
   谓词为真）。
4. **mission 卡**：`kitchen_thaw` 图 + PREDICATES + `episodic:true`，scratch 种子
   走 runtime 正路 E2E，runtime_events 全程可见。
5. **路由 + UI**：cockpit 第二 runtime、board `session` 参数三脸、浏览器
   http://172.26.112.106:3081 亲验图谱面板实时执行。
6. XPolicyLab 契约（RoboTwin 先行）——独立后续，等 1-5 稳。

<!-- ponytail: 一个 session 参数 + 一张卡 + 一个 marker，没有插件间 RPC、没有
     跨 venv 序列化层；等第三个 sim 真出现共性再抽象。 -->

## 5. LIBERO（第三 venv，脚手架已就位 2026-08-27）

VLA 论文的比较货币（vlm-graph-paper-plan §3）。骨架：`sims/libero-venv` +
`plugins/embodiment_libero/`（enabled=false，仅 embodiment.env 座）+ `libero`
marker（conftest find_spec 自动跳过，照 robocasa 模式）。

### 装法（已验证的偏离项，不是照抄 upstream requirements.txt）

```bash
cd $REPO/sims
git clone --depth 1 https://github.com/Lifelong-Robot-Learning/LIBERO   # @8f1084e，含 405M 资产/bddl/init_files，无需另下大文件
uv venv -p 3.10 libero-venv        # 3.12 不行：numpy==1.22.4 无 3.11+ 轮子
VIRTUAL_ENV=$PWD/libero-venv uv pip install \
  "numpy==1.22.4" "robosuite==1.4.0" "mujoco==2.3.2" "bddl==1.0.1" \
  "hydra-core==1.2.0" easydict future "matplotlib==3.5.3" \
  "cloudpickle==2.1.0" "gym==0.25.2" opencv-python \
  "torch==2.4.1+cpu" --extra-index-url https://download.pytorch.org/whl/cpu \
  termcolor pynput "pytest-timeout>=2"
VIRTUAL_ENV=$PWD/libero-venv uv pip install -e ./LIBERO   # 只登记 dist-info，见坑 1
echo "$PWD/LIBERO" > libero-venv/lib/python3.10/site-packages/libero_repo.pth
```

跳过未装（纯训练用，env 创建/评测不需要）：wandb、transformers、robomimic、
einops、thop。torch 必须有（`libero.libero.benchmark` 用 torch.load 读
init states），cpu 轮子即可。

### 坑（全部实测踩过）

1. **upstream editable 安装映射为空。** setup.py 用 find_packages()，但仓库顶层
   `libero/` 没有 `__init__.py`，PEP-660 finder 的 MAPPING 是空的，
   `import libero` 直接 ModuleNotFoundError。解法：一行 `.pth` 指向 checkout，
   `libero` 走隐式 namespace package。推论：任何 cwd 里有 `libero/` 目录都会
   遮蔽它——robocasa namespace 坑的同族。
2. **`~/.libero/config.yaml` 是机器级全局单例。** 首次 import 写入绝对路径并永久
   复用——本机上它指向旧的 `Learning_based_model/LIBERO`（openpi 客户端），冒烟
   一度跑在错误资产上；且无 config 时首次 import 会 `input()` 交互提问，无头环境
   直接挂死。解法：预写 `sims/libero-venv/.libero/config.yaml`（五个键指向本
   checkout），运行时 `LIBERO_CONFIG_PATH` 指过去——卡片 make_env 里
   `setdefault(sys.prefix + "/.libero")`，全局文件永不碰（openpi 的还要用）。
3. **mujoco 不钉会解析到 3.x。** robosuite 1.4.0 元数据只写 `mujoco>=2.3`，uv
   解析出 3.12.0（API 早不兼容）。钉 `mujoco==2.3.2`。
4. robosuite 1.4.0 的轮子少声明依赖：termcolor、pynput 要手补；LIBERO 侧
   matplotlib/cloudpickle/gym 同理（envs 包顶层 import 它们）。
5. venv 里跑本仓库 pytest 需要 pytest-timeout（pyproject addopts 带
   `--timeout=300`）。

### 冒烟（EGL 无头，已通过）

```bash
cd $REPO   # 或任一 worktree
MUJOCO_GL=egl PYTHONPATH=. sims/libero-venv/bin/python -c "
from harness.spec import EpisodeSpec
import plugins.embodiment_libero as card
import numpy as np
p = card.provider(); spec = EpisodeSpec(task='libero_pick_bowl', seed=420001)
env = p.make_env(spec); obs = env.reset()
for _ in range(10): obs, r, done, info = env.step(np.random.uniform(-1, 1, 7))
print('ok', obs['agentview_image'].shape)"
sims/libero-venv/bin/python -m pytest tests/test_libero_marker.py -m libero   # 1 passed
```

obs 是 dict：逐物体 `{name}_pos/_quat/_to_robot0_eef_*`、robot0 proprio、
`agentview_image`/`robot0_eye_in_hand_image`（128×128×3）、`object-state`；
动作 7 维（OSC 6 + gripper 1）。谓词面：bddl goal（如
`(On akita_black_bowl_1 plate_1)`）是 terminal oracle 候选——按仓库纪律，
先审计其区分度再当 gate 用（完整卡的活，不在骨架内）。
