# -*- coding: utf-8 -*-
"""
main.py —— AI 核心与意图识别

流程：
    读 data/input.txt → 识别意图 → 写 data/data.txt / query.txt / reply.txt

运行：python main.py
配置 Key：$env:DEEPSEEK_API_KEY="sk-xxx"（不配则用本地规则解析）
"""
import json
import os
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

# 控制台改用 UTF-8，避免 GBK 终端打印中文时报错
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

try:
    import requests
except ImportError:
    requests = None

# 路径统一基于本文件所在目录
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

INPUT_TXT = DATA_DIR / "input.txt"        # UI 写 → 本程序读
REPLY_TXT = DATA_DIR / "reply.txt"        # 本程序写 → UI 读
QUERY_TXT = DATA_DIR / "query.txt"        # 本程序写 → calc.c 读
BUDGET_TXT = DATA_DIR / "budget.txt"      # UI 写 → calc.c 读
DATA_TXT = DATA_DIR / "data.txt"          # 本程序写 → calc.c 读
RESULT_TXT = DATA_DIR / "result.txt"      # calc.c 写 → UI 读
PENDING_JSON = DATA_DIR / "pending.json"  # 记录追问时缺的信息

CONFIG_TXT = BASE_DIR / "config.txt"      # 填 API Key 的地方（不进 Git）


def load_config_file():
    """读 config.txt，每行 key=value，# 开头是注释"""
    config = {}
    try:
        text = CONFIG_TXT.read_text(encoding="utf-8-sig")
    except (FileNotFoundError, OSError):
        return config
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        config[key.strip().lower()] = value.strip().strip('"').strip("'")
    return config


# 大模型配置，优先级：环境变量 > config.txt > 默认值
# 队里正常用法：在 ui.py 菜单里选「设置 API Key」，由它来写 config.txt
_CONFIG = load_config_file()
API_KEY = (os.environ.get("DEEPSEEK_API_KEY") or _CONFIG.get("api_key") or "").strip()
API_BASE = (os.environ.get("DEEPSEEK_BASE_URL") or _CONFIG.get("base_url")
            or "https://api.deepseek.com").rstrip("/")
API_URL = API_BASE + "/chat/completions"
API_MODEL = (os.environ.get("DEEPSEEK_MODEL") or _CONFIG.get("model")
             or "deepseek-chat").strip()
API_MAX_RETRY = 2

# Key 会放进 HTTP 头，只能有 ASCII 字符；全角或中文会让 requests 抛一个看不懂的 latin-1 错误
if API_KEY and not API_KEY.isascii():
    print("[main] API Key 里含非 ASCII 字符（全角或中文），已忽略，改用本地规则")
    API_KEY = ""

# 记录这次解析用了大模型还是本地规则，给 --test-api 用
LAST_SOURCE = "local"

CATEGORIES = ["餐饮", "交通", "购物", "娱乐", "其他"]

# reply.txt 格式：第 1 行 TYPE=xx，第 2 行起是正文
KIND_OK, KIND_ASK, KIND_QUERY, KIND_REFUSE, KIND_ERROR = "ok", "ask", "query", "refuse", "error"


