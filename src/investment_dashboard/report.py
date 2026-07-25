"""静态报告和看板生成。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .volume_model import market_features, volume_signal


def build_report(bundle, output_dir: str | Path) -> dict:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    if bundle.market.empty:
        payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "status": "no_data", "warning": bundle.warning, "source": bundle.source}
        title = "量潮罗盘｜等待数据更新"
        body = f"# {title}\n\n- 状态：数据未更新\n- 数据源：{bundle.source}\n- 原因：{bundle.warning or '暂无可用行情数据'}\n\n系统不会在数据缺失时生成虚假行情或强行给出买卖结论。"
    else:
        features = market_features(bundle.market)
        latest = features.iloc[-1]
        sig = volume_signal(latest)
        payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "status": "ok", "source": bundle.source, "date": str(latest["date"].date()), "volume_state": latest["volume_state"], "volume_signal": sig, "close": float(latest["close"]), "amount_ratio20": None if pd.isna(latest["amount_ratio20"]) else float(latest["amount_ratio20"])}
        body = f"# 量潮罗盘｜{latest['date'].date()}\n\n## 今日结论\n\n- 量能状态：**{latest['volume_state']}**\n- 量能综合分数：**{sig:.2f}**（历史研究分数，不代表收益保证）\n- 上证指数：{latest['close']:.2f}\n- 20日成交额比：{latest['amount_ratio20']:.2f} 倍\n"
        if not bundle.industries.empty:
            top = bundle.industries.sort_values("pct_chg", ascending=False).head(5)
            body += "\n## 行业观察\n\n" + "\n".join(f"- {r['industry']}：{r['pct_chg']:.2f}%｜观察" for _, r in top.iterrows()) + "\n"
        if not bundle.stocks.empty:
            top_stocks = bundle.stocks.head(10)
            body += "\n## 个股候选（仅观察池）\n\n" + "\n".join(f"- {r.get('name', r.get('code'))}（{r['code']}）：{r['pct_chg']:.2f}%" for _, r in top_stocks.iterrows()) + "\n"
        body += "\n## 建议\n\n- 短线（1—5个交易日）：优先观察量价配合、行业相对强度和次日确认，不追逐单日涨幅。\n- 中长期价值投资：继续执行公司质量、估值、安全边际和反向风险检查；量能只作为市场环境变量。\n- 风险纪律：当数据源不完整、市场宽度与量能背离或出现极端状态时，降低结论置信度。\n"
    (out / "latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(body, encoding="utf-8")
    html = """<!doctype html><html lang='zh-CN'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>量潮罗盘</title><style>body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#0b1220;color:#e5e7eb;margin:0;padding:32px}main{max-width:960px;margin:auto}section{background:#121b2d;border:1px solid #263653;border-radius:16px;padding:24px;margin:16px 0}h1{color:#7dd3fc}.muted{color:#94a3b8}.kpi{font-size:2rem;color:#fbbf24}</style><main><h1>量潮罗盘</h1><p class='muted'>A股每日大盘、成交量与行业轮动研究看板</p><section><h2>今日状态</h2><div id='app'>加载中…</div></section><section><h2>研究纪律</h2><p>模型输出用于历史研究和风险管理，不构成投资建议；数据缺失时不生成虚假结论。</p></section><script>fetch('latest.json').then(r=>r.json()).then(x=>{document.querySelector('#app').innerHTML='<p>状态：'+x.status+'</p><p class="kpi">'+(x.volume_state||'数据未更新')+'</p><p>数据源：'+x.source+'</p><p>'+((x.warning||'报告已生成')+'')+'</p>'})</script></main></html>"""
    (out / "index.html").write_text(html, encoding="utf-8")
    return payload
