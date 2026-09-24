"""
scripts/download_model.py —— Whisper 模型一键下载器

用法：
    python scripts/download_model.py                     # 默认下载 medium
    python scripts/download_model.py --size small        # 下载 small
    python scripts/download_model.py --size large-v3     # 下载 large-v3
    python scripts/download_model.py --list              # 列出可选模型

说明：
    - 从 HuggingFace 仓库 Systran/faster-whisper-<size> 下载模型文件
    - 下载到 <SKILL_DIR>/models/<size>/
    - 下载后可直接被 transcriber.py 自动识别
    - 支持断点续传（huggingface_hub 默认开启）
"""
from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path

# 模型尺寸 → HuggingFace repo id / 估算体积
AVAILABLE_MODELS = {
    "tiny":     {"repo": "Systran/faster-whisper-tiny",     "size": "~75 MB"},
    "base":     {"repo": "Systran/faster-whisper-base",     "size": "~145 MB"},
    "small":    {"repo": "Systran/faster-whisper-small",    "size": "~460 MB"},
    "medium":   {"repo": "Systran/faster-whisper-medium",   "size": "~1.4 GB"},
    "large-v2": {"repo": "Systran/faster-whisper-large-v2", "size": "~3.0 GB"},
    "large-v3": {"repo": "Systran/faster-whisper-large-v3", "size": "~3.0 GB"},
}


def _skill_dir() -> Path:
    """scripts/download_model.py 所在目录的上一级（= SKILL_DIR / video-summarizer）。"""
    return Path(__file__).resolve().parent.parent


def list_models() -> None:
    print("可选的 Whisper 模型尺寸：\n")
    print(f"  {'Size':<10} {'体积':<10} {'HuggingFace Repo'}")
    print(f"  {'-'*10} {'-'*10} {'-'*40}")
    for size, info in AVAILABLE_MODELS.items():
        print(f"  {size:<10} {info['size']:<10} {info['repo']}")
    print("\n使用示例：python scripts/download_model.py --size medium")


def download(size: str, mirror: str | None = None) -> Path:
    if size not in AVAILABLE_MODELS:
        raise ValueError(
            f"不支持的模型尺寸: {size}\n"
            f"可选: {', '.join(AVAILABLE_MODELS.keys())}"
        )

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("❌ 未安装 huggingface_hub，请先执行：")
        print("   pip install -r requirements-whisper.txt")
        sys.exit(1)

    # 可选：切换到 HF 镜像（国内加速）
    if mirror:
        os.environ["HF_ENDPOINT"] = mirror
        print(f"🌐 使用镜像: {mirror}")

    repo_id = AVAILABLE_MODELS[size]["repo"]
    est_size = AVAILABLE_MODELS[size]["size"]
    target_dir = _skill_dir() / "models" / size

    print(f"📥 开始下载 Whisper 模型")
    print(f"   尺寸     : {size}")
    print(f"   估算体积 : {est_size}")
    print(f"   来源     : {repo_id}")
    print(f"   目标目录 : {target_dir}")
    print(f"   （支持断点续传，中断后重新执行会继续下载）\n")

    target_dir.mkdir(parents=True, exist_ok=True)

    # 只拉取真正需要的文件，忽略 readme/pytorch_model 等无关文件
    snapshot_download(
        repo_id=repo_id,
        local_dir=str(target_dir),
        local_dir_use_symlinks=False,  # Windows 避免 symlink 权限问题
        allow_patterns=[
            "config.json",
            "model.bin",
            "tokenizer.json",
            "vocabulary.txt",
            "vocabulary.json",
            "preprocessor_config.json",
        ],
    )

    # 验证
    model_bin = target_dir / "model.bin"
    if not model_bin.exists() or model_bin.stat().st_size < 10 * 1024 * 1024:
        print("\n⚠️ 下载似乎不完整（未找到 model.bin 或体积过小）")
        print("   请检查网络后重试。")
        sys.exit(2)

    mb = model_bin.stat().st_size / 1024 / 1024
    print(f"\n✅ 下载完成！model.bin = {mb:.1f} MB")
    print(f"   现在可以正常使用 Whisper 转写：python main.py <URL> --model {size}")
    return target_dir


def main() -> None:
    parser = argparse.ArgumentParser(
        description="下载 faster-whisper 模型到本地 models/<size>/ 目录",
    )
    parser.add_argument(
        "--size", "-s", default="medium",
        choices=list(AVAILABLE_MODELS.keys()),
        help="模型尺寸（默认 medium，中英文较均衡）",
    )
    parser.add_argument(
        "--list", "-l", action="store_true",
        help="列出所有可选模型",
    )
    parser.add_argument(
        "--mirror", default=None,
        help="HuggingFace 镜像地址，如国内用户可用：https://hf-mirror.com",
    )
    args = parser.parse_args()

    if args.list:
        list_models()
        return

    download(args.size, mirror=args.mirror)


if __name__ == "__main__":
    main()