# ======================================================================
# 文件读写
# ======================================================================
def ensure_data_dir():
    """确保 data 目录存在"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def read_text(path):
    """读文本文件，不存在返回空串"""
    try:
        return path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return ""


def write_text(path, text):
    """写文本文件，UTF-8 + \\n 换行"""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text if text.endswith("\n") else text + "\n")


def write_reply(text, kind=KIND_OK):
    """写回复文件，供 UI 读取"""
    write_text(REPLY_TXT, "TYPE=%s\n%s" % (kind, text))


def append_record(day, category, amount):
    """账本追加一条：日期,类别,金额"""
    with open(DATA_TXT, "a", encoding="utf-8", newline="\n") as f:
        f.write("%s,%s,%.2f\n" % (day, category, amount))


def write_query(keyword, start, end):
    """写查账条件：第 1 行类别（或 ALL），第 2 行开始日期，第 3 行结束日期"""
    write_text(QUERY_TXT, "%s\n%s\n%s" % (keyword, start, end))


# ======================================================================
# 多轮追问：记住上一轮缺什么
# ======================================================================
def load_pending():
    """读上一轮的追问，超过 10 分钟视为过期"""
    try:
        data = json.loads(PENDING_JSON.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return {}
    if not isinstance(data, dict) or not data:
        return {}
    if time.time() - float(data.get("ts") or 0) > 600:
        clear_pending()
        return {}
    return data


def save_pending(pending):
    """保存追问内容，带时间戳"""
    pending = dict(pending)
    pending["ts"] = time.time()
    with open(PENDING_JSON, "w", encoding="utf-8", newline="\n") as f:
        json.dump(pending, f, ensure_ascii=False)


def clear_pending():
    """删除追问记录"""
    try:
        PENDING_JSON.unlink()
    except (FileNotFoundError, OSError):
        pass


# ======================================================================
# 日期处理
# ======================================================================
def today_str():
    """今天的日期字符串"""
    return date.today().isoformat()


def _shift_month(year, month, delta):
    """把年月前后移动 delta 个月，返回 (年, 月)"""
    total = year * 12 + (month - 1) + delta
    return total // 12, total % 12 + 1


def _month_range(year, month):
    """某个月的 1 号到最后一天"""
    start = date(year, month, 1)
    ny, nm = _shift_month(year, month, 1)
    return start, date(ny, nm, 1) - timedelta(days=1)


WEEKDAY_INDEX = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}


def _parse_date_text(raw):
    """从一句话里认出具体是哪天，认不出返回 None"""
    today = date.today()
    text = str(raw or "").strip()
    if not text:
        return None

    # 2026-11-05 / 2026年11月5日
    m = re.search(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None

    # 11月5日 / 11月5号
    m = re.search(r"(\d{1,2})月(\d{1,2})[日号]", text)
    if m:
        try:
            return date(today.year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None

    # 5号 / 5日（本月）
    m = re.search(r"(\d{1,2})[日号]", text)
    if m:
        try:
            return date(today.year, today.month, int(m.group(1)))
        except ValueError:
            return None

    # "大前天"里含"前天"，必须先判
    if "大前天" in text:
        return today - timedelta(days=3)
    if "前天" in text:
        return today - timedelta(days=2)
    if "昨天" in text:
        return today - timedelta(days=1)
    if "今天" in text:
        return today

    # 上周三 / 这周五 / 下周一
    m = re.search(r"(上周|这周|本周|下周)([一二三四五六日天])", text)
    if m:
        monday = today - timedelta(days=today.weekday())
        if m.group(1) == "上周":
            monday -= timedelta(days=7)
        elif m.group(1) == "下周":
            monday += timedelta(days=7)
        return monday + timedelta(days=WEEKDAY_INDEX[m.group(2)])

    return None


def to_date_str(raw):
    """把日期说法转成 YYYY-MM-DD，认不出就用今天"""
    parsed = _parse_date_text(raw)
    return (parsed or date.today()).isoformat()


def to_time_range(text):
    """把查账语句里的时间词转成 (开始日期, 结束日期, 标签)"""
    today = date.today()
    y, m = today.year, today.month

    # 顺序很重要："上上个月"必须先于"上个月"判断，否则会被当成上个月
    if "上上个月" in text or "上上月" in text or "前个月" in text:
        sy, sm = _shift_month(y, m, -2)
        start, end = _month_range(sy, sm)
        return start.isoformat(), end.isoformat(), "上上个月"

    if "上个月" in text or "上月" in text:
        sy, sm = _shift_month(y, m, -1)
        start, end = _month_range(sy, sm)
        return start.isoformat(), end.isoformat(), "上个月"

    if "去年" in text:
        return date(y - 1, 1, 1).isoformat(), date(y - 1, 12, 31).isoformat(), "去年"
    if "今年" in text or "本年" in text:
        return date(y, 1, 1).isoformat(), today.isoformat(), "今年"

    if "最近一周" in text or "近一周" in text or "最近7天" in text or "近7天" in text:
        return (today - timedelta(days=6)).isoformat(), today.isoformat(), "最近 7 天"
    if "最近" in text or "近一个月" in text or "近30天" in text:
        return (today - timedelta(days=29)).isoformat(), today.isoformat(), "最近 30 天"

    if "大前天" in text:
        d = (today - timedelta(days=3)).isoformat()
        return d, d, "大前天"
    if "前天" in text:
        d = (today - timedelta(days=2)).isoformat()
        return d, d, "前天"
    if "昨天" in text:
        d = (today - timedelta(days=1)).isoformat()
        return d, d, "昨天"
    if "今天" in text:
        return today.isoformat(), today.isoformat(), "今天"

    if "上周" in text:
        monday = today - timedelta(days=today.weekday() + 7)
        return monday.isoformat(), (monday + timedelta(days=6)).isoformat(), "上周"
    if "这周" in text or "本周" in text:
        monday = today - timedelta(days=today.weekday())
        return monday.isoformat(), today.isoformat(), "本周"

    # 具体某个月：2026-10 / 10月
    m2 = re.search(r"(20\d{2})[-/年](\d{1,2})月?", text)
    if m2:
        try:
            start, end = _month_range(int(m2.group(1)), int(m2.group(2)))
            return start.isoformat(), end.isoformat(), "%s年%s月" % (m2.group(1), m2.group(2))
        except ValueError:
            pass
    m3 = re.search(r"(\d{1,2})月", text)
    if m3 and 1 <= int(m3.group(1)) <= 12:
        start, end = _month_range(y, int(m3.group(1)))
        last = end if end < today else today
        return start.isoformat(), last.isoformat(), "%s月" % m3.group(1)

    # 默认本月 1 号到今天
    return date(y, m, 1).isoformat(), today.isoformat(), "本月"


# ======================================================================
# 结果校验
# ======================================================================
def normalize_amount(raw):
    """转成合法的金额数字，非法返回 None"""
    if raw is None:
        return None
    try:
        value = float(str(raw).replace("元", "").replace("块", "").strip())
    except (TypeError, ValueError):
        return None
    value = round(value, 2)
    # 舍入之后再判断，避免 0.001 这种被记成 0.00
    if value <= 0 or value > 10_000_000:
        return None
    return value


def normalize_category(raw):
    """转成 5 个分类之一，认不出就归到「其他」"""
    text = str(raw or "").strip()
    if text in CATEGORIES:
        return text
    guess = guess_category(text)
    return guess if guess else "其他"


# ======================================================================
# 本地规则解析（没 Key 或断网时使用）
# ======================================================================
# 数字必须整体匹配（\d+ 而不是 \d{1,2}），否则 "12.999元" 会被截成 999
NUM_RE = re.compile(r"\d+(?:\.\d+)?")
AMOUNT_UNIT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:元|块|块钱|¥)")

# 中文数字：十块、二十五、一百二、两百五
CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_UNITS = {"十": 10, "百": 100, "千": 1000}
CN_CHARS = "零一二两三四五六七八九十百千万"

CN_YUAN_RE = re.compile(r"([%s]+)\s*(?:元|块钱|块)" % CN_CHARS)
YUAN_JIAO_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:元|块)\s*([%s\d])(?![%s])" % (CN_CHARS, CN_CHARS))
JIAO_RE = re.compile(r"([%s]+|\d+(?:\.\d+)?)\s*(?:毛|角)" % CN_CHARS)

CATEGORY_RULES = [
    ("交通", ["交通", "打车", "滴滴", "出租车", "地铁", "公交", "高铁", "火车",
              "机票", "加油", "停车", "油费", "单车", "路费"]),
    ("餐饮", ["餐饮", "吃饭", "早饭", "午饭", "晚饭", "夜宵", "外卖", "咖啡",
              "奶茶", "聚餐", "食堂", "火锅", "烧烤", "买菜", "水果", "零食",
              "中午", "早餐", "吃", "苹果", "香蕉", "橘子", "葡萄", "西瓜"]),
    ("娱乐", ["娱乐", "电影", "游戏", "演唱会", "门票", "旅游", "唱歌", "KTV",
              "健身", "剧本杀", "展览", "游泳", "打球", "爬山", "健身房"]),
    ("购物", ["购物", "买", "淘宝", "京东", "拼多多", "衣服", "鞋", "数码",
              "耳机", "手机", "电脑", "超市", "日用品"]),
    ("其他", ["其他", "话费", "水电", "电费", "水费", "燃气", "宽带", "房租",
              "物业", "医疗", "快递", "药"]),
]

QUERY_WORDS = ["多少", "花了多少", "一共", "总共", "合计", "统计", "汇总",
               "查", "账单", "支出", "花销"]
REFUSE_WORDS = ["借钱", "贷款", "借我", "算命", "写代码", "帮我写", "天气",
                "新闻", "讲个笑话", "聊天", "你是谁", "股票", "基金"]


def guess_category(text):
    """按关键词猜分类"""
    for cat, words in CATEGORY_RULES:
        for w in words:
            if w in text:
                return cat
    return None


def cn_to_number(text):
    """把"二十五""十""一百二"转成数字，认不出返回 None"""
    text = str(text or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text)

    total = 0.0        # 已经结算到"万"的部分
    section = 0.0      # 当前这一段
    number = 0
    last_unit = 1
    saw_zero = False

    for ch in text:
        if ch in CN_DIGITS:
            if CN_DIGITS[ch] == 0:
                saw_zero = True
                number = 0
            else:
                number = CN_DIGITS[ch]
        elif ch in CN_UNITS:
            unit = CN_UNITS[ch]
            if number == 0:
                number = 1          # "十五"里的"十"前面其实是 1
            section += number * unit
            last_unit = unit
            number = 0
            saw_zero = False
        elif ch == "万":
            section = (section + number) * 10000
            total += section
            section = 0
            number = 0
            last_unit = 10000
            saw_zero = False
        else:
            return None

    if number:
        if saw_zero or last_unit < 10:
            section += number                      # 一百零二 → 102
        else:
            section += number * (last_unit // 10)  # 一百二 → 120
    return round(total + section, 2)


def guess_amount(text, loose=False):
    """
    从文字里取金额，认不出来返回 None。
    支持：32 / 32.5 / 奶茶18块 / 十八块 / 十块五 / 五毛 / 一百二
    loose=True 时额外接受光杆中文数字（"二十五"），只在追问金额时用，
    避免"买了五个苹果"里的"五"被当成金额。
    """
    # 1) "10块5"、"一块五" —— 角
    m = YUAN_JIAO_RE.search(text)
    if m:
        yuan = cn_to_number(m.group(1))
        jiao = cn_to_number(m.group(2))
        if yuan is not None and jiao is not None:
            return normalize_amount(yuan + jiao / 10)

    # 2) 阿拉伯数字 + 元/块
    m = AMOUNT_UNIT_RE.search(text)
    if m:
        return normalize_amount(m.group(1))

    # 3) 中文数字 + 元/块
    m = CN_YUAN_RE.search(text)
    if m:
        value = cn_to_number(m.group(1))
        if value is not None:
            tail = text[m.end():]
            m2 = JIAO_RE.search(tail)
            if m2:
                jiao = cn_to_number(m2.group(1))
                if jiao:
                    value += jiao / 10
            else:
                m3 = re.match(r"\s*([一二两三四五六七八九])(?![%s])" % CN_CHARS, tail)
                if m3:
                    value += CN_DIGITS[m3.group(1)] / 10
            return normalize_amount(value)

    # 4) 只有"五毛""5角"
    m = JIAO_RE.search(text)
    if m:
        value = cn_to_number(m.group(1))
        if value is not None:
            return normalize_amount(value / 10)

    # 5) 兜底：取最后一个阿拉伯数字
    nums = NUM_RE.findall(text)
    if nums:
        return normalize_amount(nums[-1])

    # 6) 光杆中文数字，只在追问金额时接受
    if loose:
        m = re.search(r"[%s]+" % CN_CHARS, text)
        if m:
            value = cn_to_number(m.group(0))
            if value:
                return normalize_amount(value)
    return None


# 用来把"吃饭30，游泳195"这种句子拆成几段
CLAUSE_SPLIT_RE = re.compile(r"[，,、；;。\n]+|还有|另外|以及")


def split_clauses(text):
    """把一句话按标点拆成若干小段"""
    return [p.strip() for p in CLAUSE_SPLIT_RE.split(str(text or "")) if p and p.strip()]


def parse_multi(text):
    """
    一句话里有多笔消费时，拆成多条记录。
    只认"既有金额又有类别"的段，凑够 2 笔才返回，否则返回 None（走单笔流程）。
    """
    parts = split_clauses(text)
    if len(parts) < 2:
        return None
    records = []
    for part in parts:
        amount = guess_amount_money(part)
        category = guess_category(part)
        if amount is not None and category is not None:
            records.append({"amount": amount, "category": category})
    return records if len(records) >= 2 else None


def guess_amount_money(text):
    """
    自由语句里的金额：先走严格规则，再允许中文数字，
    但只认带"十/百/千/万"的（"二十五""三十""一百二"），
    排除"三个""两杯"这种数量词，避免把数量当金额。
    """
    amount = guess_amount(text)
    if amount is not None:
        return amount
    for match in re.finditer(r"[%s]+" % CN_CHARS, text):
        token = match.group(0)
        if any(unit in token for unit in "十百千万"):
            value = cn_to_number(token)
            if value:
                return normalize_amount(value)
    return None


def is_query(text):
    """是否在查账"""
    return any(w in text for w in QUERY_WORDS)


def is_refuse(text):
    """是否属于要拦截的无关话题"""
    return any(w in text for w in REFUSE_WORDS)



def local_parse(text, pending=None):
    """不用大模型的解析，返回 action 字典"""

    # 1. 在回答上一轮的追问
    if pending:
        # 但如果这句本身就是一笔完整的账，说明用户换了话题，按新记录处理
        fresh_amount = guess_amount(text)
        fresh_category = guess_category(text)
        if fresh_amount is not None and fresh_category is not None:
            return {"action": "record", "amount": fresh_amount,
                    "category": fresh_category, "date": text}

        draft = dict(pending.get("draft") or {})
        if not draft.get("amount"):
            amount = guess_amount(text, loose=True)
            if amount:
                draft["amount"] = amount
        if not draft.get("category"):
            cat = guess_category(text) or (text if text in CATEGORIES else None)
            if cat:
                draft["category"] = cat

        if draft.get("amount") and draft.get("category"):
            return {"action": "record", "amount": draft["amount"],
                    "category": draft["category"], "date": text}

        missing = "金额" if not draft.get("amount") else "类别"
        return {"action": "ask", "draft": draft,
                "message": "还差%s，能补充一下吗？" % missing}

    amount = guess_amount_money(text)
    category = guess_category(text)

    # 2. 无关话题，直接拒绝
    if is_refuse(text):
        return {"action": "refuse", "message": "抱歉，我只负责记账和查账哦。"}

    # 3. 查账（没有金额、有查询词）
    if amount is None and is_query(text):
        keyword = category or "ALL"
        return {"action": "query", "keyword": keyword, "time_text": text}

    # 4. 信息不全，反问
    if amount is None and category is not None:
        return {"action": "ask", "draft": {"category": category},
                "message": "这笔%s大概花了多少钱？直接回个数字就行。" % category}
    if amount is None:
        return {"action": "ask", "draft": {},
                "message": "这笔花了多少钱？说清「做了什么 + 多少钱」我就能记。"}
    if category is None:
        return {"action": "ask", "draft": {"amount": amount},
                "message": "%.2f 元这笔记到哪类？餐饮/交通/购物/娱乐/其他" % amount}

    # 5. 记账
    return {"action": "record", "amount": amount, "category": category,
            "date": text}


# ======================================================================
# 大模型解析
# ======================================================================
SYSTEM_PROMPT = """你是一个记账助手的语义解析器。你只输出 JSON，不输出任何解释，不加 markdown 代码块。

