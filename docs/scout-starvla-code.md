# StarVLA 代码级深挖 — 可直接抄的机制清单

克隆位置：`/tmp/claude-1000/.../scratchpad/starvla`（下文路径均为仓库相对路径）。对照仓库：`/home/yusenzlabpc/Desktop/ph-vlm-graph`（只读，已扫 `harness/contracts.py`、`plugins/policies/`、`plugins/model_qwen/manifest.toml`、`plugins/task/workload.py`）。

---

## 1. 裸数据 dict 边界（forward / predict_action）

**代码事实**
- 约定写在 `starVLA/model/framework/base_framework.py`（321 行）的 `baseframework` 里，L151-180：
  - `forward(examples: List[dict]) -> dict`，每个 example 至少含 `image: List[PIL.Image]`、`lang: str`、`action: np.ndarray [T, action_dim]`，可选 `state`；返回必含 `"action_loss"`。
  - `predict_action(examples) -> dict`，返回必含 `"normalized_actions": np.ndarray [B, T, action_dim]`。
- **防泄漏手段：零运行时校验，纯约定**。基类不是 ABC，默认实现 raise `NotImplementedError`（注释明说是为了让 pylance/mypy 报警而不阻止 `PreTrainedModel` 实例化，"soft-constraint interface"）。没有任何 schema/shape 检查。
- 而且泄漏实际存在：`QwenOFT.py` L232/L243-245，`predict_action` 内部自己做 `to_pil_preserve` 和按训练 config 的 `resize_images` —— 模型特定预处理就在"裸边界"里面；client 侧（见 §3）又做一次 resize。双侧重复，靠 warn-once 日志兜底，不靠机制。

**移植判断：不适用（我们已经更强）。** 我们的 `harness/contracts.py` 用 `runtime_checkable Protocol` 在 mount 时做结构校验，StarVLA 这套是它的弱化版。唯一值得抄的是**分界原则**："过边界的动作必须是已反归一化的，归一化知识只留在模型一侧"（见 §5/§6）——这是语义约定，落在未来策略卡的 docstring 和 manifest 注释里，不落 contracts.py。

---

## 2. 单文件框架变体 + `__main__` smoke test

**代码事实**（`starVLA/model/framework/VLM4A/QwenOFT.py`，391 行，其中 smoke 段 L336-391）
- 一个文件 = dataclass 默认配置（`QwenOFTDefaultConfig`，每个字段带注释文档）+ 框架类 + `__main__` smoke。
- `merge_framework_config`（`share_tools.py` L186）：dataclass 默认值与 YAML `framework:` 段 merge，YAML 覆盖默认、多余键保留、缺省回落——"Config-as-API"，YAML 极短。
- smoke 入口做的检查（`python QwenOFT.py --config_yaml ...`）：
  1. 从 YAML 真实构建整个模型（含下载 backbone）；
  2. 造假数据：随机 224×224 图 + 假指令 + 随机 `(16,7)` action + 随机 state，batch=2；
  3. 跑 `forward` 断言出 loss、跑 `predict_action` 打印 shape；
  4. **显式回归带 state / 不带 state 两个变体**（L384-389，防可选键破坏兼容）。
- 变体注册：`@FRAMEWORK_REGISTRY.register("QwenOFT")` + `base_framework.py` L60-82 的 `pkgutil` 目录自动扫描导入，加新框架 = 加一个文件，零登记。

**移植判断：抄理念，直接可用。** 我们的 manifest 自注册已等价于它的 registry 扫描（physical-harness phase 3 已做）。要抄的是"**每张策略/模型卡自带假数据 smoke**：一条命令、假观测进、shape 断言出，可选键双态都跑"。**落点**：卡片内一个 `__main__`/`--smoke` 入口 + plugin_doctor 的 Tier-A 检查项（不需要 GPU 的用 fake 权重路径 SKIP，与 model_qwen 卡 "endpoint down = graceful SKIP" 同一先例）。

---

## 3. Benchmark 薄适配器（LIBERO）

