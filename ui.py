print("UI ready")
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""智能体查账项目主程序。"""
import subprocess
from pathlib import Path

FILE= Path("data.txt")
Catgories= ["餐饮", "交通", "购物", "娱乐", "其他"]

def set_budget():
    raw=input("请输入本月预算金额：").strip()
    try:
        budget = float(raw)
        if budget < 0:
            raise ValueError
    except ValueError:
        print("输入无效：预算必须是大于等于 0 的数字。")
        return

    lines=FILE.read_text(encoding="utf-8").splitlines() if FILE.exists() else []
    kept=[line for line in lines if not line.startswith("budget=")]
    kept.insert(0, f"budget={budget:.2f}")
    FILE.write_text("\n".join(kept) + "\n", encoding="utf-8")
    print("预算设置成功")

def add_expense():
    print("可选分类：" + " / ".join(Catgories))
    category = input("请输入消费分类：").strip()
    if category not in Catgories:
        print("输入无效：分类必须是 餐饮/交通/购物/娱乐/其他。")
        return

    raw_amount = input("请输入消费金额：").strip()
    try:
        amount = float(raw_amount)
        if amount <= 0:
            raise ValueError
    except ValueError:
        print("输入无效：金额必须是大于 0 的数字。")
        return

    note = input("请输入备注：").strip()
    with FILE.open("a", encoding="utf-8") as f:
        f.write(f"{category},{amount:.2f},{note}\n")
    print("记账成功")

    result = subprocess.run(["python3", "main.py"], capture_output=True, text=True)
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="")

def check_expenses():
    if not Path("calc.exe").exists():
        print("未找到 calc.exe,请先确保 calc.exe 在当前目录。")
        return

    result = subprocess.run(["python3", "calc.exe"], capture_output=True, text=True)
    output = result.stdout
    if "[WARNING]" in Path("data.txt").read_text(encoding="utf-8"):
        print("\033[91m警告:本月已超支\033[0m")
    print(output, end="")
    if result.stderr:
        print(result.stderr, end="")

def main():
    while True:
        print("\n===== 智能体查账系统 =====")
        print("1. 记账")
        print("2. 查账")
        print("3. 设置预算")
        print("4. 退出")
        xuan= input("请选择功能 1-4 :").strip()

        if xuan== "1":
            add_expense()
        elif xuan== "2":
            check_expenses()
        elif xuan== "3":
            set_budget()
        elif xuan== "4":
            print("已退出，感谢使用！")
            break
        else:
            print("输入无效，请输入 1、2、3 或 4。")

if __name__ == "__main__":
    main()

import matplotlib.pyplot as plt
DATA_FILE = Path("data.txt")
CATEGORIES = ["餐饮", "交通", "购物", "娱乐", "其他"]
COLORS = ["#FF9999", "#66B2FF", "#99FF99", "#FFCC99", "#C2C2F0"]
def read_data():
    budget = None
    expenses = []
    lines = DATA_FILE.read_text(encoding="utf-8").splitlines() if DATA_FILE.exists() else []
    for line in lines:
        line = line.strip()
        if line.startswith("budget="):
            try:
                budget = float(line.split("=", 1)[1])
            except ValueError:
                budget = None
        elif line and line != "[WARNING]":
            parts = [x.strip() for x in line.split(",")]
            if len(parts) == 3 and parts[0] in CATEGORIES:
                try:
                    expenses.append((parts[0], float(parts[1]), parts[2]))
                except ValueError:
                    pass
    return budget, expenses, lines


def write_warning(lines):
    lines = [line for line in lines if line.strip() != "[WARNING]"]
    lines.append("[WARNING]")
    DATA_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

def remove_warning(lines):
    lines = [line for line in lines if line.strip() != "[WARNING]"]
    DATA_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

def draw_pie(totals):
    from matplotlib.font_manager import FontProperties
    font_path = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    font = FontProperties(fname=font_path)
    values = [totals.get(category, 0.0) for category in CATEGORIES]
    plt.figure(figsize=(7, 7))
    wedges, texts, autotexts = plt.pie(
        values,
        labels=CATEGORIES,
        colors=COLORS,
        autopct="%1.1f%%",
        startangle=90,
        counterclock=False,
    )
    for text in texts:
        text.set_fontproperties(font)
    for autotext in autotexts:
        autotext.set_fontproperties(font)
    plt.title("本月消费分类占比", fontproperties=font)
    plt.tight_layout()
    plt.savefig("消费分类饼图.png", dpi=150)
    plt.close()
    print("饼图已生成：消费分类饼图.png")