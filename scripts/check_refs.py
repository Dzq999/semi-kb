"""web 溯源 URL 的可达性检查。确定性,不调模型。

    python scripts/check_refs.py                    # 查全库
    python scripts/check_refs.py --file <changeset> # 查单个提案
    python scripts/check_refs.py --quiet            # 只报失败

为什么单独一个脚本而不是塞进 validate.py 的 R006:
R006 是纯本地、离线可跑、毫秒级的结构校验,全库跑一次几十毫秒。
往里加网络请求会让它变成分钟级且依赖外网——CI 里断网就全红,
而断网跟"本体结构对不对"毫无关系。两件事该分开。

为什么必须有这个检查:
2026-08-27 撤掉的 fab.process.euv_exposure 标 source_type: web,
ref 指向的 ASML 页面返回 404,内容实为模型知识。它通过了 R006
(字段非空)、通过了护栏、通过了双份回归,一路进了库。
output-contracts/changeset.md 第 59-61 行早就写明"填占位链接比标
model_prior 更糟,因为它伪装成了可核查的证据"——文档是对的,缺的是执行。

退出码:0 全部可达(或无 web 来源)| 1 有不可达 | 2 用法错误
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import sys
import urllib.error
import urllib.request
from pathlib import Path

import common as C

TIMEOUT = 20
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def probe(url: str) -> tuple[bool, str]:
    """HEAD 优先,405/403 时退回 GET。返回 (可达, 说明)。

    退回 GET 的原因:不少站点(含标准组织)对 HEAD 返回 405 或 403,
    但 GET 正常。只用 HEAD 会把好链判成死链,比不查更糟——
    它会让人开始怀疑检查本身,然后关掉它。
    """
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(url, method=method, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                code = resp.getcode()
                final = resp.geturl()
                note = f"HTTP {code}"
                if final.rstrip("/") != url.rstrip("/"):
                    note += f" (重定向至 {final})"
                return True, note
        except urllib.error.HTTPError as exc:
            if method == "HEAD" and exc.code in (403, 405, 501):
                continue                      # 换 GET 再试
            return False, f"HTTP {exc.code} {exc.reason}"
        except urllib.error.URLError as exc:
            return False, f"网络错误 {exc.reason}"
        except Exception as exc:              # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"
    return False, "HEAD 与 GET 均失败"


def _collect(prov: dict, label: str, where: str, out: list) -> None:
    """只收 source_type=web 且 ref 非空的。ref 为空是 R006 的活,不在这重复报。"""
    if (prov or {}).get("source_type") != "web":
        return
    ref = (prov or {}).get("ref")
    if isinstance(ref, str) and ref.strip().startswith("http"):
        out.append((ref.strip(), label, where))


def from_library() -> list[tuple[str, str, str]]:
    """全库的 web 溯源:实体 + 关系 + kb 实例。"""
    out: list[tuple[str, str, str]] = []
    ents, _ = C.load_entities()
    for eid, ent in ents.items():
        _collect(ent.get("provenance"), eid, ent.get("_file", "?"), out)
    for rel in C.load_relations():
        label = f"{rel.get('from')} --{rel.get('type')}--> {rel.get('to')}"
        _collect(rel.get("provenance"), label, rel.get("_file", "?"), out)
    for case in C.load_kb():
        _collect(case.get("provenance"), case.get("id", "?"), case.get("_file", "?"), out)
    return out


def from_changeset(path: Path) -> list[tuple[str, str, str]]:
    """提案里的 web 溯源。落库前查,才来得及拦。"""
    out: list[tuple[str, str, str]] = []
    doc = C.load_yaml(path) or {}
    for section in ("entities", "relations", "kb_cases"):
        for entry in (doc.get("additions") or {}).get(section) or []:
            item = entry.get("item") or {}
            label = item.get("id") or (
                f"{item.get('from')} --{item.get('type')}--> {item.get('to')}")
            _collect(item.get("provenance"), str(label), path.name, out)
    return out


def main() -> int:
    C.setup_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="只查指定提案")
    ap.add_argument("--quiet", action="store_true", help="只报失败")
    a = ap.parse_args()

    if a.file:
        p = Path(a.file)
        if not p.is_file():
            print(f"提案不存在:{p}")
            return 2
        targets = from_changeset(p)
        scope = f"提案 {p.name}"
    else:
        targets = from_library()
        scope = "全库"

    if not targets:
        print(f"{scope}:没有 web 来源的 URL 需要检查。")
        return 0

    # 去重但保留所有引用位置——同一 URL 被多条引用时,失败要能列全。
    uniq: dict[str, list[tuple[str, str]]] = {}
    for url, label, where in targets:
        uniq.setdefault(url, []).append((label, where))

    print(f"{scope}:检查 {len(uniq)} 个 URL（{len(targets)} 处引用）")
    results: dict[str, tuple[bool, str]] = {}
    with cf.ThreadPoolExecutor(max_workers=6) as pool:
        for url, res in zip(uniq, pool.map(probe, uniq)):
            results[url] = res

    bad = 0
    for url, (ok, note) in results.items():
        if ok and a.quiet:
            continue
        print(f"  [{'OK ' if ok else 'DEAD'}] {note}  {url}")
        if not ok:
            bad += 1
            for label, where in uniq[url]:
                print(f"         <- {label}  ({where})")

    print("-" * 60)
    if bad:
        print(f"{bad} 个 URL 不可达。标 web 却指向死链,溯源链是断的——"
              "改成真实可达的 URL,或老实降级成 model_prior。")
        return 1
    print(f"全部 {len(uniq)} 个 URL 可达。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