**代码事实**（`examples/simBenchmarks/LIBERO/eval_files/model2libero_interface.py`，**191 行**，其中 ~25 行是画图）
适配器（`ModelClient`）只负责六件事，全部是 env 特定逻辑：
1. **handshake 取模型不变量**：连 server 拿 `action_chunk_size` 等 metadata（L44-47）——client 不再读 `dataset_statistics.json`、不知道 `future_action_window_size`、不做反归一化（文件头注释明说这三件事已上移 server）；
2. **resize** 到目标分辨率（L115-128）；
3. **chunk 缓存调度**：`step % action_chunk_size == 0` 才发一次推理，其余步吃缓存（L131-159）；
4. **action ensembling**：`AdaptiveEnsembler`（从 SimplerEnv 目录复用）；
5. **sticky gripper 状态机**（L67-70，LIBERO 不用但保留字段）；
6. **任务切换检测**：`lang` 变了自动 `reset()`（L111-113）；输出拆成 `world_vector/rotation_delta/open_gripper`。
另外 `eval_libero.py`（293 行）里有 env 构建 + `_quat2axisangle` 等纯 LIBERO 数学。全仓 10+ 个 benchmark（Robocasa/Robotwin/SimplerEnv/Behavior/VLA-Arena...）每个都是这样一个 `model2X_interface.py`，import 同一个 client。

**移植判断：改造后用。** 职责清单直接映射到我们 `PolicyFactory.make_driver` 返回的 driver：chunk 缓存、ensembling、sticky gripper、retarget-on-task-change 都是 **driver 层**职责（对应我们 `plugins/policies/drivers.py` 和 workload.py L354-366 的 `enter_segment/retarget` 协议），**绝不进 harness**。**落点**：未来 `plugins/policy_vla_*` 卡内一个 ~200 行 driver 文件，manifest params 里放 `ensemble_horizon/alpha/image_size`（进 plan sha，同 model_qwen 卡先例）。

---

## 4. WebSocket policy server（重点：进程隔离边界）

**代码事实**（`deployment/model_server/tools/`，协议层三个文件共 409 行，零 torch 依赖）
- **序列化** `msgpack_numpy.py`（57 行）：msgpack + ndarray 扩展（`{b"__ndarray__", b"data", b"dtype", b"shape"}`），文件头明确拒绝 pickle（安全）并禁 object dtype。
- **server** `websocket_policy_server.py`（159 行）：连接建立后**第一帧推送 metadata**；之后循环收 `{"type": "ping|infer", "request_id", "payload"}`（无 type 默认 infer、无 payload 视顶层为 payload，容错路由 L89-148）；回 `{"status", "ok", "type": "inference_result", "request_id", "data": {"actions": ndarray[B,T,D]}}` 或 `error`；未捕获异常发 **text frame traceback** 再关连接（client 收到 str 即 raise，L133-134）。带 idle_timeout watchdog（5s 轮询自杀，L55-63）。
- **client** `websocket_policy_client.py`（193 行）：同步阻塞、`_wait_for_server` 300s 重试轮询（server 没起也能先启 client）、**清空 proxy 环境变量**（L97-98，好坑位）、附带一次性 train/test consistency 检查（拿 metadata 里 `training_obs_image_size` 对每帧 shape 警告一次）。
- **server 进程入口** `server_policy.py`（106 行）：`--ckpt_path --port --use_bf16 --config_override KEY=VALUE`（可重复的 OmegaConf dotlist，`base_framework.merge_config_overrides` L27-57 有完整类型/格式校验）。
- **仿真真机共用**：LIBERO/Robocasa/Robotwin/SimplerEnv/Behavior/VLA-Arena 的 interface 和 `examples/realRobots/Franka/eval_files/inference_dual_example.py` **import 同一个 WebsocketClientPolicy**——env 侧差异全在各自薄适配器里。另有 `server_policy_gr00t_zmq.py`（89 行）证明协议可换壳：同一个 `PolicyServerWrapper` 外面套 ZMQ + GR00T 协议适配器即可伺候别家 client。

**移植判断：直接抄，且正是我们策略卡进程隔离的正解。** 论证：
- 我们 sim 卡已因 numpy ABI 走 venv-per-sim（robocasa-adaptation 先例），VLA 策略是第三套依赖（flash-attn/transformers 钉版本）。server-client 把策略依赖整个关进独立进程 + 独立 venv/容器，harness 侧只需 `websockets + msgpack + numpy` 三个轻依赖——**协议层文件里没有一行 import torch**，这是实测事实，不是宣传。
- 裸 numpy dict 过网络恰好匹配我们"数据面透传形状"的既有纪律（ph-station 先例）；msgpack 拒 pickle 也符合密封证据链对不可执行数据的要求。
- handshake metadata（ckpt_path、action_chunk_size、unnorm_key、training_obs_image_size）就是我们 manifest params 的**运行时回声**：mount 时把 handshake metadata 与卡片 params 对账，不一致 fail loud——这把 StarVLA 靠"日志警告"的一致性检查升级成我们的 gate，也符合"验证要对着运行时，不是文件"的教训。
- **落点**：`msgpack_numpy.py` + 两个 websocket 文件原样 vendor（MIT）进新卡 `plugins/policy_vla_remote/`（或 `harness` 侧只放 client 三件套，server 归卡管）；卡的 provider 实现 `PolicyFactory.make_driver` 返回 §3 那种 client-backed driver；server 拉起纳入 cockpit（PH 只在 UI 里测的铁律）。metadata 全量写入 episode 证据链。

