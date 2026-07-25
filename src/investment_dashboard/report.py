"""生成每日大盘报告、机器可读 JSON 和静态看板。"""

from __future__ import annotations

import html
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
        item = {}
        for col in columns:
            value = row.get(col)
            item[col] = value if isinstance(value, str) else _num(value)
        result.append(item)
    return result


def _market_summary(features: pd.DataFrame) -> tuple[dict, dict]:
    latest = features.iloc[-1]
    signal = volume_signal(latest)
    state = latest["volume_state"]
    stance = "积极观察" if signal >= 0.35 else "等待确认" if signal > -0.2 else "控制仓位"
    summary = {
        "date": str(latest["date"].date()),
        "close": _num(latest["close"]),
        "pct_chg": _num(latest.get("pct_chg")),
        "volume_state": state,
        "volume_signal": _num(signal),
        "amount_ratio20": _num(latest.get("amount_ratio20")),
        "amount_ratio60": _num(latest.get("amount_ratio60")),
        "volume_slope5": _num(latest.get("volume_slope5")),
        "stance": stance,
    }
    history = []
    for _, row in features.tail(30).iterrows():
        history.append({"date": str(row["date"].date()), "close": _num(row["close"]), "ratio20": _num(row.get("amount_ratio20")), "state": row.get("volume_state")})
    return summary, {"history": history}


