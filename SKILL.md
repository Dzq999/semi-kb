---
name: semi-kb
description: 半导体前段厂(Fab)/后段厂(AP)本体与知识库的构建、填充、查询、自动更新与校验。当用户询问半导体制造工艺、前后段异常排查、WIP/良率/设备问题，或要求扩建本体、更新知识库时使用。
---

# semi-kb · 半导体前后段知识库主控

本 skill 管理一个**本体驱动**的半导体制造知识库，遵循四层推演链路方法论
（本体 → 知识库 → 经营模型 → 仿真引擎）。当前项目只实现前两层，
第三层通过 `economic_hooks` 字段预留接口。

## 执行模型：全流程由当前 Agent 完成

semi-kb 的每一段工作——**选题 → 取材 → 建模 → 产出 changeset → 自审 →
合并 → 验证**——都由当前 Agent 顺序完成，全程只有一个执行体。

- **不得启动 SubAgent / 子代理 / 并行代理**承担其中任何一段，也不存在
  "换另一个代理来独立审"这一环。
- 需要复核时，由当前 Agent 按对应子流程的检查清单**重新读取相关文件**
  再判一遍，而不是委派出去。
- 确定性检查归脚本：`precheck.py` 查提案外形与提案内依赖，
  `validate.py` 查合并后的全库一致性。能判定的规则持续往脚本里沉淀，
  让"需要人判断"的面积越来越小。
- verdict 的 `verifier` 字段一律标 `self:<子流程名>`（如 `self:kb-refresh`），
  如实反映是当前 Agent 自审。标成独立审核就是假记录，比自审更糟。

这么定的理由是链路短、上下文不丢：委派会把"为什么这样选题"的推导过程切掉，
而审核质量靠的是脚本吃掉可判定的部分，不是靠增加代理数量。

## 第一原则：本体是唯一真源

三层数据的关系不可颠倒：

```
ontology/   本体（唯一真源，人与 Agent 共同维护）
    ↓ 实例填充，所有 *_ref 必须指向已存在的本体实体
kb/         知识库（异常处置 Playbook）
    ↓ 脚本生成，随时可删可重建
build/      检索索引与图（派生产物，禁止手工编辑）
```

任何时候 `build/` 与本体不一致，重跑 `build_index.py` 即可，不需要人工对账。
知识库**不得凭空引入概念**——要写一条涉及新异常的处置知识，必须先在本体中建该异常实体。
这条约束由 `validate.py` 以 error 强制，绕不过去。

## 子流程路由

本 skill 与它管理的数据同处一个根目录：`prompts/`、`knowledge/`、`output-contracts/`
是行为定义，`ontology/`、`kb/`、`build/` 是数据。本库所有文档中出现的路径都以该根为基准。

根据用户意图选择，对应提示词在 `prompts/` 下：

| 用户意图 | 子流程 | 提示词 |
|---|---|---|
| 问工艺、问异常怎么查、问某站点问题 | `kb-query` | `prompts/kb-query.md` |
| 新建或扩建本体实体/关系 | `ontology-build` | `prompts/ontology-build.md` |
| 为已有异常补写处置知识 | `kb-fill` | `prompts/kb-fill.md` |
| 找资料、更新本体、产出变更提案 | `kb-refresh` | `prompts/kb-refresh.md` |
| 校验一致性、重建索引 | `kb-validate` | `prompts/kb-validate.md` |

判断规则：

- 问句（"为什么""怎么查""可能原因"）→ `kb-query`
- 祈使句且涉及新增概念 → `ontology-build`
- 祈使句且概念已存在、要补经验 → `kb-fill`
- 提到"更新""找资料""同步""定时" → `kb-refresh`
- 提到"检查""校验""重建" → `kb-validate`
- 意图不明时默认 `kb-query`，因为回答问题不改动任何文件，是零风险选项。

任何流程开跑前先跑 `python scripts/ask.py "<用户原问>"` —— 它按
`ontology/competency-questions.yaml` 判定这个问题库能不能答。
退出码 `0` 可答 / `2` 超出范围 / `3` 清单未覆盖。判成 `2` 时照输出的
`missing` 与 `precondition` 回复，**不要用模型知识硬编答案**：库外知识伪装成
库内知识，溯源链断掉且无人知晓。不要靠肉眼读 YAML 代替这一步。