---

## 5. Checkpoint 三件套（权重 + config.yaml + dataset_statistics.json)

**代码事实**
- 目录约定：`<run_dir>/checkpoints/steps_N.pt|.safetensors`，`read_mode_config`（`share_tools.py` L357-399）从 ckpt 路径 `parents[1]` 找 `config.yaml` 和 `dataset_statistics.json`，缺任一直接 assert 炸。
- 加载链（`base_framework.from_pretrained` L254-321）：读三件套 → dotlist overrides → 按 config 重建模型 → `strict=True` 加载权重（失败时先打印 missing/unexpected keys 再 raise）→ **`model.norm_stats = norm_stats` 把统计钉在实例上**。
- config 有版本化兼容层：`apply_config_compat`，`CONFIG_VERSION = "0.21"`，幂等地把旧 checkpoint 的 config 归一到当前 schema（`share_tools.py` L402-416 注释）。
- 反归一化不手搓：`policy_norm_processor.py`（400 行）从 config 的 `data_mix` → registry 查 `robot_type` → **重建训练时的 `ComposedModalityTransform`**，用 `dataset_statistics.json` 重构 metadata 后 `set_metadata` 绑定——文件头原话 "there is no second source of truth for normalization math"。多 embodiment 时 `unnorm_key` 选统计条目，server 按 key 缓存 processor（`policy_wrapper.py` L119-125）。

**移植判断：抄三条原则，不抄实现。**（我们没有训练管线，`ComposedModalityTransform` 整套不适用。）
1. "**归一化统计与权重同目录、加载即绑定**"——策略卡 manifest 指向 ckpt 目录时，doctor 检查三件套齐全，缺 = mount fail；
2. "**归一化单一事实源在 server 侧**"——反归一化留在 server 进程里，driver 永远只见物理量；
3. "**config 版本号 + 幂等兼容函数**"，比我们现在依赖 fresh-clone 纪律更能扛旧 checkpoint。
**落点**：1、2 写进策略卡 manifest 注释与 doctor 检查；3 暂缓（YAGNI，等第一个真 ckpt 卡再说）。

---

## 6. Train/test 一致性横幅（顺带发现，值得单独一条）

**代码事实**：同一段"state / img size / img count / img order / normalization 不匹配会**静默降成功率**"的警告在四处重复出现——client 构造时（`websocket_policy_client.py` L24-40）、server 启动时（`server_policy.py` L37-57）、LIBERO 适配器发请求前（`model2libero_interface.py` L139-148）、外加 client 的逐帧 shape 对账（warn-once）。这是他们对"无法机器校验的契约"的工程答案：在每个入口反复喊。

**移植判断：改造后用——我们能做得更硬。** 他们喊是因为没有 mount 时校验点；我们有。把"训练观测契约"（image_size、view 数量与顺序、use_state、unnorm_key）作为策略卡 params 写进 manifest → 进 plan sha → mount 时与 server handshake metadata 对账，不符 = fail，而不是 warning。**落点**：策略卡 manifest + kernel mount 校验处（handshake 对账逻辑 ~15 行，放卡 provider 里，不动 `harness/contracts.py`）。

---

## 汇总表

| 机制 | 判断 | 落点 |
|---|---|---|
| 裸 dict 边界（纯约定，无校验） | 不适用，我们 Protocol 更强；只抄"反归一化不出 server"分界 | 策略卡 docstring |
| 单文件 smoke（假数据 forward+predict，双态） | 直接抄理念 | 卡内 `--smoke` + doctor Tier-A |
| LIBERO 薄适配器（191 行六件事） | 改造后用 | 策略卡 driver 文件，参数入 manifest params |
| WebSocket server-client（409 行协议层，零 torch） | **直接抄（vendor，MIT）；是策略卡免 venv 地狱的正解** | 新 `plugins/policy_vla_remote/` 卡 + cockpit 拉起 |
| checkpoint 三件套 + 单一归一化事实源 | 抄原则 1/2，缓 3 | 卡 manifest + doctor 检查 |
| 一致性横幅 | 改造成 mount 时 handshake 对账 gate | 卡 provider（~15 行） |