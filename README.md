# Pain-Point Digest

每天自动抓取 Hacker News、Reddit (r/webdev, r/SaaS, r/indiehackers, r/Entrepreneur) 和
Indie Hackers,用关键词预过滤 + Claude 判断,把真实痛点和产品机会发到你邮箱。

## 部署步骤

### 1. 把代码推到 GitHub

```bash
git init
git add .
git commit -m "init"
gh repo create pain-point-digest --private --source=. --push
```

或者手动在 GitHub 网页上创建一个 private repo,再 push。**一定要 private**——不然你的关键词配置和邮件地址别人能看到。

### 2. 准备好这些东西

**Anthropic API key**:
- 去 https://console.anthropic.com/ 注册,创建一个 API key
- 充值 5 美元应该够你跑两三个月

**SMTP 邮件服务**(用来发邮件给自己):

最简单的方案是 Gmail + 应用专用密码:
- 开启两步验证: https://myaccount.google.com/security
- 生成应用密码: https://myaccount.google.com/apppasswords
- SMTP_HOST = `smtp.gmail.com`
- SMTP_PORT = `587`
- SMTP_USER = 你的 Gmail 地址
- SMTP_PASS = 刚生成的 16 位应用密码(不是 Gmail 登录密码!)
- EMAIL_TO = 收件邮箱(可以就是你自己)

其他选择:Resend(每天 100 封免费,API 也简单)、Mailgun、SendGrid、自己的邮箱服务商。

### 3. 在 GitHub repo 里配置 Secrets

进入 repo → Settings → Secrets and variables → Actions → New repository secret,
依次添加:

| Name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | sk-ant-... |
| `SMTP_HOST` | smtp.gmail.com |
| `SMTP_PORT` | 587 |
| `SMTP_USER` | your@gmail.com |
| `SMTP_PASS` | 应用专用密码 |
| `EMAIL_TO` | your@gmail.com |

### 4. 手动跑一次测试

进入 repo → Actions → Daily Pain-Point Digest → Run workflow

跑完几分钟后,看 Actions 日志,确认有没有报错;检查邮箱是不是收到了。
没收到先翻垃圾邮件。

### 5. 之后就自动每天跑了

默认时间是 UTC 13:00(北京时间 21:00,美东上午 9 点)。改时间改 `.github/workflows/digest.yml`
里的 cron 表达式即可。

## 你可能想调的地方

- **`SUBREDDITS`**:加你自己关心的 sub,比如 `selfhosted`、`devops`、`programming`
- **`PAIN_KEYWORDS`**:观察一周后,会发现某些关键词噪音大,可以删掉;也会发现自己漏掉了一些好的关键词,加上
- **`claude_classify` 里的 prompt**:这是质量的关键,跑一周后根据邮件内容微调
- **cron 时间**:如果你想早上喝咖啡时读,改成你早上的时间

## 成本估算

- GitHub Actions: 免费(每月 2000 分钟,这个脚本一次 2-3 分钟)
- Anthropic API: 关键词过滤后通常每天 20-50 条进 Claude,每条几百 token,
  **月成本 1-3 美元**
- Gmail SMTP: 免费

## 重要提醒

这个工具的价值,**不在于 Claude 总结得多好,而在于把你每天该读的东西从几千条压缩到 20 条**。
**总结只是索引,真正有价值的洞察永远在原帖和评论区。** 看到感兴趣的一定要点链接进去读原文,
特别是评论区——很多时候楼主的痛点是一回事,评论区里别人补充的痛点反而更值得做。