今天的日期是：{today}

你要把用户的一句话解析成下面四种 JSON 之一：

1) 用户提供了完整的记账信息（金额 + 消费内容）：
{"action":"record","amount":32,"category":"交通","date":"昨天"}
2) 用户想记账，但缺少金额或类别：
{"action":"ask","message":"请问这顿火锅花了多少钱？"}
   message 要像人说话，不超过 30 个字。
3) 用户想查账：
{"action":"query","keyword":"交通","time_text":"上个月"}
   - keyword 只能是 餐饮/交通/购物/娱乐/其他 之一；用户没说类别就填 "ALL"
   - time_text 原样抄用户的时间说法（上个月/这个月/昨天/上周/今年），没说就填 ""
4) 与记账无关（借钱、贷款、闲聊、写代码、问天气）：
{"action":"refuse","message":"抱歉，我只负责记账和查账哦。"}

【类别限制】category 只能是下面 5 个词之一，绝对不能自创：
- 餐饮：吃饭、买菜、零食、奶茶、火锅、食堂、外卖
- 交通：打车、公交、地铁、加油、高铁、火车、停车
- 购物：买衣服、电子产品、日用品、淘宝、超市
- 娱乐：看电影、唱歌、游戏、旅游、门票
- 其他：话费、水电费、房租、医疗、无法归类的

