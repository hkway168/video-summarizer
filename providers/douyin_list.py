"""
providers/douyin_list.py —— 抖音「列表型批量」展开器（基于 f2）

解决什么问题
------------
项目原有的下载链路是「一个 URL → 一个文件」，所谓"批量"只是把多个**已知**
URL 逐个下载。但下面这些需求给不出具体视频 URL：

    1. 任意对方个人主页的全部作品          → KIND_POST
    2. 本人「收藏」列表                    → KIND_COLLECTION
    3. 本人（或对方公开的）「喜欢/点赞」列表 → KIND_LIKE
    4. 本人某个收藏夹                      → KIND_COLLECTS

本模块负责把这些「列表型目标」**展开**成一串标准视频 URL
（https://www.douyin.com/video/{aweme_id}），交回给
`media_downloader.download_media()` 逐个下载——因此下载环节的 cookies 降级、
命名、产物定位、f2/yt-dlp 双通路全部原样复用，改动面最小。

为什么必须用 f2
---------------
yt-dlp 的抖音 extractor 不支持主页/收藏/喜欢这类聚合页，且已被风控阻断；
f2 实现了 a_bogus 签名，能正常翻页。所以列表能力是 f2 的**硬依赖**，
未安装时抛 `F2NotAvailableError`，由上层给出安装提示（而不是静默回退）。

登录态要求
----------
    · 对方主页作品：需要有效 cookies（游客态基本被风控）
    · 收藏 / 收藏夹：**必须本人登录态**（接口纯靠 cookie 鉴权，无 sec_user_id 参数）
    · 喜欢：需要 sec_user_id；本人默认自动解析，对方账号需其"公开点赞列表"

用法
----
    from providers.douyin_list import expand_target, KIND_POST

    items = expand_target(KIND_POST, "https://www.douyin.com/user/MS4wL...",
                          cookies_file="douyin_cookies.json", limit=50)
    urls = [it.url for it in items]
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlparse

from .douyin_f2 import (
    F2CookiesRequiredError,
    F2NotAvailableError,
    _DEFAULT_UA,
    _LOG_NAMES as _F2_LOG_NAMES,
    _load_cookie_header,
    _make_handler,
    _silence_f2_logs,
    is_available,
)

# ── 目标类型 ────────────────────────────────────────────────────────────
KIND_POST = "post"              # 某用户主页发布的作品
KIND_LIKE = "like"              # 某用户点赞（喜欢）的作品
KIND_COLLECTION = "collection"  # 本人收藏（无法指定他人）
KIND_COLLECTS = "collects"      # 本人某个收藏夹

ALL_KINDS = (KIND_POST, KIND_LIKE, KIND_COLLECTION, KIND_COLLECTS)

KIND_LABELS = {
    KIND_POST: "主页作品",
    KIND_LIKE: "喜欢（点赞）",
    KIND_COLLECTION: "收藏",
    KIND_COLLECTS: "收藏夹",
}

# 需要「本人登录态」才可能成功的类型
SELF_ONLY_KINDS = (KIND_COLLECTION, KIND_COLLECTS)

# 本人 sec_user_id 的缓存文件（放项目根，与 cookies 同级）
SELF_CACHE_FILENAME = "douyin_self.json"
SELF_CACHE_TTL = 30 * 24 * 3600     # 30 天

# 列表分页之间的等待秒数。f2 用 kwargs["timeout"] 同时表示
# httpx 超时和翻页间隔，8 秒是"够用且不至于太慢"的折中。
DEFAULT_PAGE_INTERVAL = 8
DEFAULT_PAGE_SIZE = 20

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# sec_uid 形态：MS4wLjABAAAA + base64url（真实值后缀通常 40+ 字符，
# 这里放宽到 4 以免个别变体漏匹配）
_SEC_UID_RE = re.compile(r"(MS4wLjABAAAA[A-Za-z0-9_\-]{4,})")
# 图集类作品的 aweme_type
_IMAGE_AWEME_TYPES = {68, 2}


class DouyinListError(RuntimeError):
    """列表展开失败（含登录态不足、目标解析不出等）。"""


class DouyinSelfIdError(DouyinListError):
    """无法确定"本人"的 sec_user_id。"""


class DouyinLoginExpiredError(DouyinListError):
    """cookies 文件存在，但抖音服务端不认这个登录态。

    典型表现：接口返回 HTTP 200 + `{"status_code": 8, "status_msg": "用户未登录"}`，
    或 POST 类接口（收藏）直接返回**空响应体**。
    注意这与"没有 cookies 文件"是两种不同的失败，提示语也应不同。
    """


# 抖音「未登录」的业务状态码（HTTP 仍是 200）
_STATUS_NOT_LOGGED_IN = 8

_LOGIN_EXPIRED_HINT = (
    "抖音不认当前 cookies 的登录态（接口返回「用户未登录」）。\n"
    "    cookies 里有 sessionid 说明**确实登录过**，但该 session 已被服务端作废\n"
    "    （常见原因：在手机/其他浏览器上退出登录、异地登录挤掉、或放置过久）。\n"
    "    请重新扫码登录：python scripts/login_douyin.py\n"
    "    登录后自检：python scripts/login_douyin.py --check"
)


# ══════════════════════════════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════════════════════════════
@dataclass
class AwemeItem:
    """列表里的一条作品。`url` 就是交给下载器的标准视频链接。"""
    aweme_id: str
    url: str = ""
    desc: str = ""
    nickname: str = ""
    sec_user_id: str = ""
    create_time: str = ""
    duration: float = 0.0
    aweme_type: int = 0
    is_image: bool = False
    play_addr: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.url:
            self.url = f"https://www.douyin.com/video/{self.aweme_id}"

    def summary(self, width: int = 48) -> str:
        desc = (self.desc or "").replace("\n", " ").strip()
        if len(desc) > width:
            desc = desc[: width - 1] + "…"
        bits = [self.aweme_id]
        if self.nickname:
            bits.append(f"@{self.nickname}")
        if desc:
            bits.append(desc)
        return "  ".join(bits)


@dataclass
class CollectsFolder:
    """收藏夹（合集）条目。"""
    collects_id: str
    name: str = ""
    total: int = 0


@dataclass
class BatchTarget:
    """一次批量展开的目标描述。"""
    kind: str
    sec_user_id: str = ""
    collects_id: str = ""
    raw: str = ""
    is_self: bool = False
    extra: dict = field(default_factory=dict)

    def label(self) -> str:
        base = KIND_LABELS.get(self.kind, self.kind)
        if self.kind == KIND_COLLECTS and self.collects_id:
            return f"{base} {self.collects_id}"
        if self.is_self or not self.sec_user_id:
            return f"本人{base}"
        return f"{base}（{self.sec_user_id[:20]}…）"


# ══════════════════════════════════════════════════════════════════════
# cookies / 前置检查
# ══════════════════════════════════════════════════════════════════════
def _require_f2() -> None:
    if not is_available():
        raise F2NotAvailableError(
            "抖音批量下载（主页/收藏/喜欢）依赖 f2 库，但它未安装或导入失败。\n"
            "请执行：pip install -r requirements-douyin.txt"
        )


def _require_cookies(cookies_file: Optional[str | Path]) -> str:
    if not cookies_file:
        raise F2CookiesRequiredError(
            "抖音批量下载需要登录态 cookies（douyin_cookies.json）。\n"
            "请先执行：python scripts/login_douyin.py 扫码登录。"
        )
    path = Path(cookies_file)
    if not path.exists():
        raise F2CookiesRequiredError(f"cookies 文件不存在：{path}")
    try:
        return _load_cookie_header(path)
    except Exception as exc:
        raise F2CookiesRequiredError(f"cookies 文件不可用（{path}）：{exc}") from exc


def probe_login(cookie_header: str) -> tuple[Optional[bool], str]:
    """用 f2 的**签名请求**确认登录态。

    为什么必须走 f2：抖音 web 接口全部要求 `a_bogus` 签名 + 一整套设备参数。
    自己用 httpx 裸调 `/aweme/v1/web/user/...` 时：
        · 不带参数   → HTTP 200 + `status_code: 8`「用户未登录」
        · 带参数不签名 → HTTP 200 + `status_code: 5`「参数不合法」
    也就是说裸调**永远**判成"未登录"，即使 cookies 刚扫码拿到、完全有效。
    历史版本正是这样误报的（登录后立刻 --check 也说无效）。

    `fetch_query_user` 走 f2 的签名通路，只读当前 cookie 对应的账号信息，
    不改变任何状态，响应体极小：
        已登录 → status_code=0 且带 user_uid
        未登录 → status_code=8 或拿不到 user_uid

    Returns:
        (verdict, message)
        verdict=True  服务端确认已登录
        verdict=False 服务端明确不认这个登录态
        verdict=None  没结论（f2 未安装 / 网络异常），**不可当作未登录**
    """
    if not is_available():
        return None, "登录态预检跳过（未安装 f2）"

    _quiet_f2_console()

    # f2 在这个接口上会无脑抱怨「请提供正确的 ttwid」，跟登录态无关，别让它误导用户。
    # 两个坑：
    #   1) _silence_f2_logs 给每个子 logger 都显式设过 level，只调父 logger "f2"
    #      压不住，必须逐个抬到 ERROR；
    #   2) _make_handler 内部又会调一次 _silence_f2_logs 把等级降回 WARNING，
    #      所以必须在 handler 构造**之后**才抬等级。
    _saved_levels = [(logging.getLogger(n), logging.getLogger(n).level)
                     for n in _F2_LOG_NAMES]

    async def _run() -> dict:
        handler = _make_handler(cookie_header, timeout=10)
        for lg, _ in _saved_levels:
            lg.setLevel(logging.ERROR)
        user = await handler.fetch_query_user()
        return user._to_dict() if hasattr(user, "_to_dict") else {}

    try:
        data = asyncio.run(_run())
    except Exception as exc:
        # 网络问题不应误判成登录失效，放行让后续真实请求去报错
        return None, f"登录态预检跳过（{exc}）"
    finally:
        for lg, lvl in _saved_levels:
            lg.setLevel(lvl)

    code = data.get("status_code")
    uid = str(data.get("user_uid") or "").strip()
    if code is not None and int(code) == _STATUS_NOT_LOGGED_IN:
        return False, _LOGIN_EXPIRED_HINT
    if not uid or uid == "0":
        if code is None:
            return None, "登录态预检无结论（接口未返回状态）"
        return False, _LOGIN_EXPIRED_HINT
    return True, f"登录态有效（uid {uid}）"


def check_login(
    cookies_file: Optional[str | Path],
    *,
    verbose: bool = False,
) -> tuple[bool, str]:
    """判定 cookies 的登录态是否真的被抖音认可。

    为什么需要这一步：抖音对「公开数据」（单视频、他人主页）和「本人私有数据」
    （收藏 / 喜欢 / 收藏夹）用不同的鉴权强度。一份游客态或已作废的 cookies
    读公开数据完全正常，但访问私有列表时只会返回 `status_code: 8`，
    甚至（收藏接口）返回**空响应体**——不预检的话报错会非常含糊。

    只有在服务端**明确**否认登录态时才返回 False；没结论一律放行，
    避免把网络抖动、缺依赖当成"登录失效"把用户挡在门外。

    Returns:
        (ok, message)：ok=False 时 message 是可直接展示给用户的原因。
    """
    try:
        cookie_header = _require_cookies(cookies_file)
    except F2CookiesRequiredError as exc:
        return False, str(exc)

    verdict, msg = probe_login(cookie_header)
    if verdict is False:
        return False, msg
    if verbose:
        print(f"    [f2] {msg}")
    return True, msg


def _assert_page_logged_in(page: Any) -> None:
    """列表响应若是「用户未登录」，立刻抛出可读错误，别让它退化成"列表为空"。"""
    try:
        code = getattr(page, "status_code", None)
    except Exception:
        return
    if code is not None and int(code or 0) == _STATUS_NOT_LOGGED_IN:
        raise DouyinLoginExpiredError(_LOGIN_EXPIRED_HINT)


def _quiet_f2_console() -> None:
    """把 f2 handler 里的 rich Console 换成静音版，避免翻页分隔线刷屏。"""
    _silence_f2_logs()
    try:
        import io

        from rich.console import Console

        from f2.apps.douyin import handler as _h

        if not getattr(_h, "_vs_quiet_patched", False):
            _h.rich_console = Console(file=io.StringIO(), quiet=True)
            _h._vs_quiet_patched = True   # type: ignore[attr-defined]
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════
# 目标解析：URL / sec_uid / self
# ══════════════════════════════════════════════════════════════════════
def extract_sec_user_id(target: str) -> Optional[str]:
    """从主页 URL 或裸 sec_uid 中提取 sec_user_id。

    支持：
        MS4wLjABAAAAxxxx
        https://www.douyin.com/user/MS4wLjABAAAAxxxx
        https://www.douyin.com/user/MS4wLjABAAAAxxxx?showTab=like
        任意含 sec_uid=MS4w... 的链接
    取不到返回 None（例如短链 v.douyin.com，需要先解析重定向）。
    """
    if not target:
        return None
    m = _SEC_UID_RE.search(target.strip())
    return m.group(1) if m else None


def is_self_target(target: str) -> bool:
    """判断目标是否指向"本人"（/user/self 或字面量 self/me）。"""
    if not target:
        return False
    t = target.strip().lower()
    if t in ("self", "me", "本人", "我"):
        return True
    try:
        path = (urlparse(t).path or "").rstrip("/")
    except Exception:
        return False
    return path in ("/user/self", "/user/me")


def parse_batch_target(target: str) -> Optional[BatchTarget]:
    """把用户粘贴的一行文本解析成 BatchTarget；不是列表型目标则返回 None。

    识别规则（按优先级）：
        · 含 modal_id / aweme_id 的链接 → 单个视频，返回 None（交给原链路）
        · /video/{id}、/share/video/{id} → 单个视频，返回 None
        · /collection/{id} 或 /favorite_collection → 收藏夹 / 收藏
        · showTab=like                  → 喜欢
        · showTab=favorite_collection / collection → 收藏
        · /user/self                    → 本人主页作品
        · /user/{sec_uid} 或裸 sec_uid  → 该用户主页作品
    """
    if not target:
        return None
    raw = target.strip().strip('"')
    if not raw or raw.startswith("#"):
        return None

    low = raw.lower()

    # ── 明确是单个视频：让原链路处理 ──
    try:
        parsed = urlparse(raw if "://" in raw else "https://" + raw)
    except Exception:
        parsed = None

    if parsed is not None and parsed.query:
        q = parse_qs(parsed.query)
        if q.get("modal_id") or q.get("aweme_id"):
            return None
    if parsed is not None and re.search(r"/(?:share/)?video/\d+", parsed.path or "", re.I):
        return None

    path = ((parsed.path if parsed is not None else "") or "").rstrip("/")
    query = parse_qs(parsed.query) if (parsed is not None and parsed.query) else {}
    show_tab = (query.get("showTab") or query.get("showtab") or [""])[0].lower()

    self_flag = is_self_target(raw)
    sec_uid = extract_sec_user_id(raw)

    # ── 收藏夹：/collection/7123456789 ──
    m_col = re.search(r"/collection/(\d+)", path, re.I)
    if m_col:
        return BatchTarget(kind=KIND_COLLECTS, collects_id=m_col.group(1),
                           raw=raw, is_self=True)

    # ── 裸关键字（GUI 下拉框会直接传这些） ──
    if low in ("collection", "favorite", "favorites", "收藏"):
        return BatchTarget(kind=KIND_COLLECTION, raw=raw, is_self=True)
    if low in ("like", "likes", "喜欢", "点赞"):
        return BatchTarget(kind=KIND_LIKE, raw=raw, is_self=True)

    # ── showTab 页签 ──
    if show_tab:
        if "like" in show_tab:
            return BatchTarget(kind=KIND_LIKE, sec_user_id=sec_uid or "",
                               raw=raw, is_self=self_flag or not sec_uid)
        if "collection" in show_tab or "favorite" in show_tab:
            # 收藏只能是本人
            return BatchTarget(kind=KIND_COLLECTION, raw=raw, is_self=True)

    # ── 个人主页 ──
    if self_flag:
        return BatchTarget(kind=KIND_POST, raw=raw, is_self=True)
    if sec_uid and (path.startswith("/user/") or parsed is None or "://" not in raw):
        return BatchTarget(kind=KIND_POST, sec_user_id=sec_uid, raw=raw)
    if sec_uid and path.startswith("/user/"):
        return BatchTarget(kind=KIND_POST, sec_user_id=sec_uid, raw=raw)

    return None


# ══════════════════════════════════════════════════════════════════════
# 本人 sec_user_id 解析（带本地缓存）
# ══════════════════════════════════════════════════════════════════════
def _self_cache_path() -> Path:
    return _PROJECT_ROOT / SELF_CACHE_FILENAME


def _read_self_cache() -> Optional[str]:
    p = _self_cache_path()
    try:
        if not p.exists():
            return None
        data = json.loads(p.read_text("utf-8"))
        sec_uid = (data or {}).get("sec_user_id") or ""
        cached_at = float((data or {}).get("cached_at") or 0)
        if sec_uid and (time.time() - cached_at) < SELF_CACHE_TTL:
            return sec_uid
    except Exception:
        pass
    return None


def _write_self_cache(sec_user_id: str, nickname: str = "") -> None:
    try:
        _self_cache_path().write_text(json.dumps({
            "sec_user_id": sec_user_id,
            "nickname": nickname,
            "cached_at": time.time(),
        }, ensure_ascii=False, indent=2), "utf-8")
    except Exception:
        pass


def resolve_self_sec_user_id(
    cookies_file: Optional[str | Path],
    *,
    verbose: bool = False,
    refresh: bool = False,
) -> str:
    """拿到「本人」的 sec_user_id。

    抖音没有直接返回自己 sec_uid 的公开接口，这里走两步：
        1) 请求 https://www.douyin.com/user/self 且 **不跟随重定向**，
           302 的 Location 就是 /user/MS4wL...；
        2) 万一没重定向，跟随到底后在 HTML 里正则搜 sec_uid。
    结果缓存到项目根 douyin_self.json（30 天）。
    """
    if not refresh:
        cached = _read_self_cache()
        if cached:
            if verbose:
                print(f"    [f2] 本人 sec_user_id（缓存）：{cached[:28]}…")
            return cached

    cookie_header = _require_cookies(cookies_file)
    try:
        import httpx
    except Exception as exc:      # pragma: no cover - httpx 随 f2 一起装
        raise DouyinSelfIdError(f"缺少 httpx，无法解析本人主页：{exc}") from exc

    headers = {
        "User-Agent": _DEFAULT_UA,
        "Referer": "https://www.douyin.com/",
        "Cookie": cookie_header,
    }
    url = "https://www.douyin.com/user/self"

    sec_uid: Optional[str] = None
    try:
        with httpx.Client(headers=headers, timeout=20.0, follow_redirects=False) as cli:
            resp = cli.get(url)
            loc = resp.headers.get("location") or ""
            sec_uid = extract_sec_user_id(loc)
            if not sec_uid and resp.status_code < 400:
                # 没有 302，改为跟随并在 HTML 里找
                resp2 = cli.get(url, follow_redirects=True)
                sec_uid = (extract_sec_user_id(str(resp2.url))
                           or extract_sec_user_id(resp2.text[:400_000]))
    except Exception as exc:
        raise DouyinSelfIdError(
            f"请求抖音个人主页失败：{exc}\n"
            "请检查网络，或重新执行 python scripts/login_douyin.py 刷新登录态。"
        ) from exc

    if not sec_uid:
        raise DouyinSelfIdError(
            "无法自动识别本人 sec_user_id（登录态可能已失效）。\n"
            "解决办法：\n"
            "  1) 重新扫码登录：python scripts/login_douyin.py\n"
            "  2) 或手动指定：浏览器打开自己的主页，复制地址栏里的 "
            "https://www.douyin.com/user/MS4wLjABAAAA... 作为 --douyin-user 参数"
        )

    if verbose:
        print(f"    [f2] 已识别本人 sec_user_id：{sec_uid[:28]}…")
    _write_self_cache(sec_uid)
    return sec_uid


# ══════════════════════════════════════════════════════════════════════
# 一页 filter → AwemeItem 列表
# ══════════════════════════════════════════════════════════════════════
def _as_list(value: Any, size: int) -> list:
    """f2 的 filter 属性单条时可能返回标量，统一补成 size 长度的 list。"""
    if value is None:
        return [None] * size
    if isinstance(value, (list, tuple)):
        out = list(value)
    else:
        out = [value]
    if len(out) < size:
        out += [None] * (size - len(out))
    return out


def _pick_play_addr(value: Any) -> Optional[str]:
    """video_play_addr 每项是 url_list（list），取第一个非空。"""
    if isinstance(value, (list, tuple)):
        for v in value:
            if isinstance(v, str) and v:
                return v
            picked = _pick_play_addr(v)
            if picked:
                return picked
        return None
    return value if isinstance(value, str) and value else None


def _page_to_items(page: Any, fallback_sec_uid: str = "") -> list[AwemeItem]:
    """把一页 UserPostFilter / UserCollectionFilter 拍平成 AwemeItem 列表。"""
    try:
        ids = page.aweme_id or []
    except Exception:
        return []
    if not isinstance(ids, (list, tuple)):
        ids = [ids]
    ids = [str(i) for i in ids if i]
    if not ids:
        return []

    n = len(ids)

    def col(name: str) -> list:
        try:
            return _as_list(getattr(page, name, None), n)
        except Exception:
            return [None] * n

    descs = col("desc")
    nicknames = col("nickname")
    sec_uids = col("sec_user_id")
    create_times = col("create_time")
    durations = col("video_duration")
    types = col("aweme_type")
    images = col("images")
    play_addrs = col("video_play_addr")

    items: list[AwemeItem] = []
    for i, aweme_id in enumerate(ids):
        try:
            duration_ms = float(durations[i] or 0)
        except (TypeError, ValueError):
            duration_ms = 0.0
        try:
            aweme_type = int(types[i] or 0)
        except (TypeError, ValueError):
            aweme_type = 0
        items.append(AwemeItem(
            aweme_id=aweme_id,
            desc=str(descs[i] or ""),
            nickname=str(nicknames[i] or ""),
            sec_user_id=str(sec_uids[i] or fallback_sec_uid or ""),
            create_time=str(create_times[i] or ""),
            duration=duration_ms / 1000.0 if duration_ms > 1000 else duration_ms,
            aweme_type=aweme_type,
            is_image=bool(images[i]) or aweme_type in _IMAGE_AWEME_TYPES,
            play_addr=_pick_play_addr(play_addrs[i]),
        ))
    return items


def _dedupe(items: list[AwemeItem]) -> list[AwemeItem]:
    seen: set[str] = set()
    out: list[AwemeItem] = []
    for it in items:
        if it.aweme_id in seen:
            continue
        seen.add(it.aweme_id)
        out.append(it)
    return out


# ══════════════════════════════════════════════════════════════════════
# 列表抓取（对外同步接口）
# ══════════════════════════════════════════════════════════════════════
ProgressFn = Callable[[int, str], None]


def _default_progress(count: int, note: str) -> None:
    print(f"    [f2] 已抓取 {count} 条{('　' + note) if note else ''}")


async def _drain(
    agen: Any,
    *,
    limit: int,
    fallback_sec_uid: str,
    on_progress: Optional[ProgressFn],
    note: str,
) -> list[AwemeItem]:
    """消费 f2 的分页 async generator（它内部已处理 max_cursor 翻页）。"""
    items: list[AwemeItem] = []
    pages = 0
    try:
        async for page in agen:
            pages += 1
            _assert_page_logged_in(page)
            page_items = _page_to_items(page, fallback_sec_uid)
            if page_items:
                items.extend(page_items)
                if on_progress:
                    on_progress(len(items), f"第 {pages} 页 {note}".strip())
            if limit and len(items) >= limit:
                break
    finally:
        # 提前 break 时主动关闭 generator，避免 f2 内部连接泄漏
        aclose = getattr(agen, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:
                pass

    items = _dedupe(items)
    return items[:limit] if limit else items


def _run_listing(
    builder: Callable[[Any], Any],
    *,
    cookie_header: str,
    limit: int,
    page_size: int,
    page_interval: int,
    fallback_sec_uid: str,
    on_progress: Optional[ProgressFn],
    note: str,
) -> list[AwemeItem]:
    """统一的「构造 handler → 消费 generator」同步封装。"""
    _quiet_f2_console()

    async def _run() -> list[AwemeItem]:
        handler = _make_handler(cookie_header,
                                timeout=page_interval,
                                page_counts=page_size)
        agen = builder(handler)
        return await _drain(agen, limit=limit, fallback_sec_uid=fallback_sec_uid,
                            on_progress=on_progress, note=note)

    try:
        return asyncio.run(_run())
    except DouyinListError:
        raise
    except Exception as exc:
        # 收藏接口在登录态失效时返回**空响应体**（而非 status_code:8），
        # f2 重试 3 次后抛 APIRetryExhaustedError。这里翻译成人能看懂的原因。
        if type(exc).__name__ == "APIRetryExhaustedError" or "次数达到上限" in str(exc):
            raise DouyinLoginExpiredError(
                f"{_LOGIN_EXPIRED_HINT}\n"
                "    （该接口连续返回空响应，通常就是登录态问题；"
                "若确认已登录，也可能是被临时限流，稍后重试或加大 --page-interval）"
            ) from exc
        raise


def list_user_posts(
    sec_user_id: str,
    cookies_file: Optional[str | Path] = None,
    *,
    limit: int = 0,
    page_size: int = DEFAULT_PAGE_SIZE,
    page_interval: int = DEFAULT_PAGE_INTERVAL,
    on_progress: Optional[ProgressFn] = None,
    verbose: bool = False,
) -> list[AwemeItem]:
    """列出某用户主页发布的全部作品（limit=0 表示不限）。"""
    _require_f2()
    cookie_header = _require_cookies(cookies_file)
    if not sec_user_id:
        raise DouyinListError("缺少 sec_user_id，无法列出主页作品")
    if verbose:
        print(f"    [f2] 列出主页作品：{sec_user_id[:28]}…"
              f"（上限 {limit or '不限'}）")
    return _run_listing(
        lambda h: h.fetch_user_post_videos(
            sec_user_id=sec_user_id,
            max_cursor=0,
            page_counts=page_size,
            max_counts=limit or None,
        ),
        cookie_header=cookie_header,
        limit=limit,
        page_size=page_size,
        page_interval=page_interval,
        fallback_sec_uid=sec_user_id,
        on_progress=on_progress,
        note="主页作品",
    )


def list_user_likes(
    sec_user_id: str,
    cookies_file: Optional[str | Path] = None,
    *,
    limit: int = 0,
    page_size: int = DEFAULT_PAGE_SIZE,
    page_interval: int = DEFAULT_PAGE_INTERVAL,
    on_progress: Optional[ProgressFn] = None,
    verbose: bool = False,
) -> list[AwemeItem]:
    """列出某用户「喜欢（点赞）」的作品。

    注意：抖音默认隐藏点赞列表。本人账号用自己的 sec_user_id 可读；
    他人账号必须其主动开启「公开我的点赞」，否则返回空列表。
    """
    _require_f2()
    cookie_header = _require_cookies(cookies_file)
    if not sec_user_id:
        raise DouyinListError("缺少 sec_user_id，无法列出喜欢列表")
    if verbose:
        print(f"    [f2] 列出喜欢列表：{sec_user_id[:28]}…"
              f"（上限 {limit or '不限'}）")
    return _run_listing(
        lambda h: h.fetch_user_like_videos(
            sec_user_id=sec_user_id,
            max_cursor=0,
            page_counts=page_size,
            max_counts=limit or None,
        ),
        cookie_header=cookie_header,
        limit=limit,
        page_size=page_size,
        page_interval=page_interval,
        fallback_sec_uid=sec_user_id,
        on_progress=on_progress,
        note="喜欢",
    )


def list_my_collection(
    cookies_file: Optional[str | Path] = None,
    *,
    limit: int = 0,
    page_size: int = DEFAULT_PAGE_SIZE,
    page_interval: int = DEFAULT_PAGE_INTERVAL,
    on_progress: Optional[ProgressFn] = None,
    verbose: bool = False,
) -> list[AwemeItem]:
    """列出**本人**收藏的作品（接口纯靠 cookie 鉴权，无法指定他人）。"""
    _require_f2()
    cookie_header = _require_cookies(cookies_file)
    if verbose:
        print(f"    [f2] 列出本人收藏（上限 {limit or '不限'}）")
    return _run_listing(
        lambda h: h.fetch_user_collection_videos(
            max_cursor=0,
            page_counts=page_size,
            max_counts=limit or None,
        ),
        cookie_header=cookie_header,
        limit=limit,
        page_size=page_size,
        page_interval=page_interval,
        fallback_sec_uid="",
        on_progress=on_progress,
        note="收藏",
    )


def list_collects_videos(
    collects_id: str,
    cookies_file: Optional[str | Path] = None,
    *,
    limit: int = 0,
    page_size: int = DEFAULT_PAGE_SIZE,
    page_interval: int = DEFAULT_PAGE_INTERVAL,
    on_progress: Optional[ProgressFn] = None,
    verbose: bool = False,
) -> list[AwemeItem]:
    """列出**本人**某个收藏夹里的作品。"""
    _require_f2()
    cookie_header = _require_cookies(cookies_file)
    if not collects_id:
        raise DouyinListError("缺少收藏夹 ID，无法列出收藏夹内容")
    if verbose:
        print(f"    [f2] 列出收藏夹 {collects_id}（上限 {limit or '不限'}）")
    return _run_listing(
        lambda h: h.fetch_user_collects_videos(
            collects_id=collects_id,
            max_cursor=0,
            page_counts=page_size,
            max_counts=limit or None,
        ),
        cookie_header=cookie_header,
        limit=limit,
        page_size=page_size,
        page_interval=page_interval,
        fallback_sec_uid="",
        on_progress=on_progress,
        note="收藏夹",
    )


def list_collects_folders(
    cookies_file: Optional[str | Path] = None,
    *,
    page_size: int = DEFAULT_PAGE_SIZE,
    page_interval: int = DEFAULT_PAGE_INTERVAL,
    verbose: bool = False,
) -> list[CollectsFolder]:
    """列出**本人**的所有收藏夹（供用户选择要下载哪个）。"""
    _require_f2()
    cookie_header = _require_cookies(cookies_file)
    ok, msg = check_login(cookies_file, verbose=verbose)
    if not ok:
        raise DouyinLoginExpiredError(msg)
    _quiet_f2_console()

    async def _run() -> list[CollectsFolder]:
        handler = _make_handler(cookie_header,
                                timeout=page_interval,
                                page_counts=page_size)
        out: list[CollectsFolder] = []
        agen = handler.fetch_user_collects(max_cursor=0, page_counts=page_size)
        try:
            async for page in agen:
                _assert_page_logged_in(page)
                ids = getattr(page, "collects_id", None) or []
                if not isinstance(ids, (list, tuple)):
                    ids = [ids]
                ids = [str(i) for i in ids if i]
                if not ids:
                    break
                names = _as_list(getattr(page, "collects_name", None), len(ids))
                totals = _as_list(getattr(page, "total_number", None), len(ids))
                for i, cid in enumerate(ids):
                    try:
                        total = int(totals[i] or 0)
                    except (TypeError, ValueError):
                        total = 0
                    out.append(CollectsFolder(collects_id=cid,
                                              name=str(names[i] or ""),
                                              total=total))
                if not getattr(page, "has_more", False):
                    break
        finally:
            aclose = getattr(agen, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception:
                    pass
        return out

    folders = asyncio.run(_run())
    if verbose:
        print(f"    [f2] 共 {len(folders)} 个收藏夹")
    return folders


# ══════════════════════════════════════════════════════════════════════
# 统一入口
# ══════════════════════════════════════════════════════════════════════
def expand_target(
    kind: str,
    target: str = "",
    cookies_file: Optional[str | Path] = None,
    *,
    limit: int = 0,
    page_size: int = DEFAULT_PAGE_SIZE,
    page_interval: int = DEFAULT_PAGE_INTERVAL,
    include_images: bool = False,
    on_progress: Optional[ProgressFn] = None,
    verbose: bool = False,
) -> list[AwemeItem]:
    """把一个列表型目标展开成 AwemeItem 列表。

    Args:
        kind: KIND_POST / KIND_LIKE / KIND_COLLECTION / KIND_COLLECTS
        target: 主页 URL、sec_uid、"self"，或收藏夹 ID（KIND_COLLECTS）
        limit: 最多取多少条，0 = 不限
        include_images: 是否保留图集类作品（默认剔除，因为下载器只处理视频）
    """
    _require_f2()
    kind = (kind or "").strip().lower()
    if kind not in ALL_KINDS:
        raise DouyinListError(f"不支持的批量类型：{kind}（可选：{', '.join(ALL_KINDS)}）")

    # 私有列表（收藏/收藏夹）和「本人喜欢」必须真有登录态，先快速预检，
    # 否则 f2 会重试 3 次后抛出含糊的"次数达到上限"
    needs_login = kind in SELF_ONLY_KINDS or (
        kind == KIND_LIKE and (not target or is_self_target(target)))
    if needs_login:
        ok, msg = check_login(cookies_file, verbose=verbose)
        if not ok:
            raise DouyinLoginExpiredError(msg)

    common = dict(limit=limit, page_size=page_size, page_interval=page_interval,
                  on_progress=on_progress, verbose=verbose)

    if kind == KIND_COLLECTION:
        items = list_my_collection(cookies_file, **common)      # type: ignore[arg-type]
    elif kind == KIND_COLLECTS:
        collects_id = (target or "").strip()
        m = re.search(r"(\d{6,})", collects_id)
        items = list_collects_videos(m.group(1) if m else collects_id,
                                     cookies_file, **common)    # type: ignore[arg-type]
    else:
        sec_uid = extract_sec_user_id(target)
        if not sec_uid:
            if not target or is_self_target(target):
                sec_uid = resolve_self_sec_user_id(cookies_file, verbose=verbose)
            else:
                raise DouyinListError(
                    f"无法从「{target}」解析出抖音用户 ID。\n"
                    "请传入完整主页链接（形如 https://www.douyin.com/user/MS4wLjABAAAA...）、"
                    "sec_uid 本身，或 self 表示本人。\n"
                    "提示：v.douyin.com 短链请先在浏览器打开，再复制跳转后的主页地址。"
                )
        if kind == KIND_POST:
            items = list_user_posts(sec_uid, cookies_file, **common)   # type: ignore[arg-type]
        else:
            items = list_user_likes(sec_uid, cookies_file, **common)   # type: ignore[arg-type]

    if not include_images:
        videos = [it for it in items if not it.is_image]
        dropped = len(items) - len(videos)
        if dropped and verbose:
            print(f"    [f2] 已跳过 {dropped} 个图文/图集作品（非视频）")
        items = videos

    return items


def expand_urls(
    kind: str,
    target: str = "",
    cookies_file: Optional[str | Path] = None,
    **kw: Any,
) -> list[str]:
    """expand_target 的便捷版：只要 URL 列表。"""
    return [it.url for it in expand_target(kind, target, cookies_file, **kw)]
