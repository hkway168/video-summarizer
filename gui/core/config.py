"""
gui/core/config.py —— 轻量配置持久化（gui_config.json）
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from . import paths

DEFAULTS: dict[str, Any] = {
    # 环境
    "python_path": "",            # 空 = 自动探测
    "pip_index": "https://pypi.tuna.tsinghua.edu.cn/simple",
    "hf_mirror": "https://hf-mirror.com",
    # 扫码登录用哪个浏览器：auto（本机优先）/ edge / chrome / brave / chromium / playwright
    "login_browser": "auto",
    # 转写参数
    "platform": "auto",
    "mode": "auto",
    "model": "auto",
    "language": "auto",
    "browser": "chrome",
    "sub_lang": "",
    "keep_audio": False,
    "no_auto_sub": False,
    "output_dir": "",             # 空 = paths.default_output_dir()
    "last_urls": "",
    # 只下载（视频下载页）
    "dl_kind": "video",           # video | audio
    "dl_quality": "best",
    "dl_container": "mp4",
    "dl_audio_format": "keep",
    "dl_subs": False,
    "dl_thumbnail": False,
    "dl_embed_metadata": False,
    "dl_output_dir": "",          # 空 = paths.videos_dir()
    "dl_urls": "",
    # 本地转写（本地转写页）
    "local_model": "auto",
    "local_language": "auto",
    "local_timestamps": True,
    "local_recursive": False,
    "local_files": [],
    # 界面
    "window_geometry": "",
    "last_page": "transcribe",
}

_lock = threading.Lock()


class Config:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else paths.config_path()
        self._data: dict[str, Any] = dict(DEFAULTS)
        self.load()

    # ── 读写 ────────────────────────────────────────────────────────────
    def load(self) -> None:
        try:
            if self.path.exists():
                # utf-8-sig：兼容用户用记事本/PowerShell 保存出的 BOM
                raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
                if isinstance(raw, dict):
                    for k, v in raw.items():
                        if k in DEFAULTS:
                            self._data[k] = v
        except Exception:
            pass

    def save(self) -> None:
        with _lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".json.tmp")
                tmp.write_text(
                    json.dumps(self._data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                tmp.replace(self.path)
            except Exception:
                pass

    # ── 字典式访问 ──────────────────────────────────────────────────────
    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any, *, autosave: bool = True) -> None:
        self._data[key] = value
        if autosave:
            self.save()

    def update(self, values: dict[str, Any], *, autosave: bool = True) -> None:
        self._data.update(values)
        if autosave:
            self.save()

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def __setitem__(self, key: str, value: Any) -> None:
        self.set(key, value)

    # ── 派生路径 ────────────────────────────────────────────────────────
    def output_dir(self) -> Path:
        raw = str(self.get("output_dir") or "").strip()
        return Path(raw) if raw else paths.default_output_dir()

    def download_dir(self) -> Path:
        """「只下载」的保存目录。"""
        raw = str(self.get("dl_output_dir") or "").strip()
        return Path(raw) if raw else paths.videos_dir()


_instance: Config | None = None


def get_config() -> Config:
    global _instance
    if _instance is None:
        _instance = Config()
    return _instance
