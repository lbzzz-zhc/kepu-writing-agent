#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一键打开写作台：起本地服务 → 等端口就绪 → 自动开浏览器。

解决的问题
    以前要两步：先开黑窗口跑服务，再手敲 http://127.0.0.1:8787/writer.html。
    现在双击 start-writer.bat（或直接跑本脚本）即可一步到位。

行为
    1. 先探测端口：若已有服务在跑（比如上次忘了关），直接开浏览器，不重复启动
    2. 否则启动 writer_server.py 子进程，轮询直到可访问（最多 20 秒）
    3. 用系统默认浏览器打开写作台
    4. 保持运行；在本窗口按 Ctrl+C 即可停止服务

用法
    python tools/open_writer.py                 # 本地完整模式（可上传/入库）
    python tools/open_writer.py --port 9000
    python tools/open_writer.py --online        # 只打开线上版，不启本地服务
    python tools/open_writer.py --no-browser    # 只起服务，不开浏览器
"""

import argparse
import atexit
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
ONLINE = "https://lbzzz-zhc.github.io/kepu-writing-agent/writer.html"


def alive(port, path="/writer.html", timeout=1.5):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path),
                                    timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--online", action="store_true",
                    help="只打开线上版（不需要本地服务，但上传与入库不可用）")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    if args.online:
        print("打开线上写作台（未启动本地服务，上传与入库功能不可用）")
        print("  " + ONLINE)
        if not args.no_browser:
            webbrowser.open(ONLINE)
        return 0

    url = "http://127.0.0.1:%d/writer.html" % args.port

    # 1) 已有服务在跑？直接用
    if alive(args.port):
        print("检测到本地服务已在运行（端口 %d），直接打开。" % args.port)
        if not args.no_browser:
            webbrowser.open(url)
        print("  浏览器地址：" + url)
        print("  若无反应，手动复制上面的地址到浏览器。")
        return 0

    # 2) 启动服务
    server = os.path.join(HERE, "writer_server.py")
    print("正在启动本地服务（端口 %d）…" % args.port)
    proc = subprocess.Popen([sys.executable, server, "--port", str(args.port)],
                            cwd=PROJECT)

    ok = False
    for _ in range(40):                      # 最多等 20 秒
        if proc.poll() is not None:          # 进程已退出
            break
        if alive(args.port):
            ok = True
            break
        time.sleep(0.5)

    if not ok:
        print("\n[启动失败] 服务未能在 20 秒内就绪。请检查：")
        print("  1. 端口 %d 是否被别的程序占用（可换 --port 9000）" % args.port)
        print("  2. 当前 Python 是否能运行 tools/writer_server.py")
        if proc.poll() is None:
            proc.terminate()
        return 1

    print("=" * 54)
    print("  科普写作台已就绪")
    print("  写作台：" + url)
    print("  展示台：http://127.0.0.1:%d/index.html" % args.port)
    print("  停止：在本窗口按 Ctrl+C")
    print("=" * 54)

    if not args.no_browser:
        webbrowser.open(url)

    # 关键：本进程退出时必须带走服务子进程。
    # 否则用任务管理器/脚本终止父进程后，服务会变成孤儿进程继续占着端口。
    try:
        atexit.register(_stop, proc)
        signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    except Exception:
        pass

    try:
        proc.wait()
    except KeyboardInterrupt:
        pass
    finally:
        _stop(proc)
    return 0


def _stop(proc):
    if proc.poll() is not None:
        return
    print("\n正在停止服务…")
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    print("服务已停止。")


if __name__ == "__main__":
    sys.exit(main())
