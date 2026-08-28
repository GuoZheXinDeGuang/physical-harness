# AnyGrasp 集成侦察 — 后续 rung(PickPlace 多样物体)的施工输入

(2026-08-22 侦察。anygrasp 进场位置: 脚本抓取最弱的多样物体处, 非 R2 Stack 关键路径。)

## 运行形态(不可绕过)

- 只能容器内跑: `anygrasp:cu128-py311`(torch 2.8/CUDA 12.8 + 源码编译 MinkowskiEngine/
  pointnet2), **不要尝试在 py3.12 venv 里 import gsnet**。
- license 绑定机器指纹(feature_id N32629936297910908604, `make feature-id` 可验) +
  sm_89 GPU(4090); `create_detector` 校验不过返回 None。服务端必须留在本机。

## 直接复用(零新轮子)

1. **服务端**: `Z-Manipulation-Stack/docker/anygrasp_runtime/server.py` —— msgpack-over-ZMQ
   REP :5557, 协议 z-manip.grasp.v1(health/metadata/infer), 服务端已做 GraspNet 轴系→TCP
   转换, 返回 (N,4,4) 位姿+分数+宽度。基于 anygrasp 镜像, bind-mount ~/anygrasp 的
   license+checkpoint。
2. **客户端**: vendor `z_manip/inference/grasp_client.py` 单文件进 harness ——
   硬依赖只有 numpy(pyzmq/msgpack 懒加载), 带 fail-closed 位姿校验(正交/det+1/齐次行),
   transport 可注入(无 ZMQ 可单测)。

## harness 侧新建(两件, 契约零改动)

1. env 工厂变体(仿 sawyer_provider 模式): `use_camera_obs=True, has_offscreen_renderer=True,
   camera_depths=True, camera_names=["agentview"]`。**不动 _default_make_env**(parity 钉死)。
   相机 obs 只是往 obs dict 加键, EnvProvider/PolicyDriver Protocol 不变。
2. `plugins/embodiment_robosuite/grasp.py`(~30 行): obs+env.sim → 相机系点云
   (robosuite.utils.camera_utils: get_real_depth_map 必须过——robosuite 深度是归一化[0,1]
   不是米; get_camera_intrinsic_matrix 反投影; get_camera_extrinsic_matrix 升到世界系),
   scene_bounds = min/max±margin, 调 vendored client。

## 风险(排序)

1. license/GPU 绑定: 换机器/换卡即全灭; 4090 本机 OK, 但要先 `make verify` 确认。
2. **静默垃圾**: robosuite 深度不过 get_real_depth_map、或 MuJoCo 相机系(-z 朝前,+y 上)
   →OpenCV 光学系(x右,y下,z前)转换错, SDK 照样返回"合理"位姿但几何全错。
   实施时先拿已知方块位姿做对照验证再信分数。
3. 推理非确定性(CUDA): 同种子重放 bit-parity 会破——推理输出内容寻址落盘做记录-重放。

## SDK 速查

`detector.get_grasp(points_(N,3)f32_米_相机光学系, {dense_grasp, collision_detection,
region_steering, approach_steering, approach_thresh})` → GraspGroup(score/width/depth/
rotation_matrix/translation); 抓取系 X=approach Y=开合; tip = translation + depth*R[:,0];
checkpoint_detection(296M)=单帧检测(唯一接线的), checkpoint_tracking 未被任何东西使用。
