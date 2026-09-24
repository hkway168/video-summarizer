# 🖥️ 视频转写助手（桌面版 exe）使用说明

原来的 Skill 现在有了图形界面：**双击 `VideoSummarizer.exe` 即可使用**，
不用记命令行参数，环境、模型、输出都能在界面上管理。

---

## 一、快速开始（3 步）

| 步骤 | 操作 |
|---|---|
| 1️⃣ | 双击 `VideoSummarizer.exe`（请保持它和 `main.py` 在同一目录） |
| 2️⃣ | 进「⚙️ 环境安装」页 → 点 **🚀 一键安装运行环境** |
| 3️⃣ | 进「📦 模型管理」页 → 选 `medium` → 点 **⬇ 下载选中模型** |

装好后回到「🎬 一键转写」页，粘贴链接 → **▶ 开始转写**，
生成的文字稿会出现在「📄 输出管理」页。

> 只想总结**有字幕**的视频（多数 YouTube 视频）？第 3 步可以跳过，字幕通路是秒级的。

---

## 二、三种任务，各走各的

下载和转写是**完全独立**的两件事，按需要选页面即可：

| 我想做的事 | 用哪个页面 | 底层调用 |
|---|---|---|
| 给个链接，直接要文字稿 | 🎬 **一键转写** | `main.py` |
| 只想把视频/音频存到电脑里 | ⬇️ **视频下载** | `media_downloader.py` |
| 电脑里已有文件，只要转文字 | 📁 **本地转写** | `transcribe_file.py` |

三者还能串起来用：

```text
一键转写页  ──「⬇ 只下载不转写」──►  视频下载页
视频下载页  ──「📁 送去本地转写」──►  本地转写页  ──►  输出管理页
```

---

## 三、六个页面分别做什么

### 🎬 一键转写（链接 → 文字稿，一步到位）
- **平台**：默认「自动识别」，识别不出来时可手动指定 YouTube / 哔哩哔哩 / 抖音
- **链接输入框**：一行一个链接，可一次粘贴多条批量处理；支持直接粘贴带文案的分享链接（会自动提取 URL）
- **模式**
  - `自动`：先抓平台字幕，没有字幕才走本地 Whisper（推荐）
  - `仅字幕`：秒出结果，没字幕就报错
  - `仅 Whisper`：忽略字幕，强制本地转写（想要时间戳时用）
- **模型 / 语言 / cookies 来源 / 字幕语言**：可留默认
- 底部实时日志；出错时会自动提示缺什么、并可一键跳到对应页面处理
- 不想要文字稿、只要文件 → 点 **⬇ 只下载不转写**

### ⬇️ 视频下载（只下载，不碰 Whisper，不吃显卡）
- **下载内容**：`视频（画面 + 声音）` 或 `仅音频`
- **画质上限**：最佳 / 2160p / 1440p / 1080p / 720p / 480p / 360p / 最小体积
- **视频格式**：`MP4`（兼容性最好，默认）/ `MKV` / `保持原始`
- **音频格式**（仅音频时可选）：保持原始 / MP3 / M4A / WAV / FLAC（转码需 FFmpeg）
- **可选附加**：同时下载字幕（自动转 srt）、下载封面图、写入标题/作者元数据
- 默认保存到 `videos/`，**不会**被「清理音频缓存」删掉
- 下载完可点 **📁 把刚下载的文件送去「本地转写」**

### 📁 本地转写（离线，只转写不联网）
- **添加文件**：多选文件、添加整个目录（可含子目录）、或从下载页一键带入
- 列表显示类型 / 体积 / 时长（自动用 ffprobe 读取），可移除单项或清空
- 支持视频 `mp4 / mkv / mov / avi / webm / flv / ts…` 与音频 `mp3 / m4a / wav / flac / aac / opus…`
- 视频会先用 **FFmpeg 抽 16kHz 单声道音轨**，再交给 Whisper，稳定且省时间
- 可选模型、语言、是否输出带时间戳的段落
- 适合场景：录屏、会议录音、手机拍的视频、以前下载好的片子

