# semi-kb

半导体**前段厂（Fab）**与**后段厂（AP/封测）**的本体驱动知识库，以 skill 形式交付。

这是 PXAI 四层推演链路（本体 → 知识库 → 经营模型 → 仿真引擎）在半导体行业的落地。
当前四层已形成可执行闭环：前两层是领域资产，第三层使用通用经营模板与数据集，第四层使用算法注册表运行确定性干预场景。自动刷新在更新本体/知识库并完成全链校验后，还会扫描 `economic_hooks`，生成只要求补充数据的经营影响候选提案；不会擅自编造经营参数。

本目录既是 skill 本身，也是它管理的数据：`SKILL.md`/`prompts/`/`knowledge/`/`output-contracts/`
是行为定义，`ontology/`/`kb/`/`build/` 是数据。全库文档中出现的路径都以本目录为基准。

## 核心约束：本体是唯一真源

三层数据的关系不可颠倒：

```
ontology/   本体 —— 唯一真源，人与 Agent 共同维护
    ↓  实例填充；所有 *_ref 必须指向已存在的本体实体，且不跨域（R014/R015 强制）
kb/         知识库 —— 异常处置 Playbook
    ↓  脚本生成
build/      检索索引与图 —— 派生产物，git 忽略，禁止手工编辑
```

知识库**不得凭空引入概念**。要写一条涉及新异常的处置知识，必须先在本体中建该异常实体。
`build/` 与本体不一致时重跑构建即可，不需要人工对账。

## 快速开始

```powershell
cd D:\coding\semi-kb
python scripts/ask.py "颗粒突增可能是什么原因"   # 这个问题库能不能答
python scripts/validate.py          # 校验一致性，有 error 时退出码非零
python scripts/build_index.py       # 重建 build/index.json 与 build/graph.json
```

改本体或知识库后必须先 `validate.py`，通过后再 `build_index.py`。
顺序不可颠倒——本体有错时重建索引只是把错误固化进派生产物。

依赖：Python 3.10+、PyYAML。`build/` 不在版本库里，首次克隆后跑一次 `build_index.py` 即可生成。

## 第三、四层通用闭环

经营层按职责拆分：`business/templates/` 是制造业、半导体、Fab、AP 的可继承结构；`business/models/` 选择领域模板；`business/datasets/` 保存带单位、来源与可选 `ontology_ref` 的输入；`business/changesets/pending/` 保存自动生成、等待人工补数与审核的经营影响候选。复杂计算由 `scripts/simulation_algorithms.py` 的算法注册表实现，YAML 只声明算法名、输入和场景参数；受限公式仍不允许函数调用或任意 Python。

仿真场景在 `simulation/scenarios/*.yaml`，只描述对通用模型输入执行的 `set` / `multiply` / `add` 干预，不复制经营公式。结果写到 `simulation/runs/`，包含 baseline、intervention、delta、回收期、证据等级和本体链路。

```bash
python3 scripts/simulate.py --check
python3 scripts/simulate.py simulation/scenarios/reduce-fixture-clog.yaml
python3 scripts/simulate.py simulation/scenarios/reduce-fixture-clog.yaml \
  --output simulation/runs/reduce-fixture-clog.yaml
```

当前算法注册表包含良率级联、瓶颈产能、OEE 产能、可售产出、收入、变动成本、单位成本、ROI 与回收期。植球治具堵塞只是 AP 模型的一个场景插件；它修改 `ball_placement_yield` 与 `intervention_opex`，并不拥有一套专用经营模型。示例输入均为 `assumption` 或 `model_prior`，输出标记为 `assumption_only`，只代表情景推演。

### 自动刷新后的经营影响提案

`daily_refresh.py` 在本体/知识库合并、全链校验通过后运行 `scripts/economic_impact.py`：

```text
新增或变化的本体实体
    ↓ 读取 economic_hooks.affects
匹配现有 business/models/
    ↓
生成 business/changesets/pending/YYYYMMDD-economic-impact-candidates.yaml
    ↓
人工补充现场数据并审核
    ↓
再写入 business/datasets/ 或 simulation/scenarios/
```

