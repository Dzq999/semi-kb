"""一条命令跑完整条检查链：precheck -> validate -> build_index -> regress。

    python scripts/checkup.py            # 全链
    python scripts/checkup.py --quick     # 跳过 build_index（只查不重建）
    python scripts/checkup.py --status     # 末尾附带选题依据

退出码：0 全过｜1 有环节失败（首个失败即停）

为什么值得单独有这个脚本：

  四个脚本分开跑要付四次 Python 冷启动 + 四次本体加载。实测每次约 1.2 秒
  用在启动上，真正的检查只占几百毫秒。合成一个进程后本体只加载一次，
  四步共享同一份数据。

  更实际的原因是少打错命令。分开跑时顺序不能颠倒（validate 必须在
  build_index 之前，否则拿旧索引校验新本体），而顺序写错不会报错、
  只会给出过期的结论。把顺序固化进脚本，就不会再有人记错。

  另外解决一个反复踩的坑：临时查询以前靠 python -c "..." 内联，
  PowerShell 与 Python 的引号嵌套极易冲突（本轮就因此失败两次，
  报 SyntaxError: unterminated string literal）。需要查询走 kb_status.py。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402


def step(name: str, args: list[str], timeout: int = 1800) -> tuple[bool, float, str]:
    t0 = time.monotonic()
    r = subprocess.run([sys.executable, str(C.ROOT / "scripts" / name), *args],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=str(C.ROOT), timeout=timeout)
    return r.returncode == 0, time.monotonic() - t0, (r.stdout or "") + (r.stderr or "")


def tail(text: str, n: int = 6) -> str:
    lines = [l for l in (text or "").strip().splitlines() if l.strip()]
    return "\n".join("    " + l for l in lines[-n:])


def main() -> int:
    C.setup_console()
    ap = argparse.ArgumentParser(description="一条命令跑完整条检查链")
    ap.add_argument("--quick", action="store_true", help="跳过 build_index")
    ap.add_argument("--status", action="store_true", help="末尾附带选题依据")
    a = ap.parse_args()

    pending = list((C.ROOT / "changesets" / "pending").glob("*.yaml"))
    chain: list[tuple[str, list[str]]] = []
    if pending:
        chain.append(("precheck.py", []))
    chain.append(("validate.py", []))
    if not a.quick:
        chain.append(("build_index.py", []))
    chain.append(("regress.py", []))

    if not pending:
        print("（changesets/pending/ 为空，跳过 precheck）")

    total = 0.0
    for name, args in chain:
        ok, dt, out = step(name, args)
        total += dt
        print(f"[{'OK  ' if ok else 'FAIL'}] {name:<18} {dt:>5.2f}s")
        if not ok:
            print(tail(out, 12))
            print(f"\n{name} 失败，链路中止。总耗时 {total:.2f}s")
            return 1
        key = [l for l in out.strip().splitlines()
               if any(k in l for k in ("ERROR", "通过", "实体 ", "已生成", "检索记录"))]
        if key:
            print(tail("\n".join(key[-3:]), 3))

    print(f"\n全链通过，总耗时 {total:.2f}s")

    if a.status:
        print()
        ok, dt, out = step("kb_status.py", ["--gaps"])
        print(out.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
