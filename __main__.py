# 启动器：python -m floor_engine  （从 D:\test 目录运行）
# 可用环境变量 FLOOR_AI_PORT 指定端口（默认 7869）
from .webui import *          # 导入即注册 @ui.page('/') 与静态目录
from .config import FAVICON_PATH
import os, webbrowser, threading, multiprocessing
from nicegui import ui


def _open_browser(run_port):
    import time
    time.sleep(1.5)
    webbrowser.open(f'http://127.0.0.1:{run_port}')


if __name__ in {'__main__', '__mp_main__'}:
    # PyInstaller 多进程支持（打包运行时必须调用）
    multiprocessing.freeze_support()
    run_port = int(os.environ.get('FLOOR_AI_PORT', '7869'))
    # 热重载默认关闭（兼容 python -m 启动与 PyInstaller 打包）。
    # 开发想要自动重载，请改用 D:\test\dev_floor.py（python dev_floor.py）。
    _reload = os.environ.get('FLOOR_AI_RELOAD', '0') != '0'
    # 默认只监听本机 127.0.0.1（NiceGUI 默认是 0.0.0.0，会让同局域网任何人都能直接打开）。
    # 将来给同事局域网共用才显式设 FLOOR_AI_HOST=0.0.0.0——且必须自行配登录/内网访问控制。
    _host = os.environ.get('FLOOR_AI_HOST', '127.0.0.1').strip() or '127.0.0.1'
    threading.Thread(target=lambda: _open_browser(run_port), daemon=True).start()
    ui.run(host=_host, port=run_port, title='地板 AI 提示词引擎 v6.0.1', dark=True, reload=_reload, show=False,
           favicon=FAVICON_PATH if os.path.exists(FAVICON_PATH) else None)
