"""
providers/__init__.py —— Provider 注册表 + URL 识别入口

用法：
    from providers import resolve_provider, ALL_PROVIDERS

    provider = resolve_provider(url)                    # 按 URL 自动识别
    provider = resolve_provider(url, platform="bilibili")  # 强制指定
"""
from __future__ import annotations

from typing import List, Optional

from .base import BaseProvider
from .youtube import YouTubeProvider
from .bilibili import BilibiliProvider
from .douyin import DouyinProvider


# 注册表：按匹配优先级排序（一般 YouTube 放最前，因为 URL 最明确）
ALL_PROVIDERS: List[BaseProvider] = [
    YouTubeProvider(),
    BilibiliProvider(),
    DouyinProvider(),
]

# name → provider 映射，方便强制指定
PROVIDERS_BY_NAME: dict[str, BaseProvider] = {p.name: p for p in ALL_PROVIDERS}


def resolve_provider(url: str, platform: Optional[str] = None) -> BaseProvider:
    """
    根据 URL 自动识别 Provider；也可通过 `platform` 参数强制指定。

    参数：
        url:       视频链接
        platform:  可选，强制使用某个平台（name，如 "youtube"/"bilibili"/"douyin"）

    返回：
        对应 Provider 实例

    异常：
        ValueError: 找不到匹配的平台
    """
    if platform:
        key = platform.lower().strip()
        if key not in PROVIDERS_BY_NAME:
            raise ValueError(
                f"未知平台 '{platform}'，可选：{list(PROVIDERS_BY_NAME.keys())}"
            )
        return PROVIDERS_BY_NAME[key]

    for p in ALL_PROVIDERS:
        if p.match(url):
            return p

    raise ValueError(
        f"无法识别该 URL 所属平台：{url}\n"
        f"目前支持的平台：{[p.display_name for p in ALL_PROVIDERS]}\n"
        f"可用 --platform 参数显式指定。"
    )


__all__ = [
    "BaseProvider",
    "YouTubeProvider",
    "BilibiliProvider",
    "DouyinProvider",
    "ALL_PROVIDERS",
    "PROVIDERS_BY_NAME",
    "resolve_provider",
]
