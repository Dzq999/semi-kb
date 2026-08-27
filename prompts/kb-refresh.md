# 子流程：kb-refresh

自动找资料、扩充本体与知识库。这是下周起交给定时任务的入口。
**产出变更提案，不直接写本体。**

## 为什么走提案而不直接写

自动改本体的风险不在于改错一条，而在于**错误被后续内容依赖**。
本体是唯一真源，一个错误概念一旦被 kb 实例引用、被 index 收录，
清理成本远高于当初拦下来。changeset 机制把"生成"与"落库"分开，
让审核策略可配置，也让每次变更留下可回溯的记录。

## 开跑前先确认环境

四项都在首次实跑时真实卡过，全部会让流程在第二段断掉。

**WebSearch 可用。** 网关故障时返回 503 而不是空结果
（实测过 `分组 claude-code企业级 下模型 xxx 无可用渠道`）。
第一段无可替代——playwright 只能去已知地址，不能发现地址。
WebSearch 挂了就等它恢复，别用猜测的 URL 顶替。
成功响应里不含后端模型信息，想确认路由要看网关侧日志。

**playwright MCP 在当前工作目录可见。** `claude mcp list` 若返回
`No MCP servers configured`，说明它注册在别的目录作用域下。
用 `claude mcp add-json playwright '<json>' -s user` 注册到 user scope
（`claude mcp add ... -- npx -y ...` 里的 `-y` 会被 CLI 当自己的选项吃掉，报
`unknown option '-y'`，必须用 `add-json`）。
**注册后必须重启会话**——MCP 工具列表在启动时固定，不重启工具不进列表。

**浏览器可用。** `%LOCALAPPDATA%\ms-playwright` 下的 `mcp-chrome-*`
是 userDataDir（Chrome 用户配置），不是浏览器安装，没有 `chrome.exe`
和 `INSTALLATION_COMPLETE` 是正常的。用 `--browser chrome` 走系统 Chrome
最省事。不要因为它们缺少安装标记就判断浏览器坏了。

**cwd 有 `.gitignore` 忽略 `.playwright-mcp/`。** 见下文第二段说明。

## 步骤

**1. 选题**

优先补三类缺口，按价值排序：

- **孤立节点** —— `validate.py` 报的孤立实体 warn，说明建了但没接进图
- **无 kb 实例的异常** —— 在 `build/index.json` 里找 `type: Anomaly` 但没有
  任何 kb_case 的 `anomaly_ref` 指向它的，这类异常在本体里存在但无法支撑排查
- **主流程断点** —— `validate.py` 报的 Route.steps 与 `precedes` 不一致处（warn）

不要漫无目的地"再加些实体"。数量不是目标，图的连通性和可用性才是。

**选题依据必须当场从 `build/index.json` 或 `common.py` 重新推导，
不要复用对话里出现过的数字。** 上下文里的统计数（"还有 N 个异常没有实例"）
可能来自更早的推测甚至凭空生成，一旦当成前提，整个选题就建在假数上。
重算一次的成本是几秒，用错数字的成本是一条建错的知识。

优先挑图上已经连得好、但没有 kb 实例的异常：关系越多说明它在风险图谱里
越关键，缺处置知识的空洞也越显眼。首次实跑挑的 `ap.anomaly.delamination`
就是这类——风险图谱里 cause、detected_at、mitigated_by、blocks 都齐，
severity high，还被另一条 kb 实例当上游原因引用，
但排查指过来之后没有任何处置内容。

只新增 kb 实例、不碰实体与关系的提案能走 `auto_apply.instances` 自动合并，
是验证全流程最省事的选题类型。

**2. 找资料**

来源顺序：模型预训练知识 → 跨行业类比 → 公网检索。
公网检索优先 SEMI/JEDEC/IPC 标准、设备商技术文档（ASML/AMAT/TEL/ASE/Amkor）、
学术综述、行业媒体技术专栏。

**每条 web 来源必须记下可验证的真实 URL。**
`require_ref_for_web: true` 会拦下没有 ref 的 web 条目，
但校验器无法判断 URL 是否真实存在——填占位或猜测的链接会污染溯源链，
比标 `model_prior` 更糟，因为它伪装成了可核查的证据。
拿不到确切 URL 就老实标 `model_prior` 并把 confidence 降为 `medium`/`low`。

### 三段取材流程

三个工具能力不重叠，是流水线的不同环节，不是备选项。按这个顺序走：

**第一段 · 发现候选源：WebSearch**

无可替代。playwright 只能去已知地址，不能发现地址。
先搜到 URL 清单，再决定用哪个工具取正文。

**第二段 · HTML 正文：WebFetch 优先，403 或需 JS 渲染时换 playwright**

WebFetch 一次往返就够，优先用。它拿不到时再上 playwright——
起 Chrome、快照可能很大，成本高得多，当后备。

