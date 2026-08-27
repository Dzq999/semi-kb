"""每日自动扩充的驱动。定时任务的唯一入口。

    python scripts/daily_refresh.py                # 完整跑一次
    python scripts/daily_refresh.py --dry-run      # 只跑到生成提案，不落库
    python scripts/daily_refresh.py --skip-agent   # 不调 agent，只落已有 pending 提案

七段流水线，任一段失败就整次回滚到开跑前的提交：

  1. 前置检查   工作区干净、脚本齐、基线校验通过、基线回归通过
  2. 快照      记下开跑前的 commit，这是回滚点
  3. 生成      调 claude CLI 无头跑 prompts/kb-refresh.md，产出 changesets/pending/*.yaml
  4. 落库      apply_changeset.py 按 config.review 自动合并（它自带原子回滚）
  5. 护栏      净增实体数与增长比例不超 config.review.guards 的上限
  6. 验证      validate.py + build_index.py + regress.py 双份夹具
  7. 提交      全过则 git commit；任一段失败则 git reset --hard 回到第 2 段的点

为什么护栏放在这一层而不是 apply_changeset：
后者只看单个提案合不合规，看不到"今天已经加了多少"。跑飞的形态不是某一条
写错，是连着几天每天加 40 条没人看，两周后库里一半内容没人认得。

退出码：0 成功（含"无事可做"）| 1 失败已回滚 | 2 前置检查未过（未做任何改动）
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

import common as C

LOG_DIR = C.ROOT / "logs"
AGENT_TIMEOUT = 3600          # agent 单次最多跑 1 小时
GIT = ["git", "-C", str(C.ROOT)]


def log(msg: str = "") -> None:
    line = f"[{datetime.now():%H:%M:%S}] {msg}" if msg else ""
    print(line, flush=True)
    LOG_DIR.mkdir(exist_ok=True)
    with (LOG_DIR / f"daily-{datetime.now():%Y%m%d}.log").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def run(cmd: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=C.ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def py(script: str, *args: str, timeout: int = 600) -> subprocess.CompletedProcess:
    return run([sys.executable, str(C.ROOT / "scripts" / script), *args], timeout=timeout)


def counts() -> dict[str, int]:
    """当前规模。用 common 直接读，不依赖 build/ 是否最新。"""
    ents, _ = C.load_entities()
    return {"entities": len(ents), "relations": len(C.load_relations()),
            "kb": len(C.load_kb())}


# ---------- 1. 前置检查 ----------

def precheck(guards: dict, skip_agent: bool) -> str | None:
    """返回 None 表示通过，否则返回失败原因。"""
    r = run([*GIT, "rev-parse", "--is-inside-work-tree"])
    if r.returncode != 0:
        return ("semi-kb 不是 git 仓库。无人值守必须有回滚线——"
                "跑 `git init && git add -A && git commit` 先立基线。")

    if guards.get("require_git_clean", True):
        r = run([*GIT, "status", "--porcelain"])
        if r.stdout.strip():
            dirty = [l for l in r.stdout.strip().splitlines()][:5]
            return ("工作区不干净，没有干净的回滚点：\n    " + "\n    ".join(dirty) +
                    "\n  先提交或撤销这些改动。自动化不该在别人没存盘的工作上叠改动。")

    for s in ("validate.py", "build_index.py", "regress.py", "apply_changeset.py", "ask.py"):
        if not (C.ROOT / "scripts" / s).is_file():
            return f"缺少 scripts/{s}"
    if not list((C.ROOT / "tests").glob("questions-*.txt")):
        return "tests/ 下没有问题夹具，回归守门形同虚设"

    r = py("validate.py", "--quiet")
    if r.returncode != 0:
        return f"基线校验就没过，先修库再谈自动扩充：\n{r.stdout.strip()[-500:]}"

    r = py("regress.py", timeout=1800)
    if r.returncode != 0:
        return f"基线回归就没过，先修再谈自动扩充：\n{r.stdout.strip()[-800:]}"

    if not skip_agent and not (C.ROOT / "prompts" / "kb-refresh.md").is_file():
        return "缺少 prompts/kb-refresh.md，agent 没有可执行的流程"
    return None


# ---------- 3. 生成：调 agent ----------

AGENT_TASK = """按 prompts/kb-refresh.md 执行一次自动扩充，无人值守模式。

