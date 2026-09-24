# 🎬 视频总结 Skill 使用文档

> 一个多平台视频智能总结工具（支持 **YouTube / 哔哩哔哩 / 抖音**），优先抓字幕、没字幕自动用本地 Whisper GPU 转写，最后由 AI 生成结构化 Markdown 笔记。**全程本地运行，不上传任何数据。**

---

## 📌 一、这个 Skill 能帮你做什么？

只需丢一个视频链接，它就能自动：

1. **识别平台** → YouTube / B 站 / 抖音
2. **拿到文字稿**
   - 首选：直接抓视频自带字幕（⚡ 秒级完成）
   - 回退：下载音频 → 本地 Whisper GPU 转写（约 1–5 分钟）
3. **智能总结** → 根据视频类型（科普 / 教程 / 影视解说 / 评测 / Vlog…）选择合适模板，生成 Markdown 笔记
4. **中文输出** → 外语视频自动翻译成中文

最终产出两个文件：
- `output/<标题>.transcript.md` —— 完整转写稿
- `output/<标题>.summary.md` —— 结构化总结笔记

---

## 🎯 二、什么时候会自动触发？

在 AI 助手对话中说出以下任一句式，都会自动启用本 Skill：

- 「总结这个视频：<链接>」
- 「帮我看看这个视频讲了啥：<链接>」
- 「把这个视频整理成笔记 / 文档：<链接>」
- 「翻译 / 转录这个视频：<链接>」
- 直接贴一个 YouTube / B 站 / 抖音 的链接

支持的链接格式：

| 平台 | URL 示例 |
|---|---|
| YouTube | `https://www.youtube.com/watch?v=xxx` / `https://youtu.be/xxx` |
| 哔哩哔哩 | `https://www.bilibili.com/video/BVxxx` / `https://b23.tv/xxx` |
| 抖音 | `https://www.douyin.com/video/xxx` / `https://v.douyin.com/xxx` |

---

## 🚀 三、最简使用流程

### 场景 A：YouTube 视频（最快）

```
用户：帮我总结这个视频 https://www.youtube.com/watch?v=xxx
```

AI 会：
1. 抓取 YouTube 原生字幕（几秒搞定）
2. 基于字幕生成结构化总结
3. 把转写稿和总结笔记路径告诉你

> YouTube 多数视频都有字幕（作者上传或自动生成），基本走**秒级快路径**。

### 场景 B：B 站 / 抖音视频（大概率走 Whisper）

```
用户：https://www.bilibili.com/video/BVxxx 帮我总结下
```

AI 会：
1. 尝试抓字幕 → 一般没有
2. 下载音频 → 本地 Whisper GPU 转写（1–5 分钟）
3. 生成总结笔记

> B 站 / 抖音绝大多数视频没 CC 字幕，**请提前准备好 Whisper 环境**（见第 4 节）。

---

## 🧰 四、首次使用前的准备

### 4.1 Python 环境（必须）

确保已装 Python 3.10+ 和项目基础依赖：

```powershell
cd <SKILL 所在目录>
pip install -r requirements.txt
```

### 4.2 FFmpeg（必须，用于音频抽取）

```powershell
winget install Gyan.FFmpeg
```

### 4.3 Whisper 环境（B 站 / 抖音 强烈建议）

**第一次 AI 告诉你需要 Whisper 时，执行两条命令：**

```powershell
# ① 安装 faster-whisper（几十 MB）
pip install -r requirements-whisper.txt

# ② 下载 Whisper 模型（medium 约 1.4 GB，中文推荐）
python scripts/download_model.py --size medium
```

模型大小可选：

| 模型 | 体积 | 适用 |
|---|---|---|
| `small` | ~460 MB | 英文视频够用 |
| `medium` | ~1.4 GB | **中文推荐（默认）** |
| `large-v3` | ~3.0 GB | 高端 GPU，最准 |

**国内访问 HuggingFace 慢？换镜像：**

```powershell
python scripts/download_model.py --size medium --mirror https://hf-mirror.com
```

### 4.4 Cookies 配置

不同平台建议程度不同：

| 平台 | 建议 | 方式 |
|---|---|---|
| YouTube | **强烈建议** | J2TEAM Cookies 插件导出 → 覆盖 `youtube_cookies.json` |
| B 站 | 匿名能看的视频可不放；会员视频必须 | **推荐扫码登录**：`python scripts/login_bilibili.py` |
| 抖音 | **强烈建议**（否则常 403） | **推荐扫码登录**：`python scripts/login_douyin.py` |

**扫码登录首次需装 Playwright：**

```powershell
pip install -r requirements-login.txt
python -m playwright install chromium
```

然后扫码登录：

```powershell
python scripts/login_bilibili.py   # B 站扫码（B 站 App 扫）
python scripts/login_douyin.py     # 抖音扫码（记得手动点击"扫码登录"标签）
```

**Cookies 有效期：** B 站约 30 天，抖音约 15–30 天，到期重跑登录脚本即可。

### 4.5 抖音增强（可选，抖音频繁失败时再装）

抖音最新风控经常会让 yt-dlp 失败，装 f2 可解决：

```powershell
pip install -r requirements-douyin.txt
```

---

## 🎨 五、总结输出的两个维度

