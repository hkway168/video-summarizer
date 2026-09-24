"""
cookies_utils.py —— Cookies 格式自动识别与转换

背景：
不同的浏览器扩展导出的 cookies 格式不一样：
  - Netscape 格式：yt-dlp 要求的，纯文本，Tab 分隔
  - JSON 格式 A：Chrome 扩展常见，{"url":..., "cookies":[{...}, ...]}
  - JSON 格式 B：直接就是 [{...}, ...]

本模块自动识别并统一转换为 Netscape 格式。
默认把转换结果写到系统临时目录，不污染项目目录；
同源文件 mtime+size 未变时直接复用缓存，避免重复转换。
"""
from __future__ import annotations
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any


NETSCAPE_HEADER = """# Netscape HTTP Cookie File
# https://curl.se/docs/http-cookies.html
# This is a generated file!  Do not edit.
"""


def _cookie_to_netscape_line(c: dict) -> str | None:
    """
    把一条 JSON cookie 对象转为 Netscape 格式一行：
        domain  include_subdomains  path  secure  expiration  name  value

    字段用 TAB 分隔。
    """
    domain = c.get("domain") or c.get("Domain")
    if not domain:
        return None

    # domain starts with '.' 则 include_subdomains = TRUE
    host_only = c.get("hostOnly", not domain.startswith("."))
    include_subdomains = "FALSE" if host_only else "TRUE"
    # 若未带前导点但 include_subdomains=TRUE，补上
    if include_subdomains == "TRUE" and not domain.startswith("."):
        domain = "." + domain

    path = c.get("path") or c.get("Path") or "/"
    secure = "TRUE" if c.get("secure") or c.get("Secure") else "FALSE"

    # 过期时间（秒）
    if c.get("session"):
        expiration = 0
    else:
        exp = (
            c.get("expirationDate")
            or c.get("expires")
            or c.get("expiry")
            or c.get("Expires")
            or 0
        )
        try:
            expiration = int(float(exp))
        except (TypeError, ValueError):
            expiration = 0

    name = c.get("name") or c.get("Name") or ""
    value = c.get("value") or c.get("Value") or ""

    if not name:
        return None

    # Netscape 标准：字段用 \t 分隔，行末 \n
    # 注意：第二列 include_subdomains 必须和 domain 是否以 '.' 开头严格一致，
    # 否则 Python http.cookiejar 会抛 AssertionError。
    return f"{domain}\t{include_subdomains}\t{path}\t{secure}\t{expiration}\t{name}\t{value}"


def _is_netscape(content: str) -> bool:
    """简单检测是否已经是 Netscape 格式。"""
    stripped = content.strip()
    if not stripped:
        return False
    if stripped.startswith("{") or stripped.startswith("["):
        return False
    # Netscape 格式通常首行是注释 # 或者 TAB 分隔的数据行
    for line in stripped.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.count("\t") >= 6:
            return True
        return False
    return False


def _default_cache_path(input_path: Path) -> Path:
    """
    基于源文件的绝对路径 + mtime + size 生成稳定的缓存文件名，
    放到系统临时目录下，避免污染项目目录。
    """
    stat = input_path.stat()
    # 指纹 = 绝对路径 + mtime + size，任一变化都会生成新缓存文件
    fingerprint = f"{input_path.resolve()}|{stat.st_mtime_ns}|{stat.st_size}"
    digest = hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:16]
    cache_dir = Path(tempfile.gettempdir()) / "video_summarizer_cookies"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{input_path.stem}.{digest}.netscape.txt"


def ensure_netscape(input_path: str | Path, output_path: str | Path | None = None) -> Path:
    """
    确保 cookies 文件是 Netscape 格式。
    - 如果已经是 Netscape → 返回原路径
    - 如果是 JSON 格式 → 自动转换并缓存

    output_path:
        - None (默认): 写到系统临时目录 (%TEMP%/video_summarizer_cookies/)，
                       基于 mtime+size 做缓存，避免重复转换。
        - 显式路径    : 强制写到该位置。

    返回最终 Netscape 格式文件的路径。
    """
    input_path = Path(input_path)
    content = input_path.read_text(encoding="utf-8", errors="replace")

    if _is_netscape(content):
        return input_path

    # 缓存命中检测：同源文件 + 未修改 → 直接返回已有缓存
    if output_path is None:
        cache_path = _default_cache_path(input_path)
        if cache_path.exists() and cache_path.stat().st_size > 0:
            return cache_path
        output_path = cache_path

    # 尝试 JSON 解析
    try:
        data: Any = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"无法识别 cookies 格式: {input_path}\n"
            f"既不是 Netscape 也不是有效 JSON。错误: {e}"
        )

    # 抽出 cookies 列表
    if isinstance(data, dict) and "cookies" in data:
        cookies_list = data["cookies"]
    elif isinstance(data, list):
        cookies_list = data
    elif isinstance(data, dict):
        # 某些扩展可能把所有 cookie 字段铺平，或用其他 key
        # 兜底：找第一个 list 类型的值
        cookies_list = next((v for v in data.values() if isinstance(v, list)), None)
        if cookies_list is None:
            raise ValueError(f"未能从 JSON 中找到 cookies 数组: {list(data.keys())}")
    else:
        raise ValueError(f"未知的 JSON 结构: {type(data).__name__}")

    if not cookies_list:
        raise ValueError("cookies 数组为空")

    # 转换为 Netscape
    lines = [NETSCAPE_HEADER]
    converted = 0
    for c in cookies_list:
        if not isinstance(c, dict):
            continue
        line = _cookie_to_netscape_line(c)
        if line:
            lines.append(line)
            converted += 1

    if converted == 0:
        raise ValueError("所有 cookies 转换失败，请检查格式")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"[cookies] 已把 {converted} 条 JSON cookies 转为 Netscape 格式: {output_path}")
    return output_path


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法：python cookies_utils.py <cookies_file> [output_path]")
        sys.exit(1)
    out = sys.argv[2] if len(sys.argv) > 2 else None
    result = ensure_netscape(sys.argv[1], out)
    print(f"✓ 最终文件: {result}")