### 📦 模型管理
- 列出 `tiny / base / small / medium / large-v2 / large-v3` 的安装状态与体积
- **下载**：支持断点续传，默认走 `hf-mirror.com` 镜像（国内快）
- **删除**：释放磁盘；模型都存在 `models/<尺寸>/`
- 选型建议：`small` 试用 → **`medium` 日常推荐** → `large-v3` 质量最佳（显存 ≥6GB）

### 📄 输出管理
- 列出输出目录下所有文字稿（标题 / 平台 / 来源 / 大小 / 时间），支持关键词搜索
- 右侧全文预览
- **📋 复制文字稿**：只复制正文（带标题与链接），粘贴给 AI 说「帮我总结这份视频文字稿」即可
- 还可打开文件、打开目录、另存为、删除、清理音频缓存

### ⚙️ 环境安装
| 功能 | 说明 |
|---|---|
| Python 运行时 | 自动扫描本机 Python；**没装过也没关系**，点「⬇ 下载便携版 Python」自动装好（约 11MB，免安装） |
| 创建独立虚拟环境 | 把依赖装到 `.venv`，不污染系统 Python |
| 环境体检 | 一次看清 Python / pip / yt-dlp / rich / faster-whisper / huggingface_hub / playwright / f2 / FFmpeg / Deno / GPU / 模型；**哪一项没装好就双击那一行，自动安装** |
| 🚀 一键安装 | 基础依赖 + FFmpeg +（可选）Whisper 依赖 |
| 单项安装 | 基础依赖、Whisper 依赖、FFmpeg、Deno、升级 yt-dlp、登录依赖、抖音增强 |
| 平台登录 | B 站 / 抖音 **扫码登录**，自动生成 cookies（有效期约 15~30 天）；**默认复用本机已装的 Edge / Chrome，不下载 Chromium** |
| pip 镜像 | 默认清华源，可切换或留空用官方源 |

---

## 四、目录结构（exe 相关）

```text
video-summarizer/
├── VideoSummarizer.exe     # ← 双击这个
├── app_gui.py              # 界面入口（开发态用）
├── build_exe.py            # 打包脚本
├── 启动界面.bat             # 开发态启动（用系统 Python 跑界面）
├── gui/                    # 界面源码
│   ├── core/               # 路径/配置/任务/环境/模型/输出/媒体/任务构造
│   ├── widgets/            # 主题、控制台、滚动容器
│   └── tabs/               # 六个功能页
├── main.py                 # CLI：一键流程（链接 → 字幕/下载 + 转写）
├── media_downloader.py     # CLI：只下载（视频/音频、画质、字幕、封面）
├── transcribe_file.py      # CLI：只转写本地文件
├── models/<尺寸>/           # Whisper 模型
├── output/                 # 生成的文字稿
├── videos/                 # 「视频下载」页保存的视频/音频 ← 不会被清理缓存删除
├── downloads/              # 转写流程的临时音频/字幕（可随时清理）
├── tools/                  # 界面自动下载的便携版 Python / FFmpeg / Deno
├── logs/gui.log            # 界面日志（出问题先看这里）
└── gui_config.json         # 界面配置（记住你的选择）
```

### 绿色免安装：所有文件都在 exe 旁边

程序**不写系统目录、不写注册表**。两种模式：

| 模式 | 触发条件 | 文件位置 |
|---|---|---|
| **独立**（放桌面等任意位置） | exe 同级没有 `main.py` | exe 旁边新建**一个**文件夹 `VideoSummarizer-Data/` |
| **项目内** | exe 与 `main.py` 同目录 | 直接用项目目录，不额外建文件夹 |

独立模式下，exe 旁边只会多出一个文件夹，里面装着一切：

```text
桌面/
├── VideoSummarizer.exe
└── VideoSummarizer-Data/        ← 唯一生成的文件夹
    ├── main.py / providers/ / scripts/    # 自动释放的运行脚本
    ├── models/        # Whisper 模型
    ├── output/        # 文字稿
    ├── videos/        # 下载的视频
    ├── downloads/     # 临时音频（可随时清理）
    ├── tools/         # 自动下载的 FFmpeg / 便携版 Python
    ├── logs/gui.log   # 运行日志
    └── gui_config.json
```

