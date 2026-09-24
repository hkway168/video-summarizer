"""
providers/youtube.py —— YouTube 平台配置
"""
from __future__ import annotations

from .base import BaseProvider


class YouTubeProvider(BaseProvider):
    name = "youtube"
    display_name = "YouTube"
    url_patterns = [
        r"(?:https?://)?(?:www\.|m\.|music\.)?youtube\.com/",
        r"(?:https?://)?youtu\.be/",
    ]
    cookies_filename = "youtube_cookies.json"
    default_sub_langs = [
        "zh-Hans", "zh-CN", "zh", "zh-Hant", "zh-TW", "zh-HK",
        "en", "en-US", "en-GB",
    ]
    # 有 cookies 时用 web，没有时用移动端更稳（原 downloader 逻辑里在运行时动态决定，
    # 这里保留 None，由 downloader.py 根据 cookies 情况选择）
    player_clients = None
    # YouTube n-sig 解密必须
    needs_deno = True
    extra_ytdlp_args = []

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
