# -*- coding: utf-8 -*-
"""
ui.py —— 终端交互、预算设置与饼图

界面五个功能：
    1. 自然语言记账   写 data/input.txt → 调 main.py → 读 data/reply.txt
    2. 查账与超支提醒 写 input.txt → 调 main.py（写 query.txt）→ 调 calc.exe → 读 result.txt
    3. 设置预算       写 data/budget.txt
    4. 查看消费饼图   读 data/data.txt 画图
    5. 退出

运行：python ui.py
"""
import math
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

# 控制台改用 UTF-8，避免 GBK 终端打印中文时报错
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# 让 Windows 控制台支持颜色
if os.name == "nt":
    os.system("")

# 路径统一基于本文件所在目录
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

INPUT_TXT = DATA_DIR / "input.txt"
REPLY_TXT = DATA_DIR / "reply.txt"
QUERY_TXT = DATA_DIR / "query.txt"
BUDGET_TXT = DATA_DIR / "budget.txt"
DATA_TXT = DATA_DIR / "data.txt"
RESULT_TXT = DATA_DIR / "result.txt"

MAIN_PY = BASE_DIR / "main.py"
CALC_EXE = BASE_DIR / "calc.exe"
CHART_PNG = BASE_DIR / "消费分类饼图.png"
CONFIG_TXT = BASE_DIR / "config.txt"      # 填 API Key 的地方（不进 Git）

COLORS = ["#FF9999", "#66B2FF", "#99FF99", "#FFCC99", "#C2C2F0"]

# 判断一句话是不是在查账，用来给用户补全说法
QUERY_HINTS = ("多少", "一共", "总共", "合计", "统计", "汇总", "查")

MISSING_CALC_HINT = (
    "没有找到 calc.exe（查账汇总需要它）。三种解决办法：\n"
    "      · 装了 MinGW/gcc：gcc calc.c -o calc.exe -Wall -O2\n"
    "      · 装了 Visual Studio：在「开发者命令提示符」里执行  cl /utf-8 /O2 calc.c\n"
    "      · 用 Docker：镜像构建时会自动编译，直接  docker run -it --rm jizhang\n"
    "    （记账、追问、预算、饼图不需要它，可以照常用）"
)


# ----------------------------------------------------------------------
# 彩色输出
# ----------------------------------------------------------------------
def _paint(text, code):
    """给文字加终端颜色代码"""
    if os.environ.get("NO_COLOR"):
        return text
    return "\033[%sm%s\033[0m" % (code, text)


def red(text):
    return _paint(text, "31")


def green(text):
    return _paint(text, "32")


def yellow(text):
    return _paint(text, "33")


def bold(text):
    return _paint(text, "1")


def print_ok(msg):
    """打印成功信息"""
    print(green("[OK] ") + msg)


def print_warn(msg):
    """打印提示信息"""
    print(yellow("[!] ") + msg)


def print_err(msg):
    """打印错误信息"""
    print(red("[X] ") + msg)


def line():
    """打印分隔线"""
    print("-" * 50)


# ----------------------------------------------------------------------
# 调外部程序
# ----------------------------------------------------------------------
def run_python_script(script_path, cwd, extra_args=None):
    """运行另一个 py 脚本，返回 (是否成功, 标准输出, 标准错误)"""
    cmd = [sys.executable, str(script_path)]
    if extra_args:
        cmd.extend(extra_args)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),                 # 指定工作目录，子程序才能找到 data/
            capture_output=True,
            timeout=90,
        )
        out = proc.stdout.decode("utf-8", "replace")
        err = proc.stderr.decode("utf-8", "replace")
        return proc.returncode == 0, out, err
    except subprocess.TimeoutExpired:
        return False, "", "main.py 超过 90 秒没有返回，可能是网络卡住了"
    except OSError as exc:
        return False, "", "启动失败：%s" % exc


def run_calc():
    """运行 calc.exe 算账，返回 (是否成功, 输出或错误信息)"""
    if not CALC_EXE.exists():
        return False, MISSING_CALC_HINT
    try:
        RESULT_TXT.unlink()          # 先删旧的，calc.exe 没产出时不会误读上一次结果
    except OSError:
        pass
    try:
        proc = subprocess.run(
            [str(CALC_EXE)],              # 用完整路径，避免调起系统的计算器
            cwd=str(BASE_DIR),
            capture_output=True,
            timeout=30,
        )
        out = proc.stdout.decode("utf-8", "replace")
        err = proc.stderr.decode("utf-8", "replace")
        if proc.returncode != 0:
            return False, "calc.exe 返回码 %d：%s" % (proc.returncode, (err or out).strip()[:300])
        return True, (out + err).strip()
    except subprocess.TimeoutExpired:
        return False, "calc.exe 超过 30 秒没有返回"
    except OSError as exc:
        return False, "calc.exe 启动失败：%s" % exc


