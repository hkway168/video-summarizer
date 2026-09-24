"""
scripts/login_bilibili.py —— 哔哩哔哩扫码登录

用法（在 SKILL_DIR 下执行）：
    python scripts/login_bilibili.py
    python scripts/login_bilibili.py --timeout 900       # 修改超时（秒）
    python scripts/login_bilibili.py --browser edge      # 指定用本机 Edge
    python scripts/login_bilibili.py --list-browsers     # 看看本机有哪些浏览器

流程：
    1. 自动拉起浏览器窗口（优先复用本机 Edge / Chrome，没有才用 Playwright 自带
       Chromium），打开 B 站登录页
    2. 用户用 B 站 App 扫码登录
    3. 脚本检测到 SESSDATA cookie 出现即判定登录成功
    4. 自动把 .bilibili.com 域下的全部 cookies 写入 <SKILL_DIR>/bilibili_cookies.json
       （JSON 数组，字段兼容 J2TEAM Cookies 扩展，可被 cookies_utils 直接消费）

B 站 cookies 有效期大约 30 天，过期后重跑本脚本即可。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 确保可以以 `python scripts/login_bilibili.py` 方式直接运行
SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _playwright_login import list_browsers, run_login  # noqa: E402

# 必备 cookie：存在即视为登录成功
REQUIRED_COOKIES = {"SESSDATA"}
LOGIN_URL = "https://passport.bilibili.com/login"
DOMAIN_FILTER = [".bilibili.com", "bilibili.com"]


def _is_logged_in(context) -> bool:
    names = {c.get("name") for c in context.cookies()}
    return REQUIRED_COOKIES.issubset(names)


def main() -> int:
    parser = argparse.ArgumentParser(description="哔哩哔哩扫码登录，自动写入 bilibili_cookies.json")
    parser.add_argument("--timeout", type=float, default=600.0,
                        help="登录超时秒数，默认 600（10 分钟）")
    parser.add_argument("--output", default=str(SKILL_DIR / "bilibili_cookies.json"),
                        help="cookies 输出路径，默认写到 <SKILL_DIR>/bilibili_cookies.json")
    parser.add_argument("--browser", default="auto",
                        help="用哪个浏览器：auto(默认，本机优先) / edge / chrome / brave / "
                             "chromium / playwright(自带) / 或直接给 .exe 路径")
    parser.add_argument("--list-browsers", action="store_true",
                        help="列出本机可用浏览器后退出")
    args = parser.parse_args()

    if args.list_browsers:
        return list_browsers()

    return run_login(
        platform_name="bilibili",
        login_url=LOGIN_URL,
        is_logged_in=_is_logged_in,
        cookies_output=Path(args.output),
        cookies_domain_filter=DOMAIN_FILTER,
        post_login_hint=(
            "B 站 cookies 有效期约 30 天；若之后下载再次出现 403 / 登录失效，"
            "重跑本脚本即可刷新。"
        ),
        timeout_sec=args.timeout,
        browser=args.browser,
    )


if __name__ == "__main__":
    sys.exit(main())
