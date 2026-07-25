"""生成专业化的每日大盘研究页：量能、行业轮动、新闻政策与结论。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .volume_model import market_features, volume_signal


def _num(value, digits=2):
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _records(frame: pd.DataFrame, columns: list[str], limit: int = 10) -> list[dict]:
    if frame.empty:
        return []
    result = []
    for _, row in frame.head(limit).iterrows():
        result.append({col: row.get(col) if isinstance(row.get(col), str) else _num(row.get(col)) for col in columns})
    return result


def _market(features: pd.DataFrame, breadth: dict | None) -> tuple[dict, dict]:
    latest = features.iloc[-1]
    score = volume_signal(latest)
    state = latest["volume_state"]
    ratio = float(latest.get("amount_ratio20") or 0)
    slope = float(latest.get("volume_slope5") or 0)
    level_score = max(0, min(100, (ratio - 0.6) / 1.0 * 100))
    trend_score = max(0, min(100, (slope + 5) / 10 * 100))
    breadth_pct = ((breadth or {}).get("up", 0) / max((breadth or {}).get("total", 0), 1)) * 100
    state_score = {"FLOOD": 100, "EXPAND": 78, "STABLE": 55, "SHRINK": 32, "DROUGHT": 15}.get(state, 50)
    composite = round(level_score * 0.4 + trend_score * 0.25 + breadth_pct * 0.2 + state_score * 0.15, 1)
    if state in ("FLOOD", "EXPAND") and score > 0.25:
        conclusion = "量能改善，市场具备继续观察条件"
        risk = "放量过快时警惕短线拥挤，等待行业扩散确认"
    elif state in ("SHRINK", "DROUGHT"):
        conclusion = "量能偏弱，市场仍处于存量博弈"
        risk = "不宜追涨，优先等待成交额回到20日均值上方"
    else:
        conclusion = "量能中性，方向尚未形成明确共识"
        risk = "重点观察量能斜率与市场宽度是否同步改善"
    market = {"date": str(latest["date"].date()), "close": _num(latest["close"]), "pct_chg": _num(latest.get("pct_chg")), "volume_state": state, "volume_signal": _num(score), "amount": _num(latest.get("amount"), 0), "amount_ratio20": _num(latest.get("amount_ratio20")), "amount_ratio60": _num(latest.get("amount_ratio60")), "volume_slope5": _num(latest.get("volume_slope5")), "breadth_pct": _num(breadth_pct), "score": {"total": composite, "level": round(level_score, 1), "trend": round(trend_score, 1), "breadth": round(breadth_pct, 1), "state": state_score, "weights": {"level": "40%", "trend": "25%", "breadth": "20%", "state": "15%"}}, "conclusion": conclusion, "risk": risk}
    history = [{"date": str(r["date"].date()), "close": _num(r["close"]), "ratio20": _num(r.get("amount_ratio20")), "state": r.get("volume_state")} for _, r in features.tail(30).iterrows()]
    return market, {"history": history}


def build_report(bundle, output_dir: str | Path) -> dict:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    if bundle.market.empty:
        payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "status": "no_data", "warning": bundle.warning, "source": bundle.source, "market": {}, "breadth": bundle.breadth or {}, "chart": {"history": []}, "indices": {}, "industries_top": [], "industries_bottom": [], "news": [], "review": {"observations": 0, "hit_rate": None}}
        report = "# 量潮罗盘\n\n暂无可验证的行情数据，系统不会生成虚假结论。\n"
    else:
        features = market_features(bundle.market)
        market, chart = _market(features, bundle.breadth)
        industries = bundle.industries.copy()
        top = industries.sort_values("pct_chg", ascending=False) if not industries.empty else industries
        bottom = industries.sort_values("pct_chg", ascending=True) if not industries.empty else industries
        payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "status": "ok", "source": bundle.source, "market": market, "breadth": bundle.breadth or {}, "chart": chart, "indices": bundle.indices or {}, "industries_top": _records(top, ["industry", "pct_chg", "breadth", "amount"], 10), "industries_bottom": _records(bottom, ["industry", "pct_chg", "breadth", "amount"], 10), "news": bundle.news or [], "review": {"observations": 0, "hit_rate": None}}
        report = _markdown(payload)
    (out / "latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(report, encoding="utf-8")
    (out / "index.html").write_text(_html(), encoding="utf-8")
    return payload


def _markdown(x: dict) -> str:
    m = x["market"]
    b = x.get("breadth", {})
    lines = [f"# 量潮罗盘｜{m['date']}", "", "## 核心结论", "", f"**{m['conclusion']}。**", "", f"风险提示：{m['risk']}。", "", "## 成交量分析", "", f"- 量能状态：{m['volume_state']}", f"- 量能总分：{m['score']['total']}/100", f"- 成交额：{m['amount'] or 0:.0f}", f"- 成交额 / 20日均值：{m['amount_ratio20'] or 0:.2f} 倍", f"- 量能斜率（5日）：{m['volume_slope5'] or 0:.2f}", f"- 上涨/下跌/平盘：{b.get('up', 0)}/{b.get('down', 0)}/{b.get('flat', 0)}", "", "## 行业轮动 Top 10", ""]
    lines += [f"- {r['industry']}：{r['pct_chg']:.2f}%｜宽度 {r['breadth']:.0f}%" for r in x["industries_top"]] or ["- 行业数据暂不可用"]
    lines += ["", "## 行业轮动 Bottom 10", ""]
    lines += [f"- {r['industry']}：{r['pct_chg']:.2f}%｜宽度 {r['breadth']:.0f}%" for r in x["industries_bottom"]] or ["- 行业数据暂不可用"]
    lines += ["", "## 新闻与政策线索", ""]
    lines += [f"- [{r.get('category', '市场')}] {r.get('title', '')}" for r in x["news"]] or ["- 新闻数据暂不可用"]
    return "\n".join(lines) + "\n"


def _html() -> str:
    return r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>量潮罗盘｜每日大盘研究</title><style>
:root{--bg:#f4f6f8;--card:#fff;--line:#e3e7eb;--text:#1d2733;--muted:#718096;--blue:#2d6cdf;--orange:#d97706;--green:#16845b;--red:#c2414b}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",Arial,sans-serif}main{max-width:1440px;margin:auto;padding:28px 28px 52px}.header{display:flex;align-items:end;justify-content:space-between;border-bottom:1px solid var(--line);padding-bottom:18px;margin-bottom:20px}h1{font-size:27px;letter-spacing:1px;margin:0;font-weight:650}h2{font-size:16px;margin:0 0 15px;font-weight:650}.subtitle,.muted{color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:18px}.card{grid-column:span 4;background:var(--card);border:1px solid var(--line);padding:20px;border-radius:6px}.wide{grid-column:span 6}.full{grid-column:span 12}.conclusion{border-top:3px solid var(--blue)}.conclusion strong{display:block;font-size:23px;line-height:1.35;margin:8px 0 12px}.risk{background:#fff8ed;border-left:3px solid var(--orange);padding:10px 12px;color:#79521c}.kpi{font-size:27px;font-weight:650}.row{display:flex;justify-content:space-between;border-bottom:1px solid #eef0f2;padding:8px 0}.row:last-child{border:0}.pos{color:var(--green)}.neg{color:var(--red)}.tag{display:inline-block;color:var(--blue);background:#eef4ff;border:1px solid #d9e6ff;padding:1px 8px;border-radius:3px;font-size:12px}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:9px 6px;border-bottom:1px solid #eef0f2}th{color:var(--muted);font-weight:500}.bar{height:6px;background:#edf0f3;border-radius:3px}.bar i{display:block;height:100%;background:var(--blue);border-radius:3px}.news{padding:8px 0;border-bottom:1px solid #eef0f2}.news a{color:var(--text);text-decoration:none}.news small{display:block;color:var(--muted)}.empty{color:var(--muted);padding:10px 0}.foot{color:var(--muted);font-size:12px;margin-top:20px}@media(max-width:900px){.card,.wide,.full{grid-column:span 12}.header{display:block}.header .muted{margin-top:6px}}
</style></head><body><main><header class="header"><div><h1>量潮罗盘</h1><div class="subtitle">A股每日大盘研究｜成交量与行业轮动</div></div><div id="updated" class="muted">正在读取数据…</div></header><div id="app" class="grid"><section class="card full">正在加载…</section></div><div class="foot">数据用于研究与风险管理，不构成投资建议。自动更新：GitHub Actions 工作日收盘后运行。</div></main><script>
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const pct=v=>v==null?'—':Number(v).toFixed(2)+'%';const cls=v=>Number(v)>=0?'pos':'neg';const empty='<div class="empty">当前数据源暂未返回内容，下一次自动更新时重试。</div>';
function rows(xs){return xs?.length?xs.map(x=>`<tr><td>${esc(x.industry)}</td><td class="${cls(x.pct_chg)}">${pct(x.pct_chg)}</td><td>${x.breadth==null?'—':Number(x.breadth).toFixed(0)+'%'}</td><td>${x.amount==null?'—':Number(x.amount).toFixed(0)}</td></tr>`).join(''):''}
fetch('latest.json').then(r=>r.json()).then(x=>{document.querySelector('#updated').textContent='数据生成：'+new Date(x.generated_at).toLocaleString('zh-CN');if(x.status!=='ok'){document.querySelector('#app').innerHTML=`<section class="card full"><h2>数据状态</h2><div class="risk">${esc(x.warning||'暂无数据')}</div></section>`;return}const m=x.market||{},ix=x.indices||{},b=x.breadth||{},s=m.score||{};const scoreRow=(label,val,weight)=>`<div class="row"><span>${label} <small class="muted">${weight}</small></span><b>${val??'—'}</b></div>`;document.querySelector('#app').innerHTML=`<section class="card wide conclusion"><h2>昨日核心结论 <span class="tag">${esc(m.date)}</span></h2><strong>${esc(m.conclusion)}</strong><p>上证指数 <b>${m.close}</b> <span class="${cls(m.pct_chg)}">${pct(m.pct_chg)}</span>；成交额 <b>${m.amount??'—'}</b></p><div class="risk">风险提示：${esc(m.risk)}</div></section><section class="card"><h2>成交量评分</h2><div class="kpi">${s.total??'—'}<small>/100</small></div>${scoreRow('量能水平',s.level,'40%')}${scoreRow('量能趋势',s.trend,'25%')}${scoreRow('市场宽度',s.breadth,'20%')}${scoreRow('状态稳定性',s.state,'15%')}</section><section class="card"><h2>市场涨跌家数</h2><div class="kpi"><span class="pos">${b.up??'—'}</span> / <span class="neg">${b.down??'—'}</span></div><div class="row"><span>平盘</span><b>${b.flat??'—'}</b></div><div class="row"><span>样本总数</span><b>${b.total??'—'}</b></div><div class="row"><span>上涨占比</span><b>${m.breadth_pct??'—'}%</b></div></section><section class="card full"><h2>主要指数</h2><div class="grid">${Object.entries(ix).map(([k,v])=>`<div class="card" style="grid-column:span 2;padding:12px"><div class="muted">${esc(k)}</div><div class="kpi" style="font-size:20px">${v.close??'—'}</div><span class="${cls(v.pct_chg)}">${pct(v.pct_chg)}</span></div>`).join('')||empty}</div></section><section class="card wide"><h2>昨日行业涨跌 Top 10</h2>${x.industries_top?.length?`<table><tr><th>行业</th><th>涨跌</th><th>上涨宽度</th><th>成交额</th></tr>${rows(x.industries_top)}</table>`:empty}</section><section class="card wide"><h2>昨日行业涨跌 Bottom 10</h2>${x.industries_bottom?.length?`<table><tr><th>行业</th><th>涨跌</th><th>上涨宽度</th><th>成交额</th></tr>${rows(x.industries_bottom)}</table>`:empty}</section><section class="card full"><h2>政策与新闻抽象</h2>${x.news?.length?x.news.map(n=>`<div class="news"><a href="${esc(n.link)}" target="_blank" rel="noreferrer"><b>${esc(n.theme)}</b>｜${esc(n.title)}</a><small>影响判断：${esc(n.impact)}｜置信度：${esc(n.confidence)}｜${esc(n.published)}</small></div>`).join(''):empty}</section>`}).catch(()=>document.querySelector('#app').innerHTML='<section class="card full"><div class="risk">数据读取失败，请刷新页面。</div></section>');</script></body></html>'''