标准组织站点常拦 WebFetch。实测 `jedec.org` 的标准页对 WebFetch 返回
403 Forbidden，playwright 用真实 Chrome 正常取到正文。
遇到 403 不要放弃这个源，换 playwright 再试一次。

playwright 会在**当前工作目录**落 `.playwright-mcp/`（页面快照与 console 日志）。
用完删掉，否则会随提案一起污染仓库。

**第三段 · PDF 数据表：WebFetch 落盘 + 本地 PyMuPDF**

标准与应用报告的关键数值大多在 PDF 里，而两个网络工具都读不出来：
WebFetch 返回 FlateDecode 压缩流，playwright 更差——Chrome 的 PDF 阅读器
渲染得出来但不向 accessibility tree 暴露文本，快照是 0 字节，
`browser_find` 连正文关键词都搜不到。**PDF 不要用 playwright。**

可行的是第三条路：WebFetch 失败时会把 PDF 存到磁盘并在结果里给出路径，
用 PyMuPDF 本地解析那个文件。

```python
import fitz, re
doc = fitz.open(r"<WebFetch 结果里给出的本地路径>")
for i, pg in enumerate(doc):
    if re.search(r"<要找的表名或字段>", pg.get_text()):
        print(f"--- 第 {i+1} 页 ---"); print(pg.get_text())
```

先用正则定位含目标表的页码，再只打印那几页，不要整份 dump。

### 记 provenance 时的三个坑

实跑中都真实遇到过，都会产生"看起来可核查、实际错"的溯源：

**数值的出处可能不是你搜的那个标准。** MSL floor life 表出自
IPC/JEDEC **J-STD-033**（管搬运、储存、floor life），不是 J-STD-020
（管回流焊峰值温度分类 Tc）。两者常被混引。以取到的文档原文对该表的
归属说明为准，不要按自己检索时用的关键词归因。

**URL 里的版本号可能不等于实际服务的版本。** `jedec.org/.../j-std-033c`
这个地址实际服务的是 J-STD-033D（Published Apr 2018），而 `j-std-033d`
路径返回 404。所以 `033c` 才是正规入口。把这类差异写进 `notes`，
否则后来的人会以为记错了。

**301 重定向后要记最终 URL。** `tij.co.jp` → `ti.com` 是 301，
playwright 静默跟随，WebFetch 会把重定向抛出来要求重发。
`provenance.ref` 记规范化后的最终地址，不记跳转前的。

### 依赖

PyMuPDF（`import fitz`）。当前环境已装 1.26.5。
不在则 `pip install pymupdf`，或退回只引 HTML 页面的概述、
不引 PDF 里的具体数值。**拿不到数值就不要写数值**，
写个大概区间比空着更糟——它看起来像有据可查的。

**3. 产出 changeset**

写到 `changesets/pending/YYYYMMDD-<topic>.yaml`，格式见 `output-contracts/changeset.md`。

**键名必须精确匹配：顶层 `changeset:`、条目内 `target_file:`。**
写成 `meta:` 或 `target:` 不会报错。`apply_changeset.py` 找不到 `target_file`
就打印一行"缺少 target_file，跳过"然后继续，结论是
"自动放行 0 条 | 待人工审核 0 条"，**退出码 0**。
定时任务只看退出码会以为成功，实际什么都没合并——
这是本流程唯一能静默骗过自动化的失败模式。

裁决输出里出现 `标题：None` 或 `来源：None` 就是顶层键写错了。
每次产出提案后先看 dry-run 的这两行，再看结论。

注意提案内部的依赖顺序：若新增 kb_case 引用了同一提案里新增的实体，
`apply_changeset.py` 会把该 kb_case 也一并转人工（因为它依赖的实体尚未落库），
这是预期行为，不是 bug。

**4. 审核与合并**

```powershell
python scripts/apply_changeset.py --dry-run     # 先看裁决结果
python scripts/apply_changeset.py               # 按 config.yaml 策略执行
python scripts/apply_changeset.py --no-review    # 全自动（谨慎）
python scripts/apply_changeset.py --force-review # 全人工
```

合并是原子的：追加后立即校验，失败则回滚，仓库不会停在坏状态。

**5. 重建索引**

```powershell
python scripts/build_index.py
```

## 定时任务接入

`config.yaml` 的 `review` 段控制自动化程度：

```yaml
review:
  mode: manual            # 改为 auto 才允许非 instances 类自动合并
  auto_apply:
    instances: true       # kb 实例自动合并
    entities: false       # 本体实体默认转人工
    relations: false
    schema: false         # 元模型改动强烈建议永远人工
```

建议的推进节奏：先跑一两周 `mode: manual`，人工审核积累对提案质量的判断，
确认误报率可接受后再逐项放开 `auto_apply`。
`schema: false` 不建议改——改元模型等于改游戏规则，影响面是全库。

`guards.max_auto_entities` 限制单次自动新增实体数，防止一次跑飞污染大面积本体。
