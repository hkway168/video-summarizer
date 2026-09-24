"""
scripts/login_douyin.py —— 抖音扫码登录

用法（在 SKILL_DIR 下执行）：
    python scripts/login_douyin.py
    python scripts/login_douyin.py --timeout 900       # 修改超时（秒）
    python scripts/login_douyin.py --browser edge      # 指定用本机 Edge
    python scripts/login_douyin.py --list-browsers     # 看看本机有哪些浏览器

流程：
    1. 自动拉起浏览器窗口（优先复用本机 Edge / Chrome，没有才用 Playwright 自带
       Chromium），打开抖音首页
    2. 抖音默认会弹出登录浮层；若没弹出请手动点击「登录」按钮并切换到「扫码登录」标签
    3. 用户用抖音 App 扫码登录
    4. 脚本检测到关键 cookies（sessionid / sessionid_ss / sid_guard）出现即判定登录成功
    5. 自动把抖音相关域的 cookies 写入 <SKILL_DIR>/douyin_cookies.json

抖音 cookies 有效期大约 15~30 天，过期后重跑本脚本即可。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _playwright_login import list_browsers, run_login  # noqa: E402

# 抖音登录后会写入多个 session 相关 cookie；任一出现即视为登录成功
LOGIN_COOKIE_CANDIDATES = {"sessionid", "sessionid_ss", "sid_guard", "uid_tt"}
# 打开首页比直接登录页更稳（抖音登录页经常重定向）
LOGIN_URL = "https://www.douyin.com/?recommend=1"
DOMAIN_FILTER = [
    ".douyin.com", "douyin.com",
    ".iesdouyin.com", "iesdouyin.com",
    ".snssdk.com",  # 抖音部分鉴权 cookie 实际落在 snssdk 域
]


def _is_logged_in(context) -> bool:
    names = {c.get("name") for c in context.cookies()}
    return bool(names & LOGIN_COOKIE_CANDIDATES)


def main() -> int:
    parser = argparse.ArgumentParser(description="抖音扫码登录，自动写入 douyin_cookies.json")
    parser.add_argument("--timeout", type=float, default=600.0,
                        help="登录超时秒数，默认 600（10 分钟）")
    parser.add_argument("--output", default=str(SKILL_DIR / "douyin_cookies.json"),
                        help="cookies 输出路径，默认写到 <SKILL_DIR>/douyin_cookies.json")
    parser.add_argument("--browser", default="auto",
                        help="用哪个浏览器：auto(默认，本机优先) / edge / chrome / brave / "
                             "chromium / playwright(自带) / 或直接给 .exe 路径")
    parser.add_argument("--list-browsers", action="store_true",
                        help="列出本机可用浏览器后退出")
    args = parser.parse_args()

    if args.list_browsers:
        return list_browsers()

    return run_login(
        platform_name="douyin",
        login_url=LOGIN_URL,
        is_logged_in=_is_logged_in,
        cookies_output=Path(args.output),
        cookies_domain_filter=DOMAIN_FILTER,
        post_login_hint=(
            "抖音 cookies 有效期约 15~30 天；若之后下载再次出现 403 / 登录失效，"
            "重跑本脚本即可刷新。\n"
            "   💡 提示：抖音网页登录浮层默认是短信登录，请手动点击左侧「扫码登录」标签再扫码。"
        ),
        timeout_sec=args.timeout,
        browser=args.browser,
    )


if __name__ == "__main__":
    sys.exit(main())