跑 `kb-refresh` 时注意一个不会报错的失败模式：changeset 的顶层键必须是
`changeset:`、条目内必须是 `target_file:`。写错时 `apply_changeset.py`
打印"跳过"后继续，结论为"自动放行 0 条 | 待人工审核 0 条"，**退出码 0**，
定时任务会以为更新成功但实际什么都没合并。
裁决输出里 `标题：None` 就是这个信号。详见 `prompts/kb-refresh.md`。

## 项目结构

```
SKILL.md                          本文件：路由规则与第一原则
config.yaml                       全局配置，含审核开关
prompts/                          五个子流程提示词
knowledge/                        建模规范 / 领域术语 / 项目笔记
output-contracts/                 输出格式契约
ontology/meta-schema.yaml         元模型：合法的实体类型、关系类型、字段契约、校验规则
ontology/competency-questions.yaml 能力问题清单：库承诺能答什么、明确答不了什么
ontology/{core,fab,ap}/entities/  实体定义
ontology/{core,fab,ap}/relations/ 关系定义
kb/{fab,ap}/                      异常处置 Playbook
build/                            index.json / graph.json（派生）
changesets/{pending,applied,rejected}/  变更提案流转
scripts/                          ask.py / validate.py / build_index.py / apply_changeset.py / common.py
```

## 四个命令

```powershell
python scripts/ask.py "<问题>"      # 这个问题库能不能答，0 可答 / 2 超范围 / 3 未覆盖
python scripts/validate.py          # 校验，退出码非零表示有 error
python scripts/build_index.py       # 重建 build/index.json 与 build/graph.json
python scripts/apply_changeset.py --dry-run   # 审核变更提案（不写入）
```

改动本体或知识库后**必须**跑 `validate.py`，通过后跑 `build_index.py`。
顺序不可颠倒——索引由本体派生，本体有错时重建索引只是把错误固化。

## 领域划分

- `core` 前后段共享：Lot、WIP、Hold、Rework、Scrap、良率、CT、OEE、洁净室环境、瓶颈站点
- `fab` 前段厂：扩散/热制程、光刻、蚀刻、薄膜、离子注入、CMP、量测、WAT/CP
- `ap` 后段厂：晶圆前处理、装片、互连（金线/倒装）、塑封、成型、测试

两域通过 `refines` 关系归入 core 骨架，通过 `precedes`（`fab.process.cp_test → ap.process.wafer_receive`）衔接。

## 跨行业类比：analogous_to

`analogous_to` 关系记录本行业概念与其他行业本体节点的映射，目标 ID 用 `ext.*` 前缀
（豁免本地解析校验）。这不是装饰性字段——它是让下一个行业冷启动更快的载体：
本次沉淀的映射会被下一个行业直接读取，而不必重新推演。

新建实体时若发现明显的跨行业共性（环境控制、批次管理、瓶颈、时效性材料、
加速老化、分级分选），应主动建 `analogous_to` 关系并在 `note` 里写清共性是什么、
什么可复用。

## 知识来源与溯源

本项目**无内部数据接口**，知识只来自三处，`provenance.source_type` 如实标注：

- `model_prior` 模型预训练的半导体制造知识
- `web` 公网公开资料，**必须**填 `ref` (URL)，否则校验报错
- `analogy` 跨行业类比推演
- `human` 人工录入

`confidence` 必须诚实：不确定的标 `low`，不要用 `high` 包装猜测。
低可信度占比超过 40% 时 `validate.py` 会发出质量预警——这是设计意图，
让知识质量可观测，而不是让它悄悄劣化。

## 详细规范

- 建模规范与常见错误：`knowledge/modeling-guide.md`
- 领域术语与中英对照：`knowledge/domain-glossary.md`
- 项目背景、设计取舍、已知缺口：`knowledge/project-notes.md`
- 完整结构与规则说明（面向人阅读）：`README.md`
- 输出格式契约：`output-contracts/` 下按子流程分文件
