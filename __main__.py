# python -m Floor_engine_server —— 旧 NiceGUI 入口已退役。
#
# 2026-06-29：webui.py（NiceGUI 界面）随 STEP3 删除，本包不再自带 UI 入口，
# 也不再依赖 nicegui。此文件保留为「退役提示桩」：打印新启动方式后以退出码 1 结束，
# 不启动任何服务（避免老脚本/老习惯执行本命令时静默无反应或抛 ImportError）。
import sys

_MSG = """\
[已退役] NiceGUI webui 已删除，`python -m Floor_engine_server` 不再启动任何界面。
请改用：
  test/ 下的一键启动脚本（Windows 一键启动.bat / Linux·macOS 一键启动.sh，后端 7870 + 前端 3000，推荐）
  python -m Floor_engine_server.server_api    （仅后端 FastAPI，7870）
详见 DEVGUIDE.md 第八节「旧版 NiceGUI（已退役）」。"""

if __name__ in {'__main__', '__mp_main__'}:
    print(_MSG, file=sys.stderr)
    sys.exit(1)
