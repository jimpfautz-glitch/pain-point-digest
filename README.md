# Pain-Point Digest

一个每天自动运行的痛点情报脚本。

它会从开发者和创业社区抓取最近 24 小时的帖子，先用关键词做预过滤，再调用 `DeepSeek` 做二次判断，把真正值得看的用户抱怨、未满足需求和潜在产品机会整理成一封邮件发给你。

## 它会做什么

- 抓取多个公开社区的数据源：
  - Hacker News
  - Reddit
  - Indie Hackers
  - V2EX
  - Product Hunt Discussions
  - Dev.to
  - GitHub Discussions
- 用关键词筛掉大部分噪音，控制模型调用成本
- 让 `DeepSeek` 给每条候选内容打分，并输出：
  - 痛点总结
  - 可做的产品方向
  - 第一批潜在用户
  - 竞品与差异化
  - MVP 技术栈建议
- 把结果保存到数据库，顺带生成：
  - 今日洞察
  - 7 天痛点聚类
  - 当月累计统计
- 最终通过邮件发送日报

## 适合谁

- 独立开发者
- 在找 SaaS / API / 小工具方向的人
- 想低成本持续跟踪用户需求的人

如果你每天没时间刷几十个社区，但又不想错过真实需求，这个项目就是给这种场景准备的。

## 工作方式

完整流程如下：

1. 定时抓取各平台最近内容
2. 用 `PAIN_KEYWORDS` 先做一轮便宜的粗筛
3. 把候选帖子交给 `DeepSeek` 精筛和结构化分析
4. 结果写入 `SQLite` 或 `PostgreSQL`
5. 生成 HTML 邮件并发送

默认通过 GitHub Actions 每天自动执行一次。

## 部署方式

### 1. 推到 GitHub

```bash
git init
git add .
git commit -m "init"
gh repo create pain-point-digest --private --source=. --push
```

也可以手动建一个私有仓库再 push。建议一定使用 private repo，避免邮箱配置、数据库地址和代理信息暴露。

### 2. 准备依赖

你需要两类外部能力：

**1. DeepSeek API**

- 去 [DeepSeek 平台](https://platform.deepseek.com/) 获取 API Key
- 本项目通过 Anthropic 兼容接口访问 `DeepSeek`
- 环境变量名称使用 `DEEPSEEK_API_KEY`

**2. SMTP 邮件服务**

最省事的是 Gmail + 应用专用密码：

- 开启两步验证: <https://myaccount.google.com/security>
- 生成应用密码: <https://myaccount.google.com/apppasswords>
- `SMTP_HOST=smtp.gmail.com`
- `SMTP_PORT=587`
- `SMTP_USER=你的 Gmail 地址`
- `SMTP_PASS=16 位应用专用密码`
- `EMAIL_TO=收件邮箱`

你也可以换成其他支持 SMTP 的邮箱服务。

### 3. 配置 GitHub Secrets

进入仓库：

`Settings -> Secrets and variables -> Actions -> New repository secret`

至少配置以下变量：

| Name | 说明 |
|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek API Key |
| `SMTP_HOST` | SMTP 地址 |
| `SMTP_PORT` | SMTP 端口，通常是 `587` |
| `SMTP_USER` | SMTP 用户名 / 发件邮箱 |
| `SMTP_PASS` | SMTP 密码或应用专用密码 |
| `EMAIL_TO` | 收件邮箱 |

可选配置：

| Name | 说明 |
|---|---|
| `PGSQL_URL` | PostgreSQL 连接串；不配时默认使用本地 `SQLite` |
| `PROXY` | HTTP/HTTPS 代理地址 |

### 4. 手动测试一次

进入 GitHub 仓库的 `Actions` 页面，找到 `Daily Pain-Point Digest`，点击 `Run workflow`。

第一次建议重点检查两件事：

- Actions 日志里是否有抓取或 API 报错
- 邮箱是否正常收到日报

如果没收到邮件，先看垃圾邮件箱，再检查 SMTP 配置。

### 5. 自动运行

当前工作流默认每天 `UTC 13:00` 执行一次。

如果你想修改时间，编辑 `.github/workflows/digest.yml` 里的 cron 表达式即可。

## 本地运行

先安装依赖：

```bash
pip install -r requirements.txt
```

然后配置好环境变量，再执行：

```bash
python digest.py
```

本地运行适合用来：

- 调试关键词
- 调 prompt
- 检查某个数据源是否失效

## 关键配置

你最可能会改的地方都在 `digest.py`：

- `SUBREDDITS`: 想监听哪些 subreddit
- `PAIN_KEYWORDS`: 哪些词算作高概率痛点信号
- `LOOKBACK_HOURS`: 看最近多少小时的帖子
- `deepseek_classify()` 里的 prompt: 决定模型筛选风格和输出格式

如果你跑了一周，通常最值得调的是 `PAIN_KEYWORDS` 和 `deepseek_classify()` 的提示词。

## 数据存储

项目支持两种存储模式：

- 默认：`SQLite`
- 可选：`PostgreSQL`

如果你主要跑在 GitHub Actions，建议尽量使用 `PostgreSQL`，否则 CI 环境中的本地 `SQLite` 文件通常不会长期保留，7 天趋势和月统计会不稳定。

## 成本预估

- GitHub Actions：通常够用
- DeepSeek API：主要成本项，但整体仍然比较低
- SMTP：如果用自带邮箱服务，通常接近免费

实际成本取决于：

- 抓取源数量
- 关键词命中率
- 每天送进模型的候选条数

## 输出结果长什么样

每天邮件大致包含三部分：

- 今日洞察：当天高分痛点的总结判断
- 痛点列表：每条对应原帖、痛点、产品点子、获客和技术栈
- 趋势统计：7 天聚类 + 月度累计

这不是“帮你自动决定做什么”的工具，而是“帮你更快发现值得进一步验证的方向”。

## 重要提醒

- 这类工具的价值不在于模型写得多漂亮，而在于先把信息量压下来
- 邮件里的结论只能作为索引，不能替代原帖
- 真正要不要做，还是得点进原文和评论区继续验证
- 评论区往往比楼主正文更有价值，尤其适合找真实使用场景和付费线索
