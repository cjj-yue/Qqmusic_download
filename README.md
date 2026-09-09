# QQ Music Downloader

一个面向 Windows 的非官方 QQ 音乐桌面工具，提供歌曲检索、版本与音质选择、歌单导入、多选队列、音频转换和完整性校验。本项目与腾讯或 QQ 音乐没有隶属关系。

## 功能

- 搜索歌曲、查看歌手 / 专辑 / 时长，选择具体发行版本。
- 读取公开歌单，支持勾选、全选、跨搜索添加队列、去重与停止后续任务。
- 优先 FLAC、没有则 MP3，或选择指定音质策略；支持输出源格式、MP3、WAV。
- 可选使用桌面 QQ 音乐当前账号，保留 QMC2 / EncV2 解码功能。
- 下载后进行整首解码及保存校验；试听片段不会当作完整歌曲发布。
- 同一保存目录只追加一个 `下载.log`，批量显示成功、失败、跳过数量。

## 使用条件

可用歌曲和音质取决于官方返回的资源及使用者账号权限。源代码许可不授予音乐版权、平台接口使用许可或技术措施规避许可。发布或使用涉及会话读取、受保护音频的功能前，应确认适用条件；详见 [发布核对说明](docs/RELEASE_REVIEW.md)。

默认只查询公开资源。勾选窗口底部“使用本机 QQ 音乐当前账号”才会读取 `QQMusic.exe` 的登录会话；只接受与客户端当前账号配置一致的候选。无法确认当前账号时继续公开资源查询，不使用历史账号。会话与密钥保留在进程内存中，不写入日志；账号请求只发送给固定 QQ 音乐 HTTPS 接口，拒绝自动重定向。

## 运行环境

- Windows 10 / 11，64 位。
- Python 3.12（包含 Tk；推荐使用 python.org 安装器）。
- 单独安装 [FFmpeg](https://ffmpeg.org/download.html)；音频校验、转换需要它。
- 单独安装 [Node.js](https://nodejs.org/en/download)；加密音频处理需要它。

本仓库和默认构建产物不包含 FFmpeg / Node 二进制文件。可以把 `ffmpeg.exe`、`node.exe` 放入程序旁的 `bin` 文件夹，加入系统 PATH，或分别设置环境变量 `QQMUSIC_FFMPEG`、`QQMUSIC_NODE` 为完整路径。不要把自己安装的二进制文件提交到仓库。

```powershell
python src/music_gui.py
```

保存位置默认是当前用户的 `Music\QQmusic`，可在窗口中更改。

## 源码目录

```text
src/        程序源码与解码模块
tests/      离线测试
scripts/    构建脚本与构建依赖清单
docs/       第三方来源及发布核对说明
licenses/   第三方许可证
README.md   使用说明
LICENSE     项目许可证
```

本目录用于手动上传源码，不包含本地 Git 仓库、旧提交历史或 Git 配置文件。运行或构建后可能产生 `__pycache__`、`build`、`dist` 等文件夹，上传源码时应排除这些生成内容。

## 单曲、多选和歌单

单曲：输入歌名 → 选择一个版本 → 检测音质 → 选择源音质 / 输出格式 → 开始下载。

多首：点击第一列方框勾选，或使用 Ctrl / Shift 多选、Ctrl+A 全选 → 加入下载队列 → 在批量设置中选择策略 → 下载队列。更换关键词不会清空队列。

歌单：切换“导入歌单”，输入完整网页链接或数字 ID → 读取歌单 → 按需取消部分选择 → 加入下载队列。首次导入且队列为空时，会在保存位置下建立 `歌单名_歌单ID` 子目录。无 ID 的短链接暂不支持；接口返回不完整时会提示数量差异。

单曲处理时使用活动动画，全部校验成功后显示 100%。批量百分比表示已处理歌曲占比，100% 不代表所有歌曲成功，请查看成功 / 失败 / 跳过数量。停止时保留实际进度；当前窗口中重试队列会忽略已完成项。队列不会跨程序重启恢复。

## 命令行

```powershell
# 仅查询公开资源
python src/music_download.py --query "歌名" --artist "歌手"

# 明确启用当前桌面账号会话
python src/music_download.py --query "歌名" --use-account

# 仅检索元数据
python src/music_download.py --query "歌名" --search-only
```

高级批量脚本 `src/batch_export.py` 和 `src/audit_export.py` 保留，供显式指定本地歌单 JSON 和客户端 PID 的操作使用；它们会产生可恢复清单，和界面版的统一日志模式不同。仓库不附带任何真实用户歌单。

## 测试和构建

```powershell
python -m unittest discover -s tests -t . -v
python -m pip install -r scripts/requirements-build.txt
python scripts/build_exe.py
```

测试使用离线模拟请求和生成的测试音频，不访问真实账号。测试音频功能需先安装 FFmpeg。

构建输出为 `dist\QQMusicDownloader.exe`，包含 Python / Tk 与本项目源文件及许可材料；FFmpeg 和 Node 继续由使用者单独安装。检查成品时执行 `QQMusicDownloader.exe --smoke-test packaged-check.json`。公开 EXE 前还需核对该次构建的 Python / Tk 许可材料，并在 Release 中提供对应项目源码。

## 开源许可与贡献

本项目整体以 **GPL-3.0-or-later** 分发，原有第三方许可和归属继续保留。完整许可见 [LICENSE](LICENSE)，来源、版本、修改范围见 [THIRD_PARTY_NOTICES.md](docs/THIRD_PARTY_NOTICES.md)。

提交问题时请使用模拟歌曲信息并清理日志中的个人路径，不要粘贴 Cookie、会话、音频密钥、签名地址或音乐文件。补丁请同时提供相关离线测试。
