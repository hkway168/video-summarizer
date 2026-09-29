"""
transcriber.py —— Whisper 语音转文字模块
使用 faster-whisper，优先 GPU（CUDA），自动回退 CPU。
针对 RTX 4060 (8GB) 优化：默认用 large-v3 + float16。
"""
from __future__ import annotations
import os
import sys
import time
from pathlib import Path
from typing import Optional, Literal


def _add_cuda_dll_dirs() -> None:
    """
    把 pip 安装的 nvidia-*-cu12 包里的 DLL 目录加到 Windows DLL 搜索路径。
    这样 faster-whisper 才能找到 cublas64_12.dll / cudnn*.dll 等运行时库。
    必须在 import faster_whisper 之前调用。
    """
    if sys.platform != "win32":
        return
    try:
        import importlib.util
    except ImportError:
        return

    for pkg in [
        "nvidia.cublas",
        "nvidia.cudnn",
        "nvidia.cuda_runtime",
        "nvidia.cuda_nvrtc",
    ]:
        try:
            spec = importlib.util.find_spec(pkg)
            if spec is None or not spec.submodule_search_locations:
                continue
            pkg_dir = Path(spec.submodule_search_locations[0])
            bin_dir = pkg_dir / "bin"
            if bin_dir.is_dir():
                try:
                    os.add_dll_directory(str(bin_dir))
                except (OSError, AttributeError):
                    pass
                # 同时加到 PATH 作为兜底
                current = os.environ.get("PATH", "")
                if str(bin_dir) not in current.split(os.pathsep):
                    os.environ["PATH"] = str(bin_dir) + os.pathsep + current
        except Exception:
            continue


_add_cuda_dll_dirs()

try:
    from faster_whisper import WhisperModel
except ImportError:
    raise ImportError("请先安装依赖：pip install -r requirements.txt")


# 全局缓存，避免每次都重新加载模型（加载 large-v3 约 3-5 秒）
_MODEL_CACHE: dict = {}

DEVICE_CHOICES = ("auto", "cuda", "cpu")

# GPU 转写所需的 CUDA 12 运行库（pip 包，约 2GB，无需安装 CUDA Toolkit）
CUDA_PIP_PACKAGES = ("nvidia-cublas-cu12", "nvidia-cudnn-cu12==9.*")
CUDA_INSTALL_CMD = "pip install " + " ".join(f'"{p}"' if "*" in p else p for p in CUDA_PIP_PACKAGES)

# ctranslate2 自带 cudnn64_9.dll 入口，但 ops/cnn 子库和 cuBLAS 必须另装
_CUDA_REQUIRED_DLLS = ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn_ops64_9.dll", "cudnn_cnn64_9.dll")


class CudaLibsMissingError(RuntimeError):
    """选择了 GPU 转写，但缺少 cuBLAS / cuDNN 运行库。"""

    def __init__(self, missing: list[str]):
        self.missing = list(missing)
        super().__init__(
            f"GPU 转写缺少 CUDA 运行库：{', '.join(self.missing)}\n"
            f"请安装：{CUDA_INSTALL_CMD}\n"
            "或把「计算设备」切换为 CPU / 自动。"
        )


def cuda_device_count() -> int:
    try:
        import ctranslate2
        return int(ctranslate2.get_cuda_device_count())
    except Exception:
        return 0


def missing_cuda_libs() -> list[str]:
    """返回当前进程加载不到的 CUDA 运行库 DLL 名（为空表示齐全）。"""
    if sys.platform != "win32":
        return []
    import ctypes

    missing: list[str] = []
    for name in _CUDA_REQUIRED_DLLS:
        try:
            ctypes.WinDLL(name)
        except OSError:
            # CTranslate2 用系统默认搜索顺序（含 PATH，如 CUDA Toolkit 的 bin）加载，这里保持一致
            try:
                ctypes.WinDLL(name, winmode=0)
            except OSError:
                missing.append(name)
    return missing


def is_cuda_lib_error(msg: str) -> bool:
    """CTranslate2 运行时报出的 "Library cublas64_12.dll is not found" 之类错误。"""
    low = (msg or "").lower()
    return ("cublas" in low or "cudnn" in low) and (
        "not found" in low or "cannot be loaded" in low or "could not locate" in low
    )


def cuda_libs_error_entry(url: str, exc: BaseException) -> Optional[dict]:
    """若 exc 属于 CUDA 运行库缺失，返回供 --json 输出的结构化错误，否则返回 None。"""
    msg = str(exc)
    if not isinstance(exc, CudaLibsMissingError) and not is_cuda_lib_error(msg):
        return None
    return {
        "url": url,
        "error_type": "cuda_libs_missing",
        "error": msg,
        "missing": getattr(exc, "missing", []),
        "install_guide": {
            "packages": list(CUDA_PIP_PACKAGES),
            "command": CUDA_INSTALL_CMD,
            "alternative": "改用 --device cpu（或 auto）继续转写，速度较慢",
        },
    }