这样管理很方便：

- **搬家 / 换电脑**：把 `VideoSummarizer.exe` + `VideoSummarizer-Data/` 一起拷走，模型、cookies、文字稿全都在
- **卸载**：直接删掉这两个，系统里不留任何痕迹
- **看文件在哪**：「环境安装」页最下方「⑤ 文件位置」卡片列出全部路径，每行都有「打开」按钮

> ⚠️ **v1.0.0 的已知问题**：那个版本会把脚本**零散地**释放到 exe 旁边，
> 所以放桌面运行会在桌面撒一堆 `.py` 文件。新版会自动检测到这些散落文件，
> 在「环境安装」页顶部显示「🧹 一键清理」按钮，点一下即可
> （你自己的文件不会被动，`output / videos / models / .venv` 也会保留）。
>
> 如果 exe 放在**没有写入权限**的位置（如 `C:\Program Files`），程序会提示并临时退回系统目录 —— 把 exe 挪到桌面或 D 盘即可恢复绿色模式。

---

## 五、也可以直接用命令行

三个入口都能单独跑，界面只是它们的外壳：

```powershell
# 一键：链接 → 文字稿
python main.py "<URL>" --json

# 只下载：1080p mp4 + 字幕，存到 D:\videos
python media_downloader.py "<URL>" -q 1080 --container mp4 --subs -o D:\videos

# 只下载音频并转成 mp3
python media_downloader.py "<URL>" --kind audio --audio-format mp3

# 只转写本地文件
python transcribe_file.py D:\videos\a.mp4 --model medium

# 批量转写一个目录
python transcribe_file.py --dir D:\videos --recursive
```

---

## 六、自己重新打包

```powershell
pip install pyinstaller
python build_exe.py              # 单文件 exe（默认）
python build_exe.py --onedir     # 目录形式，启动更快
python build_exe.py --console    # 保留黑窗口，排错用
```

产物在 `dist/VideoSummarizer.exe`，并会自动复制一份到项目根目录。

打包策略：**exe 只装界面本体（纯 tkinter + 标准库，约 10MB）**，
`yt-dlp / faster-whisper` 等重依赖由界面按需安装 —— 这样 exe 小，
而且随时能在界面上升级 yt-dlp 应对平台风控。

---

## 七、常见问题

**Q：我把 exe 放到桌面，桌面被生成了一堆 py 文件和文件夹？**
那是 v1.0.0 的缺陷：它把内置脚本**零散地**释放到了 exe 旁边。
换上新版 exe 再打开 → 「环境安装」页顶部会出现 **「🧹 一键清理这些文件」**，点它即可清掉。
新版会把这些内容统一收进 `VideoSummarizer-Data/` 一个文件夹里。

手动清理也可以，桌面上这些是程序生成的，可安全删除：

```text
main.py  media_downloader.py  transcribe_file.py  downloader.py
subtitle_fetcher.py  transcriber.py  cookies_utils.py
requirements.txt  requirements-whisper.txt  requirements-login.txt  requirements-douyin.txt
providers/  scripts/  __pycache__/  logs/  tools/  downloads/  gui_config.json
```

（`output/`、`videos/`、`models/` 里可能有你的文字稿、视频和模型，删前先确认。）

**Q：文件都生成在哪？我想自己管理**
独立模式：exe 旁边的 `VideoSummarizer-Data/`；项目内模式：项目目录。
「环境安装」页「⑤ 文件位置」卡片会列出全部路径。
输出目录还可以单独改：「一键转写 / 本地转写」页的「输出目录」、「视频下载」页的「保存目录」都能自定义。

**Q：双击 exe 没反应 / 闪退？**
看 `VideoSummarizer-Data\logs\gui.log`（项目内模式则是 `logs/gui.log`）。
也可以 `python build_exe.py --console` 重新打包，用黑窗口看报错。

