# Fudan Auto eLearning

复旦 eLearning（Canvas LMS）的本地采集与提醒框架。当前版本已经包含：

- 复旦统一身份认证登录与浏览器会话持久化
- 课程、作业、提交状态、公告、模块条目和课件文件采集
- SQLite 本地存储和增量更新
- 控制台提醒，以及可选的通用 Webhook 提醒
- Codex 草稿生成器和上传器的独立扩展接口（默认不启用上传）
- 隔离的 Codex 作业 Agent、草稿编辑、审查记录和十分钟批准令牌

## 安装

```powershell
python -m pip install -e .
Copy-Item .env.example .env
```

然后只在本机 `.env` 中填写：

```dotenv
ELEARNING_USERNAME=你的学号
ELEARNING_PASSWORD=你的密码
```

`.env`、浏览器登录状态和数据库都已被 `.gitignore` 排除。

## 使用

```powershell
autoelearning check-login
autoelearning sync
autoelearning status
autoelearning remind --days 14
```

## 桌面界面

运行 `autoelearning-desktop` 会启动本地界面并自动打开浏览器：

```powershell
autoelearning-desktop
```

界面地址固定为 `http://127.0.0.1:8765`。首次使用可输入学号和统一身份认证密码；登录后可以同步、搜索和查看作业、公告、课件及课程。桌面的“eLearning助手”快捷方式会执行同样的启动流程，重复点击只会打开已有界面。

## Agent 草稿与人工审批

在“Agent 工作台”中可以为未提交作业生成本地草稿。系统会读取作业说明、作业附件和少量相关课程资料，在 `.data/agent-jobs/` 的隔离目录内运行 `codex exec`。对已提交或已评分的作业也可以使用“测试生成”，但这类任务会被永久标记为不可批准、不可上传，适合验证真实题目而不触碰现有提交。请先确认 Codex CLI 已登录：

```powershell
codex login status
```

草稿生成后可以在界面中编辑、下载 Markdown、审查版 PDF 和去除内部自检说明的待提交 PDF，并记录审查意见。批准时必须勾选已审查、选择提交形式并完整输入作业标题；批准令牌仅有效十分钟。修改草稿会自动撤销旧批准。PDF 优先通过 Pandoc/XeLaTeX 排版，缺少该环境时使用本地后备渲染器。

提交总开关默认关闭：

```dotenv
ELEARNING_SUBMISSION_ENABLED=false
```

关闭时，最终提交接口会在创建浏览器会话或调用 Canvas API 前返回锁定错误，因此不会上传文件。只有在完成测试并主动将其改为 `true`、重新启动应用、再次审查并最终确认后，系统才会自动执行 Canvas 文件上传和作业提交。

若统一身份认证出现验证码或二次认证，设置 `ELEARNING_HEADLESS=false`，人工完成一次登录；之后会复用 `.data/browser-state.json` 中的会话。该文件含登录 Cookie，必须像密码一样保护，且已被 `.gitignore` 排除。

## 定时运行

可用 Windows 任务计划程序定时执行：

```powershell
autoelearning sync
autoelearning remind --days 7
```

如设置 `ELEARNING_WEBHOOK_URL`，提醒还会以 `{"text": "..."}` JSON 发送到该地址。不同聊天平台可能需要在 `reminders.py` 中适配消息格式。

## 后续自动化边界

草稿生成与提交被刻意分开，避免错误答案或错误文件未经检查直接提交。完整流程是“生成草稿 → 本地编辑/下载 → 输入标题批准 → 最终确认 → 自动上传并提交”。
