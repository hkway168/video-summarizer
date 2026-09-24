"""
scripts/_browser_finder.py —— 探测本机已安装的浏览器

用途：扫码登录时优先复用系统里已经装好的 Edge / Chrome / Brave，
     这样就不必让 Playwright 再单独下载一份 ~150MB 的 Chromium。

Playwright 复用本地浏览器有两种方式：
    1) channel="msedge" / "chrome" / "chrome-beta" …
       —— 官方支持的「已安装浏览器通道」，最稳，优先用
    2) executable_path="D:/.../brave.exe"
       —— 直接指定可执行文件，适用于 channel 不支持的 Chromium 系浏览器

注意：这里只做「探测」，不启动浏览器，因此可以独立于 Playwright 使用
     （GUI 的环境体检也会调用它来展示可用浏览器列表）。
"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class BrowserInfo:
    key: str                       # edge / chrome / brave / chromium / playwright
    name: str                      # 展示名
    channel: Optional[str] = None   # Playwright channel（优先）
    path: Optional[str] = None      # 可执行文件路径（channel 为空时使用）

    @property
    def is_local(self) -> bool:
        """是否是系统里已安装的浏览器（相对于 Playwright 自带 Chromium）。"""
        return self.key != "playwright"

    def describe(self) -> str:
        if self.key == "playwright":
            return f"{self.name}（Playwright 自带）"
        where = self.path or f"channel={self.channel}"
        return f"{self.name}  [{where}]"


def _registry_path(app_exe: str) -> Optional[str]:
    """从注册表 App Paths 里查可执行文件（Windows 安装的浏览器通常都会注册）。"""
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:
        return None
    sub = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{app_exe}"
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for flag in (0, getattr(winreg, "KEY_WOW64_32KEY", 0)):
            try:
                with winreg.OpenKey(root, sub, 0, winreg.KEY_READ | flag) as k:
                    val, _ = winreg.QueryValueEx(k, "")
                    val = str(val).strip('"')
                    if val and Path(val).is_file():
                        return val
            except OSError:
                continue
    return None


def _first_existing(paths: list[str]) -> Optional[str]:
    for p in paths:
        if p and Path(p).is_file():
            return p
    return None


def _win_candidates(rel: str) -> list[str]:
    bases = [
        os.environ.get("PROGRAMFILES", r"C:\Program Files"),
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    return [str(Path(b) / rel) for b in bases if b]


def find_edge() -> Optional[str]:
    if sys.platform == "win32":
        return _registry_path("msedge.exe") or _first_existing(
            _win_candidates(r"Microsoft\Edge\Application\msedge.exe"))
    if sys.platform == "darwin":
        return _first_existing(
            ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"])
    return shutil.which("microsoft-edge") or shutil.which("msedge")


def find_chrome() -> Optional[str]:
    if sys.platform == "win32":
        return _registry_path("chrome.exe") or _first_existing(
            _win_candidates(r"Google\Chrome\Application\chrome.exe"))
    if sys.platform == "darwin":
        return _first_existing(
            ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"])
    return shutil.which("google-chrome") or shutil.which("chrome")


def find_brave() -> Optional[str]:
    if sys.platform == "win32":
        return _registry_path("brave.exe") or _first_existing(
            _win_candidates(r"BraveSoftware\Brave-Browser\Application\brave.exe"))
    if sys.platform == "darwin":
        return _first_existing(
            ["/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"])
    return shutil.which("brave-browser") or shutil.which("brave")


def find_chromium() -> Optional[str]:
    if sys.platform == "win32":
        return _registry_path("chromium.exe") or _first_existing(
            _win_candidates(r"Chromium\Application\chrome.exe"))
    if sys.platform == "darwin":
        return _first_existing(["/Applications/Chromium.app/Contents/MacOS/Chromium"])
    return shutil.which("chromium") or shutil.which("chromium-browser")


#: 探测顺序：Edge 在 Windows 上一定有，放最前；其次 Chrome
_FINDERS = [
    ("edge", "Microsoft Edge", "msedge", find_edge),
    ("chrome", "Google Chrome", "chrome", find_chrome),
    ("brave", "Brave", None, find_brave),
    ("chromium", "Chromium", "chromium", find_chromium),
]


def list_local_browsers() -> list[BrowserInfo]:
    """返回本机已安装的 Chromium 系浏览器（按推荐顺序）。"""
    found: list[BrowserInfo] = []
    for key, name, channel, finder in _FINDERS:
        try:
            path = finder()
        except Exception:
            path = None
        if path:
            found.append(BrowserInfo(key=key, name=name, channel=channel, path=path))
    return found


def playwright_chromium_path() -> Optional[str]:
    """Playwright 自带 Chromium 的可执行路径；未下载时返回 None。"""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            exe = pw.chromium.executable_path
            return exe if exe and Path(exe).exists() else None
    except Exception:
        return None


def resolve_browser(prefer: str = "auto") -> Optional[BrowserInfo]:
    """选出本次登录要用的浏览器。

    prefer:
        "auto"        → 本地浏览器优先，都没有再用 Playwright 自带 Chromium
        "playwright"  → 强制用 Playwright 自带 Chromium
        "edge"/"chrome"/"brave"/"chromium" → 指定某个本地浏览器
        或直接传一个 .exe 路径
    返回 None 表示什么都找不到。
    """
    prefer = (prefer or "auto").strip()

    # 直接给了可执行文件路径
    if prefer not in ("auto", "playwright") and Path(prefer).is_file():
        return BrowserInfo(key="custom", name=Path(prefer).stem, path=prefer)

    if prefer == "playwright":
        exe = playwright_chromium_path()
        return BrowserInfo(key="playwright", name="Chromium") if exe else None

    locals_ = list_local_browsers()
    if prefer != "auto":
        for b in locals_:
            if b.key == prefer:
                return b
        # 指定的没找到 → 退回 auto 逻辑，不让用户卡住

    if locals_:
        return locals_[0]
    if playwright_chromium_path():
        return BrowserInfo(key="playwright", name="Chromium")
    return None


def launch_kwargs(info: BrowserInfo) -> dict:
    """把 BrowserInfo 转成 playwright chromium.launch(**kwargs)。"""
    kw: dict = {}
    if info.key == "playwright":
        return kw
    if info.channel:
        kw["channel"] = info.channel
    elif info.path:
        kw["executable_path"] = info.path
    return kw


if __name__ == "__main__":
    print("本机已安装的浏览器：")
    for b in list_local_browsers():
        print(f"  - {b.describe()}")
    pw_exe = playwright_chromium_path()
    print(f"\nPlaywright 自带 Chromium：{pw_exe or '（未下载）'}")
    chosen = resolve_browser("auto")
    print(f"\nauto 模式将使用：{chosen.describe() if chosen else '（无可用浏览器）'}")