**Q：提示「未找到 Python」？**
环境安装页 → 「⬇ 下载便携版 Python（免安装）」，一分钟搞定；或「浏览…」手动指定 `python.exe`。

**Q：体检表里某项没装好，怎么安装？**
**直接双击那一行**就会开始安装（也可以选中后点右上角「🔧 安装选中项」，或右键弹出菜单）。
表格里「详情 / 处理建议」列已经写明了该行双击后会做什么。
想一次装完就点「③ 安装组件」里的「🚀 一键安装运行环境」。

**Q：点了安装但看起来没反应？**
看「安装日志」面板：任务开始会打印 `▶ …`，失败会打印 `✖ …` 并附带 💡 排错建议
（网络、镜像源、权限、pip 缺失等都会分别提示）。若提示「当前页面已有任务在运行」，
说明上一个任务（比如环境体检）还没结束，等几秒再点。

**Q：只想下载视频，为什么还要装 Whisper？**
不需要。「视频下载」页只用到 `yt-dlp + FFmpeg`，一键安装时把「包含本地 Whisper 转写能力」
取消勾选也能正常下载。Whisper 依赖和模型只有转写时才用得上。

**Q：转写提示需要 Whisper / 缺模型？**
说明该视频没有字幕（或你用的是「本地转写」页）。按弹窗提示：先装 Whisper 依赖（环境安装页），
再下载模型（模型管理页）。

**Q：「视频下载」的文件会被「清理音频缓存」删掉吗？**
不会。下载页默认存在 `videos/`，清理缓存只动 `downloads/`（转写过程的临时音频）。

**Q：本地转写支持哪些格式？**
视频 mp4 / mkv / mov / avi / webm / flv / ts / m4v / wmv / mpg，音频 mp3 / m4a / wav /
flac / aac / opus / ogg / wma。视频会自动用 FFmpeg 抽音轨，所以视频文件也要装 FFmpeg。

**Q：B 站 / 抖音下载失败（403、需要登录）？**
环境安装页 → 「📱 扫码登录」；或用 J2TEAM Cookies 插件导出 JSON 覆盖
`bilibili_cookies.json` / `douyin_cookies.json`。

**Q：扫码登录会用我电脑上的浏览器，还是要另外下载？**
**默认直接用你电脑上已装的浏览器**（按 Edge → Chrome → Brave → Chromium 顺序自动挑），
不会下载那 150MB 的 Chromium。登录只需要 `playwright` 库（约 5MB）来驱动浏览器。
环境安装页「④ 平台登录」里的「登录浏览器」下拉框可以手动指定用哪个；
只有本机完全没有 Chromium 系浏览器时，才需要点「⬇ 下载 Chromium（可选）」。

> 说明：Firefox / Safari 不能用于此处，因为驱动方式不同；Windows 一般自带 Edge，通常无需任何额外安装。
>
> 命令行同样支持：
> ```powershell
> python scripts/login_bilibili.py --list-browsers   # 看本机有哪些可用浏览器
> python scripts/login_bilibili.py --browser edge    # 指定用 Edge
> python scripts/login_douyin.py --browser playwright # 强制用自带 Chromium
> ```

**Q：转写很慢？**
日志里出现 `device=cuda` 才是 GPU 加速。只有 CPU 时建议改用 `small` 模型，
或优先使用「仅字幕」模式。

**Q：杀毒软件报毒？**
PyInstaller 打包的 exe 常被误报，加白名单即可；也可以直接用 `启动界面.bat` 以脚本方式运行。

---

## 八、和原来的 Skill 有冲突吗？

没有。界面只是在后台调用这几个 CLI，**原来的命令行用法、Skill 调用方式全部照旧**：

```powershell
python main.py "<URL>" --json
```

`main.py / downloader.py / subtitle_fetcher.py / transcriber.py / providers/` 一行都没改；
新增的 `media_downloader.py`、`transcribe_file.py` 只是复用它们的能力，多开了两个入口。

也就是说：想让 AI 自动总结就继续用 Skill；想手动、可视化操作就用 exe。
