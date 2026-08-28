"""库状态与选题依据。一条命令回答"下一轮该补什么"。

    python scripts/kb_status.py              # 全部
    python scripts/kb_status.py --gaps        # 只看缺口（选题用）
    python scripts/kb_status.py --json

退出码恒为 0：这是查询工具，不是校验器。

为什么要有这个脚本：

  kb-refresh 要求选题依据每轮当场重算，不许复用对话里出现过的数字——
  上下文里的统计数可能来自更早的推测甚至凭空生成，一旦当成前提，
  整个选题就建在假数上。

  但"重算"此前是每轮临时写一个脚本丢进 %TEMP% 跑完删掉。同一段逻辑
  写了四遍，每遍都可能算错：实测有一次把 18 个 Anomaly 报成 19 个、
  覆盖数也错了一位，原因是临时脚本里的集合运算把边界情况重复计了一次。
  查询逻辑固化下来，算错的概率就从"每轮重新赌一次"变成"错一次修一次"。

只读 build/ 下的派生产物，不碰本体。若 build/ 过期，先跑 build_index.py。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402


def load() -> tuple[dict, dict]:
    ip, gp = C.ROOT / "build" / "index.json", C.ROOT / "build" / "graph.json"
    for p in (ip, gp):
        if not p.is_file():
            print(f"缺 {p.relative_to(C.ROOT)}，先跑 python scripts/build_index.py")
            raise SystemExit(0)
    return (json.loads(ip.read_text(encoding="utf-8")),
            json.loads(gp.read_text(encoding="utf-8")))


def collect() -> dict:
    idx, g = load()
    recs = idx.get("records") or []
    ents = {r["id"]: r for r in recs if r.get("kind") == "entity"}
    kbs = [r for r in recs if r.get("kind") == "kb_case"]
    covered = {r.get("anomaly_ref") for r in kbs if r.get("anomaly_ref")}
    out_adj, in_adj = g.get("out_adjacency") or {}, g.get("in_adjacency") or {}

    def deg(i: str) -> int:
        return len(out_adj.get(i, [])) + len(in_adj.get(i, []))

    anomalies = {i for i, r in ents.items() if r.get("type") == "Anomaly"}
    uncovered = sorted(anomalies - covered, key=lambda x: -deg(x))

    # 孤立实体：图上一条边都没有。建了但没接进去，等于没建。
    orphans = sorted((i for i in ents if deg(i) == 0),
                     key=lambda x: (ents[x].get("type") or "", x))

    # 只有一条边的实体：接进去了但几乎没连通，价值有限
    thin = sorted((i for i in ents if deg(i) == 1),
                  key=lambda x: (ents[x].get("type") or "", x))

    by_type: dict[str, int] = {}
    for r in ents.values():
        by_type[r.get("type") or "?"] = by_type.get(r.get("type") or "?", 0) + 1

    by_domain: dict[str, dict[str, int]] = {}
    for r in ents.values():
        d = by_domain.setdefault(r.get("domain") or "?", {})
        d[r.get("type") or "?"] = d.get(r.get("type") or "?", 0) + 1

    # provenance 分布：low 占比过高说明库在稀释
    conf: dict[str, int] = {}
    src: dict[str, int] = {}
    # build_index 把 provenance 摊平成顶层 confidence / source_type，
    # 不保留嵌套的 provenance 对象，所以直接读顶层。
    for r in list(ents.values()) + kbs:
        conf[r.get("confidence") or "?"] = conf.get(r.get("confidence") or "?", 0) + 1
        src[r.get("source_type") or "?"] = src.get(r.get("source_type") or "?", 0) + 1

    # ext.* 类比目标只被一个域连着。单域本身是常态（实测 11 个全是单域），
    # 只有当 note 自己声称"两域/跨域共用"时才算名不副实——所以只报这一种。
    CROSS = ("两域", "跨域", "共用", "同一套")
    ext_link: dict[str, set[str]] = {}
    ext_claims: set[str] = set()
    for e in g.get("edges") or []:
        if e.get("type") == "analogous_to" and str(e.get("to", "")).startswith("ext."):
            ext_link.setdefault(e["to"], set()).add(
                str(e.get("from", "")).split(".")[0])
            if any(k in (e.get("note") or "") for k in CROSS):
                ext_claims.add(e["to"])

    return {
        "entities": len(ents), "kb_cases": len(kbs),
        "edges": len(g.get("edges") or []),
        "anomaly_total": len(anomalies),
        "anomaly_covered": len(anomalies & covered),
        "by_type": by_type, "by_domain": by_domain,
        "confidence": conf, "source_type": src,
        "uncovered": [{"id": i, "degree": deg(i),
                       "name_zh": ents[i].get("name_zh"),
                       "severity": ents[i].get("severity"),
                       "domain": ents[i].get("domain")} for i in uncovered],
        "orphans": [{"id": i, "type": ents[i].get("type"),
                     "name_zh": ents[i].get("name_zh")} for i in orphans],
        "thin": [{"id": i, "type": ents[i].get("type"),
                  "name_zh": ents[i].get("name_zh")} for i in thin],
        "ext_asymmetric": sorted(k for k, v in ext_link.items()
                                 if len(v) == 1 and k in ext_claims),
    }


def main() -> int:
    C.setup_console()
    ap = argparse.ArgumentParser(description="库状态与选题依据")
    ap.add_argument("--gaps", action="store_true", help="只输出缺口")
    ap.add_argument("--json", action="store_true", dest="as_json")
    a = ap.parse_args()
    s = collect()

    if a.as_json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return 0

    if not a.gaps:
        print("=" * 68)
        print(f"实体 {s['entities']} | 知识库实例 {s['kb_cases']} | 图边 {s['edges']}")
        print("=" * 68)
        print("按类型：" + "  ".join(f"{k} {v}" for k, v in sorted(s["by_type"].items())))
        for d in ("core", "fab", "ap"):
            if d in s["by_domain"]:
                print(f"  {d:<5} " + "  ".join(
                    f"{k} {v}" for k, v in sorted(s["by_domain"][d].items())))
        print("\nprovenance 可信度：" + "  ".join(
            f"{k} {v}" for k, v in sorted(s["confidence"].items())))
        print("provenance 来源：  " + "  ".join(
            f"{k} {v}" for k, v in sorted(s["source_type"].items())))

    print("\n" + "-" * 68)
    print(f"Anomaly {s['anomaly_total']} | 有 kb 实例 "
          f"{s['anomaly_covered']} | 无实例 {len(s['uncovered'])}")
    print("-" * 68)
    if s["uncovered"]:
        print("未覆盖异常（按度数降序，度数高=风险图谱里更关键，空洞更显眼）：")
        for u in s["uncovered"]:
            print(f"  度数 {u['degree']:>2}  {u['id']:<34} {u['name_zh'] or '':<12} "
                  f"severity={u['severity'] or '-':<9} {u['domain']}")
    else:
        print("所有异常都有 kb 实例。")

    if s["orphans"]:
        print(f"\n孤立实体 {len(s['orphans'])} 个（图上零边，建了但没接进去）：")
        for o in s["orphans"][:12]:
            print(f"  {o['type']:<10} {o['id']:<38} {o['name_zh'] or ''}")
    if s["thin"]:
        print(f"\n仅一条边 {len(s['thin'])} 个（接进去了但几乎没连通）：")
        for o in s["thin"][:12]:
            print(f"  {o['type']:<10} {o['id']:<38} {o['name_zh'] or ''}")
    if s["ext_asymmetric"]:
        print(f"\n类比声称跨域但只连一个域 {len(s['ext_asymmetric'])} 个"
              "（note 说两域共用，实际只有一侧接着，补另一侧才名副其实）：")
        for k in s["ext_asymmetric"]:
            print(f"  {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
