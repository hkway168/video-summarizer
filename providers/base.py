"""
providers/base.py —— 视频平台抽象基类

所有平台（YouTube / B 站 / 抖音 / ...）都继承 BaseProvider，
通过 name / url_patterns / cookies_filename / extra_ytdlp_args 等元数据
让上层（main.py）以统一方式调度。

设计目标：
- yt-dlp 原生就支持很多平台，所以下载 / 字幕复用同一套 CLI 调用
- 每个 Provider 只负责描述「我是谁、我要用哪个 cookies、我要加什么额外 flag」
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class BaseProvider:
    """视频平台描述。子类通过类属性覆盖。

    约定：
        name:               平台标识（小写），如 "youtube" / "bilibili" / "douyin"
        display_name:       给人看的名字，如 "YouTube" / "哔哩哔哩" / "抖音"
        url_patterns:       用于识别 URL 的正则片段列表（host 级别）
        cookies_filename:   项目根下对应的 cookies 文件名（支持 .txt / .json）
        default_sub_langs:  该平台优先尝试的字幕语言顺序
        player_clients:     传给 yt-dlp --extractor-args 的 player_client（YouTube 特有；
                            非 YouTube 平台留空即可，yt-dlp 会忽略该参数）
        needs_deno:         是否需要 deno JS runtime（仅 YouTube n-sig 需要）
        extra_ytdlp_args:   额外追加到 yt-dlp 命令行的参数（比如抖音可能需要 Referer）
    """
    name: str = "base"
    display_name: str = "Base"
    url_patterns: List[str] = field(default_factory=list)
    cookies_filename: Optional[str] = None
    default_sub_langs: List[str] = field(default_factory=list)
    player_clients: Optional[str] = None
    needs_deno: bool = False
    extra_ytdlp_args: List[str] = field(default_factory=list)

    def match(self, url: str) -> bool:
        """判断该 URL 是否属于本平台。"""
        if not url:
            return False
        for pat in self.url_patterns:
            if re.search(pat, url, flags=re.IGNORECASE):
                return True
        return False

    def normalize_url(self, url: str) -> str:
        """将平台的各种 URL 变体规范化为 yt-dlp 能识别的标准形态。

        默认返回原 URL。子类可以覆盖此方法处理平台特有的 URL 变体
        （例如抖音的 /jingxuan?modal_id=XXX → /video/XXX）。
        """
        return url
