# 这些是实现之前的设计探索，不是系统的说明书

`docs/design/` 下的五份文档写于 2026-08-19 22:49，**在写第一行实现代码之前**。
实现随后在多个地方偏离了它们。把它们当成「Governor 是怎么工作的」来读会得到错误的模型。

真正的记录在三处：`progress.md`（逐轮实测）、`docs/round*.md`（单轮细节）、`docs/report.html`（总报告）。

## 已知的偏离

| 设计文档写的 | 实际建成的 |
|---|---|
| 四层命名空间 `proprio.` / `onboard.` / `estimated.` / `oracle.` | 两层 `observable.` / `privileged.` |
| `EpisodeSpec(allowed=frozenset(...))` 携带许可集 | 没有 `allowed` 字段；边界是 `FeatureView`，按实际读取记账 |
| `ContractViolation` 异常类型 | `InvariantViolation`（`governor/invariant.py`） |
| tier-2 源声明 `derived_from`，mount 时校验闭包 | 没有派生层；命名空间前缀即声明，构造即拒绝 |

保留它们的理由是**出处**：特权预算这个想法就是在这些文档里长出来的
（它们要解决的是 Zetta README 里那句没有任何东西强制执行的散文规则）。
保留探索过程，但不要把它当成现状。
