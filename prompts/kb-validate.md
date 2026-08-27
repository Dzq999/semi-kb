# 子流程：kb-validate

校验一致性并重建派生产物。

## 命令

```powershell
python scripts/validate.py           # 完整报告
python scripts/validate.py --quiet   # 只报 error（脚本内部调用用）
python scripts/build_index.py
```

顺序不可颠倒。本体有 error 时重建索引只是把错误固化进 `build/`。

## 校验规则

规则的唯一真源是 `ontology/meta-schema.yaml` 的 `validation_rules` 段，
每条含 `id`、`desc`、`severity`。需要完整清单时读那里，不要依赖本文件的副本——
副本一旦与元模型漂移，就会把 Agent 引向错误的判断。

`validate.py` 的输出自带规则编号与描述文字，直接按输出内容处理即可，
不需要预先记住编号与含义的映射。

## 处理原则

**error 必须清零。** 不要为了让校验通过而放宽元模型约束——
报错通常暴露的是建模错误。先假设自己错了，改元模型是最后手段。

**warn 要逐条看，但不必强行清零。** 孤立节点可能是刚建还没接图（该补关系），
也可能是本来不该建（该删）。低可信度占比超标是质量信号，
处置方式是补证据或降级表述，不是把 `low` 改成 `high`。

## 怎么确认校验器本身没坏

零 error 零 warn 有两种可能：真的干净，或者校验器没在工作。
定期做故障注入验证：

```powershell
Copy-Item ontology/fab/entities/18-routes.yaml 18-routes.yaml.bak
# 手工打乱 steps 顺序，或把某个 *_ref 改成不存在的 ID
python scripts/validate.py        # 应报出 Route.steps 缺 precedes 的 warn
                                  # 与引用不存在实体的 error
Move-Item -Force 18-routes.yaml.bak ontology/fab/entities/18-routes.yaml
python scripts/validate.py        # 应回到 0/0
```

一个从不报错的校验器和没有校验器是等价的。
