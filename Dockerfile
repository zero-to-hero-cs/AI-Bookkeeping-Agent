# AI 智能记账助手 —— 容器化运行
#
# 作用：评审不需要安装 Python / gcc / matplotlib，
#      一条 docker build 就能装好依赖并把 C 模块编译成 calc.exe。
#
# 用法：
#   docker build -t jizhang .
#   docker run -it --rm jizhang
#
# 说明：容器里没有显示器，所以跑的是终端菜单版界面（ui.py），功能与桌面上完全一致。

FROM python:3.11-slim

# 容器里默认没有中文环境，必须显式指定，否则中文会变成问号
ENV PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8 \
    LANG=C.UTF-8

# 安装 C 编译器（编译 calc.c）+ 中文字体（饼图上的中文才不会变成方块）
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libc6-dev \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先只拷依赖清单，利用 Docker 层缓存（改代码时不用重新装依赖）
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 再拷项目源码
COPY . .

# 关键一步：在镜像构建阶段就把 C 模块编译成 calc.exe
# Linux 里 .exe 只是文件名，gcc 产物本身带执行权限，ui.py 能直接调用
RUN gcc calc.c -o calc.exe -Wall -O2 && chmod +x calc.exe

# 保证数据目录存在（data/ 下的运行数据不进镜像）
RUN mkdir -p data

# 以终端菜单方式启动；必须用 docker run -it 才有交互
CMD ["python", "-u", "ui.py"]