该阶段只生成 `required_data` 和受影响模型候选，状态固定为 `needs_human_input`；不会自动写良率降幅、成本、停机时间等数值。已有真实数据时更新数据集后即可重跑仿真；没有数据时只能保留为候选，不得称为预测或经营承诺。

## 六个子流程

由 `SKILL.md` 按用户意图路由，提示词在 `prompts/` 下：

| 子流程 | 用途 | 是否写文件 |
|---|---|---|
| `kb-query` | 问工艺、问异常怎么查、问某站点问题 | 否，只读 |
| `ontology-build` | 新建或扩建本体实体/关系 | 是 |
| `kb-fill` | 为已有异常补写处置知识 | 是 |
| `kb-refresh` | 找资料、更新本体、产出变更提案 | 只写 `changesets/pending/` |
| `kb-validate` | 校验一致性、重建索引 | 只写 `build/` |
| `simulation-run` | 校验经营模型、运行干预场景、比较经营结果 | 可写 `simulation/runs/` |

意图不明时默认 `kb-query`——回答问题不改动任何文件，是零风险选项。

## 目录结构

```
semi-kb/
├── SKILL.md                       主控：意图路由规则 + 本体唯一真源约束 + 三条命令
├── config.yaml                    全局配置：审核策略、guards 阈值、知识来源开关
├── .gitignore                     忽略 build/ 与 Python 缓存
├── README.md                      本文件：面向人的完整说明
│
├── prompts/                       五个子流程提示词，由 SKILL.md 按意图路由
│   ├── kb-query.md                查询：定位入口 → 沿图扩展 → 按排查成本组织答案（只读）
│   ├── ontology-build.md          建本体：去重 → 定域 → 建实体 → 建语义关系 → 考虑类比
│   ├── kb-fill.md                 填知识库：为已存在的异常补处置 Playbook
│   ├── kb-refresh.md              自动更新：选题 → 找资料 → 产出变更提案（定时任务入口）
│   └── kb-validate.md             校验与重建索引，含故障注入自检方法
│
├── knowledge/                     领域与规范知识，供各子流程引用
│   ├── modeling-guide.md          建模规范：ID 命名、派生关系、provenance、常见错误
│   ├── domain-glossary.md         领域术语中英对照：通用产能与流动 / 前段 / 后段
│   └── project-notes.md           项目背景、设计取舍、建库时的判断、已知缺口
│
├── output-contracts/              输出格式契约，约束子流程的产出形态
│   ├── query-answer.md            问答输出结构：结论 → 原因 → 排查路径 → 溯源
│   └── changeset.md               变更提案 YAML 格式与硬性要求
│
├── ontology/                      ← 唯一真源，人与 Agent 共同维护
│   ├── meta-schema.yaml           元模型：实体类型、关系类型、字段契约、校验规则
│   ├── competency-questions.yaml  能力问题清单：库承诺能答什么、明确答不了什么
│   ├── core/                      前后段共享概念
│   │   ├── entities/              Lot、WIP、Hold、Rework、Scrap、良率、CT、OEE
│   │   └── relations/             core 层内部关系与骨架
│   ├── fab/                       前段厂
│   │   ├── entities/              制程段、工序、参数、指标、异常、根因动作、路径、设备
│   │   └── relations/             主流程 precedes + 风险图谱
│   └── ap/                        后段厂
│       ├── entities/              同上，覆盖晶圆前处理到测试
│       └── relations/             主流程 precedes + 风险图谱
│
├── kb/                            知识库：异常处置 Playbook 实例
│   ├── fab/                       前段案例（光刻蚀刻、缺陷与设备）
│   └── ap/                        后段案例（键合 WIP、封装与测试）
│
├── business/                      经营模型声明层
│   ├── templates/                 通用制造业 → 半导体 → Fab/AP 可继承模板
│   ├── models/                    具体领域经营模型（当前含 ap-baseline.yaml）
│   ├── datasets/                  带单位、来源、时间范围的输入数据集
│   ├── changesets/pending/        自动发现的经营影响候选，待人工补数/审核
│   └── ap/                        旧版植球模型兼容区，逐步迁移，不作为新模型主干
│
├── simulation/                    仿真层
│   ├── scenarios/                 具体干预场景，只改模型输入
│   └── runs/                      仿真运行结果，派生文件
│
├── scripts/                       必须留在根下一级，见下方说明
│   ├── common.py                  共享加载器：读本体/kb、合并 provenance、展开派生关系
│   ├── ask.py                     问题 -> 能不能答 + 沿哪条路径答，退出码即判定
│   ├── validate.py                逐条实现元模型的校验规则，有 error 时退出码非零
│   ├── build_index.py             生成 build/index.json 与 build/graph.json
│   ├── apply_changeset.py         变更提案裁决、依赖连锁转人工、原子合并与回滚
│   ├── simulate.py                通用模型加载、算法执行、场景对比与结果输出
│   ├── simulation_algorithms.py   良率、产能、成本、ROI 等算法注册表
│   ├── economic_impact.py         从 economic_hooks 生成待补数据的经营影响候选
│   └── simulate_check.py          全库经营模型与场景校验
│
├── changesets/                    变更提案流转，把"生成"与"落库"分开
│   ├── pending/                   待裁决的提案，kb-refresh 的产出落在这里
│   ├── applied/                   已合并归档
│   └── rejected/                  已否决归档
│
└── build/                         派生产物，git 忽略，禁止手工编辑
    ├── index.json                 扁平检索记录（实体 + kb 实例），关键词入口
    └── graph.json                 邻接表 + 主流程 routes，用于沿图扩展
```

