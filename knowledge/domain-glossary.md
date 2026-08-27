# 领域术语

## 通用产能与流动

| 术语 | 中文 | 说明 |
|---|---|---|
| WIP | 在制品 | 已投料未完工的 lot 总量 |
| WIP Bubble | WIP 堆积 | 某站点前 WIP 异常高于基线，产能与需求错配的可观测症状 |
| Cycle Time (CT) | 生产周期 | 投料到完工的时间 |
| Theoretical CT | 理论周期 | 纯加工时间，不含排队 |
| X-Factor | 周期倍数 | 实际 CT / 理论 CT，排队占比的度量 |
| Little's Law | 利特尔法则 | WIP = Throughput × CT，三者知二求一 |
| Throughput | 产出率 | 单位时间完工量 |
| OEE | 设备综合效率 | 可用率 × 表现率 × 良率 |
| SEMI E10 | 设备状态标准 | Productive/Standby/Engineering/ScheduledDown/UnscheduledDown/NonScheduled |
| Bottleneck | 瓶颈站点 | 产能最低、决定整线产出的站点 |

E10 状态区分是排查 WIP 堆积的第一分叉：设备 **Down**（不可用）与
设备 **Standby**（可用但没在做）指向完全不同的根因。

## 前段厂 Fab

| 术语 | 中文 | 说明 |
|---|---|---|
| Diffusion | 扩散/热制程 | 氧化、退火、掺杂扩散 |
| Photolithography | 光刻 | 涂胶、曝光、显影 |
| Etch | 蚀刻 | 干法/湿法去除材料 |
| Thin Film | 薄膜 | CVD/PVD/ALD 沉积 |
| Implant | 离子注入 | 掺杂剂量与能量控制 |
| CMP | 化学机械研磨 | 平坦化 |
| Metrology | 量测 | CD/膜厚/套刻/缺陷检测 |
| WAT | 晶圆允收测试 | 电性参数抽测 |
| CP | 晶圆针测 | 逐 die 功能测试，出 wafer map |
| CD | 关键尺寸 | 图形线宽 |
| Overlay | 套刻精度 | 层间对准偏差 |
| DOF | 焦深 | 可接受成像的焦距范围 |
| Bossung Curve | 剂量-焦距曲线 | CD 对 focus/dose 的响应，判断制程窗口 |
| Stochastic Defect | 随机缺陷 | EUV 光子散粒噪声导致的随机断线/桥接 |
| Particle Excursion | 微粒异常 | 洁净度或设备产尘失控 |

## 后段厂 AP

| 术语 | 中文 | 说明 |
|---|---|---|
| Wafer Backgrinding | 晶圆减薄 | 磨削至目标厚度 |
| Dicing | 切割 | 划片分离 die |
| Die Bond / Die Attach | 装片 | die 贴装到基板/引脚框 |
| Wire Bond | 金线键合 | 引线连接 die 与基板 |
| Flip Chip | 倒装 | 凸块朝下直接互连 |
| Reflow | 回流焊 | 温度曲线控制的焊接 |
| Molding | 塑封 | 环氧树脂包封 |
| PMC | 后固化 | 塑封后烘烤固化 |
| Singulation | 切筋成型 | 分离单颗封装体 |
| FT | 最终测试 | 封装后功能与电性测试 |
| NSOP | 非粘着 | 金线未键合成功，最典型的键合失效 |
| Delamination | 分层 | 界面剥离，常因湿气与热应力 |
| Mold Void | 塑封孔洞 | 树脂填充不完全 |
| Die Crack | 芯片裂 | 机械或热应力致裂 |
| Chipping | 崩边 | 切割导致的边缘缺损 |
| Wire Sweep | 金线偏移 | 塑封流动推移金线 |
| MSL | 湿气敏感等级 | 封装体吸湿容许度 |
| Coplanarity | 共面度 | 引脚/球栅高度一致性 |

## 材料时效性

后段厂多种材料有开封后寿命限制，是容易被忽略的 WIP 与良率根因：
环氧树脂/胶膜（floor life）、助焊剂、吸湿封装体（需 baking）。
时效超期材料要么造成停工待料，要么在不知情下投产造成批量失效。
