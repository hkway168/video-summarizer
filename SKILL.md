---
name: video-summarizer
description: 多平台视频总结工具（支持 YouTube / 哔哩哔哩 / 抖音）。优先抓原生字幕（秒级），没字幕时再用本地 Whisper (GPU) 转写，然后由 Claude 智能总结成结构化 Markdown 文档。当用户提到"总结/翻译/整理视频"、"youtube.com / youtu.be / bilibili.com / b23.tv / douyin.com / v.douyin.com 链接"、"把视频转成笔记/文档"等需求时自动触发。全程本地运行，不依赖任何 LLM API。
version: 3.0.0
author: user
---

# 🎬 视频总结工具（多平台）

本 skill 的核心工作流：**Python 脚本按 URL 自动识别平台 → 优先抓字幕 → 没字幕时本地 Whisper (GPU) 转写 → 由 Claude 智能总结**。
这样能大幅提速：有字幕的视频**不用下载、不用走 GPU**，纯文本分析更快，而且完全不用 LLM API。

## 🌐 支持的平台

| 平台 | 典型 URL 示例 | Cookies 文件 | 字幕情况 |
|---|---|---|---|
| **YouTube** | `https://www.youtube.com/watch?v=...` / `https://youtu.be/...` | `youtube_cookies.json` | 作者上传 + YouTube 自动字幕，多数有 |
| **哔哩哔哩 (B 站)** | `https://www.bilibili.com/video/BV...` / `https://b23.tv/...` | `bilibili_cookies.json` | 只有 UP 主手动上传 CC 字幕，多数没 → 会走 Whisper |
| **抖音** | `https://www.douyin.com/video/...` / `https://v.douyin.com/...` | `douyin_cookies.json` | 几乎没字幕 → 基本都走 Whisper |

> **自动识别**：只要传入链接，脚本会自动识别属于哪个平台并加载对应 cookies + 适配参数。也可用 `--platform youtube/bilibili/douyin` 强制指定。

> **📦 轻量化打包策略**：为减小 skill 分发体积，**Whisper 模型（~1.4 GB）和 `faster-whisper` 依赖默认不随包发走**，
> 只在视频没有字幕需要回退转写时，脚本会返回 `error_type=whisper_required` 的结构化提示，
> Agent 根据提示引导用户执行两条命令即可安装。详见下文「🧰 按需组件安装」小节。

## 📁 路径约定（重要）

本文档中 `<SKILL_DIR>` 代表 **本 skill 所在的目录**，它：
- 目录名为 `video-summarizer`
- **父目录可任意**（可能是 `d:\AiTest\video-summarizer`，也可能是 `e:\projects\video-summarizer`、`C:\Users\xxx\video-summarizer` 等）

**Agent 使用本 skill 时的实际操作**：
- 直接把 `<SKILL_DIR>` 替换为 `SKILL.md` 文件所在的真实绝对路径即可
- 所有 `terminal` 调用都把 `commandWorkingDirectory` 设为 `<SKILL_DIR>`
- 所有相对路径（`main.py`、`output/`、`*_cookies.json`、`bin/deno.exe` 等）都相对于 `<SKILL_DIR>`