`scripts/` 必须留在项目根下一级——`common.py` 用 `ROOT = Path(__file__).parent.parent`
定位项目根，挪动这一层会让所有脚本找错本体目录。

### 本体文件编号

文件名前缀数字决定加载顺序，也划分了内容归属，新增文件请沿用号段：

| 号段 | 归属 |
|---|---|
| `01-0x` | core 实体（物料、状态、指标） |
| `10-1x` | fab 实体（制程段、工序、参数、指标、异常、根因动作、路径） |
| `20-2x` | fab 关系（主流程 precedes、风险图谱） |
| `30-3x` | ap 实体 |
| `40-4x` | ap 关系 |

## 本体结构：主流程 + 风险图谱

跨行业复用的核心框架，两部分缺一不可。

**主流程**是正向生产链路，用 `precedes` 连成有向无环图，Route 实体的 `steps` 是其显式表达。
**风险图谱**是异常事件集及其因果、检出、处置关系：

```
Cause ──may_cause──> Anomaly ──detected_at──> Process/Metric
                        ├──mitigated_by──> Action
                        ├──may_cause────> Anomaly     （异常级联）
                        └──blocks──────> Route/Process
```

只有主流程，本体就只是工艺流程图，无法支撑排查；
只有风险图谱，异常就失去发生位置的上下文，无法判断影响范围与传播方向。

### 能力问题清单是边界声明

`ontology/competency-questions.yaml` 写明库承诺能答哪些问题、明确答不了哪些。
这不是文档,是校验对象:每条 `in_scope` 都要声明 `requires`(依赖哪些实体类型与
关系类型),R016 检查这些依赖是否真的存在且有实例。

为什么需要它:没有这份清单时,边界只存在于建库者的判断里。
问一个超出范围的问题,检索返回空或不相关,用的人分不清"库里没有"和"这事不成立"。
缺口清单能说明哪里不够细,说不了哪类问题根本答不了。

`out_of_scope` 同样重要 —— 查询流程命中它时直接回"超出范围"并说明缺什么,
**不用模型知识硬编答案**。库外知识伪装成库内知识,溯源链断掉而且没人知道断了,
比答不出来坏得多。

R016 挡的是一类静默失效:结构被删或改名,而清单还承诺着。典型形态是某个
实体类型声明了却零实例、依赖的派生规则从未触发 —— 校验不报,读元模型的人
以为这个视角已经建好。现在这种分离会报错。

**清单靠 `scripts/ask.py` 查,不靠肉眼读。** 给一个问题,脚本给判定:

```powershell
python scripts/ask.py "扩散炉停机会引发什么异常"
# 判定：超出当前本体范围（OOS11，30 分）→ 退出码 2
```

退出码 `0` 可答 / `2` 明确超范围 / `3` 清单未覆盖。三层逐层收紧:
`match_terms`(手写意图词)+ 实体名(从本体自动派生,顺带定出探针锚点)做匹配;
清单声明给判定;`--probe` 时真的沿 `probe` 定义的边走一遍看空不空。