def build_report(bundle, output_dir: str | Path) -> dict:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    if bundle.market.empty:
        payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "status": "no_data", "warning": bundle.warning, "source": bundle.source, "market": {}, "indices": {}, "industries": [], "stocks": [], "review": {"observations": 0, "hit_rate": None}}
        body = "# 量潮罗盘｜数据未更新\n\n当前没有可验证的行情数据，系统不生成虚假行情或买卖结论。"
    else:
        features = market_features(bundle.market)
        market, chart = _market_summary(features)
        industries = bundle.industries.sort_values("pct_chg", ascending=False) if not bundle.industries.empty else bundle.industries
        stocks = bundle.stocks.sort_values(["pct_chg", "amount"], ascending=False) if not bundle.stocks.empty else bundle.stocks
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "ok",
            "source": bundle.source,
            "market": market,
            "chart": chart,
            "indices": bundle.indices or {},
            "industries": _records(industries, ["industry", "pct_chg", "breadth"], 10),
            "stocks": _records(stocks, ["code", "name", "price", "pct_chg", "amount", "turnover", "pe"], 12),
            "review": {"observations": 0, "hit_rate": None, "note": "每日记录未来收益后自动更新"},
        }
        body = _markdown_report(payload)
    (out / "latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(body, encoding="utf-8")
    (out / "index.html").write_text(_html(), encoding="utf-8")
    return payload


def _markdown_report(payload: dict) -> str:
    m = payload["market"]
    lines = [f"# 量潮罗盘｜{m['date']}", "", "## 市场结论", "", f"- 市场姿态：**{m['stance']}**", f"- 上证指数：{m['close']:.2f}（{m['pct_chg'] or 0:.2f}%）", f"- 量能状态：**{m['volume_state']}**", f"- 成交额 / 20日均值：{m['amount_ratio20'] or 0:.2f} 倍", "", "## 短线建议", "", "优先观察量价配合和行业相对强度，避免追逐单日涨幅；候选池仅作研究起点。", "", "## 中长期价值投资", "", "继续执行商业质量、财务真实性、管理层、估值、安全边际和反向风险检查；成交量只作为市场环境变量。", "", "## 行业观察", ""]
    lines += [f"- {x['industry']}：{x['pct_chg']:.2f}%｜宽度 {x['breadth']:.0f}%" for x in payload["industries"]] or ["- 行业数据暂不可用"]
    lines += ["", "## 个股观察池", ""]
    lines += [f"- {x['name']}（{x['code']}）：{x['pct_chg']:.2f}%｜换手 {x.get('turnover') or 0:.2f}%" for x in payload["stocks"]] or ["- 个股数据暂不可用"]
    return "\n".join(lines) + "\n"


def _html() -> str:
    return r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>量潮罗盘</title>
<style>
:root{--bg:#07111f;--card:#101d31;--line:#243653;--text:#e6edf7;--muted:#8fa3bd;--cyan:#62d8ff;--green:#45d49a;--red:#ff7081;--gold:#ffc857}*{box-sizing:border-box}body{margin:0;background:linear-gradient(135deg,#07111f,#0a1528 55%,#111b32);font:14px/1.6 Inter,system-ui,"Microsoft YaHei",sans-serif;color:var(--text)}main{max-width:1280px;margin:auto;padding:32px 22px 56px}.top{display:flex;justify-content:space-between;align-items:end;gap:20px;margin-bottom:24px}h1{font-size:32px;letter-spacing:2px;margin:0;color:var(--cyan)}h2{font-size:17px;margin:0 0 16px}.sub,.muted{color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:16px}.card{background:rgba(16,29,49,.92);border:1px solid var(--line);border-radius:16px;padding:20px;box-shadow:0 12px 35px #0002}.hero{grid-column:span 5}.indices{grid-column:span 7}.wide{grid-column:span 8}.side{grid-column:span 4}.full{grid-column:span 12}.kpi{font-size:34px;color:var(--gold);font-weight:700;margin:8px 0}.pill{display:inline-block;padding:3px 10px;border-radius:99px;background:#1a3550;color:var(--cyan);font-size:12px}.metric{display:flex;justify-content:space-between;border-bottom:1px solid #ffffff0d;padding:8px 0}.metric:last-child{border:0}.positive{color:var(--green)}.negative{color:var(--red)}table{width:100%;border-collapse:collapse}th,td{text-align:left;border-bottom:1px solid #ffffff0d;padding:10px 6px}th{color:var(--muted);font-weight:500}.bar{height:7px;background:#1c2b43;border-radius:9px;overflow:hidden}.bar i{display:block;height:100%;background:linear-gradient(90deg,var(--cyan),var(--green));border-radius:9px}.notice{background:#18283d;border-left:3px solid var(--gold);padding:12px 14px;border-radius:8px}.empty{color:var(--muted);padding:18px 0}@media(max-width:900px){.hero,.indices,.wide,.side{grid-column:span 12}.top{display:block}.top .sub{margin-top:8px}}
</style></head><body><main><div class="top"><div><h1>量潮罗盘</h1><div class="sub">A股每日大盘 · 成交量 · 行业轮动 · 个股观察池</div></div><div id="updated" class="muted">加载中…</div></div>
<div id="app" class="grid"><div class="card full">正在加载今日数据…</div></div>
<script>
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pct=v=>v==null?'—':`${Number(v).toFixed(2)}%`, cls=v=>(Number(v)>=0?'positive':'negative');
function tableIndustries(xs){if(!xs?.length)return '<div class="empty">行业数据暂不可用，下一次收盘后自动补齐。</div>';return `<table><tr><th>行业</th><th>涨跌</th><th>市场宽度</th><th>强度</th></tr>${xs.map(x=>`<tr><td>${esc(x.industry)}</td><td class="${cls(x.pct_chg)}">${pct(x.pct_chg)}</td><td>${Number(x.breadth||0).toFixed(0)}%</td><td><div class="bar"><i style="width:${Math.max(4,Math.min(100,50+Number(x.pct_chg||0)*8))}%"></i></div></td></tr>`).join('')}</table>`}
function tableStocks(xs){if(!xs?.length)return '<div class="empty">个股观察池暂不可用，数据源恢复后自动更新。</div>';return `<table><tr><th>代码</th><th>名称</th><th>现价</th><th>涨跌</th><th>换手</th><th>标签</th></tr>${xs.map(x=>`<tr><td>${esc(x.code)}</td><td>${esc(x.name)}</td><td>${x.price??'—'}</td><td class="${cls(x.pct_chg)}">${pct(x.pct_chg)}</td><td>${x.turnover??'—'}%</td><td><span class="pill">观察池</span></td></tr>`).join('')}</table>`}
fetch('latest.json').then(r=>r.json()).then(x=>{document.querySelector('#updated').textContent='生成时间：'+new Date(x.generated_at).toLocaleString('zh-CN');if(x.status!=='ok'){document.querySelector('#app').innerHTML=`<section class="card full"><h2>数据状态</h2><div class="notice">${esc(x.warning||'暂无可用数据')}</div></section>`;return}const m=x.market||{},ix=x.indices||{};document.querySelector('#app').innerHTML=`
<section class="card hero"><h2>今日市场姿态 <span class="pill">${esc(m.date)}</span></h2><div class="kpi">${esc(m.stance)}</div><div class="metric"><span>量能状态</span><b>${esc(m.volume_state)}</b></div><div class="metric"><span>量能综合分数</span><b>${m.volume_signal}</b></div><div class="metric"><span>成交额 / 20日均值</span><b>${m.amount_ratio20??'—'} 倍</b></div><div class="notice">短线：观察行业强度与次日确认；中长期：量能不替代基本面和安全边际。</div></section>
<section class="card indices"><h2>主要指数</h2><div class="grid">${Object.entries(ix).map(([k,v])=>`<div class="card" style="grid-column:span 4;padding:14px"><div class="muted">${esc(k)}</div><b style="font-size:20px">${v.close??'—'}</b><div class="${cls(v.pct_chg)}">${pct(v.pct_chg)}</div></div>`).join('')||'<div class="empty">指数数据暂不可用</div>'}</div></section>
<section class="card wide"><h2>行业轮动排名 <span class="muted">申万/板块实时观察</span></h2>${tableIndustries(x.industries)}</section>
<section class="card side"><h2>量能雷达</h2><div class="metric"><span>指数点位</span><b>${m.close}</b></div><div class="metric"><span>当日涨跌</span><b class="${cls(m.pct_chg)}">${pct(m.pct_chg)}</b></div><div class="metric"><span>5日量能斜率</span><b>${m.volume_slope5??'—'}</b></div><div class="metric"><span>研究复盘样本</span><b>${x.review?.observations||0}</b></div></section>
<section class="card full"><h2>个股观察池 <span class="muted">不等于买入清单</span></h2>${tableStocks(x.stocks)}</section>
<section class="card wide"><h2>双周期决策框架</h2><div class="metric"><span>1—5个交易日</span><b>强势行业 + 量价确认 + 控制追高</b></div><div class="metric"><span>中长期价值投资</span><b>商业质量 + 财务真实性 + 估值 + 安全边际</b></div><div class="metric"><span>风险回避条件</span><b>量能极端、宽度背离、数据缺失或政策风险</b></div></section>
<section class="card side"><h2>数据与模型</h2><div class="metric"><span>数据源</span><b>${esc(x.source)}</b></div><div class="metric"><span>状态</span><b class="positive">已生成</b></div><div class="metric"><span>模型定位</span><b>历史研究</b></div><p class="muted">每日收盘后记录信号，未来收益回填后更新命中率和权重。</p></section>`}).catch(e=>document.querySelector('#app').innerHTML='<section class="card full"><div class="notice">数据读取失败，请稍后刷新。</div></section>');</script></main></body></html>'''
