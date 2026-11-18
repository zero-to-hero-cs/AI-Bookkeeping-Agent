import requests
import json
import os

# 1. 把你的真实 API Key 填在这里（把 YOURAPI 替换掉，保留双引号）
API_KEY = "YOURAPI"

def chat_with_ai(user_text):
    url = "https://api.deepseek.com/chat/completions"
    headers = {
        "Content-Type": "application/json",
        # 这里使用的是变量 API_KEY，不再是直接写死
        "Authorization": f"Bearer {API_KEY}"
    }
    
    # 2. 设计严格的 Prompt
    system_prompt = """
    你是一个智能记账助手。请分析用户的输入，严格返回 JSON 格式。
    规则：
    1. 如果用户提供了完整的记账信息（金额和消费内容），返回：
    {"action": "record", "amount": 32, "category": "交通", "date": "昨天"}
    
    2. 如果用户想记账，但缺少金额等关键信息，请反问，返回：
    {"action": "ask", "message": "请问这顿火锅花了多少钱？"}
    
    3. 如果用户输入的是与记账无关的内容（如借钱、闲聊），请拒绝并返回：
    {"action": "refuse", "message": "抱歉，我只负责记账哦。"}

    【极其重要：类别限制】
    category 字段必须且只能是以下 5 个词之一：
    - "餐饮" (吃饭、买菜、零食、奶茶、火锅)
    - "交通" (打车、公交、地铁、加油、高铁)
    - "购物" (买衣服、电子产品、日用品、淘宝)
    - "娱乐" (看电影、唱歌、游戏、旅游)
    - "其他" (话费、水电费、医疗等无法归类的)
    绝对不能出现"出行"、"打车"、"吃饭"等其他词！金额必须是数字！
    """

    data = {
        "model": "deepseek-chat",
        "response_format": { "type": "json_object" }, # 强制返回 JSON
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text}
        ]
    }

    try:
        # 3. 发送请求
        response = requests.post(url, headers=headers, json=data, timeout=10)
        response.raise_for_status() # 检查网络请求是否报错
        result = response.json()
        
        # 4. 解析大模型返回的结果
        ai_text = result["choices"][0]["message"]["content"]
        record = json.loads(ai_text)
        return record
        
    except Exception as e:
        print(f"请求大模型失败: {e}")
        return {"action": "error", "message": str(e)}

if __name__ == "__main__":
    # 确保 data 文件夹存在
    os.makedirs("data", exist_ok=True)
    
    # 5. 读取 UI 传来的输入
    try:
        with open("data/input.txt", "r", encoding="utf-8") as f:
            user_input = f.read().strip()
    except FileNotFoundError:
        print("未找到 data/input.txt 文件，请先创建并写入内容。")
        exit()

    if not user_input:
        print("data/input.txt 里面是空的，请写点东西再运行。")
        exit()

    print(f"正在向 AI 发送请求：{user_input} ...")
    
    # 6. 调用 AI
    result_data = chat_with_ai(user_input)
    
    # 7. 根据 AI 返回的结果，写入对应的文件并打印
    if result_data.get("action") == "record":
        amount = result_data.get("amount")
        category = result_data.get("category")
        
        # 写入 data.txt (给 C 语言读)
        with open("data/data.txt", "a", encoding="utf-8") as f:
            f.write(f"{category},{amount}\n")
            
        # 写入 reply.txt (给 UI 读)
        reply_msg = f"✅ 记账成功！金额: {amount} 元，分类: {category}"
        with open("data/reply.txt", "w", encoding="utf-8") as f:
            f.write(reply_msg)
            
        print(reply_msg) # 终端打印反馈
            
    elif result_data.get("action") == "ask":
        # 写入 reply.txt (给 UI 读)
        reply_msg = f"🤖 助手：{result_data.get('message')}"
        with open("data/reply.txt", "w", encoding="utf-8") as f:
            f.write(reply_msg)
            
        print(reply_msg) # 终端打印反馈
            
    else:
        # 异常处理
        reply_msg = f"❌ 系统出错：{result_data.get('message')}"
        with open("data/reply.txt", "w", encoding="utf-8") as f:
            f.write(reply_msg)
            
        print(reply_msg) # 终端打印反馈