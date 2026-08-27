"""问题夹具回归。自动扩充链路的守门：库劣化了立刻有非零退出码。

    python scripts/regress.py                  # 跑 tests/ 下全部夹具
    python scripts/regress.py --file tests/questions-real.txt
    python scripts/regress.py --json

两份夹具必须一起跑，缺一份都能被糊弄过去：
  questions-real.txt       58 条真实工厂问法，绝大多数期望 refuse。防误报。
  questions-type-level.txt 本体本来该答的问法，多数期望 answerable。防误拒。
只跑前者的话，把 ask.py 改成永远返回 3 就能满分。

退出码：0 全过 | 1 有失败
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import common as C

VERDICT = {0: "answerable", 2: "refuse", 3: "refuse"}


def load(path: Path) -> list[tuple[str, str]]:
    out = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        if "\t" not in ln:
            print(f"  ! 跳过无 TAB 分隔的行：{ln[:40]}")
            continue
        exp, q = ln.split("\t", 1)
        out.append((exp.strip(), q.strip()))
    return out


def run_one(question: str) -> tuple[str, str]:
    """返回 (实际判定, 命中的 CQ ID)。"""
    p = subprocess.run([sys.executable, str(C.ROOT / "scripts" / "ask.py"), question, "--json"],
                       cwd=C.ROOT, capture_output=True, text=True, encoding="utf-8")
    try:
        d = json.loads(p.stdout)
        m = d.get("matched") or {}
        tag = m.get("id", "-")
        if d.get("instance_signals"):
            tag = "实例级:" + "/".join(d["instance_signals"])
    except Exception:                                  # noqa: BLE001
        tag = "!解析失败"
    return VERDICT.get(p.returncode, f"?{p.returncode}"), tag


def main() -> int:
    C.setup_console()
    as_json = "--json" in sys.argv
    only = None
    if "--file" in sys.argv:
        only = Path(sys.argv[sys.argv.index("--file") + 1])

    files = [only] if only else sorted((C.ROOT / "tests").glob("questions-*.txt"))
    if not files:
        print("tests/ 下没有 questions-*.txt 夹具")
        return 1

    report, failed = [], 0
    for f in files:
        cases = load(f)
        fails = []
        for exp, q in cases:
            got, tag = run_one(q)
            if got != exp:
                fails.append({"q": q, "expect": exp, "got": got, "matched": tag})
        failed += len(fails)
        report.append({"file": f.name, "total": len(cases),
                       "failed": len(fails), "cases": fails})

    if as_json:
        print(json.dumps({"failed": failed, "files": report},
                         ensure_ascii=False, indent=2))
        return 1 if failed else 0

    print("=" * 68)
    for r in report:
        ok = r["total"] - r["failed"]
        print(f"{r['file']}  {ok}/{r['total']} 通过")
        for c in r["cases"]:
            print(f"  期望 {c['expect']:10} 实得 {c['got']:10} [{c['matched']}]")
            print(f"       {c['q'][:60]}")
    print("-" * 68)
    if failed:
        print(f"回归失败 {failed} 条。")
        print("误报（期望 refuse 实得 answerable）优先修：那是把答不了的说成答得了。")
        return 1
    print("回归全过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