## 🧩 核心组件（重要！修改前请先了解）
| 组件 | 路径 | 作用 | 打包默认包含？ | 能否删除 |
|---|---|---|---|---|
| 主入口 | `main.py` | 统一 CLI，按 URL 识别平台、串联「元数据 → 字幕 → Whisper → 写 md」 | ✅ | ❌ |
| 平台注册表 | `providers/` | YouTube / B 站 / 抖音 的平台描述（cookies 文件名、deno、player_client、额外参数） | ✅ | ❌ |
| 字幕抓取 | `subtitle_fetcher.py` | 调用 yt-dlp 抓 vtt 并转纯文本（支持所有平台） | ✅ | ❌ |
| 下载器 | `downloader.py` | 抓元数据 + 音频下载（支持所有平台） | ✅ | ❌ |
| Whisper 转写 | `transcriber.py` | faster-whisper GPU 转写 | ✅（文件本身小） | ❌ |
| Cookies 处理 | `cookies_utils.py` | 自动把 J2TEAM Cookies 导出的 JSON 转成 netscape 格式 | ✅ | ❌ |
| **JS 运行时** | `bin/deno.exe` (~121 MB) | **yt-dlp 解 YouTube n-sig 加密签名必需**，B 站/抖音不需要但保留无妨 | ✅ **必须包含** | ❌ 强烈保留 |
| `faster-whisper` 依赖 | `requirements-whisper.txt` | pip 包，仅 Whisper 回退时需要 | ❌ **默认不安装** | — |
| 本地模型 | `models/medium/` (~1.4 GB) | 离线 Whisper medium 模型 | ❌ **默认不打包** | — |
| 模型下载脚本 | `scripts/download_model.py` | 用户一键从 HuggingFace 拉取 Whisper 模型 | ✅ | ❌ |
| **B 站扫码登录脚本** | `scripts/login_bilibili.py` | Playwright 扫码登录 B 站并自动写入 `bilibili_cookies.json` | ✅ | ❌ |
| **抖音扫码登录脚本** | `scripts/login_douyin.py` | Playwright 扫码登录抖音并自动写入 `douyin_cookies.json` | ✅ | ❌ |
| Playwright 依赖 | `requirements-login.txt` | pip 包，仅扫码登录时需要 | ❌ **默认不安装** | — |
| **抖音增强下载器** | `providers/douyin_f2.py` | 基于 f2 实现抖音 a_bogus 动态签名，绕过 yt-dlp 当前无法应对的抖音风控 | ✅ | ❌ |
| f2 依赖 | `requirements-douyin.txt` | pip 包，仅当抖音 yt-dlp 通路被风控拦截时按需安装 | ❌ **默认不安装** | — |
| YouTube Cookies | `youtube_cookies.json` | J2TEAM Cookies 插件导出，用于 YouTube | ✅ 模板 | ❌ |
| B 站 Cookies | `bilibili_cookies.json` | J2TEAM Cookies 插件导出，用于 B 站（可选，匿名能看的视频可不放） | ✅ 空模板 | ✅ 用户不用就保持为空 |
| 抖音 Cookies | `douyin_cookies.json` | J2TEAM Cookies 插件导出，用于抖音（强烈建议放上，否则经常 403） | ✅ 空模板 | ✅ |
| 批处理脚本 | `一键转写.bat` | Windows 双击入口（不含 AI 总结） | ✅ | ✅ 可删 |

> 📌 `bin/deno.exe` 主要是 YouTube 通路需要。B 站和抖音不依赖它，但即使没用到也不会增加运行时开销，**切勿误删**。

## 🧰 按需组件安装（Whisper 通路）

> **什么时候会触发？**
> 当 Skill 调用 `python main.py` 后，JSON 结果中 `errors` 包含 `error_type == "whisper_required"` 的条目时，
> 表示视频没有可用字幕、需要回退本地 Whisper，但用户本地环境没装 `faster-whisper` 或模型文件丢失。
>
> **⚠️ 高频命中**：B 站和抖音视频大部分没有字幕，**几乎必定会要求 Whisper**。给用户 YouTube 以外的视频时，最好提前准备好 Whisper 环境。

**Agent 收到 `whisper_required` 时的标准应对流程**：

1. **不要自己尝试注册器/重试**。从 JSON 错误的 `missing` 字段读出到底缺什么。
2. 把 `install_guide` 里的两条命令原文转述给用户，并提示体积预估：
   ```powershell
   # 第一步：安装 Whisper 依赖（几十 MB，很快）
   pip install -r requirements-whisper.txt

   # 第二步：下载 Whisper 模型（medium 约 1.4 GB）
   python scripts/download_model.py --size medium
   ```
3. 询问用户是否希望换更小的模型（`install_guide.model_size_options` 里列出了所有可选项）：
   - 对英文为主的视频，`small` 已经够用（~460 MB）
   - 对中文视频推荐 `medium`（~1.4 GB，默认）
   - 高端 GPU 用户可选 `large-v3`（~3.0 GB）
