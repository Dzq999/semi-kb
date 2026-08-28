"""每日自动扩充的驱动。定时任务的唯一入口。

    python scripts/daily_refresh.py                # 完整跑一次
    python scripts/daily_refresh.py --dry-run      # 只跑到生成提案，不落库
    python scripts/daily_refresh.py --skip-agent   # 不调 agent，只落已有 pending 提案

流水线，任一段失败就整次回滚到开跑前的提交：

  1. 前置检查   工作区干净、脚本齐、基线自己就通过（kb.py check --quick）
  2. 快照      记下开跑前的 commit，这是回滚点
  3. 生成      调 claude CLI 无头跑 prompts/kb-refresh.md，产出 changesets/pending/*.yaml
  3b. 闸门     precheck.py 查提案形状，ERROR 则不进落库（未合并故不回滚）
  4. 落库      apply_changeset.py 按 config.review 自动合并（它自带原子回滚）
  5. 护栏      净增实体数与增长比例不超 config.review.guards 的上限
  6. 验证      kb.py check 全链
  7. 提交      全过则 git commit；任一段失败则 git reset --hard 回到第 2 段的点

第 1 段与第 6 段都委托给 kb.py check，不在本文件里重排 validate/build_index/
regress 的顺序。顺序只在一处定义，手动跑和无人值守跑同一条链。

为什么护栏放在这一层而不是 apply_changeset：
后者只看单个提案合不合规，看不到"今天已经加了多少"。跑飞的形态不是某一条
写错，是连着几天每天加 40 条没人看，两周后库里一半内容没人认得。

回滚的已知边界：reset --hard 收不回未跟踪文件（实测确认）。新建在 ontology/
下的文件回滚后仍在库里，靠下一轮前置检查的 require_git_clean 拦住，
不会静默累积但需要人处置。详见 rollback() 的说明。

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

def preflight(guards: dict, skip_agent: bool) -> str | None:
    """开跑前的环境与基线检查。返回 None 表示通过，否则返回失败原因。

    名字刻意不叫 precheck：scripts/precheck.py 查的是提案形状，
    本函数查的是"这台机器现在能不能开跑"。两件事同名会让人以为是同一层。
    """
    r = run([*GIT, "rev-parse", "--is-inside-work-tree"])
    if r.returncode != 0:
        return ("semi-kb 不是 git 仓库。无人值守必须有回滚线——"
                "跑 `git init && git add -A && git commit` 先立基线。")

    if guards.get("require_git_clean", True):
        r = run([*GIT, "status", "--porcelain"])
        # changesets/ 与 logs/ 不算脏：agent 先产出提案再调本脚本是正常顺序
        # （--skip-agent 那条路径就是这样），把它们算进去会让流程一开跑就卡住。
        # ontology/ kb/ 之外的改动不影响回滚的正确性——reset --hard 一样能收。
        dirty = [l for l in r.stdout.strip().splitlines()
                 if l.strip() and not any(
                     p in l for p in ("changesets/", "logs/", "build/"))]
        if dirty:
            return ("工作区有未提交的改动，没有干净的回滚点：\n    " +
                    "\n    ".join(dirty[:5]) +
                    "\n  先提交或撤销。自动化不该在别人没存盘的工作上叠改动。")

    for s in ("kb.py", "validate.py", "build_index.py", "regress.py",
              "apply_changeset.py", "ask.py", "precheck.py"):
        if not (C.ROOT / "scripts" / s).is_file():
            return f"缺少 scripts/{s}"
    if not list((C.ROOT / "tests").glob("questions-*.txt")):
        return "tests/ 下没有问题夹具，回归守门形同虚设"

    # 基线必须自己就是干净的。在坏库上做自动扩充，事后分不清问题是本来就有
    # 还是这次加进去的——而分不清就意味着不敢回滚也不敢保留。
    # 用 --quick 跳过 build_index：此刻只需知道基线好不好，不需要重建索引，
    # 重建留给合并之后那次。
    r = py("kb.py", "check", "--quick", timeout=1800)
    if r.returncode != 0:
        return ("基线自己就没过，先修库再谈自动扩充：\n"
                + "\n".join("    " + l for l in
                            r.stdout.strip().splitlines()[-10:]))

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
8. **今天的日期是 {today}。** 提案文件名用 {today_compact}-<主题>.yaml，
   changeset.created_at 与所有 provenance.created_at 一律写 {today}。
   不要从既有 changesets 的文件名往后推一天——那会让时间线每轮偏移一天，
   越跑越远，事后完全无法按日期审计知识是什么时候进来的。日期以本行为准。

跑完只回一句话：产出了几个提案文件、各自新增多少条、选题是什么。
"""


def run_agent(max_ent: int) -> tuple[bool, str]:
    # 日期必须显式注入。不注入时 agent 只能从既有 changesets 的文件名推断，
    # 实测会按"上一份是 0827，那这份就 0828"每轮 +1，时间线逐日漂移，
    # 而 created_at 一旦写错就失去按日期审计知识来源的能力。
    now = datetime.now()
    task = AGENT_TASK.format(max_ent=max_ent,
                             today=f"{now:%Y-%m-%d}",
                             today_compact=f"{now:%Y%m%d}")
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


# ---------- 5b. 提案确定性闸门 ----------

