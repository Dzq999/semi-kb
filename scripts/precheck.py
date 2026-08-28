"""提案送审前的确定性检查。把能算的算掉，只把判断题留给审核代理。

    python scripts/precheck.py                                    # 检查 pending/ 下全部
    python scripts/precheck.py changesets/pending/xxx.yaml         # 检查单份
    python scripts/precheck.py --json

退出码：0 无 error｜1 有 error（不应送审）｜2 用法/文件错误

为什么要有这一层：

  首轮实跑一份 13 条的提案被审核代理全数 reject，事后归类死因只有三类——
  5 条是同一个机械性格式错（实体写了 equipment 字段，派生出的 belongs_to
  指向 Cause/Action，违反白名单）被复制了 5 次；5 条是端点被拒后的连带拒绝，
  零信息量；真正需要判断力的只有 2 条。

  用一个慢且不确定的模型去反复发现同一条确定性规则，是把算术题当作文写：
  贵、慢、还可能漏。这些检查移到脚本里以后，生成端当场就能看到错误并改掉，
  审核代理只面对真正的判断题（provenance 标得该不该、概念是否语义重复、
  may_cause 用得对不对、类比是否成立）。

  拒绝率会因此下降，但不是靠放松标准——是错误在更早、更便宜的环节被挡掉了。

与既有脚本的分工：

  validate.py        管已落库的全库一致性，跑的是合并之后
  precheck.py        管未落库的提案，跑的是送审之前
  verify_changeset.py 管 verdict 的配套齐全，不判断内容对错

  刻意补上了 validate.py 的一个盲区：validate.py 的 check_relations() 只吃
  显式关系，派生关系不进类型校验，而 build_index.py 是 relations + derived
  一起入图。也就是说非法派生边不报错却会静默进图。本脚本对提案新增实体做
  派生展开并逐条验证白名单，就是补这个洞。

本脚本只读、不写、不改提案，可离线复跑。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

CONF_ORDER = {"low": 0, "medium": 1, "high": 2}
SECTIONS = ("entities", "relations", "kb_cases")

# kb_case 里各 ref 字段期望的目标实体类型。写死在这里而不是从 meta 推：
# kb_schema 用散文描述类型约束，机器读不出来，硬编并在注释里指明出处更诚实。
KB_REF_TYPES = {
    "anomaly_ref": ["Anomaly"],
    "detected_at_ref": ["Process"],
    "cause_ref": ["Cause", "Parameter", "Process", "Anomaly", "State"],
    "metric_ref": ["Metric"],
    "action_ref": ["Action"],
}


class Finding:
    __slots__ = ("level", "section", "key", "msg")

    def __init__(self, level: str, section: str, key: str, msg: str):
        self.level, self.section, self.key, self.msg = level, section, key, msg

    def as_dict(self) -> dict:
        return {"level": self.level, "section": self.section,
                "key": self.key, "msg": self.msg}


def norm_name(text: str) -> str:
    return re.sub(r"[\s\-_/（）()、,，.。]+", "", str(text or "")).lower()


def bigrams(text: str) -> set[str]:
    s = norm_name(text)
    return {s[i:i + 2] for i in range(len(s) - 1)} or ({s} if s else set())


def item_key(section: str, item: dict) -> str:
    """与 apply_changeset.item_key / verify_changeset 保持一致，否则对不上账。"""
    if section == "relations":
        return f"{item.get('from')}|{item.get('type')}|{item.get('to')}"
    return str(item.get("id") or item.get("title") or "")


def iter_items(doc: dict):
    additions = doc.get("additions") or {}
    for section in SECTIONS:
        for entry in additions.get(section) or []:
            entry = entry or {}
            yield section, entry, (entry.get("item") or {})


# ---------- 各项检查 ----------

def check_envelope(doc: dict, path: Path, out: list) -> None:
    """顶层键名。写错时 apply_changeset 静默跳过并以退出码 0 报成功——
    这是整条链路唯一能骗过自动化的失败模式，必须最先拦。"""
    if not isinstance(doc.get("changeset"), dict):
        out.append(Finding("error", "-", path.name,
                           "顶层缺 changeset: 段（写成 meta: 不会报错但 apply 会静默跳过，"
                           "结论显示“自动放行 0 条”且退出码 0）"))
    else:
        head = doc["changeset"]
        for f in ("id", "title", "created_at", "author", "rationale"):
            if not head.get(f):
                out.append(Finding("warn", "-", path.name, f"changeset 段缺 {f}"))
        if head.get("id") and head["id"] != path.stem:
            out.append(Finding("warn", "-", path.name,
                               f"changeset.id={head['id']} 与文件名 {path.stem} 不一致，"
                               "verdict 按文件名定位，容易对错账"))
    if not isinstance(doc.get("additions"), dict):
        out.append(Finding("error", "-", path.name, "缺 additions: 段"))
        return
    if not any((doc["additions"].get(s) or []) for s in SECTIONS):
        out.append(Finding("error", "-", path.name, "additions 下没有任何新增条目"))
    for bad in set(doc["additions"]) - set(SECTIONS):
        out.append(Finding("warn", "-", path.name,
                           f"additions 下有未知段 {bad}（只认 {', '.join(SECTIONS)}）"))


def check_target_file(section: str, entry: dict, key: str, out: list) -> None:
    tf = entry.get("target_file")
    if not tf:
        out.append(Finding("error", section, key,
                           "缺 target_file（写成 target: 不会报错，apply 会打印“跳过”后继续）"))
        return
    p = C.ROOT / tf
    if not p.is_file():
        out.append(Finding("error", section, key, f"target_file 不存在：{tf}"))
        return
    expect = {"entities": "entities", "relations": "relations", "kb_cases": "cases"}[section]
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        out.append(Finding("error", section, key, f"target_file 读取失败：{exc!r}"))
        return
    if f"{expect}:" not in text:
        out.append(Finding("warn", section, key,
                           f"target_file 里没有 {expect}: 段，apply 会在文件末尾新建"))


def check_provenance(section: str, key: str, item: dict, meta: dict,
                     min_conf: str, out: list) -> None:
    prov = item.get("provenance")
    if not isinstance(prov, dict):
        out.append(Finding("error", section, key, "缺 provenance"))
        return
    schema = meta.get("provenance_schema") or {}
    allowed_src = ((schema.get("source_type") or {}).get("allowed")
                   or ["model_prior", "web", "analogy", "human"])
    src = prov.get("source_type")
    if src not in allowed_src:
        out.append(Finding("error", section, key,
                           f"provenance.source_type={src!r} 非法（应为 {'/'.join(allowed_src)}）"))
    conf = prov.get("confidence")
    if conf not in CONF_ORDER:
        out.append(Finding("error", section, key,
                           f"provenance.confidence={conf!r} 非法（应为 low/medium/high）"))
    elif CONF_ORDER[conf] < CONF_ORDER.get(min_conf, 1):
        out.append(Finding("warn", section, key,
                           f"confidence={conf} 低于 guards.min_confidence={min_conf}，"
                           "会被 apply 拦下留在 pending/"))
    if src == "web" and not (prov.get("ref") or "").strip():
        out.append(Finding("error", section, key,
                           "source_type=web 但 provenance.ref 为空"))
    if src != "web" and (prov.get("ref") or "").strip():
        out.append(Finding("warn", section, key,
                           f"source_type={src} 却填了 ref，来源与证据不一致"))
    if not str(prov.get("created_at") or "").strip():
        out.append(Finding("warn", section, key, "provenance 缺 created_at"))

    # 顶层 confidence 是个真陷阱：apply_changeset.py 只读 provenance.confidence，
    # 顶层那个对放行判断完全无效，写了容易以为已经达标。
    if "confidence" in item and section == "relations":
        top, inner = item.get("confidence"), prov.get("confidence")
        if top != inner:
            out.append(Finding("warn", section, key,
                               f"顶层 confidence={top} 与 provenance.confidence={inner} 不一致；"
                               "apply 只读后者，顶层不参与放行判断"))


def check_entity(item: dict, meta: dict, existing: dict, out: list) -> None:
    key = item_key("entities", item)
    etypes = meta.get("entity_types") or {}
    req = meta.get("entity_required_fields") or []
    opt = set(meta.get("entity_optional_fields") or [])

    missing = [f for f in req if f not in item or item.get(f) in (None, "", [], {})]
    if missing:
        out.append(Finding("error", "entities", key, f"缺必填字段：{', '.join(missing)}"))
    unknown = {f for f in set(item) - set(req) - opt - {"provenance"}
               if not f.startswith("_")}
    if unknown:
        out.append(Finding("error", "entities", key,
                           f"未知字段：{', '.join(sorted(unknown))}（R003 无未知字段）"))

    etype = item.get("type")
    if etype not in etypes:
        out.append(Finding("error", "entities", key,
                           f"type={etype!r} 不在 entity_types 内"))
    eid = item.get("id") or ""
    pattern = ((meta.get("id_rules") or {}).get("pattern")
               or r'^(core|fab|ap|ext)\.[a-z_]+\.[a-z0-9_]+$')
    if not re.match(pattern, eid):
        out.append(Finding("error", "entities", key, f"ID 不符合 id_rules.pattern：{eid}"))
    else:
        parts = eid.split(".")
        if etype in etypes:
            want = etypes[etype].get("type_slug")
            if parts[1] != want:
                out.append(Finding("error", "entities", key,
                                   f"ID 中段 {parts[1]!r} 与 type={etype} 的 "
                                   f"type_slug={want!r} 不一致（R002）"))
        if item.get("domain") and parts[0] != item["domain"]:
            out.append(Finding("error", "entities", key,
                               f"domain={item['domain']} 与 ID 前缀 {parts[0]} 不一致（R007）"))
    if eid in existing:
        out.append(Finding("error", "entities", key,
                           f"ID 已存在于 {existing[eid].get('_file')}（R001 全局唯一）"))

    hooks = item.get("economic_hooks")
    if isinstance(hooks, dict):
        allowed = (((meta.get("economic_hooks_schema") or {}).get("affects") or {})
                   .get("allowed") or [])
        for a in hooks.get("affects") or []:
            if allowed and a not in allowed:
                out.append(Finding("error", "entities", key,
                                   f"economic_hooks.affects={a!r} 不在允许集合内（R011）"))


def check_derived(new_entities: list[dict], meta: dict, out: list) -> None:
    """对提案新增实体做派生展开并验证白名单。

    这是 validate.py 的盲区：它的 check_relations() 只吃显式关系，
    build_index.py 却把 relations + derived 一起入图，非法派生边
    不报错却会静默进图。首轮 5 条 reject 全部死在这里。
    """
    if not new_entities:
        return
    ents = {e["id"]: dict(e, _file="<changeset>") for e in new_entities if e.get("id")}
    rtypes = meta.get("relation_types") or {}
    try:
        derived = C.derived_relations(ents, meta)
    except Exception as exc:                               # noqa: BLE001
        out.append(Finding("warn", "entities", "-", f"派生关系展开失败：{exc!r}"))
        return
    types = {i: e.get("type") for i, e in ents.items()}
    for rel in derived:
        spec = rtypes.get(rel["type"])
        if not spec:
            continue
        for side, allowed in (("from", spec.get("from") or []), ("to", spec.get("to") or [])):
            node = rel[side]
            ntype = types.get(node)
            if ntype is None:          # 指向库内已有实体，交由 validate 合并后统一校验
                continue
            if allowed and ntype not in allowed:
                owner = rel["from"] if side == "to" else rel["to"]
                out.append(Finding(
                    "error", "entities", owner,
                    f"字段 {rel['_field']} 派生出 {rel['from']} --{rel['type']}--> {rel['to']}，"
                    f"{side} 端类型 {ntype} 不在白名单 {allowed}（R005）。"
                    f"注意 validate.py 不校验派生关系，此边会静默进图——删掉该字段"))


def check_relation(item: dict, meta: dict, existing: dict, new_ids: dict,
                   existing_rels: set, out: list) -> None:
    key = item_key("relations", item)
    rtypes = meta.get("relation_types") or {}
    for f in meta.get("relation_required_fields") or ["from", "type", "to"]:
        if not item.get(f):
            out.append(Finding("error", "relations", key, f"缺必填字段 {f}"))
    allowed_fields = (set(meta.get("relation_required_fields") or ["from", "type", "to"])
                      | set(meta.get("relation_optional_fields") or []))
    unknown = {f for f in set(item) - allowed_fields if not f.startswith("_")}
    if unknown:
        out.append(Finding("warn", "relations", key,
                           f"未知字段：{', '.join(sorted(unknown))}"))

    rtype = item.get("type")
    spec = rtypes.get(rtype)
    if not spec:
        out.append(Finding("error", "relations", key,
                           f"关系类型 {rtype!r} 不在 relation_types 内（R005）"))
        return

    def type_of(node: str) -> str | None:
        if node in new_ids:
            return new_ids[node]
        if node in existing:
            return existing[node].get("type")
        return None

    for side in ("from", "to"):
        node = item.get(side)
        if not isinstance(node, str):
            continue
        if C.is_external(node):
            # ext.* 豁免解析，但仍应确认用在了合适的位置
            if side == "from":
                out.append(Finding("warn", "relations", key,
                                   "from 端是 ext.*，跨行业类比通常应从本域实体指向 ext.*"))
            continue
        ntype = type_of(node)
        if ntype is None:
            out.append(Finding("error", "relations", key,
                               f"{side} 端 {node} 在库内与本提案中都找不到（R004 悬空引用）"))
            continue
        allowed = spec.get(side) or []
        if allowed and ntype not in allowed:
            out.append(Finding("error", "relations", key,
                               f"{side} 端 {node} 类型 {ntype} 不在白名单 {allowed}（R005）"))
    if key in existing_rels:
        out.append(Finding("error", "relations", key, "该关系已存在于库中，重复新增"))


def check_kb_case(item: dict, meta: dict, existing: dict, new_ids: dict,
                  existing_kb: dict, out: list) -> None:
    key = item_key("kb_cases", item)
    schema = meta.get("kb_schema") or {}
    req = schema.get("required_fields") or []
    opt = set(schema.get("optional_fields") or [])
    missing = [f for f in req if f not in item or item.get(f) in (None, "", [], {})]
    if missing:
        out.append(Finding("error", "kb_cases", key, f"缺必填字段：{', '.join(missing)}"))
    unknown = {f for f in set(item) - set(req) - opt if not f.startswith("_")}
    if unknown:
        out.append(Finding("error", "kb_cases", key,
                           f"未知字段：{', '.join(sorted(unknown))}"))
    pat = schema.get("id_pattern") or r'^kb\.(fab|ap)\.[a-z0-9_]+$'
    if not re.match(pat, str(item.get("id") or "")):
        out.append(Finding("error", "kb_cases", key,
                           f"ID 不符合 kb_schema.id_pattern：{item.get('id')}"))
    if item.get("id") in existing_kb:
        out.append(Finding("error", "kb_cases", key,
                           f"ID 已存在于 {existing_kb[item['id']].get('_file')}"))

    domain = item.get("domain")

    def resolve(ref: str, field: str) -> None:
        if not isinstance(ref, str) or C.is_external(ref):
            return
        ntype = new_ids.get(ref) or (existing.get(ref) or {}).get("type")
        if ntype is None:
            out.append(Finding("error", "kb_cases", key,
                               f"{field}={ref} 在库内与本提案中都找不到（R014）"))
            return
        want = KB_REF_TYPES.get(field)
        if want and ntype not in want:
            out.append(Finding("error", "kb_cases", key,
                               f"{field}={ref} 类型为 {ntype}，应为 {'/'.join(want)}（R014）"))
        # 域自洽：ap 的实例不该引用 fab 的实体，反之亦然
        prefix = ref.split(".")[0]
        if domain and prefix not in (domain, "core"):
            out.append(Finding("error", "kb_cases", key,
                               f"{field}={ref} 跨域引用：domain={domain} 只应引用 "
                               f"{domain}.* 或 core.*"))

    for f in ("anomaly_ref", "detected_at_ref"):
        if item.get(f):
            resolve(item[f], f)
    for lst, field in (("possible_causes", "cause_ref"),
                       ("detection", "metric_ref"),
                       ("actions", "action_ref")):
        for sub in item.get(lst) or []:
            if isinstance(sub, dict) and sub.get(field):
                resolve(sub[field], field)
    for p in (item.get("impact") or {}).get("blocked_processes") or []:
        if isinstance(p, str):
            resolve(p, "detected_at_ref")   # 同样要求 Process 类型

    likert = (schema.get("possible_causes_item") or {}).get("likelihood") or {}
    allowed_lk = likert.get("allowed") or ["high", "medium", "low"]
    for sub in item.get("possible_causes") or []:
        if isinstance(sub, dict) and sub.get("likelihood") not in allowed_lk:
            out.append(Finding("warn", "kb_cases", key,
                               f"possible_causes.likelihood={sub.get('likelihood')!r} "
                               f"不在 {allowed_lk}"))
        if isinstance(sub, dict) and not str(sub.get("discriminator") or "").strip():
            out.append(Finding("warn", "kb_cases", key,
                               f"cause_ref={sub.get('cause_ref')} 缺 discriminator"
                               "（kb_schema 称其为最有价值的字段）"))
    orders = [s.get("order") for s in item.get("actions") or []
              if isinstance(s, dict) and s.get("order") is not None]
    if orders and sorted(orders) != list(range(min(orders), min(orders) + len(orders))):
        out.append(Finding("warn", "kb_cases", key,
                           f"actions.order 不连续：{orders}"))


def check_dup_names(new_entities: list[dict], existing: dict, out: list) -> None:
    """名称近似查重。只给候选、不下结论——是否真重复要模型判断语义。"""
    for item in new_entities:
        key = item_key("entities", item)
        etype = item.get("type")
        cands = []
        for name_field in ("name_zh", "name_en"):
            nm = item.get(name_field)
            if not nm:
                continue
            nb = bigrams(nm)
            for eid, ent in existing.items():
                if ent.get("type") != etype:
                    continue
                for ef in ("name_zh", "name_en"):
                    en = ent.get(ef)
                    if not en:
                        continue
                    if norm_name(nm) == norm_name(en):
                        cands.append((1.0, eid, en))
                        continue
                    eb = bigrams(en)
                    if nb and eb:
                        j = len(nb & eb) / len(nb | eb)
                        if j >= 0.5:
                            cands.append((j, eid, en))
        seen, uniq = set(), []
        for sim, eid, en in sorted(cands, reverse=True):
            if eid in seen:
                continue
            seen.add(eid)
            uniq.append(f"{eid}（{en}，相似度 {sim:.0%}）")
        if uniq:
            out.append(Finding("warn", "entities", key,
                               "名称接近已有同类实体，请确认是否语义重复："
                               + "；".join(uniq[:3])))


def check_internal_deps(doc: dict, out: list) -> None:
    """提案内依赖顺序。被依赖的实体若排在后面，apply 会把依赖方一并转人工。"""
    order: dict[str, int] = {}
    idx = 0
    for section, _entry, item in iter_items(doc):
        if section == "entities" and item.get("id"):
            order[item["id"]] = idx
        idx += 1
    idx = 0
    for section, _entry, item in iter_items(doc):
        if section != "entities":
            for ref in _refs_of(section, item):
                if ref in order and order[ref] > idx:
                    out.append(Finding("warn", section, item_key(section, item),
                                       f"引用了排在本条之后的新增实体 {ref}，"
                                       "建议把被依赖项前置"))
        idx += 1


def _refs_of(section: str, item: dict) -> set[str]:
    refs: set[str] = set()
    if section == "relations":
        for k in ("from", "to"):
            if isinstance(item.get(k), str):
                refs.add(item[k])
    elif section == "kb_cases":
        for k in ("anomaly_ref", "detected_at_ref"):
            if isinstance(item.get(k), str):
                refs.add(item[k])
        for lst, key in (("possible_causes", "cause_ref"), ("actions", "action_ref"),
                         ("detection", "metric_ref")):
            for sub in item.get(lst) or []:
                if isinstance(sub, dict) and isinstance(sub.get(key), str):
                    refs.add(sub[key])
        for p in (item.get("impact") or {}).get("blocked_processes") or []:
            if isinstance(p, str):
                refs.add(p)
    return refs


# ---------- 主流程 ----------

def precheck(path: Path, meta: dict, cfg: dict, existing: dict,
             existing_rels: set, existing_kb: dict) -> list[Finding]:
    out: list[Finding] = []
    try:
        doc = C.load_yaml(path) or {}
    except Exception as exc:                               # noqa: BLE001
        return [Finding("error", "-", path.name, f"YAML 解析失败：{exc!r}")]
    if not isinstance(doc, dict):
        return [Finding("error", "-", path.name, "顶层不是映射")]

    check_envelope(doc, path, out)
    min_conf = (((cfg.get("review") or {}).get("guards") or {})
                .get("min_confidence", "medium"))

    new_entities = [i for s, _e, i in iter_items(doc) if s == "entities"]
    new_ids = {i["id"]: i.get("type") for i in new_entities if i.get("id")}

    for section, entry, item in iter_items(doc):
        key = item_key(section, item)
        if not item:
            out.append(Finding("error", section, key or "?", "条目缺 item: 段"))
            continue
        check_target_file(section, entry, key, out)
        check_provenance(section, key, item, meta, min_conf, out)
        if section == "entities":
            check_entity(item, meta, existing, out)
        elif section == "relations":
            check_relation(item, meta, existing, new_ids, existing_rels, out)
        else:
            check_kb_case(item, meta, existing, new_ids, existing_kb, out)

    check_derived(new_entities, meta, out)
    check_dup_names(new_entities, existing, out)
    check_internal_deps(doc, out)
    return out


def main() -> int:
    C.setup_console()
    ap = argparse.ArgumentParser(description="提案送审前的确定性检查")
    ap.add_argument("changeset", nargs="*", help="提案路径；省略则检查 pending/ 下全部")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    if args.changeset:
        paths = []
        for raw in args.changeset:
            p = Path(raw)
            if not p.is_absolute():
                p = (C.ROOT / raw).resolve()
            if not p.is_file():
                print(f"提案不存在：{p}")
                return 2
            paths.append(p)
    else:
        paths = sorted((C.ROOT / "changesets" / "pending").glob("*.yaml"))
        if not paths:
            print("changesets/pending/ 下没有提案。")
            return 0

    meta, cfg = C.load_meta(), C.load_config()
    existing, dup = C.load_entities()
    existing_rels = {f"{r.get('from')}|{r.get('type')}|{r.get('to')}"
                     for r in C.load_relations()}
    existing_kb = {c.get("id"): c for c in C.load_kb() if c.get("id")}
    if dup:
        print(f"注意：库内已有重复 ID {len(dup)} 处，先跑 validate.py")

    report, n_err, n_warn = [], 0, 0
    for p in paths:
        fs = precheck(p, meta, cfg, existing, existing_rels, existing_kb)
        errs = [f for f in fs if f.level == "error"]
        warns = [f for f in fs if f.level == "warn"]
        n_err += len(errs)
        n_warn += len(warns)
        report.append({"file": p.name, "error": len(errs), "warn": len(warns),
                       "findings": [f.as_dict() for f in fs]})

    if args.as_json:
        print(json.dumps({"error": n_err, "warn": n_warn, "files": report},
                         ensure_ascii=False, indent=2))
        return 1 if n_err else 0

    for r in report:
        print("=" * 72)
        print(f"提案 {r['file']}：ERROR {r['error']} | WARN {r['warn']}")
        print("=" * 72)
        if not r["findings"]:
            print("  确定性检查全过。")
        for f in r["findings"]:
            tag = "ERROR" if f["level"] == "error" else "warn "
            print(f"  [{tag}] [{f['section']}] {f['key']}")
            print(f"          {f['msg']}")
    print("-" * 72)
    if n_err:
        print(f"共 ERROR {n_err} | WARN {n_warn}。有 error 不应送审——"
              "这些都是确定性规则，交给审核代理只会换来一次昂贵的 reject。")
        return 1
    print(f"共 ERROR 0 | WARN {n_warn}。确定性检查通过，可以送审。")
    print("审核代理只需判断：provenance 标得该不该、概念是否语义重复、"
          "may_cause 用得对不对、类比是否成立。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
