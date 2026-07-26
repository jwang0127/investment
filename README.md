# 量潮罗盘 · A股每日大盘分析看板

“量潮罗盘”是一个面向 A 股的收盘后研究看板：用成交量状态、市场宽度、行业相对强度和基本面/事件信息，输出大盘环境、行业轮动、候选个股、风险警报及双周期建议。

## 设计来源

- `A股成交量-行业轮动研究模型_v2.md`：量能五维拆解、行业轮动、拥挤/背离/极端宽度信号。
- [ai-berkshire](https://github.com/xbtlin/ai-berkshire)：多视角研究、反向检验、数据不足时保留灰区。
- [daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis)：免费数据源优先、静态报告、GitHub Actions、飞书推送。

## 当前能力

- A 股宽基指数与市场宽度；东财公共接口为主源（成交额为真实值），AkShare 兜底，降级原因全程透出。
- 成交量状态：FLOOD / EXPAND / STABLE / SHRINK / DROUGHT / UNKNOWN。阈值按近一年滚动分位自适应，样本不足回退固定阈值；极端状态需连续两日确认；缺数据一律 UNKNOWN，绝不当 DROUGHT。
- 量能分数与当日价格方向联动：放量下跌（恐慌/出货）不给正分；宽度真实参与打分。
- 行业评分：相对强度（5/20 日）、成交额份额变化、宽度；行业快照逐日落盘 `data/industry_history.csv`，历史攒够后时序因子自动生效；缺失因子先 z 后填中性并重归一权重。
- 预测复盘闭环：每个交易日记录一次方向预测（`data/prediction_journal.jsonl`，按日去重），次日自动回填实际方向，输出滚动胜率并与“始终看多”基线对照。
- 历史回测：1 日与 5 日双窗口，含基线胜率、多空拆分、分状态胜率与信号五分位单调性检验。
- 看板：30 日量价 SVG 走势图（红涨绿跌，A 股习惯；空心柱标记下跌日）、深色模式、移动端单列、stale 数据横幅、复盘/回测卡片。
- 静态 HTML 看板、Markdown 报告、GitHub Actions 自动生成（发布前先跑离线测试）；飞书 Webhook 可选。

## 本地运行

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m investment_dashboard.cli --days 320
```

生成物在 `public/`：`index.html`、`report.md`、`latest.json`。数据源失败会生成带有“数据未更新”标识的报告并沿用最近一次成功快照（`data/last_success.json`），不会填充虚假数值。

测试（全部离线，不访问网络）：

```powershell
pip install pytest
python -m pytest tests/ -q
```

## 可选配置

复制 `.env.example` 为 `.env`：

- `TUSHARE_TOKEN`：有权限后作为 A 股历史数据的稳定来源。
- `FEISHU_WEBHOOK_URL`：推送日报到飞书群机器人。
- `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`：接入兼容 OpenAI API 的解释层。没有 Key 时使用确定性模板报告。

## GitHub Actions

工作流默认在工作日北京时间 18:00 左右运行，先检查交易日和数据状态，再生成静态站点并发布到 GitHub Pages。第一次使用需在仓库 Settings → Pages 中选择 GitHub Actions；飞书和模型 Key 放在 Actions Secrets 中。

## 研究纪律

所有排名、评分和信号都是历史数据上的研究输出，不代表未来收益或提高胜率。报告会把“模型状态”“候选观察”“风险回避”和“价值投资通过/灰区”分开，数据不足时不强行给结论。