# ----------------------------------------------------------------------
# 读 main.py 的回复
# ----------------------------------------------------------------------
def read_reply():
    """读 reply.txt，返回 (类型, 正文)"""
    if not REPLY_TXT.exists():
        return "", ""
    try:
        raw = REPLY_TXT.read_text(encoding="utf-8", errors="replace").strip()
    except OSError as exc:
        return "", "读取 reply.txt 失败：%s" % exc
    if not raw:
        return "", ""

    lines = raw.splitlines()
    if lines[0].startswith("TYPE="):
        kind = lines[0][5:].strip()
        body = "\n".join(lines[1:]).strip()
        return kind, body
    return "ok", raw            # 没有 TYPE 头的老格式


def ask_main(user_text):
    """把一句话交给 main.py 处理，返回 (类型, 正文)"""
    try:
        with open(INPUT_TXT, "w", encoding="utf-8", newline="\n") as f:
            f.write(user_text.strip() + "\n")
    except OSError as exc:
        return "error", "写入 input.txt 失败：%s" % exc

    # 先删旧回复，这样能判断本次有没有真的产生结果
    try:
        REPLY_TXT.unlink()
    except (FileNotFoundError, OSError):
        pass

    _ok, out, err = run_python_script(MAIN_PY, BASE_DIR)
    kind, body = read_reply()
    if not body:
        detail = (err or out).strip()
        detail = detail[:400] if detail else "没有任何输出"
        return "error", "main.py 没有给出回复，可能崩溃了。\n控制台输出：\n" + detail
    return kind, body


# ----------------------------------------------------------------------
# 查账结果解析
# ----------------------------------------------------------------------
def parse_result(text):
    """解析 result.txt，返回 (是否超支, 总计, [(分类, 金额), ...])"""
    raw = (text or "").strip()
    # C 那边只要表达"超支"就算，写法宽容一点：[WARNING] / WARNING / 【警告】/ 超支 都认
    warning = bool(re.search(r"warning|警告|超支|超预算", raw, re.IGNORECASE))

    # 去掉告警标记，统一分隔符
    body = re.sub(r"[\[【(（]?\s*warning\s*[\]】)）]?", " ", raw, flags=re.IGNORECASE)
    body = re.sub(r"[\[【(（]?\s*(警告|超支|超预算)\s*[\]】)）]?", " ", body)
    for ch in ("，", "；", "、", ";", "\n"):
        body = body.replace(ch, ",")

    total = ""
    categories = []
    for item in body.split(","):
        item = item.strip().replace("：", ":")
        if "=" in item and ":" not in item:
            item = item.replace("=", ":", 1)      # 兼容 总计=520 这种写法
        if ":" not in item:
            continue
        key, _, value = item.partition(":")
        key, value = key.strip(), value.strip()
        if key in ("总计", "合计", "总额", "总支出"):
            total = value
        elif key:
            categories.append((key, value))
    return warning, total, categories


def in_current_month():
    """这次查询的区间是不是"本月"（预算按月，只能跟本月比）"""
    if not QUERY_TXT.exists():
        return True
    try:
        lines = QUERY_TXT.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return True
    if len(lines) < 3:
        return True
    start = lines[1].strip()
    today = date.today()
    return start[:7] == today.strftime("%Y-%m")


def read_budget():
    """读预算金额，没有则返回 None"""
    if not BUDGET_TXT.exists():
        return None
    try:
        text = BUDGET_TXT.read_text(encoding="utf-8-sig", errors="replace").strip()
    except OSError:
        return None
    try:
        value = float(text)
        return value if value > 0 else None
    except ValueError:
        return None


def read_query_keyword():
    """读 query.txt 第一行，即这次查询的类别（ALL 表示全部）"""
    if not QUERY_TXT.exists():
        return "ALL"
    try:
        lines = QUERY_TXT.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "ALL"
    return lines[0].strip() if lines and lines[0].strip() else "ALL"


