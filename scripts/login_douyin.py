"""
scripts/login_douyin.py —— 抖音扫码登录

用法（在 SKILL_DIR 下执行）：
    python scripts/login_douyin.py
    python scripts/login_douyin.py --timeout 900       # 修改超时（秒）
    python scripts/login_douyin.py --browser edge      # 指定用本机 Edge
    python scripts/login_douyin.py --list-browsers     # 看看本机有哪些浏览器
    python scripts/login_douyin.py --check             # 校验已存 cookies 是否真有效

流程：
    1. 自动拉起浏览器窗口（优先复用本机 Edge / Chrome，没有才用 Playwright 自带
       Chromium），打开抖音首页
    2. 抖音默认会弹出登录浮层；若没弹出请手动点击「登录」按钮并切换到「扫码登录」标签
    3. 用户用抖音 App 扫码登录
    4. 脚本检测到关键登录 cookie（游客态不会有 sessionid / sid_guard）
    5. 自动把抖音相关域的 cookies 写入 <SKILL_DIR>/douyin_cookies.json
    6. 落盘后用 f2 的签名通路真实校验一次登录态，直接给出结论

抖音 cookies 有效期大约 15~30 天，过期后重跑本脚本即可。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _playwright_login import list_browsers, run_login  # noqa: E402

# Windows 默认控制台是 GBK，直接 print emoji / 部分符号会抛 UnicodeEncodeError，
# 让用户看到一堆 traceback 而不是提示内容。这里统一切到 UTF-8 并容错。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 抖音登录后会写入多个 session 相关 cookie；任一出现即视为登录成功
LOGIN_COOKIE_CANDIDATES = {"sessionid", "sessionid_ss", "sid_guard", "uid_tt"}
# 打开首页比直接登录页更稳（抖音登录页经常重定向）
LOGIN_URL = "https://www.douyin.com/?recommend=1"
DOMAIN_FILTER = [
    ".douyin.com", "douyin.com",
    ".iesdouyin.com", "iesdouyin.com",
    ".snssdk.com",  # 抖音部分鉴权 cookie 实际落在 snssdk 域
]


# 关键 cookie 出现后再多等几秒，让抖音把剩余的 sid_guard / uid_tt 等写全
COOKIE_SETTLE_SEC = 4.0


def _cookie_names_present(context) -> bool:
    """关键登录 cookie 是否出现。

    实测：纯游客态下抖音只下发 ttwid / odin_tt / passport_csrf_token 等，
    **不会**有 sessionid / sessionid_ss / sid_guard / uid_tt。
    所以这几个名字一出现就是「确实走完了登录流程」的强信号。
    """
    names = {c.get("name") for c in context.cookies() if (c.get("value") or "")}
    return bool(names & LOGIN_COOKIE_CANDIDATES)


# 轮询期间的判定状态
_verify_state: dict = {"first_seen": 0.0}


def _is_logged_in(context) -> bool:
    """登录成功判定：以关键 cookie 为准 + 几秒稳定期。

    为什么**不**在这里调接口确认：抖音 web 接口全都要 `a_bogus` 签名，
    Playwright 的 `context.request` 发的是裸请求，服务端一律回
    `status_code: 8`「用户未登录」——即使已经扫码成功。
    历史版本据此判定，会在扫码成功后打印"抖音暂未确认登录态"并空等宽限期，
    再打一段吓人的警告，纯属误报。

    登录态的真实校验改到 cookies 落盘之后、用 f2 的签名通路做（见 main()）。
    """
    if not _cookie_names_present(context):
        return False

    now = time.time()
    if not _verify_state["first_seen"]:
        _verify_state["first_seen"] = now
        print("   ✓ 已检测到登录 cookie，等待抖音写全会话信息...")
        return False
    return (now - _verify_state["first_seen"]) >= COOKIE_SETTLE_SEC


def check_saved_cookies(path: Path) -> int:
    """校验已保存的 cookies 是否真的被抖音认可（`--check`）。"""
    if not path.exists() or path.stat().st_size == 0:
        print(f"❌ cookies 文件不存在或为空：{path}")
        print("   请先执行：python scripts/login_douyin.py")
        return 2

    sys.path.insert(0, str(SKILL_DIR))
    try:
        from providers.douyin_list import check_login
    except Exception as exc:
        print(f"❌ 无法加载校验模块（需要 f2）：{exc}")
        print("   请先执行：pip install -r requirements-douyin.txt")
        return 2

    ok, msg = check_login(str(path), verbose=False)
    if ok:
        if "跳过" in msg or "无结论" in msg:
            # 没拿到服务端结论（f2 缺失 / 网络异常）时别谎报"有效"
            print(f"⚠️  未能校验登录态：{msg}")
            print(f"   cookies 已保存在 {path.name}，可稍后重试 --check。")
            return 0
        print(f"✅ 抖音登录态有效：{path.name}（{msg}）")
        print("   收藏 / 喜欢 / 收藏夹等批量下载可以正常使用。")
        return 0
    print(f"❌ 抖音登录态无效：{path.name}")
    print("   " + msg.replace("\n", "\n   "))
    return 2


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
    parser.add_argument("--check", action="store_true",
                        help="只校验已保存的 cookies 是否真的被抖音认可，然后退出")
    args = parser.parse_args()

    if args.list_browsers:
        return list_browsers()

    if args.check:
        return check_saved_cookies(Path(args.output))

    rc = run_login(
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
    if rc == 0:
        # cookies 已落盘，这时才有条件用 f2 的签名请求做真实校验
        print("\n🔎 正在校验登录态...")
        check_saved_cookies(Path(args.output))
    return rc


if __name__ == "__main__":
    sys.exit(main())