4. 如果用户在国内网络访问 HuggingFace 不通，提醒可走镜像：
   ```powershell
   python scripts/download_model.py --size medium --mirror https://hf-mirror.com
   ```
5. 安装完成后，**使用同一命令重跑 `python main.py <URL> --json`** 即可。无需重新设置 cookies。

> **轻量化技巧**：如果用户明确表示"我只会用有字幕的视频"（典型 YouTube 场景），可让其添加 `--mode subtitle-only` 彻底关闭 Whisper 回退。

## 🔐 按需组件安装（扫码登录 · B 站 / 抖音）

> **什么时候会触发？**
> 当脚本 JSON 结果的 `errors[i].error_type == "login_required"` 时。
> 典型触发场景：
> - B 站登录/会员视频首次下载 → 提示 `login_required`
> - B 站/抖音下载时返回 HTTP 403 / "账号未登录" → **自动升级**为 `login_required`
> - 用户此前导出的 `bilibili_cookies.json` / `douyin_cookies.json` 已过期

**Agent 收到 `login_required` 时的标准应对流程**：

1. **不要自己改 cookies、不要重试**。从 JSON 错误中读出 `platform`、`install_guide`。
2. 把 `install_guide` 里的三条命令原文转述给用户：
   ```powershell
   # 第一步：安装 Playwright（仅首次，约 10 秒）
   pip install -r requirements-login.txt

   # 第二步：下载 Chromium（仅首次，约 150 MB）
   python -m playwright install chromium

   # 第三步：扫码登录（浏览器会自动弹出）
   python scripts/login_bilibili.py     # B 站
   python scripts/login_douyin.py       # 抖音
   ```
3. 告知用户扫码登录流程：
   - **B 站**：用 B 站 App 扫描浏览器里的二维码
   - **抖音**：抖音登录浮层默认是手机号登录，需**手动点击左侧「扫码登录」标签**再用抖音 App 扫码
4. 登录成功后脚本会自动把 cookies 写到 `<SKILL_DIR>/<平台>_cookies.json`，然后 **用原命令重跑** `python main.py <URL> --json` 即可。
5. cookies 有效期：B 站约 30 天、抖音约 15~30 天。到期再跑一次登录脚本即可。

> **🪶 零依赖兜底**：如果用户不愿安装 Playwright（额外 150 MB Chromium），可引导用户继续走旧路径——**J2TEAM Cookies 扩展手动导出 JSON 覆盖对应 cookies 文件**。两条路径完全等价。
>
> **🤝 与 YouTube 的区别**：YouTube 只提供手动导出通路（J2TEAM Cookies 插件）。B 站和抖音除手动外还额外提供 Playwright 扫码登录，因为它们没有 Chrome/Edge 127+ 那种 App-Bound Encryption 阻碍，Playwright 自带浏览器能完整拿到登录 cookies。

## 🎧 按需组件安装（抖音增强下载器 · f2）