def show_result(keyword="ALL"):
    """读 result.txt 并打印汇总，超支时红色告警"""
    if not RESULT_TXT.exists():
        print_err("calc.exe 没有生成 result.txt，检查一下 data/data.txt 是否存在。")
        return

    raw = RESULT_TXT.read_text(encoding="utf-8", errors="replace")
    warning, total, categories = parse_result(raw)

    title = "  账单汇总" if keyword == "ALL" else "  账单汇总（%s）" % keyword
    line()
    print(bold(title))
    line()

    # 一条记录都没有时，告诉用户怎么造数据，别让人以为程序坏了
    if not categories:
        print_warn("这段时间没有任何记录（总支出 0 元）")
        print("    可以这样做：")
        print("      1) 选 1 自己记一笔，比如输入「打车32」")
        print("      2) 或者先灌一份示例数据，看到统计效果：")
        print("         copy examples\\示例账本.txt data\\data.txt")
        line()
        return

    print("  总支出：%s 元" % (total or "0"))
    if categories:
        shown = [item for item in categories if keyword == "ALL" or item[0] == keyword]
        if not shown:
            # 查某个分类但那个分类没记录，不能把别的分类列出来
            line()
            print_warn("这段时间没有「%s」的记录" % keyword)
            line()
            return
        print()
        for name, amount in shown:
            print("    %-4s %10s 元" % (name, amount))
    line()

    # 预算对比：预算按月设置，只有查的是本月才比较
    budget = read_budget()
    if not in_current_month():
        if budget:
            print(yellow("  （预算是按月设置的，本次查询区间跨月，不做预算比较）"))
        line()
        return
    if warning:
        print(red(bold("  [!!] 警告：这个统计区间已超出预算！")))
        if budget:
            try:
                over = float(total) - budget
                print(red("       预算 %.2f 元，已超支 %.2f 元" % (budget, over)))
            except (TypeError, ValueError):
                pass
        print(red("       建议：接下来少点两顿外卖，下个月把预算调高一点也可以。"))
    elif budget:
        try:
            left = budget - float(total)
            print(green("  预算正常，还剩 %.2f 元可用（预算 %.2f 元）" % (left, budget)))
        except (TypeError, ValueError):
            print(green("  预算正常。"))
    else:
        print(yellow("  还没有设置预算，选 3 可以设置本月预算。"))
    line()


# ----------------------------------------------------------------------
# 功能 1：自然语言记账
# ----------------------------------------------------------------------
def add_expense():
    """输入一句话记账，被追问时可以直接继续补充"""
    print(yellow("提示：直接回车可以返回主菜单"))
    while True:
        text = input("账单> ").strip()
        if not text:
            return

        print("AI 正在思考……")
        kind, body = ask_main(text)

        if kind == "ask":
            print_warn(body)
            continue                  # 还在追问，接着让用户补充
        if kind == "query":
            print_ok(body)            # 用户其实在查账，直接查给他看
            ok, detail = run_calc()
            if not ok:
                print_err(detail)
                return
            show_result(read_query_keyword())
            return
        if kind == "refuse":
            print_warn(body)
            return
        if kind == "error":
            print_err(body)
            return

        print_ok(body)
        return


# ----------------------------------------------------------------------
# 功能 2：查账与超支提醒
# ----------------------------------------------------------------------
def check_expenses():
    """按条件查账，并显示是否超支"""
    if not CALC_EXE.exists():
        print_err(MISSING_CALC_HINT)
        return

    query = input("想看什么？（直接回车=看本月总账，或输入「交通」「上个月餐饮」）> ").strip()

    # 先让 main.py 解析成 query.txt（直接回车就让 main 写成本月总账）
    prompt_text = query if query else "这个月一共花了多少"
    if query and not any(word in query for word in QUERY_HINTS):
        # 用户只打了"交通"这种短词，补上"花了多少"，避免被当成记账
        prompt_text = query + " 花了多少"

    print("正在理解你的查询……")
    kind, body = ask_main(prompt_text)

    # 只接受 TYPE=query。如果被当成记账了，必须停下来，不能让用户看到旧结果
    if kind != "query":
        print_warn(body)
        if kind == "ok":
            print(yellow("   这句被当成记账了。想查账可以换个说法：「上个月交通花了多少」"))
        elif kind == "ask":
            print(yellow("   可以试试：「上个月交通花了多少」"))
        return
    print_ok(body)                    # 例：查询条件：交通，时间：上个月

    ok, detail = run_calc()
    if not ok:
        print_err(detail)
        return

    show_result(read_query_keyword())


