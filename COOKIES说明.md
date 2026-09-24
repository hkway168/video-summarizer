# 🔐 Cookies 说明

B 站高清/会员视频、抖音视频常需要登录态才能下载。本项目通过 cookies 文件携带登录信息。

> ⚠️ **cookies 等同于账号密码，已被 `.gitignore` 排除，请勿提交到任何公开仓库。**

## 需要哪些文件

放在项目根目录（文件名固定）：

| 平台 | 文件名 | 是否必需 |
|---|---|---|
| 哔哩哔哩 | `bilibili_cookies.json` | 下载登录/高清视频时需要 |
| 抖音 | `douyin_cookies.json` | 基本必需 |
| YouTube | `youtube_cookies.json` | 一般不需要，遇 403/429 时补上 |

格式参考仓库里的 `cookies.example.json`（JSON 数组，兼容 J2TEAM Cookies 扩展导出格式）。
程序会用 `cookies_utils.ensure_netscape()` 自动转换成 yt-dlp 需要的 Netscape 格式。

## 获取方式

### 方式 A：扫码登录（推荐）

```powershell
pip install -r requirements-login.txt
python scripts/login_bilibili.py      # B 站
python scripts/login_douyin.py        # 抖音
```

会弹出浏览器窗口，手机扫码后自动写入对应的 `*_cookies.json`。
**默认复用你电脑上已装的 Edge / Chrome**，无需下载 Chromium。

```powershell
python scripts/login_bilibili.py --list-browsers   # 查看本机可用浏览器
python scripts/login_bilibili.py --browser edge    # 指定浏览器
```

图形界面里也能操作：exe →「环境安装」页 →「④ 平台登录」→「📱 扫码登录」。

### 方式 B：浏览器插件导出

1. 安装 **J2TEAM Cookies** 扩展
2. 登录目标平台后导出 JSON
3. 保存为对应文件名（如 `bilibili_cookies.json`）

## 有效期

| 平台 | 大约有效期 |
|---|---|
| 哔哩哔哩 | 30 天 |
| 抖音 | 15~30 天 |

失效后下载会报 `HTTP 403` / `需要登录`，重新扫码即可。
程序会自动识别这类错误并给出引导（`error_type=login_required`）。
