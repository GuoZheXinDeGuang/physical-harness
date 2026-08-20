# Round 8：一次每步都合理、终点却更差的 campaign

2026-08-20。`search_recovery=True`，其余 preregistration 与 round 5 完全一致
（dev 2000-2119，held-out 1000-1199，零特权，recovery 感知 sd=0.020）。509 秒。

## 过程

```
gen 1
  recovery 搜索: 20 evals, 子集 75.0%（手写程序 70.0%）
  recovery 门禁 vs 手写: +5.8%  fixed=8 broken=1  p=0.039  -> 采纳
  候选: gripper_effort > 0.0509 dwell2 @59
  dev 门禁 vs 父代: +18.3%  fixed=22 broken=0  p<1e-5  -> 提升

gen 2
  recovery 搜索: 无改进，收敛
  候选: finger_gap < 0.0105 dwell1 @90
  dev 门禁 vs 父代: +2.5%  fixed=3 broken=0  p=0.250  -> 拒绝
```

held-out（n=200，只评一次）：**48.5% → 71.5%，+23.0pp，p<1e-5，零特权，0 破坏**

| recovery 感知 sd | 受治理 | 提升 | 特权 |
|---|---|---|---|
| 0.000 真值 | 92.0% | +43.5% | 1 |
| 0.010 | 89.0% | +40.5% | 0 |
| 0.020 | 71.5% | +23.0% | 0 |
| 0.030 | 62.0% | +13.5% | 0 |

## 发现一：贪心地每代最优，收敛更早、终点更差

同一套 preregistration，唯一区别是开不开 recovery 搜索：

| | 规则数 | held-out 提升 |
|---|---|---|
| Round 5（不搜 recovery） | 2 | **+27.5pp** |
| Round 8（搜 recovery） | 1 | +23.0pp |

**每一步都是局部正确的。** gen1 的 recovery 通过了它的门禁，合并候选 +18.3% p<1e-5 通过，
gen2 确实没有能过门禁的候选。没有任何一步作弊。

但更强的 gen1 把 dev 从 54.2% 直接推到 72.5%，剩下的失败**更少也更难**，
于是第二条规则再也够不着显著性线，campaign 提前收敛。
round 5 那条「每条更弱但有两条」的链，泛化反而更好。

**这是贪心 + 显著性门禁的结构性后果，不是 bug。** Zetta 论文里
"success continues to scale with self-exploration experience" 这句话，
在这个实验里的对应现象是：**单调上升是真的，终点高低和每一步多贪心有关。**

我没有对这两个数字做配对检验 —— held-out 对每个 campaign 都已经用掉了它唯一的一次评分机会，
再拿它做新的比较就是把测试集变成训练信号。这里只做描述性对比。

## 发现二：recovery 门禁有一半是样本内的（已修）

`_maybe_search_recovery` 在 `dev[:60]` 上搜 recovery，却在**全部 120 个 dev** 上过门禁 ——
其中 60 个正是它搜过的。

这个缺陷是靠**跨轮对照**发现的，不是靠读代码：

| 测量 | 切分 | 结果 |
|---|---|---|
| Round 6 | 干净 held-out n=200 | +4.0pp, **p=0.096 拒绝** |
| Round 8 | 一半样本内的 dev n=120 | +5.8%, **p=0.039 采纳** |

同一个改动、同一个方向、量级接近，但一个过一个不过。差别就在切分。

已修：recovery 门禁改用 `dev[recovery_search_n:]`，与搜索种子不相交；
剩余门禁种子少于 20 个直接报错而不是静默降级。
搜索种子和门禁种子都写进产物，事后可查。