# ----------------------------------------------------------------------
# 功能 3：设置预算
# ----------------------------------------------------------------------
def set_budget():
    """输入本月预算并保存到 budget.txt"""
    raw = input("请输入本月预算金额（例如 3000）> ").strip()
    if not raw:
        print_warn("没有输入，已取消。")
        return
    try:
        value = float(raw.replace("元", "").strip())
        if value <= 0:
            raise ValueError
    except ValueError:
        print_err("请输入一个大于 0 的数字，比如 3000 或 3000.5")
        return

    try:
        with open(BUDGET_TXT, "w", encoding="utf-8", newline="\n") as f:
            f.write("%.2f\n" % value)
    except OSError as exc:
        print_err("写入 budget.txt 失败：%s" % exc)
        return

    print_ok("预算设置成功：%.2f 元" % value)

    # 先把查询条件刷成"本月总账"，否则会沿用上一次查询留下的 query.txt
    ask_main("这个月一共花了多少")

    ok, _ = run_calc()
    if ok and RESULT_TXT.exists():
        warning, total, _categories = parse_result(RESULT_TXT.read_text(encoding="utf-8", errors="replace"))
        try:
            spent = float(total or 0)
        except ValueError:
            spent = 0.0
        if spent:
            print("   本月已花费 %.2f 元，剩余 %.2f 元" % (spent, value - spent))
            if warning:
                print(red("   [!!] 当前已经超支了！"))


# ----------------------------------------------------------------------
# 功能 4：消费分类饼图
# ----------------------------------------------------------------------
def read_data():
    """读账本，返回 [(日期, 类别, 金额), ...]，兼容两列和三列"""
    expenses = []
    if not DATA_TXT.exists():
        return expenses
    try:
        content = DATA_TXT.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return expenses

    for raw_line in content.splitlines():
        raw_line = raw_line.strip()
        if not raw_line or raw_line.startswith("#"):
            continue
        parts = [p.strip() for p in raw_line.split(",")]
        if len(parts) >= 3:
            day, category, amount_text = parts[0], parts[1], parts[2]
        elif len(parts) == 2:
            day, category, amount_text = "", parts[0], parts[1]
        else:
            continue
        try:
            value = float(amount_text)
        except ValueError:
            continue
        # nan / inf 会让后续求和、画图全崩，直接跳过
        if not math.isfinite(value) or value <= 0:
            continue
        expenses.append((day, category, value))
    return expenses


def collect_totals():
    """
    按分类汇总账本，返回 (分类->金额, 统计口径说明)。
    有日期就只算本月；本月没记录就退回全部。
    """
    all_expenses = read_data()
    if not all_expenses:
        return {}, ""

    month_prefix = date.today().strftime("%Y-%m")
    dated = [item for item in all_expenses if item[0]]
    this_month = [item for item in dated if item[0].startswith(month_prefix)]

    if this_month:
        expenses, scope = this_month, "本月"
    elif dated:
        expenses, scope = dated, "全部（本月暂无记录）"
    else:
        expenses, scope = all_expenses, "全部"

    totals = {}
    for _day, category, amount in expenses:
        totals[category] = totals.get(category, 0.0) + amount
    return totals, scope


def show_chart():
    """按分类统计并生成饼图"""
    totals, scope = collect_totals()
    if not totals:
        print_warn("还没有记账数据，先去选 1 记一笔吧。")
        return

    try:
        import matplotlib
        matplotlib.use("Agg")         # 直接存图，不弹窗
        import matplotlib.pyplot as plt
    except ImportError:
        print_err("没有安装 matplotlib，先执行：pip install matplotlib")
        print(yellow("   （只影响饼图，记账和查账不受影响）"))
        return

    # 中文字体
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    labels, values, colors = [], [], []
    for index, (category, amount) in enumerate(
            sorted(totals.items(), key=lambda item: item[1], reverse=True)):
        if amount <= 0:
            continue                  # 跳过 0 元分类，避免标签重叠
        labels.append(category)
        values.append(amount)
        colors.append(COLORS[index % len(COLORS)])

    try:
        plt.figure(figsize=(7, 7))
        plt.pie(values, labels=labels, colors=colors, autopct="%1.1f%%",
                startangle=90, counterclock=False)
        plt.title("消费分类占比（%s）" % scope)
        plt.tight_layout()
        plt.savefig(CHART_PNG, dpi=130, bbox_inches="tight")
        plt.close()
    except Exception as exc:          # noqa: BLE001
        print_err("画图失败：%s" % exc)
        return

    print_ok("饼图已保存：%s" % CHART_PNG.name)
    for category, amount in sorted(totals.items(), key=lambda item: item[1], reverse=True):
        print("    %-4s %10.2f 元" % (category, amount))

    # 自动打开图片
    try:
        if os.name == "nt":
            os.startfile(str(CHART_PNG))  # noqa: S606
        else:
            print("   用看图软件打开：%s" % CHART_PNG)
    except Exception:                 # noqa: BLE001
        print("   请手动打开：%s" % CHART_PNG)


