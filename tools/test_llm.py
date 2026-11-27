# -*- coding: utf-8 -*-
"""
test_llm.py —— 用本地假服务验证大模型链路

不联网、不花额度、不需要真 Key。启动一个假的 OpenAI 兼容接口，
让它返回固定 JSON，然后真的去跑 main.py，检查 reply.txt / data.txt / query.txt 对不对。

用法：
    python tools/test_llm.py
"""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 假模型对不同输入的"回答"。故意混进几种脏输出，验证我们的容错
FAKE_REPLIES = {
    "打车32": '{"action":"record","amount":32,"category":"交通","date":"昨天"}',
    "上个月交通花了多少": '{"action":"query","keyword":"交通","time_text":"上个月"}',
    "买衣服": '{"action":"ask","message":"这笔衣服花了多少钱？","draft":{"category":"购物"}}',
    "帮我借贷": '{"action":"refuse","message":"抱歉，我只负责记账和查账哦。"}',
    # 脏输出 1：包了 markdown 代码块、金额带单位、分类是乱写的
    "奶茶18": '```json\n{"action":"record","amount":"18元","category":"莫名其妙","date":"今天"}\n```',
}

FAILURES = []


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            body = {}
        user = ""
        messages = body.get("messages") or []
        if messages:
            user = messages[-1].get("content", "")

        content = None
        for key, reply in FAKE_REPLIES.items():
            if key in user:
                content = reply
                break
        if content is None:
            # 脏输出 2：彻底不是 JSON，验证重试 + 本地规则兜底
            content = "服务器忙，稍后再试"

        payload = {"choices": [{"message": {"content": content}}]}
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


def clean_data():
    """清空 data 目录里的文件（跑之前先拍快照，跑完会还原）"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for path in DATA_DIR.glob("*"):
        try:
            path.unlink()
        except OSError:
            pass


def snapshot_data():
    """跑之前把 data 目录整个存到内存里，避免把用户真实账本洗掉"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return {p.name: p.read_bytes() for p in DATA_DIR.glob("*") if p.is_file()}


def restore_data(saved):
    """跑完之后把 data 目录还原成原样"""
    for path in DATA_DIR.glob("*"):
        if path.is_file() and path.name not in saved:
            try:
                path.unlink()
            except OSError:
                pass
    for name, content in saved.items():
        try:
            (DATA_DIR / name).write_bytes(content)
        except OSError:
            pass


def run_main(text, env):
    global LAST_OUTPUT
    clean_data()
    with open(DATA_DIR / "input.txt", "w", encoding="utf-8", newline="\n") as f:
        f.write(text + "\n")
    proc = subprocess.run([sys.executable, str(BASE_DIR / "main.py")],
                          cwd=str(BASE_DIR), capture_output=True, env=env, timeout=60)
    reply = ""
    reply_file = DATA_DIR / "reply.txt"
    if reply_file.exists():
        reply = reply_file.read_text(encoding="utf-8", errors="replace")
    LAST_OUTPUT = proc.stdout.decode("utf-8", "replace")
    return reply, LAST_OUTPUT, proc.stderr.decode("utf-8", "replace")


LAST_OUTPUT = ""


def read(path):
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def check(name, condition, detail=""):
    if condition:
        print("  [PASS] %s" % name)
    else:
        print("  [FAIL] %s   %s" % (name, detail))
        if LAST_OUTPUT.strip():
            print("         main.py 输出：%s" % " / ".join(
                line for line in LAST_OUTPUT.splitlines() if line.strip()))
        FAILURES.append(name)


def main():
    saved = snapshot_data()
    try:
        return _run_cases()
    finally:
        restore_data(saved)


def _run_cases():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print("假模型服务：http://127.0.0.1:%d\n" % port)

    env = dict(os.environ)
    env["DEEPSEEK_API_KEY"] = "test-key-123"          # 必须是纯 ASCII，否则发不出去
    env["DEEPSEEK_BASE_URL"] = "http://127.0.0.1:%d" % port
    env["PYTHONUTF8"] = "1"

    # 1. 正常记账
    reply, out, _err = run_main("打车32", env)
    data = read(DATA_DIR / "data.txt")
    check("记账：TYPE=ok", reply.startswith("TYPE=ok"), reply[:60])
    check("记账：写进账本且分类金额正确", "交通,32.00" in data, data)
    from datetime import date, timedelta
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    check("记账：日期用的是昨天", yesterday in data, data)

    # 2. 查账
    reply, _out, _err = run_main("上个月交通花了多少", env)
    query = read(DATA_DIR / "query.txt").splitlines()
    check("查账：TYPE=query", reply.startswith("TYPE=query"), reply[:60])
    check("查账：query.txt 三行齐全", len(query) >= 3, query)
    check("查账：类别是交通", query and query[0] == "交通", query)

    # 3. 追问
    reply, _out, _err = run_main("买衣服", env)
    check("追问：TYPE=ask", reply.startswith("TYPE=ask"), reply[:60])
    check("追问：状态已保存", (DATA_DIR / "pending.json").exists())

    # 4. 越界拦截
    reply, _out, _err = run_main("帮我借贷", env)
    check("拦截：TYPE=refuse", reply.startswith("TYPE=refuse"), reply[:60])

    # 5. 模型返回脏数据（代码块 + 带单位金额 + 乱写分类）
    reply, _out, _err = run_main("奶茶18", env)
    data = read(DATA_DIR / "data.txt")
    check("脏输出：仍能记账", reply.startswith("TYPE=ok"), reply[:80])
    check("脏输出：金额 18.00 被正确提取", "18.00" in data, data)
    check("脏输出：非法分类落到「其他」", "其他,18.00" in data, data)

    # 6. 模型返回非 JSON：应重试后走本地规则，不崩
    reply, _out, _err = run_main("这个月一共花了多少钱", env)
    check("坏响应：本地规则兜底，仍返回结果", reply.startswith("TYPE="), reply[:80])
    check("坏响应：没有崩溃（reply 非空）", bool(reply.strip()))

    server.shutdown()

    print()
    if FAILURES:
        print("结果：%d 项不通过 → %s" % (len(FAILURES), FAILURES))
        return 1
    print("结果：全部通过（大模型链路已验证）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
