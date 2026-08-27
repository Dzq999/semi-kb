# 子流程：kb-fill

为已存在的本体异常补写处置知识（Playbook）。**会改动 `kb/` 下的文件。**

## 前置约束

`anomaly_ref` 指向的异常**必须已在本体中存在**。不存在就先走 `ontology-build`。
校验器会以 error 强制这条，绕不过去。

## 一条 kb 实例的字段

```yaml
- id: kb.ap.wire_bond_wip_bubble        # ^kb\.(fab|ap)\.[a-z0-9_]+$
  domain: ap
  title: 后段厂 Wire Bond 站点 WIP 堆积排查
  anomaly_ref: core.anomaly.wip_bubble   # 必须解析到 Anomaly 实体
  detected_at_ref: ap.process.wire_bond  # 可选，检出位置
  severity: high
  symptoms:                              # 可观测现象，不是原因
    - Wire Bond 站前 WIP 数量持续高于基线
  possible_causes:
    - cause_ref: ap.cause.fixture_shortage   # 必须解析到本体实体
      discriminator: 设备状态为 Standby 而非 Down，且换线记录显示等治具
      likelihood: medium
  detection:                             # 怎么确认
    - 拉取该站 E10 状态时间分布，区分 Down 与 Standby
  actions:
    - action_ref: ap.action.rebalance_capacity
      note: 先止血再找根因
  impact:
    - 交期延误风险
  provenance: {...}
```

## 写作要点

**symptoms 是现象，possible_causes 是原因，不要混。**
"设备故障"是原因不是症状；"站前 WIP 高于基线且设备状态为 UnscheduledDown"才是症状。
混淆会让 Playbook 退化成同义反复。

**discriminator 是核心价值。**
列 5 条可能原因谁都会，真正省时间的是"怎么在这 5 条里定位到 1 条"。
每条 discriminator 都要写成一个**可执行的判别动作**及其**预期结果差异**：
不是"检查设备状态"，而是"若设备状态为 Standby 而非 Down，则排除设备故障，转查治具与来料"。

**按排查成本排序。**
可能原因的排列顺序应该是排查者的实际动作顺序：
先做能一眼排除大片可能性的检查（E10 状态分布、上下游 WIP 对比），
再做需要拉数据或做实验的（DOE、切片分析）。
`likelihood` 反映概率，排列顺序反映排查效率，两者不必一致。

**区分"本站问题"与"上游传播"。**
WIP 类异常尤其如此。本站设备正常但上游爆量，处置动作完全不同。
排查路径要显式包含这个分叉。

**警惕伪装成产能问题的质量问题。**
返工流回、Hold 批解除后集中释放，都会表现为 WIP 堆积，
但根因在良率不在产能。加了产能反而放大损失。

## 输出

直接写入 `kb/{fab,ap}/*.yaml`，然后：

```powershell
python scripts/validate.py
python scripts/build_index.py
```

自动模式下改为产出 changeset 的 `kb_cases` 段。