【金额】amount 必须是纯数字（不带"元"），不能是负数。
【日期】date 只填用户原话里的时间词（昨天/今天/前天/上周），用户没说就填空字符串。
如果用户是在回答你上一轮的追问，请结合上下文直接给出结果。"""


def _extract_json(content):
    """模型有时会包一层 ```json ... ```，剥掉再解析"""
    text = str(content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"```\s*$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    return json.loads(text)


def chat_with_ai(user_text, pending=None):
    """调大模型解析，失败或返回结构不对就退回本地规则"""
    global LAST_SOURCE
    LAST_SOURCE = "local"

    if requests is None:
        print("[main] 没有安装 requests，使用本地规则解析")
        return local_parse(user_text, pending)
    if not API_KEY:
        print("[main] 没有配置 DEEPSEEK_API_KEY，使用本地规则解析")
        return local_parse(user_text, pending)

    # 带上上一轮的追问，模型才知道这句话在回答什么
    user_content = user_text
    if pending:
        user_content = "（上一轮你问过：%s）\n用户现在回答：%s" % (
            pending.get("question", ""), user_text)

    payload = {
        "model": API_MODEL,
        "temperature": 0,                      # 要稳定结果，不做创作
        "max_tokens": 200,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT.replace("{today}", today_str())},
            {"role": "user", "content": user_content},
        ],
    }
    headers = {"Content-Type": "application/json",
               "Authorization": "Bearer %s" % API_KEY}

    last_error = ""
    for attempt in range(API_MAX_RETRY):
        try:
            resp = requests.post(API_URL, headers=headers, json=payload, timeout=15)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            data = _extract_json(content)
            if isinstance(data, dict) and data.get("action"):
                LAST_SOURCE = "llm"
                return data
            last_error = "模型返回的结构不对：%s" % str(content)[:80]
        except Exception as exc:               # noqa: BLE001
            last_error = str(exc)
            if attempt + 1 < API_MAX_RETRY:
                time.sleep(1)                  # 等 1 秒再试一次

    print("[main] 大模型调用失败（%s），改用本地规则" % last_error)
    return local_parse(user_text, pending)


# ======================================================================
# 主流程
# ======================================================================
LOCK_FILE = DATA_DIR / ".lock"


def acquire_lock(timeout=5.0):
    """抢文件锁，防止两个实例同时读写 data/（超过 30 秒的锁视为残留）"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                if time.time() - LOCK_FILE.stat().st_mtime > 30:
                    LOCK_FILE.unlink()
                    continue
            except OSError:
                pass
            time.sleep(0.2)
        except OSError:
            return True        # 锁机制不可用就放行，不能把主流程卡死
    return False


