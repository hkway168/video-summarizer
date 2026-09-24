"""
providers/douyin_f2.py —— Douyin 增强下载器（基于 f2）

背景：
    yt-dlp 主线的 Douyin extractor 被抖音最新风控阻断，会返回
    "Fresh cookies (not necessarily logged in) are needed" 错误。
    f2 项目实现了完整的抖音动态签名算法（a_bogus / X-Bogus），
    能正常拿到大多数公开视频的元数据 + 播放直链。

设计：
    - 本模块是抖音 provider 的"备用下载通路"
    - 未安装 f2 时 is_available() 返回 False，上游自动回退到 yt-dlp
    - 返回的元数据 dict 与 downloader.probe_metadata_only 保持同构，
      便于 downloader.download_audio 透明替换
    - 下载策略：f2 给的是"视频文件"直链（带音轨），直接下 mp4/m4a
      供 Whisper 消费即可（Whisper 自己用 ffmpeg 解码）
    - 同步封装：f2 是 async，我们用 asyncio.run 把单次查询变成同步调用

使用（供 downloader.py 调用）：
    from providers.douyin_f2 import f2_probe_metadata, f2_download_video, is_available, F2NotAvailableError

    if is_available():
        try:
            meta = f2_probe_metadata(url, cookies_file=...)
            dl_meta = f2_download_video(url, meta, out_dir, cookies_file=...)
        except F2NotAvailableError:
            # 降级到 yt-dlp
            ...
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Optional

# ------ 可选依赖：只在运行时检查 ------
try:
    import httpx  # f2 的依赖，正好拿来下载
    from f2.apps.douyin.handler import DouyinHandler
    from f2.apps.douyin.utils import AwemeIdFetcher
    _F2_IMPORT_ERROR: Optional[str] = None
except Exception as _e:
    DouyinHandler = None        # type: ignore[assignment]
    AwemeIdFetcher = None       # type: ignore[assignment]
    httpx = None                # type: ignore[assignment]
    _F2_IMPORT_ERROR = str(_e)


class F2NotAvailableError(RuntimeError):
    """f2 库未安装或无法加载。上层收到此错误应回退 yt-dlp。"""


class F2CookiesRequiredError(RuntimeError):
    """抖音 cookies 文件不存在或为空。f2 不带 cookie 会被风控挡回。"""


def is_available() -> bool:
    """f2 是否可导入。"""
    return _F2_IMPORT_ERROR is None


# ---------- 工具函数 ----------
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_LOG_NAMES = ("f2", "f2.apps", "f2.apps.douyin", "f2.crawler", "httpx", "httpcore")


def _silence_f2_logs() -> None:
    """把 f2 及其依赖的日志降到 WARNING，避免 console 被淹。"""
    for n in _LOG_NAMES:
        lg = logging.getLogger(n)
        lg.setLevel(logging.WARNING)


def _load_cookie_header(cookies_file: str | Path) -> str:
    """
    把 J2TEAM Cookies / Playwright 导出的 JSON cookies 转成
    HTTP `Cookie` 头那样的 'k1=v1; k2=v2' 字符串格式（f2 要求的）。
    """
    path = Path(cookies_file)
    raw = json.loads(path.read_text("utf-8"))
    if isinstance(raw, dict) and "cookies" in raw:
        raw = raw["cookies"]
    if not isinstance(raw, list):
        raise ValueError(f"cookies 文件结构不识别: {path}")

    parts: list[str] = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        n = c.get("name") or c.get("Name")
        v = c.get("value") or c.get("Value")
        if n and v is not None:
            parts.append(f"{n}={v}")
    if not parts:
        raise ValueError(f"cookies 文件不含任何可用 cookie: {path}")
    return "; ".join(parts)


def _build_handler_kwargs(cookie_header: str) -> dict:
    """组装 f2 DouyinHandler 需要的 kwargs。"""
    return {
        "cookie": cookie_header,
        "headers": {
            "User-Agent": _DEFAULT_UA,
            "Referer": "https://www.douyin.com/",
        },
        "timeout": 30,
        "max_retries": 3,
        "max_connections": 5,
        "max_counts": 0,
        "max_tasks": 5,
        "page_counts": 20,
        "naming": "{create}_{desc}",
        "path": "./downloads",
        "proxies": {"http://": None, "https://": None},
    }


def _make_handler(cookie_header: str) -> "DouyinHandler":
    """构造 DouyinHandler 并关闭 Bark 通知（避免网络噪音）。"""
    if not is_available():
        raise F2NotAvailableError(f"f2 未安装或导入失败：{_F2_IMPORT_ERROR}")
    _silence_f2_logs()
    handler = DouyinHandler(_build_handler_kwargs(cookie_header))
    # 我们只用到 fetch_one_video，一律关闭 Bark 通知
    try:
        handler.enable_bark = False
    except Exception:
        pass
    return handler


def _safe_filename(name: str) -> str:
    """与 downloader._safe_filename 等价，独立一份避免循环依赖。"""
    name = re.sub(r'[\\/:*?"<>|]', "_", name or "")
    return name.strip()[:120] or "video"


# ---------- 对外接口：探测元数据 ----------
def f2_probe_metadata(
    url: str,
    cookies_file: Optional[str | Path] = None,
    verbose: bool = False,
) -> dict:
    """
    用 f2 抓取抖音视频元数据，返回结构与 downloader.probe_metadata_only 同构。

    关键返回字段：
        title / video_id / duration / uploader / description / url / safe_title
        subtitles / automatic_captions  —— 抖音没有，全部返回 {}
        _cookies_file / _browser / _provider
        _f2_raw —— 原始 f2 PostDetailFilter._to_dict() 结果，供下游 download 复用

    失败时抛 RuntimeError（非 cookies 相关）或 F2CookiesRequiredError。
    """
    if not is_available():
        raise F2NotAvailableError(f"f2 未安装或导入失败：{_F2_IMPORT_ERROR}")
    if not cookies_file:
        raise F2CookiesRequiredError("抖音通路需要 douyin_cookies.json")

    cookie_header = _load_cookie_header(cookies_file)
    if verbose:
        print(f"    [f2] cookies 已加载（{len(cookie_header)} 字符）")

    async def _run() -> dict:
        aweme_id = await AwemeIdFetcher.get_aweme_id(url)
        handler = _make_handler(cookie_header)
        video = await handler.fetch_one_video(aweme_id)
        # 让 f2 自己把嵌套数据拍平为 dict，方便后续取字段
        try:
            raw = video._to_dict() if hasattr(video, "_to_dict") else {}
        except Exception:
            raw = {}

        duration_ms = getattr(video, "duration", 0) or 0
        try:
            duration_sec = float(duration_ms) / 1000.0
        except Exception:
            duration_sec = 0.0

        # video_play_addr 可能是 list，取第一个非空
        play_addrs = getattr(video, "video_play_addr", None) or []
        if isinstance(play_addrs, (list, tuple)):
            play_addr = next((x for x in play_addrs if x), None)
        else:
            play_addr = play_addrs

        title = getattr(video, "desc", None) or f"douyin_{aweme_id}"

        return {
            "title": title,
            "video_id": str(aweme_id),
            "safe_title": _safe_filename(title),
            "duration": duration_sec,
            "uploader": getattr(video, "nickname", "") or "",
            "url": url,
            "description": (getattr(video, "desc", "") or "")[:500],
            "subtitles": {},
            "automatic_captions": {},
            "_cookies_file": str(cookies_file) if cookies_file else None,
            "_browser": None,
            "_provider": "douyin",
            "_f2_play_url": play_addr,
            "_f2_music_url": getattr(video, "music_play_url", None),
            "_f2_seo_ocr": getattr(video, "seo_ocr_content", None) or "",
            "_f2_raw": raw,
        }

    try:
        return asyncio.run(_run())
    except F2NotAvailableError:
        raise
    except Exception as e:
        raise RuntimeError(f"f2 获取抖音元数据失败: {e}") from e


# ---------- 对外接口：下载视频文件 ----------
def f2_download_video(
    url: str,
    meta: dict,
    out_dir: Path,
    cookies_file: Optional[str | Path] = None,
    verbose: bool = False,
) -> dict:
    """
    根据 f2 探到的 play_url 直接下载视频文件（含音轨，.mp4 为主）。

    返回 dict 与 downloader.download_audio 同构：
        audio_path / title / duration / uploader / url / video_id / description

    注：抖音 `video_play_addr` 给的是 h264 mp4 + aac 音轨的"视频文件"，
    不是纯音频，但 Whisper 内部用 ffmpeg 解码，有音轨就够用了。
    """
    if not is_available():
        raise F2NotAvailableError(f"f2 未安装或导入失败：{_F2_IMPORT_ERROR}")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    play_url = meta.get("_f2_play_url")
    if not play_url:
        raise RuntimeError("f2 元数据里没有 video_play_addr，无法下载")

    title = meta.get("title") or "douyin_video"
    video_id = meta.get("video_id") or "unknown"
    safe_title = meta.get("safe_title") or _safe_filename(title)

    # 抖音 play_url 多为 mp4
    out_path = out_dir / f"{safe_title}_{video_id}.mp4"

    # 请求头必须带 Referer，否则 CDN 经常 403
    headers = {
        "User-Agent": _DEFAULT_UA,
        "Referer": "https://www.douyin.com/",
    }
    # 有 cookies 就顺手带上
    if cookies_file:
        try:
            headers["Cookie"] = _load_cookie_header(cookies_file)
        except Exception:
            pass

    if verbose:
        print(f"    [f2] 开始下载视频文件: {play_url[:100]}...")

    # 流式下载 + 简单进度
    total_bytes = 0
    with httpx.stream("GET", play_url, headers=headers, timeout=60.0, follow_redirects=True) as resp:
        resp.raise_for_status()
        expected = int(resp.headers.get("content-length") or 0)
        with open(out_path, "wb") as f:
            for chunk in resp.iter_bytes(chunk_size=64 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                total_bytes += len(chunk)
                if verbose and expected:
                    pct = total_bytes * 100.0 / expected
                    # 简单原地刷新
                    print(f"\r    [f2] 下载进度 {pct:5.1f}%  ({total_bytes/1024/1024:6.2f} / {expected/1024/1024:6.2f} MiB)", end="")
    if verbose:
        print()  # 换行

    return {
        "audio_path": str(out_path),
        "title": title,
        "duration": meta.get("duration", 0),
        "uploader": meta.get("uploader", ""),
        "url": url,
        "video_id": video_id,
        "description": (meta.get("description") or "")[:500],
    }