第三层挡的是 R016 挡不住的东西。R016 只看依赖类型有没有实例,查不了遍历路径
真不真通:依赖全有实例、R016 放行,而具体对象上的路径可能只有一部分走得通 ——
例如 `detected_at` 是检出点,只覆盖量测与测试工序,设备维度的问题就只在这些
工序上通。`--probe` 会把这个比例直接打出来。清单未覆盖(`3`)时,输出里的
"分数不足的候选"若明显对得上,补 `match_terms` 而不是新增条目。

### 领域划分

- **core** 前后段共享：Lot、WIP、Hold、Rework、Scrap、良率、CT、OEE、洁净室环境、瓶颈站点
- **fab** 前段厂：扩散/热制程、光刻、蚀刻、薄膜、离子注入、CMP、量测、WAT/CP
- **ap** 后段厂：晶圆前处理、装片、互连（金线/倒装）、塑封、成型、测试

两域通过 `refines` 归入 core 骨架，通过 `fab.process.cp_test precedes ap.process.wafer_receive` 衔接。

### 12 种实体类型

`Process` 工序 / `Stage` 制程段 / `Equipment` 机台 / `Material` 物料 / `Parameter` 可调参数 /
`Metric` 量测指标 / `State` 批次状态 / `Anomaly` 异常模式 / `Cause` 根因 / `Action` 处置动作 /
`Route` 流程路径 / `RiskNode` 风险节点

ID 规范 `^(core|fab|ap|ext)\.[a-z_]+\.[a-z0-9_]+$`，格式 `<domain>.<type_slug>.<name_slug>`，
例如 `fab.process.photo_exposure`、`ap.anomaly.wire_nsop`、`core.metric.wip`。
`type_slug` 必须与 `type` 字段对应（R002），`domain` 必须与 ID 前缀一致（R007）。

### 派生关系不手写

`belongs_to`/`controls`/`measured_by`/`performed_on` 由 `common.py` 从实体的
`stage`/`parameters`/`metrics`/`materials`/`equipment` 字段自动派生。
写在关系文件里会造成同一事实两处维护、必然漂移。

关系文件里只写语义关系：`precedes`、`may_cause`、`detected_at`、`mitigated_by`、
`blocks`、`refines`、`analogous_to`。

## 知识库实例契约

每条 kb 实例是一份异常处置 Playbook，ID 形如 `kb.fab.*` / `kb.ap.*`。
必填 `id`/`domain`/`title`/`anomaly_ref`/`symptoms`/`possible_causes`/`detection`/`actions`/`impact`/`provenance`。

三个核心子结构：

| 字段 | 内容 |
|---|---|
| `possible_causes[]` | `cause_ref` 指向本体 Cause/Parameter/Process，`likelihood` 经验先验，`discriminator` **如何与其他根因区分** |
| `detection[]` | `metric_ref` 指向 Metric，`signal` 该指标上的具体信号形态 |
| `actions[]` | `action_ref` 指向 Action，`condition` 适用前提（含不适用情形），`order` 优先级 |

`discriminator` 是知识库最有价值的字段——它必须是一个可执行的判断动作加预期结果差异，
不是"检查一下键合参数"这种无法据此行动的描述。

## 溯源：每条知识都必须标来源

本项目**无内部数据接口**，知识只来自四类来源，`provenance.source_type` 如实标注：

| 取值 | 含义 |
|---|---|
| `model_prior` | 模型预训练的半导体制造知识 |
| `web` | 公网公开资料，**必须**填 `ref` (URL)，否则 R006 报错 |
| `analogy` | 跨行业类比推演 |
| `human` | 人工录入 |

写法是**文件级默认 + 条目级覆盖**：文件头写一个 `default_provenance`，
`common.py` 的 `_merge_provenance` 在加载时逐字段合并。要给单条标不同来源，
在该条目下写 `provenance:` 只覆盖需要改的字段即可。

Agent 将自动改本体。没有 provenance 就无法区分"专家确认的结论"和"模型三个月前的猜测"，
也无法在发现错误时定位受影响范围。`confidence` 必须诚实——低可信度占比超 40% 时
`validate.py` 发质量预警（R012），让知识质量可观测，而不是让它悄悄劣化。

