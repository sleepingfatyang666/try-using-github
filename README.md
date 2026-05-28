# Daily Discord Digest

这个项目用于每天自动生成并发送一个中英双语 Discord 新闻简报。它不依赖你每天打开 Codex；只要 GitHub Actions 正常运行，就会按计划执行。

## 每天会生成什么

- `1. 每日世界新闻 / Daily World News`
  - 政治、经济、国际、科技、艺术与文化等综合热点。
- `2. 材料学文章、突破与热点论文 / Materials Breakthroughs and Papers`
  - 材料学新闻、arXiv 材料相关论文、能源材料、半导体材料、软物质等。
- `3. GitHub 流行项目 / Popular GitHub Projects`
  - 最近活跃且星标较高的开源项目。
- `4. GitHub 材料学项目 / Materials-related GitHub Projects`
  - 材料信息学、DFT、分子动力学、晶体结构、pymatgen/matminer 生态等相关项目。

每个条目会尽量包含：

- 中文标题
- English title
- 中文简介
- English summary
- 来源和链接
- 如果来源提供图片，会在归档页面里展示图片

## Discord 页面和历史归档

每天脚本都会生成：

- `docs/YYYY-MM-DD/index.html`：当天完整页面
- `docs/index.html`：历史归档首页
- `docs/latest.html`：跳转到最新一天
- `data/YYYY-MM-DD.json`：结构化数据备份

启用 GitHub Pages 后，Discord 消息里会附上当天网页链接。页面会在 GitHub Pages 部署完成后刷新，通常需要几十秒到几分钟。

## GitHub 设置

1. 新建一个 GitHub 仓库，把这些文件推上去。
2. 在仓库里打开 `Settings` -> `Secrets and variables` -> `Actions`。
3. 添加 Repository secrets：
   - `DISCORD_WEBHOOK_URL`：你的 Discord webhook，不要写进代码。
   - `OPENAI_API_KEY`：用于生成中英双语标题和摘要。
4. 添加 Repository variables：
   - `OPENAI_MODEL`：你想用的 OpenAI 模型名称。
   - `PUBLIC_BASE_URL`：GitHub Pages 地址，例如 `https://yourname.github.io/your-repo`。
5. 打开 `Settings` -> `Pages`：
   - Source 选择 `Deploy from a branch`
   - Branch 选择 `main`
   - Folder 选择 `/docs`

## 发送时间

workflow 每天在 UTC 15:05 和 16:05 都会触发一次，脚本会用 `America/Chicago` 判断本地时间，只在 10 点那一次真正发送。这样可以兼容夏令时和冬令时。

你也可以在 GitHub Actions 页面手动点 `Run workflow` 测试，它会忽略时间窗口并立即发送。

## 本地测试

```bash
python -m pip install -r requirements.txt
DRY_RUN=1 FORCE_SEND=1 OPENAI_MODEL=your-model python src/daily_digest.py
```

如果没有设置 `OPENAI_API_KEY`，脚本仍会生成一个简化版归档，但双语质量会明显下降；正式使用建议设置 API key。
