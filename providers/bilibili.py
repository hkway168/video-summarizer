"""
providers/bilibili.py —— B 站（哔哩哔哩）平台配置

yt-dlp 原生支持 bilibili.com，主要覆盖两类链接：
  - https://www.bilibili.com/video/BVxxx       普通视频
  - https://www.bilibili.com/bangumi/play/...  番剧（多数要会员）
  - https://b23.tv/xxx                         短链
  - https://www.bilibili.com/opus/...          动态视频

B 站字幕在 yt-dlp 里通过 subtitles 字段暴露（只有 UP 主上传的 CC 字幕，没有"自动字幕"），
所以默认没字幕时会走 Whisper 回退。

cookies 用 `bilibili_cookies.json`，同样用 J2TEAM Cookies 扩展导出。
"""
from __future__ import annotations

from .base import BaseProvider


class BilibiliProvider(BaseProvider):
    name = "bilibili"
    display_name = "哔哩哔哩"
    url_patterns = [
        r"(?:https?://)?(?:www\.|m\.)?bilibili\.com/",
        r"(?:https?://)?b23\.tv/",
    ]
    cookies_filename = "bilibili_cookies.json"
    default_sub_langs = [
        "zh-CN", "zh-Hans", "zh", "zh-Hant", "zh-TW",
        "en",
    ]
    player_clients = None
    needs_deno = False
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