# ----------------------------------------------------------------------
# 功能 5：设置 API Key
# ----------------------------------------------------------------------
def read_config():
    """读 config.txt，返回 {key: value}"""
    config = {}
    if not CONFIG_TXT.exists():
        return config
    try:
        text = CONFIG_TXT.read_text(encoding="utf-8-sig")
    except OSError:
        return config
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        config[key.strip().lower()] = value.strip().strip('"').strip("'")
    return config


def write_config(config):
    """把配置写回 config.txt，顺带写上说明"""
    lines = [
        "# API Key 配置（这个文件不会提交到 Git，可以放心填）",
        "# 也可以在界面里用「5. 设置 API Key」自动写入",
        "",
    ]
    for key in ("api_key", "base_url", "model"):
        if config.get(key):
            lines.append("%s=%s" % (key, config[key]))
    with open(CONFIG_TXT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def test_api():
    """调 main.py --test-api，看 Key 通不通"""
    print("正在测试连接……")
    ok, out, err = run_python_script(MAIN_PY, BASE_DIR, extra_args=["--test-api"])
    for line in (out + err).strip().splitlines():
        if line.strip():
            print("   " + line.strip())
    if ok:
        print_ok("大模型连接正常，以后记账会用大模型解析")
    else:
        print_err("没连上，原因见上面几行；看不懂就整段发给队长")


def set_api_key():
    """设置大模型 API Key，写进 config.txt"""
    config = read_config()
    current = config.get("api_key", "")
    if current:
        masked = (current[:6] + "*" * 8) if len(current) > 6 else "***"
        print("当前 Key：%s（已配置）" % masked)
    else:
        print(yellow("当前还没有配置 Key，用的是本地规则（离线）模式。"))

    print("在 https://platform.deepseek.com 创建一个 Key，复制过来粘贴就行。")
    key = input("粘贴 API Key（直接回车=不改）> ").strip()
    if not key:
        print_warn("没有输入，保持原样。")
        return
    if not key.isascii():
        print_err("里面有中文或全角字符（多半是输入法没切），请重新粘贴。")
        return
    if len(key) < 10:
        print_err("这个太短了，正常的 Key 是 sk- 开头的一长串。")
        return

    config["api_key"] = key
    config.setdefault("base_url", "https://api.deepseek.com")
    config.setdefault("model", "deepseek-chat")
    try:
        write_config(config)
    except OSError as exc:
        print_err("写 config.txt 失败：%s" % exc)
        return

    print_ok("已保存到 config.txt（这个文件不会提交到 Git）")
    if input("现在测试一下能不能连上？(y/N) > ").strip().lower() in ("y", "yes", "是"):
        test_api()


# ----------------------------------------------------------------------
# 主菜单
# ----------------------------------------------------------------------
def main():
    """显示菜单，循环处理用户选择"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not read_config().get("api_key"):
        print(yellow("提示：还没配置 API Key，现在是离线模式（选 5 可以设置，不配也能正常记账）。"))

    first_run = True
    while True:
        print()
        line()
        print(bold("        AI 智能记账助手"))
        line()
        print("  1. 自然语言记账")
        print("  2. 查账与超支提醒")
        print("  3. 设置预算")
        print("  4. 查看消费饼图")
        print("  5. 设置 API Key")
        print("  6. 退出")
        line()

        # 只在第一次显示用法示例，免得每轮都刷屏
        if first_run:
            print("  可以这样说：")
            print("    记账 → 打车32 ／ 昨天午饭吃了28 ／ 买菜二十五")
            print("    查账 → 这个月一共花了多少 ／ 上个月交通花了多少")
            line()
            first_run = False

        try:
            choice = input("请选择功能 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        try:
            if choice == "1":
                add_expense()
            elif choice == "2":
                check_expenses()
            elif choice == "3":
                set_budget()
            elif choice == "4":
                show_chart()
            elif choice == "5":
                set_api_key()
            elif choice == "6":
                print("再见！")
                break
            else:
                print_warn("请输入 1 到 6 之间的数字。")
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break
        except Exception as exc:      # noqa: BLE001  出错只提示，不退出
            print_err("出错了：%s" % exc)


if __name__ == "__main__":
    main()
