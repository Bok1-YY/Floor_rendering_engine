# 启动器：python -m floor_engine  （从 D:\test 目录运行）
# 可用环境变量 FLOOR_AI_PORT 指定端口（默认 7869）
from .webui import *          # 导入即注册 @ui.page('/') 与静态目录
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
    threading.Thread(target=lambda: _open_browser(run_port), daemon=True).start()
    ui.run(port=run_port, title='地板 AI 提示词引擎 v5.2.1', dark=True, reload=False, show=False)
