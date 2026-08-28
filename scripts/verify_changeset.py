"""生成审核任务书，并校验写回的 verdict。

为什么拆成两步而不是让审核逻辑直接改库：
  apply_changeset.py 必须保持确定性（可离线复跑、可进 CI），不能内嵌模型调用。
  中间的接头就是 verdict 文件：审核方写结论，applier 只读结论。
  这条设计与审核由谁做无关，任何审核方式都走同一个接头。

审核由谁做（历经两次调整，记下来免得来回摇）：
  最初是人工。之后改独立子代理——取证独立，避免同一段上下文里边写边审时
  "看到的是自己刚写下的理由而非页面正文"。实测独立审核确实抓到过真问题
  （5 条非法派生边、1 条 MTTR 语义错、1 条概念重复），但代价是慢：
  11 个条目要几分钟，且大部分时间在重复推导 precheck.py 能算出的结论。

  现在是 precheck.py + 自审：确定性检查全部由脚本承担，需要判断的部分自审，
  verdict 里如实标 self:*。本脚本不校验 verifier 与 author 是否不同——
  强制不了，也不该假装强制。要紧的是标得诚实：知道哪些结论没有第二双眼睛
  看过，才知道该重点复查什么。假装独立审过比自审本身更糟。

  自审的偏差靠一件事缓解：把能判定的规则持续往 precheck.py 沉淀，
  让"需要判断"的面积越来越小。已沉淀的包括派生关系合法性、MTTR 措辞与
  may_cause 并存、model_prior 却写标准号或数值、affects 集合完全重复、
  kb_case 引用在图上缺边。

用法：
    python scripts/verify_changeset.py --brief <提案路径>   # 输出审核任务书
    python scripts/verify_changeset.py --check <提案路径>   # 校验 verdict 是否配套齐全
退出码：0 正常；1 verdict 有缺口；2 用法/文件错误。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

import common as C

VERDICT_DIR = C.ROOT / "changesets" / "verdicts"
LEGAL_VERDICT = {"pass", "reject", "unsure"}

# 按来源类型分派审核标准。审核标准不该一刀切：
# web 条目的风险是"URL 是真的但正文不支持这句话"，只能靠重新取正文；
# model_prior 的风险是"其实是模型知识却标成了 web"和"库里已经有等价概念"；
# analogy 的风险是类比关系没落成 analogous_to，变成硬断言。
CRITERIA = {
    "web": [
        "自己用 WebFetch 或 Playwright 重新打开 ref 里的 URL，不要相信提案里的转述。",
        "确认正文里能找到支撑该条目 description / parameters 的原话或数据；",
        "  找不到就判 reject，理由写清楚页面里实际有什么、缺什么。",
        "URL 可达但内容无关（导航页、产品总览页）同样判 reject。",
    ],
    "model_prior": [
        "判断这条是否本该有公开来源：外部客观事实（标准号、设备参数、行业阈值）",
        "  标 model_prior 是偷懒，判 unsure 并说明该去查什么；",
        "  领域惯例、工程判断、内部本体结构标 model_prior 是正确的。",
        "检索全库是否已有等价概念（同义不同名），重复则 reject。",
    ],
    "analogy": [
        "确认类比落成了 analogous_to 关系，而不是直接断言成因果/包含关系。",
        "确认被类比的两端在库里都真实存在。",
    ],
    "human": ["人工录入，核对格式与 ID 命名规范即可。"],
}


def iter_items(doc: dict):
    """遍历提案里的新增条目，产出 (section, key, item)。"""
    additions = doc.get("additions") or {}
    for section in ("entities", "relations", "kb_cases"):
        for entry in additions.get(section) or []:
            item = (entry or {}).get("item") or {}
            if section == "relations":
                key = f"{item.get('from')}|{item.get('type')}|{item.get('to')}"
            else:
                key = str(item.get("id") or item.get("title") or "")
            yield section, key, item


def touches_schema(doc: dict) -> list[str]:
    """挑出会动元模型的改动。全自动放行下这类风险最高,必须在任务书里单独点名。"""
    hits = []
    for entry in (doc.get("schema_changes") or []):
        hits.append(str(entry))
    for _, key, item in iter_items(doc):
        for field in item:
            if field.startswith("_"):
                continue
        if item.get("type") and str(item.get("type")).startswith("meta."):
            hits.append(f"{key}: type={item['type']}")
    return hits


def brief(path: Path) -> int:
    doc = C.load_yaml(path) or {}
    head = doc.get("changeset") or {}
    rows = list(iter_items(doc))
    if not rows:
        print(f"提案 {path.name} 没有新增条目，无需审核。")
        return 0

    by_src: dict[str, list[tuple[str, str, dict]]] = {}
    for section, key, item in rows:
        src = (item.get("provenance") or {}).get("source_type") or "?"
        by_src.setdefault(src, []).append((section, key, item))

    print("=" * 72)
    print(f"审核任务书：{path.name}")
    print(f"标题：{head.get('title')}")
    print(f"共 {len(rows)} 条新增，涉及来源类型 {', '.join(sorted(by_src))}")
    print("=" * 72)
    print("\n先决条件（确定性检查，已由脚本完成，不要重复做）：")
    print(f"  python scripts/check_refs.py --file {path.as_posix()}")
    print("  它只保证 URL 可达。URL 可达 != 正文支持该说法——后者是你的活。")

    schema_hits = touches_schema(doc)
    if schema_hits:
        print("\n!! 本提案触及元模型，全自动放行下没有人眼兜底，请逐条重点核：")
        for h in schema_hits:
            print(f"   · {h}")

    for src in sorted(by_src):
        print(f"\n--- 来源 {src}（{len(by_src[src])} 条）审核标准 ---")
        for line in CRITERIA.get(src, ["未知来源类型，判 reject。"]):
            print(f"  {line}")
        for section, key, item in by_src[src]:
            ref = (item.get("provenance") or {}).get("ref") or ""
            print(f"\n  [{section}] {key}")
            desc = (item.get("description") or item.get("title") or "").strip()
            if desc:
                print(f"    断言：{desc[:300]}")
            if ref:
                print(f"    ref：{ref}")

    out = VERDICT_DIR / f"{path.stem}.verdict.yaml"
    print("\n" + "=" * 72)
    print(f"把结论写到 {out.relative_to(C.ROOT).as_posix()}，格式：")
    print(f"""