## 跨行业类比：知识飞轮的载体

`analogous_to` 记录本行业概念与其他行业本体节点的映射，目标用 `ext.*` 前缀
（豁免本地解析校验）。这不是装饰字段——它让本次沉淀的映射能被第 7 个行业直接读取，
而不必重新推演，冷启动成本随行业数递减靠的就是这个结构化沉淀。

已建的映射例如 `fab.stage.diffusion → ext.stage.thermal_process`
（复用玻璃制造的热制程框架）、洁净室环境控制 → 温室环境控制、
Lot 批次管理 → 畜牧批次管理、Scrap → 淘汰、Rework → 复治。
完整清单从关系文件里筛 `type: analogous_to`。

新建实体时若发现明显的跨行业共性（环境控制、批次管理、瓶颈、时效性材料、加速老化、分级分选），
应主动建 `analogous_to` 并在 `note` 里写清共性是什么、什么可复用。

## 校验规则 R001~R016

`ontology/meta-schema.yaml` 的 `validation_rules` 段是唯一真源，`validate.py` 逐条实现。
下表是给人看的速查，**以元模型为准**——两者不一致时改这张表，不要改元模型去迁就它。

Agent 侧的提示词刻意不引用规则编号：`validate.py` 输出自带编号与描述，
按输出内容处理即可。副本越少，漂移的机会越少。

error 必须清零，warn 需逐条看过再决定是否接受。

| 规则 | 内容 | 级别 |
|---|---|---|
| R001 | ID 全局唯一 | error |
| R002 | ID 符合 pattern，`type_slug` 与 `type` 一致 | error |
| R003 | 必填字段齐全，无未知字段 | error |
| R004 | 所有引用指向已存在实体；`ext.*` 豁免 | error |
| R005 | 关系类型合法，两端实体 type 满足 `from`/`to` 约束 | error |
| R006 | provenance 完整合法；`source_type=web` 时 `ref` 非空 | error |
| R007 | `domain` 字段与 ID 前缀一致 | error |
| R008 | 无孤立实体（不参与任何关系，也未被任何字段引用） | warn |
| R009 | `precedes` 不构成环 | error |
| R010 | `Route.steps` 相邻工序应存在 `precedes` | warn |
| R011 | `economic_hooks.affects` 取值在允许集合内 | error |
| R012 | `confidence=low` 占比超 40% 时预警 | warn |
| R013 | kb 实例 ID 唯一、符合 kb_schema、字段齐全、provenance 合法 | error |
| R014 | kb 实例的所有 `*_ref` 指向已存在本体实体且类型匹配 | error |
| R015 | kb 实例的 `*_ref` 域自洽：只能引用自身域或 `core.*`，禁止 fab↔ap 互引 | error |
| R016 | 能力问题清单结构合法，`in_scope` 依赖的类型与关系均已声明且有实例 | error |

R005 报错通常暴露的是建模错误，不是校验太严。改建模，不要放宽 meta-schema 去迁就。

## 自动更新怎么接入

已经就位，只需打开开关。

**流程**：定时任务调用 `prompts/kb-refresh.md` → 选题（优先补孤立节点、无 kb 实例的异常、
主流程断点）→ 找资料 → 产出 `changesets/pending/YYYYMMDD-<topic>.yaml`
→ `apply_changeset.py` 按策略裁决 → 合并后重建索引。

**为什么走提案而不直接写本体**：自动改本体的风险不在改错一条，而在错误被后续内容依赖。
changeset 把"生成"与"落库"分开，让审核策略可配置，也让每次变更留下可回溯记录。

**谁来执行**：选题、取材、建模、写 changeset、自审、合并、验证全部由**当前 Agent
顺序完成**，不启动 SubAgent / 子代理，也没有"另一个代理独立复审"这一环。
需要复核时由当前 Agent 按子流程的检查清单重新读文件再判一次。
确定性检查归脚本——`precheck.py` 查提案外形与提案内依赖，`validate.py` 查合并后的
全库一致性；剩下需要判断的部分自审，verdict 的 `verifier` 如实标 `self:<子流程名>`。
自审的代价是同一段上下文里边写边审容易盖章通过，缓解办法是把能判定的规则持续
往 `precheck.py` 里沉淀，而不是增加代理数量。

