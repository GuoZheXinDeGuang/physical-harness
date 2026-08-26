# 开源 VLA 策略卡调研（physical-harness 接入 + RSI 适配，2026-08）

前提复核：本地 `~/Desktop/Learning_based_model/openpi` 已有 checkout，`scripts/serve_policy.py`（websocket+msgpack 服务）与 `examples/libero`（独立 venv 客户端）齐全，且现在多了 `train_pytorch.py` / `convert_jax_model_to_pytorch.py`（JAX→torch 双栈）。已知坑：JAX 必须 `XLA_PYTHON_CLIENT_MEM_FRACTION=0.45`（同机有其他显存占用）；libero 客户端 venv 是老 pin（Python 3.8 + cu113），`PYTHONPATH` 要加 `third_party/libero`——这正是"server-client 进程隔离"必要性的活证据。

---

## 1. π0.5 / openpi（含 pi0-FAST）

- **许可**：Apache-2.0（代码+权重），商用无障碍。
- **4090 推理**：已本地实测跑通。推理约 8GB 级，留 JAX 预分配旗标即可。lerobot 另有 torch 版 `pi05_libero_base` / `pi05_libero_finetuned`，绕开 JAX。
- **Fine-tune**：官方标称 LoRA 约 22.5GB——4090 贴边可行但没余量（关其他占用）；openpi 自带 `train_pytorch.py` 后 torch 路线更可控。全参不可行。
- **动作接口**：连续，flow matching，chunk 50 步（LIBERO 例子客户端按 replan 间隔取前几步执行）；LIBERO 7 维（EE 6 + gripper）；归一化走 per-dataset `norm_stats`（`compute_norm_stats.py` 产出，quantile/z-score 约定要随卡冻结）。pi0-FAST 变体：自回归离散 FAST token，微调显存更低但推理慢一个量级，仓库有 `pi0_fast_libero` checkpoint。
- **LIBERO checkpoint**：官方 `pi05_libero`（Spatial ~98%）。robosuite/RoboCasa 无官方。
- **接入成本**：**最低**。官方 policy server 就是 websocket+msgpack obs→action，与 harness 卡接口是同构的，mount 时 Protocol 校验只需包一层。
- **RSI 适配面**：改进对象成本中等（LoRA 贴边）；但作为冻结基线/对照组是现成的。

## 2. OpenVLA-OFT

- **许可**：仓库 MIT；注意底座是 Llama-2 7B，权重商用受 Llama 2 社区许可传染，(A) 类内部用没事，随 PH 公开发布要单独审。
- **4090 推理**：可行，LIBERO 推理约 16GB。
- **Fine-tune**：官方数字 LoRA(r=32) bs=1 约 25.7GB——**超 24GB**，4090 上要砍输入视角/8bit/gradient checkpointing 才勉强，微调体验差。
- **动作接口**：连续（L1 回归 MLP 头，parallel decoding），chunk 8，双视角+proprio，q01/q99 dataset statistics 归一化。
- **LIBERO checkpoint**：四套件齐全（moojink/openvla-7b-oft-finetuned-libero-*，平均 97.1%）。
- **接入成本**：无官方 server（OFT 仓库没有 deploy 服务），需自己包薄 server；模型加载是标准 HF 栈，包一层不难。
- **RSI 适配面**：LoRA 配方最成熟、文档最细，但 4090 显存贴边是硬伤，候选产生慢。

## 3. GR00T N1.7（N1.5 已被 lerobot 移除；无 "N2"，现行版本号是 N1.7）

- **许可**：N1.7 换成了 NVIDIA Open Model License——**商用可**（带归属和使用限制条款）；N1.5 是研究许可，别用。
- **4090 推理**：3B 模型，可行；NVIDIA 官方 fine-tune 推荐清单里明列 4090。
- **Fine-tune**：默认设置约 25GB（N1.5 时代数字）——4090 上走 LoRA/减 batch 可落地；lerobot 支持 N1.7。
- **动作接口**：flow matching 连续，chunk 16，embodiment tag + modality config 定义观测/动作归一化（约定较重，但结构化好，适合塞进 manifest）。
- **LIBERO checkpoint**：官方有（还有 SimplerEnv、DROID）。
- **接入成本**：低——官方自带 policy server / PolicyClient（ZMQ 序列化），进程隔离形态现成。
- **RSI 适配面**：官方微调脚本+lerobot 双通道，中等成本；许可条款对"改进后再分发"要过一眼。

## 4. SmolVLA

- **许可**：Apache-2.0（lerobot 生态）。
- **4090 推理**：450M，轻松（<4GB 级）。
- **Fine-tune**：**全参都能在单张 24GB 上跑**（官方口径 3090 即可），LoRA 更是余量巨大；一轮微调是小时级不是天级。
- **动作接口**：flow matching 连续，chunk 50，lerobot 标准归一化统计；lerobot async inference 自带 policy server（gRPC）形态。
- **LIBERO checkpoint**：有（`lerobot/smolvla_libero`），但成绩不饱和——对 RSI 这反而是**优点**：round 97 的教训是零失败轴的卡零候选零晋级，SmolVLA 有真实失败分布可供证据门吃。
- **接入成本**：低，纯 torch + lerobot，venv 干净。
- **RSI 适配面**：**最佳**。离线 fine-tune/LoRA 产生候选的边际成本全场最低，一晚能烧出多个候选进 paired gate。

