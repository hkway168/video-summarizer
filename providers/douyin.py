"""
providers/douyin.py —— 抖音平台配置

yt-dlp 原生支持 douyin.com。抖音视频通常没字幕，几乎都要走 Whisper 转写。
短链 v.douyin.com 也由 yt-dlp 统一处理（会先 302 解到真实链接）。

抖音接口对 Referer / UA 比较敏感，必要时通过 extra_ytdlp_args 注入。
cookies 文件名：douyin_cookies.json（J2TEAM Cookies 扩展导出即可）。
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from .base import BaseProvider


# 抖音"弹窗式"播放页路径（内容通过 modal_id 参数传入）
_DOUYIN_MODAL_PATHS = ("/jingxuan", "/discover", "/user", "/hot", "/follow", "/")
# 形如 /share/video/7613... 也统一转成标准路径
_DOUYIN_SHARE_VIDEO_RE = re.compile(r"/share/video/(\d+)", re.IGNORECASE)


class DouyinProvider(BaseProvider):
    name = "douyin"
    display_name = "抖音"
    url_patterns = [
        r"(?:https?://)?(?:www\.|m\.)?douyin\.com/",
        r"(?:https?://)?v\.douyin\.com/",
        r"(?:https?://)?(?:www\.)?iesdouyin\.com/",
    ]
    cookies_filename = "douyin_cookies.json"
    default_sub_langs = [
        "zh-CN", "zh-Hans", "zh",
    ]
    player_clients = None
    needs_deno = False
    # 抖音 web 接口要求带 Referer，否则偶发 403
    extra_ytdlp_args = [
        "--add-header", "Referer:https://www.douyin.com/",
    ]

    def __init__(self):
        super().__init__(
            name=self.name,
            display_name=self.display_name,
            url_patterns=list(self.url_patterns),
            cookies_filename=self.cookies_filename,
            default_sub_langs=list(self.default_sub_langs),
            player_clients=self.player_clients,
            needs_deno=self.needs_deno,
            extra_ytdlp_args=list(self.extra_ytdlp_args),
        )

    def normalize_url(self, url: str) -> str:
        """把抖音的各种 URL 变体统一改写成 yt-dlp 能识别的 /video/{id} 形式。

        支持的变体：
            https://www.douyin.com/jingxuan?modal_id=7613396176133459219
            https://www.douyin.com/discover?modal_id=7613396176133459219
            https://www.douyin.com/?modal_id=7613396176133459219
            https://www.douyin.com/user/MS4wL...?modal_id=7613396176133459219
            https://www.douyin.com/share/video/7613396176133459219
        目标：
            https://www.douyin.com/video/7613396176133459219
        不匹配上述形态时原样返回（保持 /video/、v.douyin.com 短链等不变）。
        """
        if not url:
            return url
        try:
            parsed = urlparse(url)
        except Exception:
            return url

        host = (parsed.netloc or "").lower()
        if "douyin.com" not in host:
            return url

        path = parsed.path or "/"

        # 情况 1：已经是 /video/{id}，直接返回
        if re.match(r"^/video/\d+", path, flags=re.IGNORECASE):
            return url

        # 情况 2：/share/video/{id}  → /video/{id}
        m = _DOUYIN_SHARE_VIDEO_RE.search(path)
        if m:
            return f"https://www.douyin.com/video/{m.group(1)}"

        # 情况 3：modal_id 型弹窗页面
        #   路径命中白名单 或 根路径 时，从 query 中取 modal_id
        path_norm = path.rstrip("/") or "/"
        query = parse_qs(parsed.query or "")
        modal_ids = query.get("modal_id") or query.get("aweme_id")
        if modal_ids and (
            path_norm in _DOUYIN_MODAL_PATHS
            or path.startswith("/user/")
        ):
            aweme_id = modal_ids[0].strip()
            if aweme_id.isdigit():
                return f"https://www.douyin.com/video/{aweme_id}"

        return url
