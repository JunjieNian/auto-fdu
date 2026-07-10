# Fudan Auto eLearning

复旦 eLearning（Canvas LMS）的本地采集与提醒框架。当前版本已经包含：

- 复旦统一身份认证登录与浏览器会话持久化
- 课程、作业、提交状态、公告、模块条目和课件文件采集
- SQLite 本地存储和增量更新
- 控制台提醒，以及可选的通用 Webhook 提醒
- Codex 草稿生成器和上传器的独立扩展接口（默认不启用上传）

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

若统一身份认证出现验证码或二次认证，设置 `ELEARNING_HEADLESS=false`，人工完成一次登录；之后会复用 `.data/browser-state.json` 中的会话。该文件含登录 Cookie，必须像密码一样保护，且已被 `.gitignore` 排除。

## 定时运行

可用 Windows 任务计划程序定时执行：

```powershell
autoelearning sync
autoelearning remind --days 7
```

如设置 `ELEARNING_WEBHOOK_URL`，提醒还会以 `{"text": "..."}` JSON 发送到该地址。不同聊天平台可能需要在 `reminders.py` 中适配消息格式。

## 后续自动化边界

`AssignmentDraftProvider` 用于把作业描述、附件和相关课件整理成 Codex 草稿任务；`SubmissionProvider` 用于上传已经确认的产物。两步被刻意分开，避免错误答案或错误文件未经检查直接提交。下一阶段可以先实现“生成草稿 → 本地预览 → 明确确认 → 上传”的闭环。