def resolve_device(prefer: Optional[str] = "auto", verbose: bool = True) -> tuple[str, str]:
    """根据用户偏好（auto / cuda / cpu）确定 (device, compute_type)。

    auto：有 CUDA 显卡且运行库齐全 → GPU；缺库时退回 CPU 并提示。
    cuda：强制 GPU，没显卡抛 RuntimeError，缺库抛 CudaLibsMissingError。
    """
    prefer = (prefer or "auto").lower()
    if prefer == "gpu":
        prefer = "cuda"
    if prefer == "cpu":
        return "cpu", "int8"

    gpus = cuda_device_count()
    if gpus <= 0:
        if prefer == "cuda":
            raise RuntimeError("选择了 GPU 转写，但未检测到可用的 NVIDIA CUDA 显卡。请改用 CPU / 自动。")
        return "cpu", "int8"

    missing = missing_cuda_libs()
    if missing:
        if prefer == "cuda":
            raise CudaLibsMissingError(missing)
        if verbose:
            print(f"[Whisper] ⚠️ 检测到 NVIDIA 显卡，但缺少 CUDA 运行库（{', '.join(missing)}），"
                  f"本次改用 CPU 转写（较慢）。\n"
                  f"          安装后即可启用 GPU 加速：{CUDA_INSTALL_CMD}")
        return "cpu", "int8"
    return "cuda", "float16"


def _resolve_model_path(model_size: str) -> str:
    """
    解析模型路径：优先使用项目本地目录 models/<size>/（避免 Windows symlink 权限问题）。
    如果本地目录不存在，回退为 model_size 让 faster-whisper 自动下载。
    """
    script_dir = Path(__file__).parent
    local_dir = script_dir / "models" / model_size
    # 必须包含 model.bin 且大小 > 10MB 才认为是有效模型
    model_bin = local_dir / "model.bin"
    if model_bin.exists() and model_bin.stat().st_size > 10 * 1024 * 1024:
        return str(local_dir)
    return model_size


def load_model(
    model_size: str = "large-v3",
    device: Optional[str] = "auto",
    compute_type: Optional[str] = None,
) -> WhisperModel:
    """加载 Whisper 模型（带缓存）。device 取值见 resolve_device。"""
    device, auto_compute = resolve_device(device)
    compute_type = compute_type or auto_compute

    key = (model_size, device, compute_type)
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]

    model_path = _resolve_model_path(model_size)
    if model_path != model_size:
        print(f"[Whisper] 加载本地模型: {model_path} | device={device} | compute_type={compute_type}")
    else:
        print(f"[Whisper] 加载模型: {model_size} (将从HF下载) | device={device} | compute_type={compute_type}")

    model = WhisperModel(model_path, device=device, compute_type=compute_type)
    _MODEL_CACHE[key] = model
    return model


def transcribe(
    audio_path: str | Path,
    model_size: str = "large-v3",
    language: Optional[str] = None,
    initial_prompt: Optional[str] = None,
    verbose: bool = True,
    device: Optional[str] = "auto",
) -> dict:
    """
    对音频执行转写。

    参数：
        audio_path:    音频文件路径
        model_size:    tiny / base / small / medium / large-v3 (默认)
        language:      None=自动检测；'zh'=中文，'en'=英文……
        initial_prompt: 提示词（有助于中文标点）
        device:        auto（默认）/ cuda / cpu

    返回：
        {
            "language": 检测到的语言,
            "duration": 音频时长（秒）,
            "segments": [{"start", "end", "text"}, ...],
            "full_text": 完整转写文本（不带时间戳）,
            "timed_text": 带时间戳的文本（用于章节定位）,
            "elapsed": 转写耗时（秒）,
        }
    """
    audio_path = str(audio_path)
    model = load_model(model_size, device=device)

    start = time.time()
    segments_iter, info = model.transcribe(
        audio_path,
        language=language,
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
        initial_prompt=initial_prompt,
    )

    detected_lang = info.language
    duration = info.duration

    # 如果是中文但没给 prompt，提供一个以帮助标点
    if detected_lang == "zh" and initial_prompt is None:
        # 重新转写一次带上中文 prompt（提升标点正确率）
        segments_iter, info = model.transcribe(
            audio_path,
            language="zh",
            beam_size=5,
            vad_filter=True,
            initial_prompt="以下是普通话的对话。请准确添加标点符号。",
        )

    segments: list[dict] = []
    full_text_parts: list[str] = []
    timed_parts: list[str] = []

    for seg in segments_iter:
        text = seg.text.strip()
        if not text:
            continue
        segments.append({
            "start": round(seg.start, 2),
            "end": round(seg.end, 2),
            "text": text,
        })
        full_text_parts.append(text)
        timed_parts.append(f"[{_fmt_time(seg.start)}] {text}")

        if verbose:
            # 简易进度提示
            if len(segments) % 20 == 0:
                pct = (seg.end / duration * 100) if duration else 0
                print(f"  ...已转写 {len(segments)} 段 | 进度 {pct:.1f}%")

    elapsed = time.time() - start
    if verbose:
        print(f"[Whisper] 完成！语言={detected_lang} | 音频={duration:.1f}s | 耗时={elapsed:.1f}s | 倍速={duration/max(elapsed,0.1):.1f}x")

    return {
        "language": detected_lang,
        "duration": duration,
        "segments": segments,
        "full_text": " ".join(full_text_parts),
        "timed_text": "\n".join(timed_parts),
        "elapsed": elapsed,
    }


def _fmt_time(sec: float) -> str:
    """把秒转成 mm:ss 或 hh:mm:ss。"""
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法：python transcriber.py <audio_file> [model_size]")
        sys.exit(1)
    size = sys.argv[2] if len(sys.argv) > 2 else "large-v3"
    result = transcribe(sys.argv[1], model_size=size)
    print(f"\n===== 转写文本（前 500 字）=====\n{result['full_text'][:500]}...")