def gate_pending() -> str | None:
    """落库前对 pending 提案跑 precheck.py。约 2 秒，挡的是整轮白跑。

    位置在 apply_changeset.py 之前、任何内容合并之前，所以失败可以直接返回
    而不必回滚。这一层专治两类无人值守下最贵的失败：

      顶层键写错 —— apply_changeset 会静默跳过并以退出码 0 报成功，
                    日志只显示"自动放行 0 条"。定时任务看到 0 就以为成功了，
                    实际每天空跑。precheck 把它变成显式 ERROR。
      格式违规 —— 进了 apply 才发现就要 rollback 整轮，丢掉的是
                  上游几十分钟的 agent 时间。

    只在 ERROR 时阻断。WARN 是"值得看一眼"而非"不合规"，
    无人值守时因为 warn 停掉整轮太激进，记进日志由人事后看。
    """
    r = py("precheck.py")
    out = r.stdout.strip()
    log(out[-1200:] if out else "(precheck 无输出)")
    if r.returncode == 2:
        return f"precheck 用法/文件错误（退出码 2）：{out[-300:]}"
    if r.returncode != 0:
        return "提案有 ERROR 级问题，不应落库"
    return None


# ---------- 6. 验证 ----------

def verify() -> str | None:
    """合并后的整体验证。委托给 kb.py check，不自己编排。

    这里曾经手写 validate -> build_index -> regress 三步。委托出去有两个理由：

    顺序只在一处定义。validate 必须在 build_index 之前（反了就是拿旧索引校验
    新本体，不报错但结论过期），这个约束原先同时写在本函数和 kb.py 里，
    改一处忘一处就会不一致。

    手动跑和无人值守跑同一条链，不会再出现"手动查得过、定时任务查不过"
    这种只能靠读代码解释的差异。

    apply_changeset.py 内部已经跑过一次 validate + build_index（它的合并是
    原子的：追加后立即校验、失败即回滚）。这里再跑一遍不多余——apply 保证的是
    "合并本身没把库弄坏"，而这里要确认的是全库最终状态：build/ 与本体一致、
    问题夹具仍然全通。两者的判定范围不同。
    """
    r = py("kb.py", "check", timeout=1800)
    log(r.stdout.strip()[-900:])
    if r.returncode != 0:
        return "合并后全链验证失败（validate / build_index / regress 之一）"
    return None


# ---------- 2 / 7. 快照与回滚 ----------

def snapshot() -> str:
    return run([*GIT, "rev-parse", "HEAD"]).stdout.strip()


def rollback(point: str, why: str) -> None:
    """把已跟踪文件恢复到 point。只在真的合并过内容之后调。

    刻意不碰 changesets/：提案是数据，可能是人手工放进去的，
    合并失败时更需要留着看为什么失败。`git clean -fd -- changesets` 会把它删掉。
    也刻意不 clean 未跟踪文件：新建的实体文件如果没进索引，留着比删掉安全。

    要清楚这个选择的代价，它不是无害的：**reset --hard 收不回未跟踪文件**。
    实测确认过——新建一个 ontology/ 下的文件后 reset，实体数仍是 241 而非 240，
    该文件还在，仍会被 validate 与 build_index 读到。所以"回滚完成"不等于
    "库回到了开跑前的状态"。

    兜底靠的是下一次前置检查：未跟踪的 ontology/ 文件会让 require_git_clean
    拦住整轮（已实测 git status 能看到它、dirty 过滤不会漏）。也就是说
    污染不会静默累积，但会挡住下一次自动运行，需要人来处置。
    这是有意的取舍：留证据 + 挡下一轮，好过悄悄删掉一份可能有价值的产出。
    """
    log(f"!! {why}")
    log(f"回滚已跟踪文件到 {point[:8]}")
    log("注意：未跟踪文件与 changesets/ 保留，reset --hard 收不回它们——"
        "若下面列出了 ontology/ 或 kb/ 下的文件，库并未真正回到开跑前状态，"
        "需人工处置，否则下一轮前置检查会被拦住。")
    run([*GIT, "reset", "--hard", point])
    r = run([*GIT, "status", "--porcelain"])
    left = [l for l in r.stdout.strip().splitlines() if l.startswith("??")]
    if left:
        log("回滚后仍有未跟踪文件，需人工处置：")
        for l in left[:8]:
            log(f"    {l}")


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

    why = preflight(guards, a.skip_agent)
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
    if pending:
        gate = gate_pending()
        if gate:
            # 刻意不 rollback：此刻还没合并任何内容，没有要撤的东西。
            # reset --hard 在这里只会破坏——提案与诊断信息都还有价值。
            log(f"!! 提案确定性检查未过：{gate}")
            log("未落库、未改动已跟踪文件。提案留在 pending/ 待修。")
            return 1
    if not pending:
        # 不回滚。没合并过任何内容，没有要撤的东西；而 reset --hard 在这里
        # 只会破坏——比如删掉 agent 中途写坏但还有诊断价值的文件。
        log("无事可做（没有待落库的提案）。不算失败，也不动任何文件。")
        r = run([*GIT, "status", "--porcelain"])
        if r.stdout.strip():
            log("注意：工作区有改动但没有提案，agent 可能中途失败了：")
            for l in r.stdout.strip().splitlines()[:8]:
                log(f"    {l}")
        return 0

    if a.dry_run:
        log("--dry-run：到此为止。提案留在 pending/，不落库、不改文件、不回滚。")
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