`apply_changeset.py` 的开关：

```powershell
python scripts/apply_changeset.py --dry-run        # 只裁决不写入
python scripts/apply_changeset.py --no-review      # 整体覆盖为全自动合并
python scripts/apply_changeset.py --force-review   # 整体转人工
python scripts/apply_changeset.py --file <path>    # 只处理指定提案
```

合并是原子的：备份 → 追加 → 立即校验 → 失败自动回滚。
追加走文本级操作以保留 YAML 注释。

**审核策略**在 `config.yaml`：

```yaml
review:
  mode: manual              # 改为 auto 才允许非 instances 类自动合并
  auto_apply:
    instances: true         # kb 实例：自动合并
    entities: false         # 本体实体：默认转人工
    relations: false
    schema: false           # 元模型：建议永远人工
  guards:
    min_confidence: medium
    max_auto_entities: 30   # 防止一次跑飞污染大面积本体
    require_ref_for_web: true
```

分级依据是**回退成本**：kb 实例是叶子节点，改错了删掉就行；本体实体会被 kb 引用、
被 index 收录，清理成本高得多；元模型是判据本身，动它影响全库。

提案内部有依赖时**自动连锁转人工**：若新增 kb_case 引用了同一提案里待审核的实体，
该 kb_case 也一并转人工。这是设计行为，避免依赖"合并失败再回滚"来兜底。

**建议节奏**：先跑一两周 `mode: manual`，人工审核积累对提案质量的判断，
确认误报率可接受后再逐项放开 `auto_apply`。

`changesets/rejected/20260826-euv-litho.yaml` 是一份可参照格式的示例提案
（其 web URL 为占位，正式运行须填真实可验证链接）。

## 当前规模

本文档不记录具体数量。规模随每次更新变化，抄一份到文档里就多一个维护点，
而漂了不会有任何报错。

要看当前规模就跑：

```powershell
python scripts/validate.py      # 实体/显式关系/派生关系/知识库实例 四项计数
python scripts/build_index.py   # 检索记录、图节点、边、主流程及各自步数
```

两个脚本都从本体与知识库现场统计，永远准。
覆盖缺口（哪些 Anomaly 还没有处置实例）见 `prompts/kb-refresh.md` 的选题步骤，
那里给了当场推导的方法。

## 本期简化范围

诚实记录，避免后续误以为这些已经做完：

- **深度优先于广度**：光刻/蚀刻/键合/塑封等高价值站点建得较细，
  多金属层互连、先进封装（2.5D/3D、TSV、Chiplet）只留了扩展位
- **参数区间是典型值**，来自公开资料与模型知识，不对应任何具体厂的实际配方
- **`economic_hooks` 只有定性传导，没有量化系数**。每个实体都填了
  `affects`（波动传导到哪些经营变量）与 `note`（传导机制一句话），
  但没有任何弹性系数、单位成本、周期时间数值。也就是说现在能回答
  "干蚀刻波动会传导到良率/重工率/稼动率"，答不出"传导多少"。
  量化留给第三层经营模型 —— 在本体层随手填系数会让不可信的数字获得
  和工艺知识同等的地位
- **设备维度只到机台粒度，不到腔体与班别**。机台已覆盖全部工序，
  但多腔体机台（蚀刻机、PVD/CVD）实际的调度单元是腔体而非整机，
  同机不同腔需分开做 SPC。班别、操作员、治具这三类资源完全未建模
- **检索是关键词匹配**，未做向量化。当前规模够用，实体数上千后需引入语义检索
- **`ext.*` 类比目标只有 ID 与说明**，未真正打通其他行业的本体文件，跨库联查是后续工作
- **`human` 来源零出现**，全库无人工录入条目。`web` 来源已有真实条目，
  R006 那条 web-必须带-URL 的分支已被真实数据走过

## 详细规范

- 建模规范与常见错误：`knowledge/modeling-guide.md`
- 领域术语与中英对照：`knowledge/domain-glossary.md`
- 项目背景与上下文：`knowledge/project-notes.md`
- 输出格式契约：`output-contracts/`