AI 生成总结时会自动做两个决策，你也可以指定：

### 5.1 视频类型（决定字段结构）

| 类型 | 适用 | 输出字段 |
|---|---|---|
| 📖 论点型 | 科普 / 观点 / 解密 | 核心论点 → 论证链 → 证据 → 反方视角 |
| 🛠 步骤型 | 教程 / How-to | 前置条件 → 分步清单 → 完整代码 → 常见坑 |
| 🎬 叙事型 | 影视解说 / 故事 | 人物表 → 时间线 → 反转点 → 结局 |
| 🎙 对谈型 | 访谈 / 播客 | 嘉宾背景 → 金句卡片 → 观点碰撞 |
| 📰 事件型 | 时事 / 爆料 | 5W1H → 时间线 → 各方反应 |
| ⚖️ 评测型 | 产品对比 | 对比矩阵 → 适用场景 → 推荐 |
| 📷 随笔型 | Vlog / 吐槽 | 今日干货 → 有趣瞬间 → 情绪态度 |
| ⚡ 极简型 | 短视频（≤60 秒） | 一句话 + 3 个要点 |

### 5.2 深度档位（决定篇幅）

| 档位 | 字数 | 触发条件 |
|---|---|---|
| `brief` | 200–400 | 说「简短 / 一句话 / 要点就行」或视频 ≤ 60 秒 |
| `standard` | 800–1800 | **默认**，1–30 分钟视频 |
| `deep` | 2500–5000 | 说「详细 / 深度 / 学习笔记」或视频 > 30 分钟 |

### 5.3 一键换档

生成后觉得不合适，直接说：

- 「改成速览」→ 切到 `brief`
- 「改成深度笔记」→ 切到 `deep`
- 「按叙事 / 教程 / 评测重写」→ 换模板

**不会重新转写，只重新生成总结，秒出。**

---

## 🐛 六、常见问题速查

| 问题 | 解决 |
|---|---|
| `ffmpeg not found` | `winget install Gyan.FFmpeg` |
| `error_type == "whisper_required"` | 按 4.3 装 Whisper 和模型 |
| `error_type == "login_required"` | 按 4.4 扫码登录对应平台 |
| YouTube `HTTP 403 / Sign in to confirm` | 用 J2TEAM Cookies 插件重新导出 `youtube_cookies.json` |
| B 站会员视频失败 | 扫码登录：`python scripts/login_bilibili.py` |
| 抖音 `Fresh cookies ... are needed` | 首选装 f2：`pip install -r requirements-douyin.txt` |
| 字幕不准 | 加参数 `--mode whisper-only` 强制重新转写 |
| 运行卡住超过 10 分钟 | 换小模型：`--model medium` |
| 国内访问 HuggingFace 慢 | 加参数 `--mirror https://hf-mirror.com` |

---

## 💡 七、进阶用法

### 7.1 手动调用命令（绕过 AI）

```powershell
python main.py "<视频URL>" --output-dir ./output --download-dir ./downloads --json
```

常用参数：

| 参数 | 作用 |
|---|---|
| `--platform youtube/bilibili/douyin` | 强制指定平台 |
| `--mode auto` | 字幕优先（默认） |
| `--mode subtitle-only` | 只用字幕，没就失败（超快） |
| `--mode whisper-only` | 跳过字幕直接 Whisper |
| `--sub-lang zh-Hans/en/...` | 指定字幕语言 |
| `--model medium/large-v3` | Whisper 模型大小 |
| `--language zh/en` | 强制转写语言 |
| `--json` | 结构化输出（脚本使用时必加） |
| `--list-platforms` | 列出所有支持的平台 |

### 7.2 只要速览不要慢路径

```powershell
python main.py "<URL>" --mode subtitle-only --json
```

没字幕时直接失败，绝不走 Whisper，适合批量扫 YouTube 视频。

### 7.3 产物目录

- 转写稿：`output/<标题>_<id>.transcript.md`
- 总结稿：`output/<标题>_<id>.summary.md`
- 下载的音频：`downloads/`（可定期清理）

---

## ⚠️ 八、使用须知

- ✅ **全程本地运行**，视频内容不会上传云端
- ✅ **不依赖任何 LLM API**，总结由对话中的 AI 直接生成
- ⚠️ 首次跑 Whisper 需下载 ~1.4 GB 模型，请预留空间和时间
- ⚠️ Whisper 转写依赖 **NVIDIA GPU**（CUDA），纯 CPU 会非常慢
- ⚠️ 总结严格基于转写内容，视频没讲到的会标注「视频未涉及」，不会编造
- ⚠️ B 站 / 抖音 Cookies 约 30 天过期，出现批量失败时重跑登录脚本
- ⚠️ **切勿删除** `bin/deno.exe`——YouTube 解签名必需

---

## 📞 九、遇到问题怎么办？

1. **先看 AI 的错误提示**：大多数错误都会带 `install_guide` 字段，直接照着做
2. **检查 Cookies 是否过期**：重跑对应平台的登录脚本
3. **更新工具链**：`pip install -U yt-dlp f2 faster-whisper`
4. **万能兜底**：用 J2TEAM Cookies 浏览器插件手动导出 JSON 覆盖对应 `<平台>_cookies.json`

---

**Happy Summarizing! 🎉**