> **什么时候会触发？**
> 抖音视频的 yt-dlp 主线 extractor 目前会被抖音最新一轮风控阻断，典型错误是
> `Fresh cookies (not necessarily logged in) are needed`。
> 本项目已经集成了 [f2](https://github.com/Johnserf-Seed/f2) 作为**抖音专属的增强下载通路**，
> 能补上 yt-dlp 目前缺的动态签名（a_bogus / X-Bogus）。
> 当 f2 已安装时，抖音视频会**默认优先走 f2**；失败或未装时透明回退到 yt-dlp。

**判断是否需要安装**：
- 用户提供的抖音链接无法下载、`install_guide` 带有 `install_f2` 字段时
- 或用户主动问"抖音为什么总是失败"时

**标准应对流程**（引导用户执行一条命令即可）：
```powershell
pip install -r requirements-douyin.txt
```
安装完成后重跑 `python main.py <URL> --json`，抖音通路会自动切换到 f2。

**注意事项**：
- f2 仍然需要 `douyin_cookies.json`——但这一步之前扫码登录已经完成过，无需重做
- f2 体积约 30~50 MB（含依赖 httpx / pydantic / cryptography 等），首次安装较慢但只需一次
- f2 会把 httpx / click / rich / protobuf 等锁到较老版本，**可能影响同一 Python 环境内其他依赖较新版本的工具**；如有冲突，建议把本 skill 放到独立的 venv
- 只对抖音 provider 生效，**不会影响 YouTube / B 站任何行为**
- 下载得到的是 MP4 视频（带音轨），Whisper 能直接消费，无需额外 ffmpeg 抽音步骤

**f2 失败时的备用方案**（罕见）：
- 如果 f2 也返回签名校验失败之类的错误，把 `douyin_cookies.json` 重新扫码一次再跑；
- 仍然不行就只能等 f2 上游跟进抖音最新风控，用户可以 `pip install -U f2` 升级
- 实在不行，手动用 J2TEAM Cookies 扩展导出 `douyin_cookies.json` 覆盖——这是最后的兜底

## 🎯 触发条件

当用户提出以下任一需求时，立即触发本 skill：
- "总结这个视频: <URL>"（任意平台）
- "帮我看看这个视频讲了什么: <URL>"
- "把这个视频整理成文档/笔记: <URL>"
- "翻译/转录这个视频: <URL>"
- 提供了以下任一域名的 URL：
  - YouTube：`youtube.com/watch`、`youtu.be/`
  - B 站：`bilibili.com/video/`、`b23.tv/`
  - 抖音：`douyin.com/video/`、`v.douyin.com/`

## 📋 执行步骤（严格按序）

### Step 1: 检查环境并告知用户

**⚠️ 告知话术必须准确反映「字幕优先」的双通路设计，并提示当前平台的字幕预期！**

推荐的告知模板（根据 URL 平台灵活调整）：

- **YouTube 链接**：
  > "我来帮你处理这个 YouTube 视频。流程是：
  > 1. **首选**：直接抓 YouTube 字幕（作者上传 或 自动生成），**通常只需几秒到十几秒** ⚡
  > 2. **回退**：没字幕时才下载音频 + 本地 Whisper GPU 转写（约 1–5 分钟）
  > 3. 最后由我基于文字稿生成结构化总结
  >
  > 全程本地运行，**不会上传任何数据到云端**。"

- **B 站 / 抖音链接**：
  > "我来帮你处理这个 [B 站/抖音] 视频。需要提醒的是，国内平台**大多数视频没有 CC 字幕**，我很可能要下载音频后用本地 Whisper (GPU) 转写，约 1–5 分钟。之后会基于文字稿生成结构化总结。
  >
  > 全程本地运行，**不会上传任何数据到云端**。"

关键点：
- **不要**说"整个过程分为：下载音频 → Whisper 转写 → 智能总结"——这会让 YouTube 用户误以为每次都走慢路径
- **不要**对时长一刀切说"1-5 分钟"——YouTube 字幕通路其实只需几秒
- 如果用户没特别指定，不用提前让他选通路；脚本的 `--mode auto` 会自动决策

### Step 2: 调用转写脚本

**使用 terminal 工具执行**（工作目录必须是 `<SKILL_DIR>`）：

```powershell
python main.py "<视频_URL>" --output-dir ./output --download-dir ./downloads --json
```

参数说明：
- `--platform`：可选，强制指定平台（`youtube` / `bilibili` / `douyin`）。不传时按 URL 自动识别
- `--mode`：可选，默认 `auto`（字幕优先，失败回退 Whisper）
  - `subtitle-only`：只用字幕，没字幕就失败（超快，完全不走 GPU；适合 YouTube）
  - `whisper-only`：跳过字幕直接 Whisper（字幕不准时使用）
- `--sub-lang`：可选，如 `zh-Hans` / `zh-Hant` / `en` / `zh-CN`，不指定时按平台默认偏好自动挑
- `--no-auto-sub`：可选，禁用自动字幕（主要影响 YouTube）
- `--model`：只在 Whisper 回退时生效，默认 `large-v3`；用户说"快点"时改 `medium`
- `--language`：可选，强制 Whisper 语言，如 `zh` / `en`
- `--cookies-file`：可选，手动指定 cookies 文件（优先级最高，会跳过按平台自动加载）
- `--browser`：可选，从哪个浏览器读 cookies，默认 `chrome`，传 `none` 禁用
- `--json`：**必须加上**，方便解析输出路径
- `--list-platforms`：列出所有支持的平台并退出

> **🔓 cookies 处理（首选 JSON 文件方案）**：
> 脚本会按「识别出的平台」自动选用对应 cookies 文件：
> - YouTube → `youtube_cookies.json`
> - B 站 → `bilibili_cookies.json`
> - 抖音 → `douyin_cookies.json`
>
> 每个平台都遵循相同的兜底顺序：
> 1. 项目根目录的 `<平台>_cookies.json`（J2TEAM Cookies 插件导出的 JSON 格式，脚本会自动转成 netscape 格式）——**当前唯一稳定方案**
> 2. 浏览器 cookies 自动读取（仅 YouTube 通路且装了 yt-dlp-ChromeCookieUnlock 插件才能用；⚠️ Chrome 127+ / Edge 的 App-Bound Encryption 目前无法被第三方工具绕过）
> 3. 以上都失败时，输出清晰的解决指引
>
> **✅ 正确做法**（务必按此操作）：
> 1. 浏览器安装 **J2TEAM Cookies** 插件（Chrome / Edge 均可）
> 2. 分别登录要用的平台（YouTube / 哔哩哔哩 / 抖音）
> 3. 点击插件图标 → **Export** → 得到 JSON
> 4. 把内容覆盖到 `<SKILL_DIR>\<平台>_cookies.json`（文件名严格对应，不要写错）
>
> **� B 站 / 抖音推荐走扫码登录脚本**（更省心，见下文「🔐 按需组件安装」小节）：
>   ```powershell
>   python scripts/login_bilibili.py    # B 站扫码登录
>   python scripts/login_douyin.py      # 抖音扫码登录
>   ```
>   首次需按需安装 Playwright，之后每 30 天重跑一次即可。YouTube 仍走 J2TEAM Cookies 插件方案。
>
> **�💡 哪些平台必须放 cookies？**
> - **YouTube**：强烈建议，否则很多视频会触发 bot 检测
> - **B 站**：匿名能看的视频可以不放；需要清晰度、需要"登录才能看"的视频则必须放
> - **抖音**：强烈建议放上，匿名访问经常 403

### Step 3: 解析脚本输出

脚本成功后，会在末尾输出类似：
```
===JSON_RESULT_BEGIN===
{"success": [{"md_path": "<SKILL_DIR>\\output\\xxx.transcript.md", "platform": "bilibili", ...}], "errors": []}
===JSON_RESULT_END===
```

从这里提取 `md_path`。结果对象还包含：
- `platform`：`youtube` / `bilibili` / `douyin`（告诉你是哪个平台的视频）
- `source_type`：`subtitle` / `whisper`（告诉你文字稿来源）
- `source_info.subtitle_source`：`manual`（作者上传） / `auto`（自动字幕）
- `language`、`duration`、`char_count` 等

> **⚠️ 特殊错误：`error_type == "whisper_required"`**
>
> 当 `errors` 里有元素且 `error_type == "whisper_required"` 时，说明视频无字幕且用户未安装 Whisper 通路。
> 此时 `errors[i]` 里会包含 `install_guide` 字段，**请按前文「🧰 按需组件安装」小节的流程引导用户**，
> 不要直接报错，也不要报 `Traceback`。B 站/抖音上这类错误会高频出现，请耐心指引。

### Step 4: 读取转写文档

使用 `read_file` 工具读取 Step 3 得到的 `md_path`。读完后**不要立刻套模板**，先做 Step 4.5 的两项决策。

### Step 4.5: 决策「视频类型」与「深度档位」（核心新增）

> 固定模板生成的总结对不同题材水土不服（例如给"影视解说"套"金句+延伸思考"就很怪）。
> 所以先按两个维度做决策，再挑模板：**① 视频类型（决定字段）② 深度档位（决定篇幅）**。

#### ① 识别视频类型（8 选 1，不确定选 `generic`）

根据转写内容的语言特征、时长、说话人数做判断。信号表：

| 类型 | 代号 | 典型信号 |
|---|---|---|
| 📖 论点型（科普/观点/解密） | `argument` | "为什么/其实/真相/原理/本质"；单人长独白；有论点链和证据 |
| 🛠 步骤型（教程/How-to） | `howto` | "第一步/然后/安装/配置/运行"；出现命令行、代码、按钮名 |
| 🎬 叙事型（影视/故事/新闻复盘） | `narrative` | 大量人名/角色、"他/她/然后/接着"；有情节推进和反转 |
| 🎙 对谈型（访谈/播客/圆桌） | `dialogue` | 明显多说话人；问答结构；"你觉得……我认为……" |
| 📰 事件型（时事/爆料/社会新闻） | `event` | 时间地点人物 + 数据 + "发生了/爆出/宣布" |
| ⚖️ 评测型（产品对比/测评） | `review` | "对比/优缺点/XX vs XX/值不值"；出现参数、得分 |
| 📷 随笔型（Vlog/生活/吐槽） | `vlog` | 日常口语、无强结构、第一人称流水账 |
| ⚡ 极简型（短视频） | `micro` | **时长 ≤ 60 秒**，任何内容都优先归到这里 |
| 🧩 通用兜底 | `generic` | 识别不出来、或横跨多个类型时使用 |

> 💡 判断建议：先看时长 ≤60s → 直接 `micro`；否则扫一遍转写前 10% 抓信号词；仍不确定 → `generic`。

#### ② 决策深度档位（3 选 1）

| 档位 | 何时触发 | 预计字数 |
|---|---|---|
| `brief` | 用户说"简短/一句话/快速扫一遍/要点就行"；或视频 ≤ 60 秒 | 200–400 |
| `standard`（默认） | 未明说 + 视频 1–30 分钟 | 800–1800 |
| `deep` | 用户说"详细/深度/学习笔记/精读/写成文档"；或视频 > 30 分钟默认尝试此档 | 2500–5000 |

> **告知用户**：在 Step 7 展示时顺带说明本次用的"类型 + 档位"，并提示可一键换档（如"改成速览/改成深度"）。

### Step 5: 按「类型 + 档位」生成总结

公共规则（所有模板通用）：
- 外语视频**翻译成中文**输出（用户指定其他语言除外）
- 时间戳仅在 Whisper 模式下精确；字幕模式下用"约 X 分钟"或省略
- **基于真实转写内容**，不得编造；视频未提及的信息明确说明
- 所有模板都以 `# 📺 [视频标题]` 开头、以 `## 📋 视频信息` 结尾（平台/作者/时长/链接）

#### 🪶 档位裁剪规则（通用叠加）

不管选哪类模板，按深度档位做如下裁剪：

- **brief**：只保留"一句话 + 最核心 1–2 个字段（如核心要点/剧情一句话）+ 视频信息"
- **standard**：输出对应类型的完整模板
- **deep**：在 standard 基础上加：**逐段精读**（按转写分块展开） + **术语/人物表** + **延伸阅读**

---

#### 📖 模板 A · `argument` 论点型

字段：一句话总结 → **核心论点** → **论证链（前提→推导→结论）** → 支撑证据/数据 → **反方视角/局限** → 延伸思考 → 视频信息

（现有老高《最神奇的星球》产出即此类的 standard 参考。）

#### 🛠 模板 B · `howto` 步骤型

字段：一句话目标 → **前置条件**（环境/权限/依赖） → **分步清单（编号、可勾选）** → **完整命令/代码块**（可直接复制） → 常见坑 & 报错对照 → 视频信息

要求：命令/代码必须用 ``` fenced block，便于复制；步骤编号连续。

#### 🎬 模板 C · `narrative` 叙事型（影视解说/故事）

字段：**一句话剧情** → **登场人物表**（表格：角色/身份/关键点） → **剧情时间线**（分幕，带时间戳） → **关键反转点** → **结局** → **爽点/解读**（可选） → 视频信息

> ⚠️ **不要**生成"金句摘录"和"延伸思考"——这类模板下是噪音。

#### 🎙 模板 D · `dialogue` 对谈型

字段：一句话主题 → **嘉宾背景** → **金句卡片（按主题聚合，不是按时间）** → **观点碰撞 / 分歧点** → 未展开但值得追的坑 → 视频信息

#### 📰 模板 E · `event` 事件型

字段：一句话摘要 → **5W1H**（谁/何时/何地/何事/为何/如何） → **时间线** → **各方反应** → **影响评估** → **存疑 / 待核实点** → 视频信息

#### ⚖️ 模板 F · `review` 评测型

字段：一句话结论 → **参测对象表** → **维度对比矩阵**（表格：维度 × 产品） → 各自适用场景 → **最终推荐** → 视频信息

#### 📷 模板 G · `vlog` 随笔型

字段：一句话主题 → **今天干了啥（清单）** → **有意思的瞬间**（带时间戳） → **UP 主的情绪/态度** → 视频信息

#### ⚡ 模板 H · `micro` 极简型（≤ 60 秒）

固定输出（无论啥档位都用此版，brief 默认即此）：

```markdown
# 📺 [标题]

> **一句话**：xxx（≤ 30 字）

## 🎯 关键信息
- 要点 1
- 要点 2
- 要点 3

## 📋 视频信息
- **平台** / **作者** / **时长** / [原视频](URL)
```

#### 🧩 模板 I · `generic` 通用兜底

字段：一句话总结 → 核心要点（TL;DR） → 内容大纲（带时间戳） → 金句摘录 → 延伸思考 → 视频信息

（即旧版固定模板，仅在识别不出类型时使用。）

### Step 6: 保存总结文档

统一写入 `<SKILL_DIR>/output/<视频标题>_<video_id>.summary.md`（无论哪类模板、哪个档位都用同一文件名，覆盖式写入），使用 `edit_file` 工具。命名与 `transcript.md` 前缀保持一致，便于一一对应。

### Step 7: 向用户展示结果

告诉用户：
- ✅ 转写稿位置：`...transcript.md`
- ✅ 总结文档位置：`...summary.md`
- 🎯 **本次采用**：类型 `<argument/narrative/...>` · 档位 `<brief/standard/deep>`
- 💡 **换档提示**：
  - "觉得太长 → 回复『改成速览』"
  - "想要更详尽 → 回复『改成深度笔记』"
  - "类型不对 → 回复『按叙事/教程/评测重写』"

收到换档/换类型指令时：重跑 Step 5 + Step 6 覆盖原 `.summary.md` 即可，无需重新转写。

## 🐛 错误处理

| 错误 | 应对 |
|------|------|
| `ffmpeg not found` | 提示用户安装 FFmpeg：`winget install Gyan.FFmpeg` |
| `error_type == "whisper_required"` | ✅ **不是程序崩溃！** 是无字幕需回退 Whisper、但 Whisper 未安装。按前文「🧰 按需组件安装」流程，把 `install_guide` 里的两条命令给用户 |
| `error_type == "login_required"` | ✅ **不是程序崩溃！** 是 B 站/抖音 cookies 缺失或过期。按前文「🔐 按需组件安装（扫码登录）」流程，把 `install_guide` 里的三条命令给用户。也可选择手动导出 JSON（J2TEAM Cookies 扩展） |
| `ImportError: faster_whisper` | 同上：在 `<SKILL_DIR>` 下执行 `pip install -r requirements-whisper.txt` 并跑 `python scripts/download_model.py --size medium` |
| `无法识别该 URL 所属平台` | 链接不在支持列表里。确认是 YouTube / B 站 / 抖音 链接；或改用 `--platform <name>` 强制指定 |
| YouTube `HTTP 403 / 429` / `Sign in to confirm` / `bot` 检测 | 1) 用 **J2TEAM Cookies** 插件重新导出 JSON 覆盖 `youtube_cookies.json` 2) 确认已登录 YouTube |
| B 站 登录/会员视频失败 | **推荐**：`python scripts/login_bilibili.py` 扫码登录刷新 cookies；**或**：用 J2TEAM Cookies 插件导出覆盖 `bilibili_cookies.json` |
| 抖音 `HTTP 403` / 下载失败 / `Fresh cookies ... are needed` | **首选**：`pip install -r requirements-douyin.txt` 安装 f2 增强下载器；**次选**：`python scripts/login_douyin.py` 扫码刷新 cookies；**兜底**：用 J2TEAM Cookies 插件导出覆盖 `douyin_cookies.json` |
| `Failed to decrypt with DPAPI` / `App-Bound encryption` | 浏览器 cookies 解密失败，改走 `<平台>_cookies.json` 路径（推荐） |
| `nsig extraction failed` / `Could not find JS runtime` | 检查 `bin/deno.exe` 是否存在且未被杀毒软件隔离；此错误只会出现在 YouTube |
| `Unable to download webpage: <urlopen error>` | 网络问题，重试或检查代理；国内网访问 YouTube 可能需要代理 |
| "没有可用字幕"（mode=subtitle-only） | B 站 / 抖音几乎必定触发。建议改为 `--mode auto`（默认）让它回退 Whisper |
| 脚本超过 10 分钟无响应 | 询问用户是否改用 `--model medium` 加速 |

## 📝 使用示例

### 示例 1：YouTube 视频
**用户**："帮我总结这个视频 https://www.youtube.com/watch?v=dQw4w9WgXcQ"

**Agent 应该做的**：
1. 回复：按 Step 1 的 YouTube 话术告知
2. 执行 `python main.py "https://..." --json`（工作目录 `<SKILL_DIR>`）
3. 等待 `===JSON_RESULT_BEGIN===` 块，提取 `md_path` 和 `platform`
4. 读取生成的 `*.transcript.md`
5. 按模板生成 `*.summary.md`
6. 展示结果并询问是否美化

### 示例 2：B 站视频
**用户**："https://www.bilibili.com/video/BV1xx411c7mD 帮我总结下"

**Agent**：
1. 回复：按 Step 1 的 B 站 / 抖音话术告知，提醒"大概率要走 Whisper，1–5 分钟"
2. 执行同样的命令（脚本自动识别 B 站）
3. 如果拿到 `whisper_required` 错误 → 引导安装；否则按正常流程走

### 示例 3：抖音短视频
**用户**："这个抖音讲了啥 https://v.douyin.com/abcd1234/"

**Agent**：
1. 回复：提醒需要 Whisper
2. 如果 cookies 未配置并返回错误 → 引导用户用 J2TEAM Cookies 插件导出 `douyin_cookies.json`

## 📦 产物示例（`output/` 目录已有成品）

- `FULL Claude Tutorial For Beginners in 2026 FULL COURSE_Xg55nTrbYYY.transcript.md`（YouTube 英文字幕转写稿）
- `Claude完整教程2026中文总结_Xg55nTrbYYY.summary.md`（对应中文总结）

新产物保持同样的命名风格即可。

## ⚠️ 注意事项

- **不要自己手写 yt-dlp 或 whisper 的调用代码**，一律用 `python main.py` 调用
- **工作目录必须是 `<SKILL_DIR>`**（即 `SKILL.md` 所在目录），否则 import、相对路径、`bin/deno.exe` 定位、`providers/` 模块加载都会失败
- Whisper 首次运行会下载模型（~1.4 GB），提前告知用户；默认打包**不包含 `models/` 目录**，由 `scripts/download_model.py` 按需拉取到本地
- **字幕模式识别技巧**：
  - YouTube 课程/访谈/讲话类，优先试 `--mode auto`（大概率秒出字幕）
  - B 站 / 抖音基本都没字幕，`--mode auto` 会自动回退 Whisper
  - 如果用户说字幕不准，用 `--mode whisper-only`
- **多语言视频**：英文视频可直接抓原生英文字幕，总结时再翻译成中文（更准确）
- 总结要**基于实际转写内容**，不要编造，没讲到的内容要说"视频未涉及"
- **字幕模式时**：转写稿不会有逐句时间戳，总结时如需标注章节时间就不要声称精确到秒，改为"大概在 X 分钟附近"或省略时间戳
- **cookies 过期**：各平台 cookies 大约 30 天会过期，出现大规模下载失败时建议用 **J2TEAM Cookies** 插件重新导出 JSON 覆盖对应文件
