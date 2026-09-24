# 🎬 视频转写 + AI 总结工具（多平台）

> **设计理念**：Python 只做苦力活（识别平台、下载、转写），让 **Claude (在 VSCode 中)** 做智能活（总结、提炼、美化）。
> 
> 不依赖任何 LLM API，不花一分钱。

**🌐 支持平台：**
- **YouTube**  `youtube.com/watch` / `youtu.be/`
- **哔哩哔哩 (B 站)**  `bilibili.com/video/` / `b23.tv/`
- **抖音**  `douyin.com/video/` / `v.douyin.com/`

先试原生字幕（秒级），没字幕再回退本地 Whisper GPU 转写。B 站 / 抖音视频多数没字幕，基本都走 Whisper。

---

## 📦 项目结构

```text
video-summarizer/           # skill 名与目录名
├── README.md              # 本文件
├── SKILL.md               # CodeBuddy skill 描述
├── requirements.txt       # 核心 Python 依赖
├── requirements-whisper.txt  # Whisper 回退通路按需装
├── .gitignore
├── providers/             # 多平台注册表（YouTube / B 站 / 抖音）
├── downloader.py          # yt-dlp 下载模块（多平台）
├── subtitle_fetcher.py    # yt-dlp 字幕抓取模块（多平台）
├── cookies_utils.py       # cookies JSON → Netscape 自动转换
├── transcriber.py         # faster-whisper 转写模块（GPU 加速）
├── main.py                # CLI 入口：一键流程（链接 → 字幕/下载 + 转写）
├── media_downloader.py    # CLI 入口：只下载（视频/音频、画质、字幕、封面）
├── transcribe_file.py     # CLI 入口：只转写本地音视频文件
├── app_gui.py             # 主入口（图形界面 / exe）
├── build_exe.py           # exe 打包脚本
├── gui/                   # 图形界面源码（core / widgets / tabs）
├── youtube_cookies.json   # YouTube cookies（J2TEAM Cookies 导出）
├── bilibili_cookies.json  # B 站 cookies
├── douyin_cookies.json    # 抖音 cookies
├── bin/deno.exe           # YouTube n-sig 解密 JS runtime
├── models/medium/         # Whisper 本地模型（按需下载）
└── 一键转写.bat            # Windows 双击启动
```

## 🔧 环境要求

| 项 | 要求 |
|---|---|
| Python | 3.9+ |
| FFmpeg | 必装（yt-dlp 和 whisper 都依赖） |
| GPU | NVIDIA 显卡（可选但强烈推荐） |
| CUDA | 12.x（匹配 faster-whisper） |

## 🚀 首次安装

### 1. 安装 FFmpeg

**Windows 推荐方式**（管理员 PowerShell）：
```powershell
winget install Gyan.FFmpeg
# 或
choco install ffmpeg
```

验证：`ffmpeg -version`

### 2. 安装 Python 依赖

```powershell
cd d:\AiTest\video-summarizer
pip install -r requirements.txt
```

### 3. 【推荐】启用 GPU 加速

如果有 NVIDIA 显卡，额外安装 CUDA 版的 cuDNN 以获得 10 倍加速：

```powershell
# 最简单的方式：让 faster-whisper 自己带的 CUDA 库生效
# 实测 RTX 4060 可以直接跑 large-v3，无需额外配置

# 如果报 "cuDNN missing" 错误，可以：
pip install nvidia-cudnn-cu12==9.*
```

## 🎯 使用方式

### 方式 0：桌面图形界面（exe，零命令行）🖥️

双击 **`VideoSummarizer.exe`**，在界面上即可完成：

- 🎬 **一键转写**：选平台 + 粘链接（支持多行批量）+ 选模式/模型，链接直接变文字稿
- ⬇️ **视频下载**：**只下载不转写** —— 视频/仅音频、画质上限、MP4/MKV、可带字幕与封面
- 📁 **本地转写**：**只转写不下载** —— 选本地音视频文件或整个目录，离线转文字（视频自动抽音轨）
- 📦 **模型管理**：Whisper 模型下载（镜像加速、断点续传）/ 删除 / 查看占用
- 📄 **输出管理**：文字稿列表、搜索、预览、一键复制给 AI、另存、删除
- ⚙️ **环境安装**：一键装 Python 依赖 / FFmpeg / Deno，甚至能下载便携版 Python；支持扫码登录 B 站、抖音（**复用本机 Edge/Chrome，无需下载 Chromium**）

三条链路彼此独立，也能串起来：一键转写页「⬇ 只下载不转写」→ 下载页，下载页「📁 送去本地转写」→ 本地转写页。

详见 [GUI使用说明.md](GUI使用说明.md)。开发态运行：`python app_gui.py`；重新打包：`python build_exe.py`。

### 方式 A：在 VSCode 中用 Skill 调用（推荐）⭐