## 5. RDT-1B / RDT2

- **许可**：Apache-2.0（两代都是）。
- **4090**：RDT2-FM 推理和微调都标称 ~16GB 可行；RDT2-VQ LoRA 要 A100 40GB，出局。
- **动作接口**：RDT2 chunk 24×20 维（双臂 EE pos3+rot6+gripper ×2）；RDT-1B diffusion chunk 64。
- **LIBERO checkpoint**：**无**（两代都面向真机双臂/UMI 数据，无仿真 checkpoint）。
- **结论**：与 LIBERO/robosuite 目标错配，双臂形态也和现有单臂技能段不对口。不进候选池，留观察（若未来接双臂真机再回头）。

## 6. MolmoAct2（调研新增，Ai2，2026-05）

- **许可**：Ai2 系列一贯 Apache-2.0（MolmoAct 一代已是；MolmoAct2 卡上未明示，接入前核对 model card 一行字的事）。
- **4090 推理**：4.85B 底座（Molmo2-ER）+ flow matching action expert，bf16 **<16GB**，可行；base 模式单次动作 ~180ms（H100 数字，4090 打折后仍可用）。
- **Fine-tune**：官方称 LIBERO checkpoint "intended for further fine-tuning"，但 LoRA 显存无官方数字，5.5B 在 24GB 上估计 LoRA 可行、全参不行。
- **动作接口**：连续 flow matching（推荐模式，num_steps=10），另有离散 token 调试模式；HF transformers `AutoModelForImageTextToText` 直接加载。
- **LIBERO checkpoint**：`allenai/MolmoAct2-LIBERO`（97.2%，Think 版 98.1%，当前 LIBERO 头部），另有 LeRobot 版。
- **接入成本**：中——无专用 server，但 transformers 单接口，包薄 msgpack server 半天工作量。
- **RSI 适配面**：成绩接近饱和（LIBERO 上失败轴薄），当改进对象性价比低；当 (A) 类高分冻结卡有价值。可搭配 LIBERO-Plus/PRO（扰动轴）恢复失败轴。

## 7. WALL-OSS-0.5（调研新增，X Square Robot，2026-05）

- 4B，宣称开源+零样本泛化，lerobot 已集成（actions_per_chunk=32）。真机向，**无 LIBERO checkpoint**，许可细节需核对 HF 卡。暂不进候选池，观察。

---

## 推荐

| 用途 | 选择 | 一句话理由 |
|---|---|---|
| (A) 冻结技能卡 首选 | **π0.5 / openpi** | 已本地跑通+官方 websocket/msgpack policy server 与 harness 卡接口零改造，LIBERO 官方 checkpoint 98% 级 |
| (A) 备选 | **GR00T N1.7** | 商用许可+自带 policy server+官方 LIBERO checkpoint，3B 显存友好；若嫌 openpi JAX 栈重可整体替换 |
| (B) RSI 改进对象 首选 | **SmolVLA** | 450M 单卡全参小时级微调=候选产生成本全场最低，且 LIBERO 成绩不饱和——有真实失败轴，证据门才有晋级空间（round 97 教训的直接解） |

补充一条：MolmoAct2-LIBERO 值得作为 (A) 的"高分对照卡"挂进来（Protocol 校验+薄 server 半天），它和 π0.5 卡并排能给 RSI 的 held-out 报告提供跨策略参照系。

## 来源

- [openpi (Physical-Intelligence)](https://github.com/Physical-Intelligence/openpi) / [lerobot pi05_libero_base](https://huggingface.co/lerobot/pi05_libero_base) / [lerobot Pi05 docs](https://huggingface.co/docs/lerobot/en/pi05)
- [openvla-oft](https://github.com/moojink/openvla-oft) / [OFT 项目页](https://openvla-oft.github.io/) / [openvla-7b-oft-finetuned-libero-10](https://huggingface.co/moojink/openvla-7b-oft-finetuned-libero-10)
- [Isaac GR00T N1.7 (NVIDIA)](https://github.com/NVIDIA/Isaac-GR00T) / [GR00T N1.7 HF blog](https://huggingface.co/blog/nvidia/gr00t-n1-7) / [lerobot GR00T docs](https://huggingface.co/docs/lerobot/en/groot)
- [SmolVLA docs](https://huggingface.co/docs/lerobot/en/smolvla) / [lerobot/smolvla_libero](https://huggingface.co/lerobot/smolvla_libero)
- [RDT2 (thu-ml)](https://github.com/thu-ml/RDT2) / [RDT2-VQ](https://huggingface.co/robotics-diffusion-transformer/RDT2-VQ)
- [MolmoAct2 blog (Ai2)](https://allenai.org/blog/molmoact2) / [allenai/MolmoAct2-LIBERO](https://huggingface.co/allenai/MolmoAct2-LIBERO) / [MolmoAct2 论文](https://arxiv.org/abs/2605.02881)
- [Wall-OSS-0.5 技术报告](https://arxiv.org/abs/2605.30877) / [x-square-robot/wall-oss-0.5](https://huggingface.co/x-square-robot/wall-oss-0.5) / [lerobot WALL-OSS docs](https://huggingface.co/docs/lerobot/walloss)
- [LIBERO-plus](https://github.com/sylvestf/LIBERO-plus)