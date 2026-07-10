#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Floor engine —— 单一程序启动入口（前后端合一）。

用途：
  * 源码运行：  python Floor_engine_server/serve.py   （在 test/ 目录下）
  * Nuitka 冻结：本文件即 --onefile 的编译入口，产出单个 exe。

行为：单进程启动 FastAPI（server_api:app），后端 /api + 前端静态站 / 同端口，
      启动后自动打开浏览器。端口/host 可用环境变量覆盖：
        FLOOR_API_HOST（默认 127.0.0.1）
        FLOOR_API_PORT（默认 7870）
        FLOOR_NO_BROWSER=1  不自动开浏览器
"""
import os
import sys
import threading
import time

# —— 让 `import Floor_engine_server.*` 可用（源码运行时；冻结后由 Nuitka 内部解析，无副作用）——
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_PKG_DIR)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from Floor_engine_server.server_api import app  # noqa: E402


def _open_browser_later(url: str):
    if os.environ.get('FLOOR_NO_BROWSER') == '1':
        return
    def _go():
        time.sleep(1.5)  # 等 uvicorn 起来
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
    threading.Thread(target=_go, daemon=True).start()


def main():
    import uvicorn
    host = os.environ.get('FLOOR_API_HOST', '127.0.0.1')
    if host not in ('127.0.0.1', 'localhost', '::1'):
        raise SystemExit('Floor Engine 当前仅支持本机监听，请使用 FLOOR_API_HOST=127.0.0.1')
    port = int(os.environ.get('FLOOR_API_PORT', '7870'))
    open_host = '127.0.0.1' if host == '::1' else host
    _open_browser_later(f'http://{open_host}:{port}/')
    print(f'[Floor engine] 服务启动中 → http://{open_host}:{port}/  （Ctrl+C 退出）')
    # 传 app 对象 = 单进程单 worker（_job_history/信号量是进程内状态，必须单 worker）。
    # 显式用纯 Python 的 h11 + asyncio：不依赖 uvloop/httptools/websockets 这些
    # 可选 C 扩展，Nuitka 打包最稳；本服务只用 SSE(StreamingResponse)，无 WebSocket。
    uvicorn.run(app, host=host, port=port, loop='asyncio', http='h11', ws='none')


if __name__ == '__main__':
    main()
