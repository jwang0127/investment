# 免费数据与推送配置

## 1. 先零配置运行

项目默认先尝试 AkShare，适合开发和试运行；免费接口可能受限流、网络和上游变更影响。即使没有 Token，也能运行测试和生成“数据未更新”状态页。

## 2. Tushare Pro

1. 打开 [Tushare Pro](https://tushare.pro/)，注册并登录。
2. 进入个人中心，复制 Token。
3. 在本地 `.env` 中填入 `TUSHARE_TOKEN`；在 GitHub 仓库 Settings → Secrets and variables → Actions 中新增同名 Secret。
4. 首版仍保留 AkShare 兜底；后续接入更完整的指数、行业、财务和资金流接口。

Tushare 官方说明：注册后可在 Pro 网站获取免费 Token，但不同接口有积分/权限要求，实际可用范围以账号显示为准。[官方说明](https://tushare.pro/document/1?doc_id=133)

## 3. 飞书自动推送

1. 在飞书建立一个接收日报的群。
2. PC 端打开群设置 → 群机器人 → 添加自定义机器人，复制 Webhook 地址。
3. 在 GitHub Actions Secrets 中新增 `FEISHU_WEBHOOK_URL`，值为该地址。
4. 下一次工作日 18:00 的任务会推送简短摘要；完整报告仍在 GitHub Pages。

飞书官方支持通过自定义机器人的 Webhook 向群组推送外部系统消息。[官方指南](https://www.feishu.cn/hc/zh-CN/articles/360024984973-%E5%9C%A8%E7%BE%A4%E7%BB%84%E4%B8%AD%E4%BD%BF%E7%94%A8%E6%9C%BA%E5%99%A8%E4%BA%BA)

## 4. 模型 API

没有模型 Key 时，系统使用确定性模板；有兼容 OpenAI 的 API 时，再接入新闻/公告摘要和多视角质询。不要把 API Key 写入代码或提交到 Git。