def release_lock():
    """释放文件锁"""
    try:
        LOCK_FILE.unlink()
    except OSError:
        pass


def run():
    """读输入 → 解析意图 → 写结果文件（带锁，同一时刻只处理一个请求）"""
    ensure_data_dir()

    if not acquire_lock():
        write_reply("上一条还在处理，请稍等一秒再试。", KIND_ERROR)
        print("[main] 另一个 main.py 正在处理，本次跳过")
        return 1
    try:
        return _run_flow()
    finally:
        release_lock()


def _run_flow():
    # 1. 读 UI 写入的输入
    user_text = read_text(INPUT_TXT)
    if not user_text:
        write_reply("请先输入一句话，比如「昨天打车32块」。", KIND_ERROR)
        print("[main] data/input.txt 是空的")
        return 1
    print("[main] 收到输入：%s" % user_text)

    pending = load_pending()

    if user_text in ("算了", "取消", "不用了"):
        was_waiting = bool(pending)
        clear_pending()
        msg = "好的，这次不记了。" if was_waiting else "好，有需要再叫我。"
        write_reply(msg, KIND_OK)
        print("[main] " + msg)
        return 0

    # 2. 先看这一句里是不是有多笔消费（"吃饭30，游泳195"）
    multi = None
    if not pending:
        multi = parse_multi(user_text)

    if multi:
        result = {"action": "records", "items": multi}
    else:
        result = chat_with_ai(user_text, pending)
    if not isinstance(result, dict):
        result = local_parse(user_text, pending)
    action = result.get("action")

    # 3. 按意图写对应文件
    if action == "record":
        amount = normalize_amount(result.get("amount"))
        category = normalize_category(result.get("category"))
        day = to_date_str(result.get("date"))

        # 金额不合法就转成追问
        if amount is None:
            save_pending({"question": "这笔花了多少钱？", "draft": {}})
            write_reply("这个金额我没看懂，能再报一次吗？比如「32」或「32.5」。", KIND_ASK)
            print("[main] 金额不合法，转追问")
            return 0

        append_record(day, category, amount)
        clear_pending()
        msg = "记账成功：%s %.2f 元（%s）" % (category, amount, day)
        write_reply(msg, KIND_OK)
        print("[main] " + msg)
        return 0

    if action == "records":
        items = result.get("items") or []
        day = to_date_str(user_text)
        saved = []
        for item in items:
            amount = normalize_amount(item.get("amount"))
            if amount is None:
                continue
            category = normalize_category(item.get("category"))
            append_record(day, category, amount)
            saved.append((category, amount))

        if not saved:
            write_reply("这几笔我都没看懂，换种说法试试？", KIND_ERROR)
            print("[main] 多笔解析失败")
            return 1

        clear_pending()
        detail = "；".join("%s %.2f 元" % (c, a) for c, a in saved)
        msg = "记账成功 %d 笔：%s（%s）" % (len(saved), detail, day)
        write_reply(msg, KIND_OK)
        print("[main] " + msg)
        return 0

    if action == "query":
        keyword = result.get("keyword") or "ALL"
        keyword = keyword if (keyword in CATEGORIES or keyword == "ALL") else "ALL"
        start, end, label = to_time_range(result.get("time_text") or "")
        write_query(keyword, start, end)
        clear_pending()
        who = "全部消费" if keyword == "ALL" else keyword
        msg = "查询条件：%s，时间：%s" % (who, label)
        write_reply(msg, KIND_QUERY)
        print("[main] " + msg)
        return 0

    if action == "ask":
        message = result.get("message") or "能再说详细一点吗？"
        save_pending({"question": message, "draft": result.get("draft") or {}})
        write_reply(message, KIND_ASK)
        print("[main] 追问：" + message)
        return 0

    if action == "refuse":
        message = result.get("message") or "抱歉，我只负责记账和查账哦。"
        clear_pending()
        write_reply(message, KIND_REFUSE)
        print("[main] 越界拦截：" + message)
        return 0

    # 其他意外情况
    clear_pending()
    detail = result.get("message") or "未知原因"
    write_reply("解析失败了，换个说法试试？(%s)" % detail, KIND_ERROR)
    print("[main] 未知 action：%s" % action)
    return 1


def test_api():
    """自检：拿一句话试一下大模型通不通"""
    if requests is None:
        print("[自检] 没有安装 requests，先执行：pip install requests")
        return 1
    if not API_KEY:
        print("[自检] 还没有配置 API Key")
        print("[自检] 打开界面 python ui.py，选「5. 设置 API Key」；或者手写 config.txt")
        return 1

    print("[自检] 接口：%s" % API_BASE)
    print("[自检] 模型：%s" % API_MODEL)
    result = chat_with_ai("打车32")
    if LAST_SOURCE != "llm":
        print("[自检] 失败：没连上大模型，已经退回本地规则")
        print("[自检] 请检查：Key 是否正确、网络通不通、base_url 有没有写错")
        return 1
    print("[自检] 成功！模型返回：%s" % json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    if "--test-api" in sys.argv:
        sys.exit(test_api())
    sys.exit(run())
