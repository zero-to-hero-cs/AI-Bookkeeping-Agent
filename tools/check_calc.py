# -*- coding: utf-8 -*-
"""
check_calc.py —— 给 calc.exe 对答案的自检工具

用法：
    python tools/check_calc.py

它会：
    1. 读 data/data.txt，用 Python 算出「应该是多少」
    2. 按几种场景写 query.txt，调 calc.exe，读 result.txt
    3. 把两边数字逐条对比，打印 PASS / FAIL

C 那边改一次就跑一次，全绿才算算账模块没问题。
"""
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

# 复用 UI 里的结果解析函数，保证「判卷标准」和界面显示完全一致
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ui                                     # noqa: E402

BASE_DIR = ui.BASE_DIR
DATA_TXT = ui.DATA_TXT
QUERY_TXT = ui.QUERY_TXT
RESULT_TXT = ui.RESULT_TXT
BUDGET_TXT = ui.BUDGET_TXT
CALC_EXE = ui.CALC_EXE


def read_ledger():
    """读账本，返回 [(日期, 类别, 金额), ...]"""
    return ui.read_data()


def expected(rows, keyword, start, end):
    """算标准答案：(总金额, 笔数, {分类: 小计})"""
    total = 0.0
    count = 0
    per_category = {}
    for day, category, amount in rows:
        # 没有日期的老数据（两列格式）不参与过滤，与 calc.c 的规则一致
        if day and not (start <= day <= end):
            continue
        per_category[category] = per_category.get(category, 0.0) + amount
        if keyword == "ALL" or category == keyword:
            total += amount
            count += 1
    return round(total, 2), count, {k: round(v, 2) for k, v in per_category.items()}


def run_calc(exe, keyword, start, end, budget=None):
    """写 query.txt（可选预算），调 calc.exe，返回解析后的结果"""
    with open(QUERY_TXT, "w", encoding="utf-8", newline="\n") as f:
        f.write("%s\n%s\n%s\n" % (keyword, start, end))

    if budget is None:
        try:
            BUDGET_TXT.unlink()
        except OSError:
            pass
    else:
        with open(BUDGET_TXT, "w", encoding="utf-8", newline="\n") as f:
            f.write("%.2f\n" % budget)

    try:
        RESULT_TXT.unlink()
    except OSError:
        pass

    proc = subprocess.run([str(exe)], cwd=str(BASE_DIR),
                          capture_output=True, timeout=30)
    if proc.returncode != 0:
        return None, "calc.exe 返回码 %d：%s" % (
            proc.returncode, proc.stderr.decode("utf-8", "replace")[:200])
    if not RESULT_TXT.exists():
        return None, "calc.exe 没有生成 result.txt"
    return ui.parse_result(RESULT_TXT.read_text(encoding="utf-8", errors="replace")), ""


def near(a, b, tol=0.011):
    """金额比较，容忍浮点误差"""
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


def main():
    print("=" * 60)
    print("  calc.exe 对答案自检")
    print("=" * 60)

    ui.DATA_DIR.mkdir(parents=True, exist_ok=True)

    exe = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else CALC_EXE
    if not exe.exists():
        print("  [X] 没找到 calc.exe，先编译：gcc calc.c -o calc.exe")
        return 1

    rows = read_ledger()
    if not rows:
        print("  [X] data/data.txt 是空的，先记账或复制 examples/示例账本.txt")
        return 1
    print("  账本：%d 条记录\n" % len(rows))

    # 备份用户自己设的预算，跑完要还原回去
    saved_budget = None
    if BUDGET_TXT.exists():
        try:
            saved_budget = BUDGET_TXT.read_text(encoding="utf-8", errors="replace")
        except OSError:
            saved_budget = None

    # 场景：全部 / 本月 / 上个月 / 某个分类
    today = date.today()
    month_start = today.replace(day=1)
    last_end = month_start - timedelta(days=1)         # 上个月最后一天
    last_start = last_end.replace(day=1)               # 上个月第一天

    scenarios = [
        ("全部记录", "ALL", "0000-01-01", "9999-12-31"),
        ("本月", "ALL", month_start.isoformat(), today.isoformat()),
        ("上个月", "ALL", last_start.isoformat(), last_end.isoformat()),
        ("交通分类", "交通", "0000-01-01", "9999-12-31"),
    ]

    failures = 0
    for name, keyword, start, end in scenarios:
        want_total, want_count, want_cats = expected(rows, keyword, start, end)
        parsed, error = run_calc(exe, keyword, start, end)
        if parsed is None:
            print("  [X] %-8s %s" % (name, error))
            failures += 1
            continue

        got_warning, got_total, got_cats = parsed
        ok_total = near(got_total, want_total) if want_count else near(got_total or 0, 0)
        ok_cats = True
        for cat, value in want_cats.items():
            found = dict(got_cats).get(cat)
            if found is None or not near(found, value):
                ok_cats = False
                break

        status = "PASS" if (ok_total and ok_cats) else "FAIL"
        if status == "FAIL":
            failures += 1
        print("  [%s] %-8s 期望总计 %10.2f（%d 笔）  实际 %10s" %
              (status, name, want_total, want_count, got_total or "0"))
        if not ok_cats:
            print("         分类对不上：期望 %s" % want_cats)
            print("                     实际 %s" % dict(got_cats))

    # 超支告警测试
    all_total, _, _ = expected(rows, "ALL", "0000-01-01", "9999-12-31")
    if all_total > 0:
        parsed, error = run_calc(exe, "ALL", "0000-01-01", "9999-12-31", budget=all_total / 2)
        if parsed is None:
            print("  [X] 超支告警    %s" % error)
            failures += 1
        else:
            warned = parsed[0]
            print("  [%s] %-8s 预算设一半，期望出现 [WARNING]" %
                  ("PASS" if warned else "FAIL", "超支告警"))
            if not warned:
                failures += 1

        parsed, error = run_calc(exe, "ALL", "0000-01-01", "9999-12-31", budget=all_total * 2)
        if parsed is not None:
            quiet = not parsed[0]
            print("  [%s] %-8s 预算设两倍，期望不出现 [WARNING]" %
                  ("PASS" if quiet else "FAIL", "额度充足"))
            if not quiet:
                failures += 1

    # 收尾：还原用户原来的预算（测试期间被改过）
    try:
        if saved_budget is None:
            BUDGET_TXT.unlink()
        else:
            with open(BUDGET_TXT, "w", encoding="utf-8", newline="\n") as f:
                f.write(saved_budget if saved_budget.endswith("\n") else saved_budget + "\n")
    except OSError:
        pass

    print()
    print("=" * 60)
    if failures:
        print("  结果：%d 项不通过，检查 calc.c" % failures)
    else:
        print("  结果：全部通过，算账模块没问题")
    print("=" * 60)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
