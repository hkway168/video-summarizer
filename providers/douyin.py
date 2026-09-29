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
    # 支持「个人主页 / 收藏 / 喜欢 / 收藏夹」等聚合页展开成多条视频 URL
    supports_batch = True

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
            supports_batch=self.supports_batch,
        )

    def normalize_url(self, url: str) -> str:
        """把抖音的各种 URL 变体统一改写成 yt-dlp 能识别的 /video/{id} 形式。

        支持的变体：
            https://www.douyin.com/jingxuan?modal_id=7613396176133459219
            https://www.douyin.com/jingxuan/animal?modal_id=7613396176133459219
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

        # 情况 3：modal_id 型弹窗页面（任意路径，如 /jingxuan/animal、/search/xxx）
        query = parse_qs(parsed.query or "")
        modal_ids = query.get("modal_id") or query.get("aweme_id")
        if modal_ids:
            aweme_id = modal_ids[0].strip()
            if aweme_id.isdigit():
                return f"https://www.douyin.com/video/{aweme_id}"

        return url

    # ── 列表型批量：个人主页 / 收藏 / 喜欢 / 收藏夹 ────────────────────
    def parse_batch_target(self, target: str):
        """识别抖音的聚合页入口，返回 douyin_list.BatchTarget 或 None。

        能识别（详见 providers/douyin_list.parse_batch_target）：
            https://www.douyin.com/user/MS4wL...                  → 主页作品
            https://www.douyin.com/user/MS4wL...?showTab=like     → 该用户的喜欢
            https://www.douyin.com/user/self?showTab=favorite_collection → 本人收藏
            https://www.douyin.com/collection/7123456789          → 本人收藏夹
            裸 sec_uid / "self" / "collection" / "like"
        普通单视频链接（/video/xxx、带 modal_id 的弹窗页）一律返回 None。
        """
        from .douyin_list import parse_batch_target as _parse
        return _parse(target)

    def expand_batch(
        self,
        target,
        cookies_file: str | None = None,
        *,
        limit: int = 0,
        verbose: bool = False,
        **kwargs,
    ) -> list[str]:
        """把 BatchTarget 展开成标准视频 URL 列表（需要 f2 + 登录 cookies）。"""
        from .douyin_list import KIND_COLLECTS, expand_target

        if target.kind == KIND_COLLECTS:
            arg = target.collects_id
        elif target.sec_user_id:
            arg = target.sec_user_id
        else:
            # is_self 但还没解析出 sec_uid：交给 expand_target 自动识别本人
            arg = "self"

        items = expand_target(
            target.kind, arg, cookies_file,
            limit=limit, verbose=verbose, **kwargs,
        )
        return [it.url for it in items]
