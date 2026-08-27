# 建模规范

## 主流程 + 风险图谱

这是跨行业复用的核心框架，本体的骨架由两部分构成：

**主流程（Flow）** —— 正向生产链路，用 `precedes` 关系连成有向无环图。
它回答"东西是怎么被做出来的"。Route 实体的 `steps` 列表是主流程的显式表达，
`precedes` 关系是它的图表达，两者必须一致（校验器会交叉比对，不一致报 warn）。

**风险图谱（Risk Graph）** —— 异常事件集合及其因果、检出、处置关系：

```
Cause ──may_cause──> Anomaly ──detected_at──> Process/Metric
                        │
                        ├──mitigated_by──> Action
                        ├──may_cause────> Anomaly     （异常级联）
                        └──blocks──────> Route/Process （阻断产出）
```

只有主流程没有风险图谱，本体就只是一张工艺流程图，无法支撑排查；
只有风险图谱没有主流程，异常就失去了发生位置的上下文，无法判断影响范围与传播方向。

## ID 命名

```
^(core|fab|ap|ext)\.[a-z_]+\.[a-z0-9_]+$
   域         类型slug      具体名
```

中段的 type_slug 必须与 `type` 字段对应（`Process` → `process`，`RiskNode` → `risknode`），
域前缀必须与 `domain` 字段一致。两者都是 error 级校验，不一致直接拒。

`ext.*` 是外部行业占位 ID，只作为 `analogous_to` 的目标，不需要本地定义。

## 派生关系不要手写

以下关系由 `common.py` 从实体字段自动派生，**写在关系文件里就是重复，会导致两处漂移**：

| 实体字段 | 派生关系 |
|---|---|
| `stage` | entity `belongs_to` stage |
| `parameters` | parameter `controls` entity |
| `metrics` | entity `measured_by` metric |
| `materials` | entity `performed_on` material |
| `equipment` | equipment `belongs_to` entity |

关系文件里只写 `precedes`、`may_cause`、`detected_at`、`mitigated_by`、
`blocks`、`refines`、`analogous_to` 这些**无法从字段推出**的语义关系。

## provenance 是硬性要求

每个实体、每条知识库实例都必须有。这不是形式主义：下周起 Agent 会自动改本体，
没有 provenance 就无法区分"专家确认的结论"和"模型三个月前的猜测"，
更无法在发现错误时定位受影响范围。

```yaml
provenance:
  source_type: model_prior      # model_prior | web | analogy | human
  ref: null                     # web 来源必填 URL
  confidence: high              # high | medium | low
  created_at: "2026-08-26"
  reviewed_by: null             # 人工审核后填姓名
```

文件级可写 `default_provenance` 减少重复，实体级字段覆盖文件级。

## economic_hooks 现在是空字段

预留给第三层经营模型，Week 1 不填也不用填。白名单键：
`yield / cycle_time / equipment_oee / throughput / cost_per_unit / rework_rate / scrap_rate / capacity_commit`。
写白名单外的键会被校验器拒绝（error）——保持这个字段干净，等经营模型层来定义它的语义，
比现在随手填数字更有价值。

## 常见错误

**关系端点类型不合法** —— `blocks` 的 to 端只接受 Process/Route。
遇到报错先想语义：Hold 阻断的是**路线**（lot 走不下去），不是**队列状态**。
报错常常暴露的是建模错误，不是校验太严。改元模型放宽约束是最后手段。

**知识库引用了不存在的本体实体** —— 正确顺序是先建本体实体，
再写知识库实例。反过来会被拒。

**新增异常没有 refines 父节点** —— fab/ap 层的异常应尽量 `refines` 到
core 层的通用状态（如 `core.state.equipment_down`、`core.state.quality_excursion`），
这样跨域查询才能沿骨架聚合。找不到合适的父节点时，先在 core 层建泛化概念。

**孤立节点（warn）** —— 既无入边也无出边的实体是可疑的。
要么它还没接进图，要么它本来就不该存在。
