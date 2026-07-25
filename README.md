# 量潮罗盘 · A股每日大盘分析看板

“量潮罗盘”是一个面向 A 股的收盘后研究看板：用成交量状态、市场宽度、行业相对强度和基本面/事件信息，输出大盘环境、行业轮动、候选个股、风险警报及双周期建议。

## 设计来源

- `A股成交量-行业轮动研究模型_v2.md`：量能五维拆解、行业轮动、拥挤/背离/极端宽度信号。
- [ai-berkshire](https://github.com/xbtlin/ai-berkshire)：多视角研究、反向检验、数据不足时保留灰区。
- [daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis)：免费数据源优先、静态报告、GitHub Actions、飞书推送。

## 当前能力

- A 股宽基指数与市场宽度；申万一级行业可扩展到二级行业。
- 成交量状态：FLOOD / EXPAND / STABLE / SHRINK / DROUGHT。
- 行业评分：相对强度、量能 Beta、成交额占比变化、宽度、拥挤度、背离。
- 候选个股：在强势行业中按动量、流动性、波动率和风险项进行排序。
- 双周期结论：1—5 个交易日与中长期价值投资分别输出。
- 五年滚动回测接口与每日预测记录，后续可根据预测误差校准权重。
- 静态 HTML 看板、Markdown 报告、GitHub Actions 自动生成；飞书 Webhook 可选。
- 港股、日韩市场保留数据适配器接口，首版不与 A 股评分混合。

## 本地运行

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m investment_dashboard.cli --days 120
```

生成物在 `public/`：`index.html`、`report.md`、`latest.json`。没有 API Key 时优先尝试 AkShare；数据源失败会生成带有“数据未更新”标识的报告，不会填充虚假数值。

## 可选配置

复制 `.env.example` 为 `.env`：

- `TUSHARE_TOKEN`：有权限后作为 A 股历史数据的稳定来源。
- `FEISHU_WEBHOOK_URL`：推送日报到飞书群机器人。
- `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`：接入兼容 OpenAI API 的解释层。没有 Key 时使用确定性模板报告。

## GitHub Actions

工作流默认在工作日北京时间 18:00 左右运行，先检查交易日和数据状态，再生成静态站点并发布到 GitHub Pages。第一次使用需在仓库 Settings → Pages 中选择 GitHub Actions；飞书和模型 Key 放在 Actions Secrets 中。

## 研究纪律

所有排名、评分和信号都是历史数据上的研究输出，不代表未来收益或提高胜率。报告会把“模型状态”“候选观察”“风险回避”和“价值投资通过/灰区”分开，数据不足时不强行给结论。
