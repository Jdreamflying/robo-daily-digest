# 机器人研究日报

抓取 arXiv cs.RO 和 `config.yaml` 中的机器人资讯 RSS/Atom 源，按期生成论文精读、资讯、双语三联图和 RSS。静态页面使用原生 HTML、CSS 和 JavaScript。

## 预览

在仓库根目录运行以下命令，会打开浏览器并启动本地服务。

```sh
python -m to_web.preview
```

也可以运行 `python3 -m http.server 8000 --bind 127.0.0.1`，然后访问 [资讯](http://127.0.0.1:8000/news.html) 或 [论文](http://127.0.0.1:8000/index.html)。顶部语言按钮可即时切换中英文，`?lang=zh` 或 `?lang=en` 可指定链接的初始语言。搜索会同时匹配两种语言和原始 RSS 摘要。

收藏与已读状态保存在当前浏览器。「仅看收藏」筛选当前期次，或论文首页已加载的期次。部署后，可用浏览器的安装菜单或 iOS Safari 的「添加到主屏幕」打开独立窗口。

## 生成一期

使用 Python 3.10 或以上版本。在 Linux、macOS 或 Windows Git Bash 的仓库根目录执行。

```sh
python -m venv .venv
# Linux / macOS
.venv/bin/python -m pip install -r requirements.txt
# Windows Git Bash 使用这条安装命令
# .venv/Scripts/python.exe -m pip install -r requirements.txt
bash ./run.sh --dry-run
```

`run.sh` 按 `config.yaml` 中的 `Asia/Tokyo` 确定期次。资讯取最近 48 小时，先按网址和标题去重，同批合并可识别的融资或发布报道。模型初筛还会对照近七天的标题和摘要，过滤重复报道，并把有证据的新进展交给编辑。扫描窗口和送评数记录在期次及原始元数据中。

资讯目标为 8–10 条，最多刊登 10 条。机器人稿先选并排在前面。AI 模型、应用、芯片算力及产业稿按分数补位，最多 5 条，机器人稿达到 6 条时最多补 4 条。AI 垂直频道的窗内条目经通用过滤后直接送评。Hacker News 的机器人稿须达到 15 分或 8 条评论，AI 前沿稿须达到 35 分或 15 条评论。`selection.min_news_score` 默认设为 3，达标稿不足时按实际数量发布。主卡原图符合条件时会保存为 WebP 并附来源。

论文通过 arXiv 官方 API 按 UTC 首次提交日期查询期次前三天，周末或无可用论文时最多向前回退三天。近七天已刊登的 arXiv ID 按去掉版本号后的值排除。页面显示实际查询日期。Hugging Face Daily Papers 的点赞、评论和关联 GitHub 仓库 Stars 用于辅助排序。终端环境变量 `DIGEST_GITHUB_TOKEN` 可提高 GitHub API 限额。

无 API Key 或带 `--dry-run` 时，编辑阶段按关键词打分，生成双语字段和论文四段式摘录，标题与摘要沿用原始材料。需要模型编选时，从 `.env.example` 复制一份 `.env` 并填写接口参数，再运行 `bash ./run.sh`。接口使用 `/chat/completions`，单次精读调用生成中英两版。可设置 `OPENAI_JSON_MODE=true` 启用 JSON mode。成功结果写入 `data/cache/`，重跑时复用，超过 14 天未修改的缓存会清理。提示词在 `ai/prompts/`，IMAP 预留在 `daily_news/fetch_news.py`，默认关闭。

每期产物保存在 `data/YYYY-MM-DD/`。仅精选论文会请求 arXiv HTML，提取项目链接并尝试压缩一张原图至 `figures/`。无 HTML 或合适配图时，论文卡片仍可阅读。Pillow 会自动探测中文字体。缺少中文字体时，三联图优先选用可用的英文标题。

配置模型密钥后，可在独立工作副本里用少量条目验收真实双语编选。也可在终端设置 `DIGEST_DATA_DIR` 指向临时目录下名为 `data` 的子目录，让整条流水线在独立位置生成期次、缓存与索引，RSS 写到它的上一级。

```sh
./run.sh --limit-papers 2 --limit-news 3
```

以上参数让每类最多 12 条候选进入初筛，并把小样本写入目标期次。可用 `--max-filter-candidates N` 覆盖候选数。双语校验失败会触发重试。编辑器的输出上限默认为 3072 tokens，可通过 `EDITOR_MAX_TOKENS` 调整。带 `--dry-run` 时会使用规则编选并跳过通知。

## GitHub Pages

推送到 GitHub 后，在仓库的 **Settings → Pages** 中将 Source 设为 **GitHub Actions**。兜底计划在日本时间 08:37 触发，发现当天已有 API 版论文时跳过生成和部署。**Actions** 手动触发仍会更新。工作流基于远端默认分支重建索引后提交当日期次，并发布该提交中的静态文件。启用了分支保护的仓库需允许该工作流账号写入归档。

规则模式和定时发布无需手填 Secrets 或 Variables。模型编选只需设置 Secret `OPENAI_API_KEY`。使用兼容服务时按需设置 `OPENAI_BASE_URL`、`FILTER_MODEL`、`EDITOR_MODEL`，其余模型参数也可用同名 Variables 配置。站点地址由 Pages 提供。

## 可选通知

仅在 `NOTIFY_ENABLED=true` 且配置了通道时发送。GitHub Actions 在 Pages 部署成功后发送。本地运行可在 `.env` 中另设已上线的 `SITE_URL`，再执行 `python -m to_web.notify --date YYYY-MM-DD`。默认关闭，发送失败会打印通道警告。

| 通道 | Actions Secrets | Actions Variables |
| --- | --- | --- |
| Webhook | `NOTIFY_WEBHOOK_URL` | `NOTIFY_ENABLED=true` |
| Telegram | `TELEGRAM_BOT_TOKEN` | `NOTIFY_ENABLED=true`、`TELEGRAM_CHAT_ID` |
| SMTP | `SMTP_USER`、`SMTP_PASS`、`SMTP_TO` | `NOTIFY_ENABLED=true`、`SMTP_HOST`、可选 `SMTP_PORT`（默认 587） |

Webhook 根据地址自动选择飞书、钉钉、Slack 或通用 JSON 格式。SMTP 在 465 端口使用隐式 TLS，其余端口使用 STARTTLS。