硬约束，逐条遵守：
1. 只产出 changeset 到 changesets/pending/，**不要直接改 ontology/ 或 kb/**。
   落库由 scripts/apply_changeset.py 负责，你越过它写文件会绕开原子回滚。
2. 顶层键必须严格照 output-contracts/changeset.md。键名写错脚本会静默什么都不做，
   无人值守时这等于每天空跑还报成功。写完自己核对一遍键名。
3. web 来源必须带真实 URL 到 provenance.ref。**没检索到就写 model_prior**，
   不要把模型知识标成 web——那样溯源链断了而且没人知道断了。
4. 本次最多新增 {max_ent} 个实体。宁少勿滥，一条建对比十条建歪有价值。
5. 不要改 ontology/meta-schema.yaml，不要改 ontology/competency-questions.yaml
   的既有条目，不要改 scripts/、tests/、config.yaml。
6. 新增实体如果开出了新的可答问题，在 changeset 里用 competency_questions 段提出，
   由人工决定是否进清单。**不要自己往清单里加 in_scope**——
   R016 只查依赖类型有没有实例，查不了路径真不真通，自动加等于自动生产空承诺。
7. 选题优先补 validate.py 报的孤立实体、无 kb 实例的异常、knowledge/project-notes.md
   的已知缺口。不要重复已有内容。

跑完只回一句话：产出了几个提案文件、各自新增多少条、选题是什么。
"""


def run_agent(max_ent: int) -> tuple[bool, str]:
    task = AGENT_TASK.format(max_ent=max_ent)
    cmd = ["claude", "-p", task,
           "--permission-mode", "acceptEdits",
           "--add-dir", str(C.ROOT),
           "--output-format", "json"]
    log(f"调 agent（超时 {AGENT_TIMEOUT}s，权限 acceptEdits）")
    try:
        r = run(cmd, timeout=AGENT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, f"agent 超时（{AGENT_TIMEOUT}s）"
    if r.returncode != 0:
        return False, f"agent 退出码 {r.returncode}：{(r.stderr or r.stdout)[-600:]}"
    try:
        d = json.loads(r.stdout)
        return True, str(d.get("result", ""))[:800]
    except Exception:                                  # noqa: BLE001
        return True, r.stdout[-800:]


# ---------- 5. 护栏 ----------

def check_growth(before: dict, after: dict, guards: dict) -> str | None:
    d_ent = after["entities"] - before["entities"]
    cap = guards.get("max_entities_per_day", 25)
    ratio_cap = guards.get("max_growth_ratio", 0.15)
    log(f"净增：实体 {d_ent:+d}｜关系 {after['relations'] - before['relations']:+d}"
        f"｜kb {after['kb'] - before['kb']:+d}")
    if d_ent > cap:
        return f"单日净增实体 {d_ent} 超过上限 {cap}"
    if before["entities"] and d_ent / before["entities"] > ratio_cap:
        return (f"单日净增实体 {d_ent} 占现有规模 "
                f"{d_ent / before['entities']:.1%}，超过 {ratio_cap:.0%}")
    if d_ent < 0 or after["relations"] < before["relations"]:
        return f"规模下降了（实体 {d_ent:+d}），自动扩充不该删东西"
    return None


# ---------- 6. 验证 ----------

def verify() -> str | None:
    r = py("validate.py")
    log(r.stdout.strip()[-400:])
    if r.returncode != 0:
        return "合并后校验失败"

    r = py("build_index.py")
    if r.returncode != 0:
        return f"构建索引失败：{r.stdout.strip()[-300:]}"
    log(r.stdout.strip()[-200:])

    r = py("regress.py", timeout=1800)
    log(r.stdout.strip()[-600:])
    if r.returncode != 0:
        return "问题夹具回归失败"
    return None


# ---------- 2 / 7. 快照与回滚 ----------

def snapshot() -> str:
    return run([*GIT, "rev-parse", "HEAD"]).stdout.strip()


def rollback(point: str, why: str) -> None:
    log(f"!! {why}")
    log(f"回滚到 {point[:8]}")
    run([*GIT, "reset", "--hard", point])
    run([*GIT, "clean", "-fd", "--", "ontology", "kb", "build", "changesets"])


def commit(before: dict, after: dict, note: str) -> None:
    run([*GIT, "add", "-A"])
    msg = (f"auto: 每日扩充 {datetime.now():%Y-%m-%d}\n\n"
           f"实体 {before['entities']} -> {after['entities']}｜"
           f"关系 {before['relations']} -> {after['relations']}｜"
           f"kb {before['kb']} -> {after['kb']}\n\n{note[:400]}\n\n"
           f"校验 + 双份问题夹具回归均通过。\n\n"
           f"Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>")
    r = run([*GIT, "-c", "user.name=semi-kb-bot",
             "-c", "user.email=bot@local", "commit", "-q", "-m", msg])
    if r.returncode == 0:
        log(f"已提交 {snapshot()[:8]}")
    else:
        log(f"提交失败（可能无改动）：{(r.stdout + r.stderr).strip()[:200]}")


def main() -> int:
    C.setup_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只生成提案，不落库")
    ap.add_argument("--skip-agent", action="store_true", help="不调 agent，只落已有提案")
    a = ap.parse_args()

    cfg = yaml.safe_load((C.ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
    guards = ((cfg.get("review") or {}).get("guards") or {})

    log("=" * 60)
    log(f"每日自动扩充开跑  dry_run={a.dry_run} skip_agent={a.skip_agent}")

    why = precheck(guards, a.skip_agent)
    if why:
        log(f"!! 前置检查未过：{why}")
        log("未做任何改动。")
        return 2
    log("前置检查通过（工作区干净、基线校验与回归均过）")

    point = snapshot()
    before = counts()
    log(f"回滚点 {point[:8]}｜当前 实体 {before['entities']}"
        f"｜关系 {before['relations']}｜kb {before['kb']}")

    note = "（--skip-agent）"
    if not a.skip_agent:
        ok, note = run_agent(int(guards.get("max_auto_entities", 30)))
        log(f"agent: {note[:300]}")
        if not ok:
            rollback(point, note)
            return 1

    pending = sorted((C.ROOT / "changesets" / "pending").glob("*.yaml"))
    log(f"pending 提案 {len(pending)} 个：{[p.name for p in pending]}")
    if not pending:
        log("无事可做（agent 没产出提案）。不算失败。")
        rollback(point, "无提案，清掉 agent 可能留下的临时改动")
        return 0

    if a.dry_run:
        log("--dry-run：到此为止，提案留在 pending/ 供人工查看。")
        return 0

    r = py("apply_changeset.py")
    log(r.stdout.strip()[-1200:])
    if r.returncode != 0:
        rollback(point, f"落库失败（apply_changeset 退出码 {r.returncode}）")
        return 1

    after = counts()
    if (why := check_growth(before, after, guards)):
        rollback(point, why)
        return 1

    if (why := verify()):
        rollback(point, why)
        return 1

    commit(before, after, note)
    log("本次扩充完成。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("!! 被中断")
        sys.exit(1)
