"""视频转写助手 GUI 包（桌面可执行程序）。

模块划分：
    gui.core      —— 与界面无关的底层能力（路径、配置、子进程任务、环境/模型/输出管理）
    gui.widgets   —— 通用控件与主题
    gui.tabs      —— 四个功能页：环境安装 / 转写 / 模型管理 / 输出管理
    gui.app       —— 主窗口装配
"""

__all__ = ["APP_NAME", "APP_VERSION"]

APP_NAME = "视频转写助手"
APP_VERSION = "1.0.0"
