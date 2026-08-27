# 子流程：ontology-build

新建或扩建本体实体与关系。**会改动 `ontology/` 下的文件。**

## 前置

先读 `ontology/meta-schema.yaml`。它是校验的判据，
也是唯一能告诉你"哪些实体类型合法、哪些关系端点组合合法"的地方。
凭印象建模会在 validate 阶段被打回。

再读 `knowledge/modeling-guide.md` 确认命名与派生关系规则。

## 步骤

**1. 查重**

在 `build/index.json` 里搜同义概念。半导体术语一词多名很常见
（Die Attach / Die Bond，减薄 / Backgrinding），建重复实体比不建更糟：
图会分裂成两个互不相连的子图，查询时只能命中一半。

**2. 确定归属**

- 前后段共享 → `core`
- 只属前段 → `fab`
- 只属后段 → `ap`

判断标准是概念本身的通用性，不是它当前在哪个流程里出现。
"设备停机"是共享概念，即使你是在写后段厂时才需要它。

**3. 建实体**

必填字段：`id / type / name_zh / name_en / domain / description / provenance`。

`description` 要写清**这个概念是什么、边界在哪**，不要写成名字的同义反复。
一句"塑封孔洞是塑封过程中产生的孔洞"没有信息量；
"环氧树脂填充不完全形成的内部空腔，多发于引脚密集区与 die 边角，
与分层的区别是孔洞在树脂内部而分层在界面"才是可用的。

按类型补充：
- `Process`：`stage`、`parameters`、`metrics`、`materials`、`equipment`
- `Anomaly`：`severity`，并考虑 `refines` 到 core 层
- `Parameter`：`unit`、`typical_range`
- `Route`：`steps`（有序），必须与 `precedes` 关系一致
- `Equipment`：`tags` 记 `batch / single_wafer / single_unit / bottleneck`

**不要**在实体里写 `stage` 又在关系文件里写 `belongs_to`——后者是派生的。

**新增 Process 一律要挂 `equipment`。** 没有对应机台才留空，
并在 description 里说清为什么（例如来料接收本身没有专用设备）。
留空不会报错，所以这条只能靠自觉——`equipment` 字段空着时，
这道工序在产能视角里就是不存在的。

**属于机台的事实不要写进工序。** 批式还是单片式、换型时间、
专机专用、耗材寿命，这些是设备的属性；工序只描述工艺动作。
写串了的症状是同一台机器服务多个工序时，同一句话被复制多份。

**4. 建关系**

只写语义关系：`precedes / may_cause / detected_at / mitigated_by / blocks / refines / analogous_to`。

新增 Process 若插入主流程，必须同时更新对应 Route 的 `steps` 和 `precedes` 边，
否则校验器会报 Route.steps 与 `precedes` 不一致的 warn。

**5. 考虑跨行业类比**

若新概念具备跨行业共性（环境控制、批次管理、瓶颈、材料时效、加速老化、分级分选），
建 `analogous_to` 指向 `ext.*`，并在 `note` 写清共性与可复用点。
这一步很容易被跳过，但它是知识飞轮唯一的结构化载体——
不写，下个行业就要从零重推。

**6. 回头看能力问题清单**

`ontology/competency-questions.yaml` 声明了库能答什么、答不了什么。
新增实体或关系后过一遍:

- 新结构让某条 `out_of_scope` 变得可答了?把它挪进 `in_scope`,
  写清 `requires` 与 `answered_via`。**忘了挪的后果是库有能力但对外仍宣称没有**,
  查询流程会照着旧声明拒答。
- 开了一个新维度(不只是补细节)?加一条 `in_scope`。
  判据是能不能写出一条别人问得出、而且沿图能走通的问句 ——
  写不出说明这批实体还没接进图里。
- 删改结构导致某条 `in_scope` 的依赖没实例了?R016 会报错。
  **不要靠删 CQ 条目消错** —— 那是把"答不了"改成"没承诺过",
  正好是这份清单要防的事。真答不了就挪进 `out_of_scope` 并写明 `missing`。

`requires` 只声明类型和关系,不声明具体实体 ID。粒度到具体 ID 会让清单
跟着每次增删动,又变成一个必须同步维护的地方。

新增或改动 `in_scope` 条目时,两个字段不能省:

- `match_terms`:用户会怎么问。实体名脚本会从本体自动派生,但问法派生不出来,
  漏写的后果是 `ask.py` 报"清单里没有这个问题"(退出码 3)。
  写 3~7 个不同角度的说法,别只写一遍问句里的词。
- `probe`:`{from_type, steps: [{via, dir, to_type}]}`,让路径可机器验证。

写完立刻用脚本验,**不要只看 R016 放行**:

```powershell
python scripts/ask.py "<你想象中用户会怎么问>" --probe
```

R016 只查依赖类型有没有实例,查不了这条路径真不真通。CQ16 就是这样过关的 ——
五个依赖全有实例,而 35 台设备里只有 9 台走得通。`--probe` 打出走通比例:
比例低说明问句和路径配错了,要么收窄问句并加 `caveat`,要么这条根本该进
`out_of_scope`。

**7. 校验**

```powershell
python scripts/validate.py
python scripts/build_index.py
```

validate 报错不要靠改元模型放宽约束来解决。先假设是建模错了。

## 通过 changeset 提交（Agent 自动模式）

自动运行时不直接写 `ontology/`，而是产出 `changesets/pending/YYYYMMDD-<topic>.yaml`，
交由 `apply_changeset.py` 按 `config.yaml` 的审核策略裁决。
格式见 `output-contracts/changeset.md`。
