"""
gui/core/env_manager.py —— 运行环境探测与一键安装

能力：
    * 探测可用的 Python 解释器（配置指定 / 内置便携版 / 虚拟环境 / 系统安装）
    * 探测 Python 依赖（yt-dlp、rich、faster-whisper、huggingface_hub、playwright、f2）
    * 探测外部工具（FFmpeg、Deno、NVIDIA GPU）
    * 生成安装任务：创建虚拟环境、pip 安装各依赖组、下载便携版 Python /
      FFmpeg / Deno、安装 playwright 浏览器、平台扫码登录
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

from . import paths
from .runner import CREATE_NO_WINDOW, FuncTask, ProcessTask, TaskChain

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) VideoSummarizerGUI/1.0"

# 便携版 Python（免安装，解压即用）
EMBED_PY_VERSION = "3.11.9"
EMBED_PY_URLS = (
    f"https://registry.npmmirror.com/-/binary/python/{EMBED_PY_VERSION}/python-{EMBED_PY_VERSION}-embed-amd64.zip",
    f"https://www.python.org/ftp/python/{EMBED_PY_VERSION}/python-{EMBED_PY_VERSION}-embed-amd64.zip",
)
GET_PIP_URLS = (
    "https://bootstrap.pypa.io/get-pip.py",
    "https://ghfast.top/https://raw.githubusercontent.com/pypa/get-pip/main/public/get-pip.py",
)

# FFmpeg（BtbN 的 Windows 静态构建）
FFMPEG_URLS = (
    "https://ghfast.top/https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
)

# Deno（YouTube n-sig 解密用的 JS 运行时）
DENO_URLS = (
    "https://ghfast.top/https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip",
    "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip",
)

REQ_FILES = {
    "base": "requirements.txt",
    "whisper": "requirements-whisper.txt",
    "login": "requirements-login.txt",
    "douyin": "requirements-douyin.txt",
}

MODULE_DISTS = {
    "yt_dlp": "yt-dlp",
    "rich": "rich",
    "faster_whisper": "faster-whisper",
    "huggingface_hub": "huggingface_hub",
    "playwright": "playwright",
    "f2": "f2",
    "ctranslate2": "ctranslate2",
}

_PROBE_SCRIPT = '''
import importlib.util, json, sys

MODS = ["yt_dlp", "rich", "faster_whisper", "huggingface_hub", "playwright", "f2", "ctranslate2"]
DISTS = {
    "yt_dlp": "yt-dlp", "rich": "rich", "faster_whisper": "faster-whisper",
    "huggingface_hub": "huggingface_hub", "playwright": "playwright",
    "f2": "f2", "ctranslate2": "ctranslate2",
}
out = {"python_version": sys.version.split()[0], "executable": sys.executable, "modules": {}}
for m in MODS:
    try:
        ok = importlib.util.find_spec(m) is not None
    except Exception:
        ok = False
    ver = ""
    if ok:
        try:
            import importlib.metadata as md
            ver = md.version(DISTS.get(m, m))
        except Exception:
            ver = ""
    out["modules"][m] = {"installed": ok, "version": ver}
try:
    out["has_pip"] = importlib.util.find_spec("pip") is not None
except Exception:
    out["has_pip"] = False
try:
    import ctranslate2
    out["cuda_devices"] = int(ctranslate2.get_cuda_device_count())
except Exception:
    out["cuda_devices"] = -1
sys.stdout.write("###PROBE###" + json.dumps(out))
'''


# ══════════════════════════════════════════════════════════════════════
# Python 解释器
# ══════════════════════════════════════════════════════════════════════
def embedded_python() -> Path:
    return paths.tools_dir() / "python-embed" / "python.exe"


def venv_python() -> Path:
    if sys.platform == "win32":
        return paths.venv_dir() / "Scripts" / "python.exe"
    return paths.venv_dir() / "bin" / "python"


def find_python_candidates() -> list[str]:
    """返回本机所有可用 Python 解释器路径（去重、按推荐顺序）。"""
    found: list[str] = []

    def push(p: str | Path | None) -> None:
        if not p:
            return
        try:
            rp = str(Path(p).resolve())
        except Exception:
            rp = str(p)
        if os.path.isfile(rp) and rp not in found:
            found.append(rp)

    push(venv_python())
    push(embedded_python())
    if not paths.is_frozen():
        push(sys.executable)

    for name in ("python.exe", "python3.exe", "python", "python3"):
        w = shutil.which(name)
        # WindowsApps 下的 python.exe 往往是应用商店占位程序，排除
        if w and "WindowsApps" not in w:
            push(w)

    # py -0p 列出所有已注册版本
    if sys.platform == "win32":
        try:
            r = subprocess.run(["py", "-0p"], capture_output=True, text=True,
                               timeout=8, creationflags=CREATE_NO_WINDOW)
            for line in (r.stdout or "").splitlines():
                line = line.strip()
                if not line:
                    continue
                part = line.split(None, 1)[-1].strip().strip('"')
                if part.lower().endswith("python.exe"):
                    push(part)
        except Exception:
            pass
        for base in filter(None, [os.environ.get("LOCALAPPDATA"), r"C:\\", os.environ.get("ProgramFiles")]):
            for pat in ("Programs/Python/Python3*/python.exe", "Python3*/python.exe"):
                try:
                    for p in Path(base).glob(pat):
                        push(p)
                except Exception:
                    pass
    return found


def resolve_python(cfg=None) -> str:
    """返回当前应使用的 Python 解释器路径（可能为空字符串表示未找到）。"""
    if cfg is not None:
        configured = str(cfg.get("python_path") or "").strip()
        if configured and Path(configured).is_file():
            return configured
    for cand in (venv_python(), embedded_python()):
        if cand.is_file():
            return str(cand)
    if not paths.is_frozen() and Path(sys.executable).is_file():
        return sys.executable
    cands = find_python_candidates()
    return cands[0] if cands else ""


def python_version(python: str) -> str:
    try:
        r = subprocess.run([python, "-c", "import sys;print(sys.version.split()[0])"],
                           capture_output=True, text=True, timeout=10,
                           creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def probe(python: str) -> dict:
    """在目标解释器里探测依赖情况，返回 dict（失败返回 {"error": ...}）。"""
    if not python or not Path(python).is_file():
        return {"error": "未找到 Python 解释器"}
    script = paths.logs_dir() / "_gui_probe.py"
    try:
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(_PROBE_SCRIPT, encoding="utf-8")
        r = subprocess.run(
            [python, str(script)],
            capture_output=True, timeout=60,
            creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        out = (r.stdout or b"").decode("utf-8", "replace")
        if "###PROBE###" in out:
            return json.loads(out.split("###PROBE###", 1)[1].strip())
        err = (r.stderr or b"").decode("utf-8", "replace").strip()
        return {"error": err or "探测脚本无输出"}
    except Exception as exc:
        return {"error": str(exc)}


# ══════════════════════════════════════════════════════════════════════
# 外部工具
# ══════════════════════════════════════════════════════════════════════
def ffmpeg_bin_dir() -> Path:
    return paths.tools_dir() / "ffmpeg" / "bin"


def find_ffmpeg() -> Optional[str]:
    local = ffmpeg_bin_dir() / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    if local.is_file():
        return str(local)
    in_project = paths.deno_dir() / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    if in_project.is_file():
        return str(in_project)
    return shutil.which("ffmpeg")


def find_deno() -> Optional[str]:
    local = paths.deno_dir() / ("deno.exe" if sys.platform == "win32" else "deno")
    if local.is_file():
        return str(local)
    return shutil.which("deno")


def has_nvidia_gpu() -> bool:
    if shutil.which("nvidia-smi"):
        try:
            r = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True,
                               timeout=10, creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            return "GPU 0" in (r.stdout or "") or "GPU 0" in (r.stderr or "")
        except Exception:
            return False
    return False


def extra_path_dirs() -> list[str]:
    """需要追加到子进程 PATH 前面的目录（ffmpeg / deno）。"""
    dirs: list[str] = []
    ff = find_ffmpeg()
    if ff:
        dirs.append(str(Path(ff).parent))
    dn = find_deno()
    if dn:
        dirs.append(str(Path(dn).parent))
    return [d for d in dict.fromkeys(dirs) if d]


def build_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """构造子进程环境变量：UTF-8 输出 + 工具目录入 PATH。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONUTF8"] = "1"
    env["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
    extra_dirs = extra_path_dirs()
    if extra_dirs:
        env["PATH"] = os.pathsep.join(extra_dirs + [env.get("PATH", "")])
    if extra:
        env.update({k: str(v) for k, v in extra.items()})
    return env


# ══════════════════════════════════════════════════════════════════════
# 环境体检报告
# ══════════════════════════════════════════════════════════════════════
@dataclass
class CheckItem:
    key: str
    name: str
    status: str          # ok | missing | optional | unknown
    detail: str = ""
    hint: str = ""

    @property
    def icon(self) -> str:
        # 用纯符号而非 emoji，避免部分 Windows 字体渲染成方块
        return {"ok": "✓", "missing": "✗", "optional": "–", "unknown": "?"}.get(self.status, "?")

    @property
    def status_text(self) -> str:
        return {"ok": "已就绪", "missing": "未安装", "optional": "未安装(可选)", "unknown": "未知"}.get(
            self.status, self.status)


@dataclass
class EnvReport:
    python: str = ""
    python_ver: str = ""
    items: list[CheckItem] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @property
    def core_ready(self) -> bool:
        need = {"python", "yt_dlp", "ffmpeg"}
        return all(i.status == "ok" for i in self.items if i.key in need)

    @property
    def whisper_ready(self) -> bool:
        keys = {"faster_whisper", "model"}
        return all(i.status == "ok" for i in self.items if i.key in keys)

    def get(self, key: str) -> Optional[CheckItem]:
        for i in self.items:
            if i.key == key:
                return i
        return None


def inspect_environment(cfg=None) -> EnvReport:
    """完整体检（耗时 1~3 秒，请放到后台线程执行）。"""
    from . import model_manager

    python = resolve_python(cfg)
    rep = EnvReport(python=python)
    items: list[CheckItem] = []

    if not python:
        items.append(CheckItem("python", "Python 解释器", "missing",
                               "未检测到任何 Python", "点击「下载便携版 Python」即可一键装好"))
        rep.items = items
        return rep

    info = probe(python)
    rep.raw = info
    if info.get("error"):
        rep.python_ver = python_version(python)
        items.append(CheckItem("python", "Python 解释器", "missing",
                               f"{python} 无法正常执行：{info['error']}", "请重新选择解释器"))
        rep.items = items
        return rep

    rep.python_ver = info.get("python_version", "")
    items.append(CheckItem("python", "Python 解释器", "ok",
                           f"{rep.python_ver}  ({python})"))
    items.append(CheckItem("pip", "pip 包管理器",
                           "ok" if info.get("has_pip") else "missing",
                           "可用" if info.get("has_pip") else "缺失，无法安装依赖",
                           "" if info.get("has_pip") else "便携版 Python 请点「下载便携版 Python」重新初始化"))

    mods = info.get("modules", {})

    def mod_item(key: str, name: str, required: bool, hint: str) -> CheckItem:
        m = mods.get(key) or {}
        if m.get("installed"):
            return CheckItem(key, name, "ok", f"v{m.get('version') or '?'}")
        return CheckItem(key, name, "missing" if required else "optional", "未安装", hint)

    items.append(mod_item("yt_dlp", "yt-dlp（下载/字幕核心）", True, "点击「安装基础依赖」"))
    items.append(mod_item("rich", "rich（终端美化）", True, "点击「安装基础依赖」"))
    items.append(mod_item("faster_whisper", "faster-whisper（本地转写）", False, "点击「安装 Whisper 依赖」"))
    items.append(mod_item("huggingface_hub", "huggingface_hub（模型下载）", False, "点击「安装 Whisper 依赖」"))
    items.append(mod_item("playwright", "playwright（扫码登录）", False, "点击「安装登录依赖」"))
    items.append(mod_item("f2", "f2（抖音增强下载）", False, "点击「安装抖音增强」"))

    ff = find_ffmpeg()
    items.append(CheckItem("ffmpeg", "FFmpeg（音频处理）",
                           "ok" if ff else "missing",
                           ff or "未安装", "" if ff else "点击「安装 FFmpeg」自动下载便携版"))

    dn = find_deno()
    items.append(CheckItem("deno", "Deno（YouTube 解密）",
                           "ok" if dn else "optional",
                           dn or "未安装", "" if dn else "只影响部分 YouTube 视频，可点「安装 Deno」"))

    # 扫码登录用的浏览器：本机已装的即可，不必再下 Playwright 的 Chromium
    browsers = list_local_browsers()
    if browsers:
        names = "、".join(b[1] for b in browsers)
        items.append(CheckItem("browser", "浏览器（扫码登录用）", "ok",
                               f"将复用本机：{names}"))
    elif playwright_chromium_installed(python):
        items.append(CheckItem("browser", "浏览器（扫码登录用）", "ok",
                               "将使用 Playwright 自带 Chromium"))
    else:
        items.append(CheckItem("browser", "浏览器（扫码登录用）", "optional",
                               "未检测到 Edge / Chrome / Brave",
                               "装个 Edge/Chrome 即可；或双击本行下载 Chromium"))

    cuda = int(info.get("cuda_devices", -1))
    if cuda > 0:
        items.append(CheckItem("gpu", "GPU 加速", "ok", f"检测到 {cuda} 张 CUDA 显卡"))
    elif has_nvidia_gpu():
        items.append(CheckItem("gpu", "GPU 加速", "unknown", "有 NVIDIA 显卡，但 ctranslate2 未就绪",
                               "安装 Whisper 依赖后再检测"))
    else:
        items.append(CheckItem("gpu", "GPU 加速", "optional", "未检测到 CUDA 显卡（将用 CPU 转写，较慢）"))

    installed_models = model_manager.installed_models()
    if installed_models:
        names = ", ".join(m.size for m in installed_models)
        items.append(CheckItem("model", "Whisper 模型", "ok", f"已安装：{names}"))
    else:
        items.append(CheckItem("model", "Whisper 模型", "missing", "未下载任何模型",
                               "到「模型管理」页下载（推荐 medium）"))

    rep.items = items
    return rep


# ══════════════════════════════════════════════════════════════════════
# 下载工具函数
# ══════════════════════════════════════════════════════════════════════
def http_download(task: FuncTask, urls: Sequence[str], dest: Path, label: str) -> bool:
    """依次尝试多个 URL 下载到 dest，带进度输出。成功返回 True。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    for idx, url in enumerate(urls, 1):
        if task.cancelled:
            return False
        task.emit(f"📥 [{idx}/{len(urls)}] 正在下载 {label}\n    {url}", "info")
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as fh:
                total = int(resp.headers.get("Content-Length") or 0)
                got = 0
                last = 0.0
                while True:
                    if task.cancelled:
                        task.emit("⏹ 下载已取消", "warn")
                        return False
                    chunk = resp.read(262144)
                    if not chunk:
                        break
                    fh.write(chunk)
                    got += len(chunk)
                    now = time.time()
                    if now - last > 0.2:
                        last = now
                        if total:
                            pct = got * 100 / total
                            bar = "█" * int(pct // 4) + "░" * (25 - int(pct // 4))
                            task.emit(f"    {bar} {pct:5.1f}%  "
                                      f"{paths.fmt_bytes(got)} / {paths.fmt_bytes(total)}",
                                      "info", transient=True)
                        else:
                            task.emit(f"    已下载 {paths.fmt_bytes(got)}", "info", transient=True)
            tmp.replace(dest)
            task.emit(f"✅ 下载完成：{paths.fmt_bytes(dest.stat().st_size)} → {dest}", "success")
            return True
        except Exception as exc:
            task.emit(f"⚠️ 该地址失败：{exc}", "warn")
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
    task.emit(f"❌ {label} 下载失败（所有地址均不可用），请检查网络或代理", "error")
    return False


def _extract_members(task: FuncTask, zip_path: Path, target: Path,
                     wanted: Sequence[str]) -> int:
    """从 zip 中提取文件名匹配 wanted 的文件（扁平化到 target）。返回提取数量。"""
    target.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = Path(info.filename).name.lower()
            if name in wanted:
                with zf.open(info) as src, open(target / Path(info.filename).name, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                count += 1
                task.emit(f"    解压 {Path(info.filename).name}", "info")
    return count


# ══════════════════════════════════════════════════════════════════════
# 安装任务工厂
# ══════════════════════════════════════════════════════════════════════
def _pip_base(python: str, cfg) -> list[str]:
    argv = [python, "-m", "pip", "install", "--disable-pip-version-check",
            "--no-warn-script-location"]
    index = str((cfg.get("pip_index") if cfg else "") or "").strip()
    if index:
        argv += ["-i", index]
        host = index.split("//")[-1].split("/")[0]
        argv += ["--trusted-host", host]
    return argv


def read_requirements(path: Path | str) -> list[str]:
    """按 UTF-8 解析 requirements 文件，返回纯依赖声明列表（去掉注释/空行）。

    为什么不直接用 `pip install -r 文件`：
        pip 解析 -r 文件时若文件没有 BOM，会按系统 locale 编码解码。
        中文 Windows 是 GBK，遇到 UTF-8 中文注释就抛 UnicodeDecodeError，
        安装直接失败。这里自己读成依赖列表传给 pip，彻底绕开该问题。
    """
    specs: list[str] = []
    try:
        text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return specs
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            specs.append(line)
    return specs


REQ_TITLES = {
    "base": "安装基础依赖 (yt-dlp / rich)",
    "whisper": "安装 Whisper 依赖 (faster-whisper)",
    "login": "安装扫码登录依赖 (playwright)",
    "douyin": "安装抖音增强依赖 (f2)",
}


def pip_install_requirements_task(python: str, group: str, cfg) -> ProcessTask:
    req = REQ_FILES.get(group, "requirements.txt")
    req_path = paths.runtime_dir() / req
    specs = read_requirements(req_path)
    argv = _pip_base(python, cfg)
    # 解析成功就直接传包名（免受 locale 编码影响）；解析不到再回退 -r
    argv += specs if specs else ["-r", req]
    return ProcessTask(argv, cwd=paths.runtime_dir(), env=build_env(),
                       title=REQ_TITLES.get(group, f"安装 {req}"))


def pip_install_packages_task(python: str, packages: Sequence[str], cfg,
                              title: str = "", upgrade: bool = False) -> ProcessTask:
    argv = _pip_base(python, cfg)
    if upgrade:
        argv.append("-U")
    argv += list(packages)
    return ProcessTask(argv, cwd=paths.runtime_dir(), env=build_env(),
                       title=title or f"安装 {' '.join(packages)}")


def upgrade_ytdlp_task(python: str, cfg) -> ProcessTask:
    return pip_install_packages_task(python, ["yt-dlp"], cfg,
                                     title="升级 yt-dlp 到最新版", upgrade=True)


def ensure_pip_task(python: str, cfg) -> ProcessTask:
    return ProcessTask([python, "-m", "ensurepip", "--upgrade"],
                       cwd=paths.runtime_dir(), env=build_env(),
                       title="初始化 pip")


def create_venv_task(base_python: str, cfg) -> FuncTask:
    """在数据目录下创建 .venv 虚拟环境，并把配置指向它。"""

    def _run(task: FuncTask) -> int:
        vdir = paths.venv_dir()
        if venv_python().is_file():
            task.emit(f"ℹ️ 虚拟环境已存在：{vdir}", "info")
        else:
            task.emit(f"🧪 正在创建虚拟环境：{vdir}", "info")
            proc = ProcessTask([base_python, "-m", "venv", str(vdir)],
                              cwd=paths.runtime_dir(), env=build_env(),
                              title="创建虚拟环境")
            proc._q = task._q            # noqa: SLF001 - 复用日志队列
            code = proc._run()           # noqa: SLF001
            if code != 0 or not venv_python().is_file():
                task.emit("❌ 虚拟环境创建失败（便携版 Python 不支持 venv，可直接使用便携版本身）", "error")
                return code or 1
        if cfg is not None:
            cfg.set("python_path", str(venv_python()))
        task.emit(f"✅ 已切换到虚拟环境解释器：{venv_python()}", "success")
        return 0

    return FuncTask(_run, title="创建虚拟环境")


def setup_embedded_python_task(cfg) -> FuncTask:
    """下载官方便携版 Python + get-pip，实现零安装可用。"""

    def _run(task: FuncTask) -> int:
        tools = paths.tools_dir()
        target = tools / "python-embed"
        zip_path = tools / f"python-{EMBED_PY_VERSION}-embed.zip"

        if not zip_path.is_file():
            if not http_download(task, EMBED_PY_URLS, zip_path, f"便携版 Python {EMBED_PY_VERSION}"):
                return 1
        else:
            task.emit(f"ℹ️ 复用已下载的安装包：{zip_path}", "info")

        task.emit("📦 正在解压...", "info")
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(target)
        except Exception as exc:
            task.emit(f"❌ 解压失败（安装包可能损坏，请删除后重试）：{exc}", "error")
            return 1

        # 打开 site 支持，否则 pip 装的包无法被 import
        for pth in target.glob("python*._pth"):
            try:
                lines = pth.read_text(encoding="utf-8").splitlines()
                out: list[str] = []
                for line in lines:
                    out.append("import site" if line.strip() == "#import site" else line)
                if "import site" not in out:
                    out.append("import site")
                if "Lib\\site-packages" not in out:
                    out.insert(min(2, len(out)), "Lib\\site-packages")
                pth.write_text("\n".join(out) + "\n", encoding="utf-8")
                task.emit(f"    已修正 {pth.name}（启用 site-packages）", "info")
            except Exception as exc:
                task.emit(f"⚠️ 修正 {pth.name} 失败：{exc}", "warn")

        py = target / "python.exe"
        if not py.is_file():
            task.emit("❌ 解压后未找到 python.exe", "error")
            return 1

        get_pip = tools / "get-pip.py"
        if not get_pip.is_file():
            if not http_download(task, GET_PIP_URLS, get_pip, "get-pip.py"):
                return 1
        task.emit("🔧 正在安装 pip ...", "info")
        argv = [str(py), str(get_pip), "--no-warn-script-location"]
        index = str((cfg.get("pip_index") if cfg else "") or "").strip()
        if index:
            argv += ["-i", index, "--trusted-host", index.split("//")[-1].split("/")[0]]
        proc = ProcessTask(argv, cwd=str(target), env=build_env(), title="安装 pip")
        proc._q = task._q                 # noqa: SLF001
        if proc._run() != 0:              # noqa: SLF001
            task.emit("❌ pip 安装失败", "error")
            return 1

        if cfg is not None:
            cfg.set("python_path", str(py))
        task.emit(f"✅ 便携版 Python 就绪并已设为当前解释器：{py}", "success")
        return 0

    return FuncTask(_run, title=f"下载便携版 Python {EMBED_PY_VERSION}")


def install_ffmpeg_task() -> FuncTask:
    """下载 FFmpeg 便携版到 tools/ffmpeg/bin（自动加入子进程 PATH）。"""

    def _run(task: FuncTask) -> int:
        zip_path = paths.tools_dir() / "ffmpeg-win64.zip"
        if not zip_path.is_file():
            if not http_download(task, FFMPEG_URLS, zip_path, "FFmpeg (约 90 MB)"):
                task.emit("💡 备选方案：以管理员身份运行  winget install Gyan.FFmpeg", "info")
                return 1
        task.emit("📦 正在解压 ffmpeg / ffprobe ...", "info")
        try:
            n = _extract_members(task, zip_path, ffmpeg_bin_dir(),
                                 ("ffmpeg.exe", "ffprobe.exe"))
        except Exception as exc:
            task.emit(f"❌ 解压失败：{exc}", "error")
            return 1
        if n == 0:
            task.emit("❌ 压缩包里没找到 ffmpeg.exe", "error")
            return 1
        found = find_ffmpeg()
        task.emit(f"✅ FFmpeg 就绪：{found}", "success")
        return 0

    return FuncTask(_run, title="安装 FFmpeg")


def install_deno_task() -> FuncTask:
    """下载 Deno 到项目 bin/ 目录（YouTube 部分视频需要）。"""

    def _run(task: FuncTask) -> int:
        zip_path = paths.tools_dir() / "deno-win64.zip"
        if not zip_path.is_file():
            if not http_download(task, DENO_URLS, zip_path, "Deno (约 40 MB)"):
                return 1
        task.emit("📦 正在解压 deno.exe ...", "info")
        try:
            n = _extract_members(task, zip_path, paths.deno_dir(), ("deno.exe",))
        except Exception as exc:
            task.emit(f"❌ 解压失败：{exc}", "error")
            return 1
        if n == 0:
            task.emit("❌ 压缩包里没找到 deno.exe", "error")
            return 1
        task.emit(f"✅ Deno 就绪：{find_deno()}", "success")
        return 0

    return FuncTask(_run, title="安装 Deno")


def playwright_browser_task(python: str) -> ProcessTask:
    return ProcessTask([python, "-m", "playwright", "install", "chromium"],
                       cwd=paths.runtime_dir(), env=build_env(),
                       title="下载 Chromium（扫码登录用）")


def login_task(python: str, platform: str, browser: str = "auto") -> ProcessTask:
    """扫码登录任务。

    browser: auto（优先复用本机 Edge/Chrome）| edge | chrome | brave | chromium
             | playwright（用 Playwright 自带 Chromium）| 可执行文件路径
    """
    script = {"bilibili": "scripts/login_bilibili.py",
              "douyin": "scripts/login_douyin.py"}[platform]
    argv = [python, script]
    if browser and browser != "auto":
        argv += ["--browser", browser]
    return ProcessTask(argv, cwd=paths.runtime_dir(), env=build_env(),
                       title=f"{platform} 扫码登录")


# ══════════════════════════════════════════════════════════════════════
# 本机浏览器（扫码登录用，避免额外下载 Chromium）
# ══════════════════════════════════════════════════════════════════════
_BROWSER_FINDER_CACHE: list = []   # [module | None]


def _browser_finder():
    """延迟加载 scripts/_browser_finder.py（它在 runtime_dir 下，不属于 gui 包）。

    注意：必须先把模块放进 sys.modules 再 exec，否则模块里的 @dataclass
    解析类型注解时找不到自己的命名空间，会抛 AttributeError。
    """
    if _BROWSER_FINDER_CACHE:
        return _BROWSER_FINDER_CACHE[0]

    import importlib.util

    mod = None
    path = paths.runtime_dir() / "scripts" / "_browser_finder.py"
    if path.is_file():
        name = "_vs_browser_finder"
        try:
            if name in sys.modules:
                mod = sys.modules[name]
            else:
                spec = importlib.util.spec_from_file_location(name, path)
                if spec is not None and spec.loader is not None:
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules[name] = mod          # ← 关键：先注册再执行
                    try:
                        spec.loader.exec_module(mod)
                    except Exception:
                        sys.modules.pop(name, None)
                        mod = None
        except Exception:
            mod = None
    _BROWSER_FINDER_CACHE.append(mod)
    return mod


def list_local_browsers() -> list[tuple[str, str, str]]:
    """本机已安装的 Chromium 系浏览器：[(key, 展示名, 路径), ...]。"""
    mod = _browser_finder()
    if mod is None:
        return []
    try:
        return [(b.key, b.name, b.path or "") for b in mod.list_local_browsers()]
    except Exception:
        return []


def playwright_chromium_installed(python: str = "") -> bool:
    """Playwright 自带 Chromium 是否已下载。

    优先在目标解释器里查（GUI 的 exe 自身没装 playwright），失败再退回本进程。
    """
    if python and Path(python).is_file():
        code = (
            "import sys,pathlib\n"
            "try:\n"
            "    from playwright.sync_api import sync_playwright\n"
            "    with sync_playwright() as pw:\n"
            "        p = pw.chromium.executable_path\n"
            "        sys.stdout.write('1' if p and pathlib.Path(p).exists() else '0')\n"
            "except Exception:\n"
            "    sys.stdout.write('0')\n"
        )
        try:
            r = subprocess.run(
                [python, "-c", code], capture_output=True, text=True, timeout=30,
                creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            return (r.stdout or "").strip().endswith("1")
        except Exception:
            return False
    mod = _browser_finder()
    if mod is None:
        return False
    try:
        return bool(mod.playwright_chromium_path())
    except Exception:
        return False


def install_all_task(python_getter: Callable[[], str], cfg,
                     with_whisper: bool = True) -> TaskChain:
    """一键安装：基础依赖 → FFmpeg →（可选）Whisper 依赖。"""
    factories: list[Callable[[], object]] = [
        lambda: pip_install_requirements_task(python_getter(), "base", cfg),
        lambda: install_ffmpeg_task(),
    ]
    if with_whisper:
        factories.append(lambda: pip_install_requirements_task(python_getter(), "whisper", cfg))
    return TaskChain(factories, title="一键安装运行环境")  # type: ignore[arg-type]