changeset: {path.name}
verifier: <你的子代理标识>
verified_at: <YYYY-MM-DD HH:MM>
items:""")
    for section, key, _ in rows[:2]:
        print(f"  - key: \"{key}\"\n    section: {section}\n"
              f"    verdict: pass | reject | unsure\n"
              f"    reason: \"取证结论。web 条目写明在页面哪一段看到了什么。\"")
    print("  # ... 每条都要有，漏一条那条就不会被合并")
    return 0


def check(path: Path) -> int:
    doc = C.load_yaml(path) or {}
    rows = list(iter_items(doc))
    vp = VERDICT_DIR / f"{path.stem}.verdict.yaml"
    if not vp.exists():
        print(f"[缺失] 没有 {vp.relative_to(C.ROOT).as_posix()}，提案不会被合并。")
        return 1

    data = yaml.safe_load(vp.read_text(encoding="utf-8")) or {}
    problems: list[str] = []
    if (data.get("changeset") or "") not in ("", path.name):
        problems.append(f"changeset 字段 {data.get('changeset')} 与提案 {path.name} 不符")
    for field in ("verifier", "verified_at"):
        if not data.get(field):
            problems.append(f"缺 {field}")

    seen: dict[str, dict] = {}
    for row in data.get("items") or []:
        row = row or {}
        key = row.get("key")
        if not key:
            problems.append("有条目缺 key")
            continue
        if key in seen:
            problems.append(f"重复裁决：{key}")
        seen[key] = row
        v = row.get("verdict")
        if v not in LEGAL_VERDICT:
            problems.append(f"{key}: verdict={v!r} 非法（应为 {'/'.join(sorted(LEGAL_VERDICT))}）")
        if v != "pass" and not row.get("reason"):
            problems.append(f"{key}: 非 pass 必须写 reason")

    n_pass = 0
    for section, key, _ in rows:
        row = seen.get(key)
        if not row:
            problems.append(f"未裁决：[{section}] {key}")
        elif row.get("verdict") == "pass":
            n_pass += 1
    extra = set(seen) - {k for _, k, _ in rows}
    for k in sorted(extra):
        problems.append(f"裁决了提案里不存在的条目：{k}")

    print(f"提案 {path.name}：{len(rows)} 条新增，裁决 {len(seen)} 条，pass {n_pass} 条")
    if problems:
        print(f"\n{len(problems)} 处问题：")
        for p in problems:
            print(f"  · {p}")
        return 1
    print("verdict 配套齐全，可以交给 apply_changeset.py。")
    return 0


def main() -> int:
    C.setup_console()
    ap = argparse.ArgumentParser(description="生成审核任务书 / 校验 verdict")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--brief", metavar="CHANGESET", help="输出审核任务书")
    g.add_argument("--check", metavar="CHANGESET", help="校验 verdict 是否齐全")
    args = ap.parse_args()

    raw = args.brief or args.check
    path = Path(raw)
    if not path.is_absolute():
        path = (C.ROOT / raw).resolve()
    if not path.exists():
        print(f"提案不存在：{path}")
        return 2

    VERDICT_DIR.mkdir(parents=True, exist_ok=True)
    return brief(path) if args.brief else check(path)


if __name__ == "__main__":
    sys.exit(main())
