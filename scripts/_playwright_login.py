"""
scripts/_playwright_login.py —— Playwright 扫码登录通用框架

职责：
- 检测 Playwright 是否按需安装；未安装时打印清晰指引并退出
- **优先复用本机已安装的 Edge / Chrome / Brave**，没有才用 Playwright 自带 Chromium
  （这样用户不必额外下载 ~150MB 的 Chromium）
- 打开指定平台的登录页，轮询检测「登录成功条件」
- 把登录后的 cookies 写回到 <SKILL_DIR>/<平台>_cookies.json
  （JSON 数组格式，字段兼容 J2TEAM Cookies 扩展，可被 cookies_utils.ensure_netscape 直接消费）

用法（各平台登录脚本会 import run_login 使用）：
    from scripts._playwright_login import run_login
    run_login(
        platform_name="bilibili",
        login_url="https://passport.bilibili.com/login",
        is_logged_in=lambda ctx: any(c["name"] == "SESSDATA" for c in ctx.cookies()),
        cookies_output=SKILL_DIR / "bilibili_cookies.json",
        cookies_domain_filter=[".bilibili.com", "bilibili.com"],
        browser="auto",     # auto | edge | chrome | brave | chromium | playwright | <exe 路径>
    )
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _browser_finder import (  # noqa: E402
    BrowserInfo,
    launch_kwargs,
    list_local_browsers,
    playwright_chromium_path,
    resolve_browser,
)


# ── 依赖检测 ────────────────────────────────────────────────────────────
def _ensure_playwright() -> None:
    """检查 playwright 库是否已安装；未安装则给出清晰指引并退出。

    注意：只检查 **库**，不再强制要求下载 Chromium ——
    本机有 Edge/Chrome 时可以直接复用，无需那 150MB。
    """
    try:
        import playwright  # noqa: F401
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        skill_dir = SCRIPT_DIR.parent
        print("\n❌ 未检测到 Playwright 库，请先按需安装：\n")
        print(f"   cd {skill_dir}")
        print("   pip install -r requirements-login.txt\n")
        print("说明：扫码登录依赖 playwright 库（约 5MB）。")
        print("     浏览器本体会优先复用你电脑上已装的 Edge / Chrome，无需额外下载。")
        print("     若本机没有任何 Chromium 系浏览器，可执行：")
        print("       python -m playwright install chromium\n")
        sys.exit(10)


def _pick_browser(prefer: str) -> BrowserInfo:
    """选定浏览器；一个都找不到时打印指引并退出。"""
    info = resolve_browser(prefer)
    if info is not None:
        return info

    skill_dir = SCRIPT_DIR.parent
    print("\n❌ 没有找到可用的浏览器。\n")
    print("   本机未检测到 Edge / Chrome / Brave / Chromium，")
    print("   Playwright 自带的 Chromium 也尚未下载。\n")
    print("   二选一即可：")
    print("     A) 安装 Microsoft Edge 或 Google Chrome（推荐，通常系统已自带 Edge）")
    print(f"     B) cd {skill_dir}")
    print("        python -m playwright install chromium    # 约 150MB\n")
    sys.exit(11)


def list_browsers() -> int:
    """打印本机可用浏览器（供各登录脚本的 --list-browsers 复用）。"""
    locals_ = list_local_browsers()
    print("\n🌐 本机已安装的浏览器（扫码登录可直接复用）：")
    if locals_:
        for b in locals_:
            print(f"   - {b.key:<9} {b.describe()}")
    else:
        print("   （未检测到 Edge / Chrome / Brave / Chromium）")
    pw_exe = playwright_chromium_path()
    print(f"\n📦 Playwright 自带 Chromium：{pw_exe or '（未下载）'}")
    chosen = resolve_browser("auto")
    print(f"\n🎯 默认（auto）将使用：{chosen.describe() if chosen else '（无可用浏览器）'}")
    print("\n用法：加 --browser edge / chrome / brave / playwright 可强制指定。\n")
    return 0


def _fallback_browser(failed: BrowserInfo) -> Optional[BrowserInfo]:
    """首选浏览器启动失败时，按顺序找下一个候选。"""
    for b in list_local_browsers():
        if b.key != failed.key:
            return b
    if failed.key != "playwright" and playwright_chromium_path():
        return BrowserInfo(key="playwright", name="Chromium")
    return None


# ── 主流程 ─────────────────────────────────────────────────────────────
def run_login(
    *,
    platform_name: str,
    login_url: str,
    is_logged_in: Callable[["object"], bool],
    cookies_output: Path,
    cookies_domain_filter: Iterable[str],
    post_login_hint: Optional[str] = None,
    poll_interval_sec: float = 1.5,
    timeout_sec: float = 600.0,
    user_data_dir: Optional[Path] = None,
    browser: str = "auto",
) -> int:
    """
    启动浏览器扫码登录，检测到登录成功后把 cookies 写入文件。

    参数：
        platform_name:     "bilibili" / "douyin"，仅用于日志
        login_url:         登录页 URL
        is_logged_in:      (BrowserContext) -> bool 的回调，轮询判定是否已登录
        cookies_output:    cookies 最终写入的路径
        cookies_domain_filter: 只保留这些域的 cookies（避免把无关域 cookies 也写进去）
        post_login_hint:   登录成功后的附加提示文本（可选）
        poll_interval_sec: 轮询间隔
        timeout_sec:       总超时（默认 10 分钟）
        user_data_dir:     若传入，使用持久化上下文（可复用浏览器登录态）
        browser:           auto（本地优先）| edge | chrome | brave | chromium
                           | playwright（强制用自带 Chromium）| <可执行文件路径>

    返回值：0 表示成功。非 0 表示异常退出码。
    """
    _ensure_playwright()
    from playwright.sync_api import sync_playwright

    chosen = _pick_browser(browser)

    domain_filter = set(cookies_domain_filter)

    def _match_domain(cookie_domain: str) -> bool:
        if not cookie_domain:
            return False
        for allowed in domain_filter:
            a = allowed.lstrip(".").lower()
            d = cookie_domain.lstrip(".").lower()
            if d == a or d.endswith("." + a):
                return True
        return False

    print(f"\n🔐 正在启动浏览器扫码登录：{platform_name}")
    print(f"   浏览器：{chosen.describe()}")
    if chosen.is_local:
        print("   ✓ 复用本机已安装的浏览器，无需下载 Chromium")
    print(f"   登录页：{login_url}")
    print(f"   超时时间：{int(timeout_sec)} 秒")
    print(f"   ⚠️  请保持浏览器窗口打开，扫码/登录成功后无需手动关闭。\n")

    launch_opts = dict(
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
        **launch_kwargs(chosen),
    )

    with sync_playwright() as pw:
        try:
            browser_obj = pw.chromium.launch(**launch_opts)
        except Exception as exc:
            print(f"⚠️  用「{chosen.name}」启动失败：{exc}")
            fallback = _fallback_browser(chosen)
            if fallback is None:
                print("\n❌ 没有其它可用浏览器了。可执行以下命令下载 Playwright 自带 Chromium：")
                print(f"   cd {SCRIPT_DIR.parent}")
                print("   python -m playwright install chromium\n")
                return 14
            print(f"   ↻ 自动改用：{fallback.describe()}\n")
            chosen = fallback
            browser_obj = pw.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                **launch_kwargs(fallback),
            )
        browser = browser_obj
        try:
            if user_data_dir is not None:
                user_data_dir.mkdir(parents=True, exist_ok=True)
            context = browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()
            try:
                page.goto(login_url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                print(f"⚠️  打开登录页超时或失败：{e}")
                print("   浏览器仍会保持打开，你可以手动处理后继续扫码。")

            deadline = time.time() + timeout_sec
            while time.time() < deadline:
                try:
                    if is_logged_in(context):
                        print("\n✅ 检测到登录成功！正在导出 cookies...")
                        break
                except Exception as e:
                    # 判定回调可能在页面跳转时临时报错，不影响下一轮
                    print(f"   (登录检测抛错，忽略继续：{e})")
                time.sleep(poll_interval_sec)
            else:
                print(f"\n❌ 登录超时（{int(timeout_sec)} 秒内未检测到登录成功状态）。")
                print("   如果确认已经登录，请直接关闭浏览器窗口；或增大超时后重试。")
                return 12

            # 导出 cookies（字段对齐 J2TEAM Cookies 扩展格式）
            all_cookies = context.cookies()
            kept: list[dict] = []
            for c in all_cookies:
                if not _match_domain(c.get("domain", "")):
                    continue
                item = {
                    "domain": c.get("domain"),
                    "hostOnly": not c.get("domain", "").startswith("."),
                    "path": c.get("path") or "/",
                    "secure": bool(c.get("secure", False)),
                    "httpOnly": bool(c.get("httpOnly", False)),
                    "session": c.get("expires", -1) in (None, -1),
                    "name": c.get("name"),
                    "value": c.get("value"),
                }
                exp = c.get("expires")
                if exp not in (None, -1):
                    item["expirationDate"] = float(exp)
                if c.get("sameSite"):
                    item["sameSite"] = c["sameSite"]
                kept.append(item)

            if not kept:
                print("\n⚠️  未捕获到任何该平台域下的 cookies。可能的原因：")
                print("   - 登录后域名不在预期列表内")
                print(f"   - 当前允许域：{sorted(domain_filter)}")
                return 13

            cookies_output.parent.mkdir(parents=True, exist_ok=True)
            cookies_output.write_text(
                json.dumps(kept, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"   ✓ 共写入 {len(kept)} 条 cookies → {cookies_output}")
            if post_login_hint:
                print(f"\n💡 {post_login_hint}")
            print("\n✅ 登录完成，可以关闭浏览器。下次运行 main.py 即可自动使用该 cookies。")
            return 0
        finally:
            try:
                browser.close()
            except Exception:
                pass