1. 打开 CodeBuddy/Claude 对话
2. 说：**"帮我总结这个视频：<链接>"**（YouTube / B 站 / 抖音 都行）
3. Skill 会自动：
   - 按 URL 识别平台
   - 先试字幕，没字幕再下载音频 + 本地 Whisper 转写
   - Claude 读取转写文本 → 智能总结 → 输出 Markdown 文档

### 方式 B：命令行直接调用

```powershell
# 查看支持的平台
python main.py --list-platforms

# 单个视频（自动识别平台）
python main.py "https://www.youtube.com/watch?v=xxx"
python main.py "https://www.bilibili.com/video/BV1xx/"
python main.py "https://v.douyin.com/abcd/"

# 强制指定平台
python main.py "<URL>" --platform bilibili

# 指定 Whisper 模型
python main.py "<URL>" --model medium

# 指定语言
python main.py "<URL>" --language zh

# 批量处理
python main.py --file urls.txt

# 完整参数
python main.py "URL" `
    --model large-v3 `
    --output-dir ./output `
    --download-dir ./downloads `
    --keep-audio `
    --json
```

### 方式 C：双击 `一键转写.bat`

最简单，但只做转写，不含总结。

### 方式 D：只下载 / 只转写（两个独立 CLI）

```powershell
# ── 只下载，不转写 ────────────────────────────────────────
python media_downloader.py "<URL>"                                  # 最佳画质 mp4
python media_downloader.py "<URL>" -q 1080 --container mp4 --subs   # 1080p + 字幕
python media_downloader.py "<URL>" --kind audio --audio-format mp3  # 只要 mp3
python media_downloader.py --file urls.txt -o D:\videos             # 批量
python media_downloader.py "<URL>" --thumbnail --embed-metadata     # 带封面和元数据

# ── 只转写本地文件，不联网 ─────────────────────────────────
python transcribe_file.py D:\videos\a.mp4                  # 单个文件
python transcribe_file.py a.mp4 b.m4a c.mkv --model medium  # 多个文件
python transcribe_file.py --dir D:\videos --recursive       # 整个目录
python transcribe_file.py a.mp4 --language zh --json        # 指定语言 + 结构化输出
```

`media_downloader.py` 复用了主流程的 cookies 降级链、平台识别和抖音 f2 增强通路，
所以登录能力与支持平台跟 `main.py` 完全一致；
`transcribe_file.py` 会先用 FFmpeg 把视频抽成 16kHz 单声道音轨再交给 Whisper，
输出的 `*.transcript.md` 与主流程同构。

## 📊 Whisper 模型选择

| 模型 | 显存 | 速度（4060） | 精度 | 适用 |
|------|------|-------------|------|------|
| tiny | 1GB | 极快 | ⭐⭐ | 快速试用 |
| base | 1GB | 很快 | ⭐⭐⭐ | 英文播客 |
| small | 2GB | 快 | ⭐⭐⭐⭐ | 一般视频 |
| medium | 5GB | 中 | ⭐⭐⭐⭐ | 日常推荐 |
| **large-v3** ⭐ | **6GB** | **中** | **⭐⭐⭐⭐⭐** | **默认，最佳中文** |

实测：RTX 4060 上 1 小时英文视频用 large-v3 约需 **3-5 分钟**。

## 🧰 输出示例

脚本运行完成后，`./output/` 下会生成：

```markdown
# 📺 视频标题

## 📋 视频信息
- 标题、作者、时长、URL、视频ID、语言

## 📝 视频简介
（从 YouTube 抓取）

## ⏱️ 带时间戳的转写
[00:00] 各位朋友大家好...
[00:15] 今天我们聊的话题是...
...

## 📖 完整转写文本
（纯文本，便于 AI 总结）
```

这份 Markdown 就是**喂给 Claude 的最佳输入**，它会帮你生成漂亮的总结。

## 🐛 常见问题

### Q: 提示 "FFmpeg not found"
装 FFmpeg 并加到 PATH，见上面「环境要求」。

### Q: 转写巨慢
检查是否用了 GPU：启动时应该看到 `device=cuda | compute_type=float16`。如果是 `cpu`，说明没检测到显卡。

### Q: YouTube 下载失败（403/429）/ B 站 登录视频不行 / 抖音报 403
- 升级 yt-dlp：`pip install -U yt-dlp`
- 用 **J2TEAM Cookies** 浏览器插件，登录目标平台后导出 JSON，覆盖到对应文件：
  - YouTube → `youtube_cookies.json`
  - B 站 → `bilibili_cookies.json`
  - 抖音 → `douyin_cookies.json`

### Q: 中文转写标点不对
已经在代码里自动处理了。如果仍不理想，可以试 `--model large-v3 --language zh`。

## 🔗 相关链接

- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [Whisper 模型列表](https://huggingface.co/Systran)

---

**Happy summarizing!** 🎉
