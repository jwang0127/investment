"""生成每日大盘研究页：成交额、宽度、行业轮动、回测复盘、新闻政策与结论。

结构：build_payload 纯计算 → write_outputs 统一落盘（latest.json / report.md / index.html）。
所有数值经 NaN 清洗后才进 JSON——裸 NaN 会产生非法 JSON 并让前端整页挂掉。
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .volume_model import SIGNAL_DEAD_ZONE, backtest_volume, market_features, score_industries, volume_signal


def _num(v, digits=2):
    try:
        value = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return round(value, digits)


def _clean(obj):
    """递归清洗：NaN/Inf → None，numpy 标量 → Python 标量，保证 JSON 严格合法。"""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if hasattr(obj, "item"):  # numpy 标量
        return _clean(obj.item())
    return obj


def clean_payload(payload: dict) -> dict:
    """公开的 payload 清洗入口，供 cli 在落盘/快照前统一调用。"""
    return _clean(payload)


def _records(frame, columns, limit=10):
    if frame is None or frame.empty:
        return []
    rows = []
    for _, row in frame.head(limit).iterrows():
        rows.append({c: row.get(c) if isinstance(row.get(c), str) else _num(row.get(c)) for c in columns})
    return rows


_STATE_LABELS = {
    "FLOOD": "洪峰放量",
    "EXPAND": "温和放量",
    "STABLE": "量能平稳",
    "SHRINK": "温和缩量",
    "DROUGHT": "地量",
    "UNKNOWN": "数据不足",
}

_STATE_SCORES = {"FLOOD": 100, "EXPAND": 78, "STABLE": 55, "SHRINK": 32, "DROUGHT": 15}


def _conclusion(state: str, signal: float, pct_chg) -> tuple[str, str]:
    falling = _num(pct_chg) is not None and pct_chg < 0
    if state == "UNKNOWN":
        return "量能数据不足，今日不给方向判断", "等待数据源恢复后再评估，勿以缺数据当利空"
    if state in ("FLOOD", "EXPAND") and falling and signal <= 0:
        return "放量下跌，资金分歧加大", "警惕情绪继续恶化；反弹需先看到缩量企稳或宽度修复"
    if state in ("FLOOD", "EXPAND") and signal > SIGNAL_DEAD_ZONE:
        return "量能改善，市场具备继续观察条件", "放量过快时警惕短线拥挤，等待行业扩散确认"
    if state in ("SHRINK", "DROUGHT"):
        return "量能偏弱，市场仍处于存量博弈", "不宜追涨，优先等待成交额回到20日均值上方"
    return "量能中性，方向尚未形成明确共识", "观察量能斜率与市场宽度是否同步改善"


def _market(features: pd.DataFrame, breadth: dict | None):
    latest = features.iloc[-1].copy()
    breadth_pct = None
    if breadth and breadth.get("total"):
        breadth_pct = breadth.get("up", 0) / max(breadth["total"], 1) * 100
        latest["breadth"] = breadth_pct  # 让宽度真正参与 volume_signal
    state = str(latest["volume_state"])
    signal = volume_signal(latest)
    ratio = _num(latest.get("amount_ratio20"))
    slope = _num(latest.get("volume_slope5"))
    pctl = _num(latest.get("amount_ratio20_pctl"), 4)

    # 各分项缺数据时置 None，总分按可用分项重归一权重，不再让 NaN 变成满分。
    components: dict[str, float] = {}
    if pctl is not None:
        components["level"] = round(pctl * 100, 1)  # 近一年量能分位，最可解释
    elif ratio is not None:
        components["level"] = round(max(0.0, min(100.0, 50 + (ratio - 1) * 125)), 1)
    if slope is not None:
        components["trend"] = round(max(0.0, min(100.0, 50 + slope * 10)), 1)
    if breadth_pct is not None:
        components["breadth"] = round(breadth_pct, 1)
    if state in _STATE_SCORES:
        components["state"] = float(_STATE_SCORES[state])
    weights = {"level": 40, "trend": 25, "breadth": 20, "state": 15}
    used = {k: weights[k] for k in components}
    composite = round(sum(components[k] * used[k] for k in components) / sum(used.values()), 1) if components else None

    conclusion, risk = _conclusion(state, signal, latest.get("pct_chg"))
    if signal > SIGNAL_DEAD_ZONE:
        predicted = "up"
    elif signal < -SIGNAL_DEAD_ZONE:
        predicted = "down"
    else:
        predicted = None if state == "UNKNOWN" else "flat"
    market = {
        "date": str(pd.to_datetime(latest["date"]).date()),
        "close": _num(latest["close"]),
        "pct_chg": _num(latest.get("pct_chg")),
        "volume_state": state,
        "volume_state_label": _STATE_LABELS.get(state, state),
        "volume_signal": _num(signal, 4),
        "predicted_direction": predicted,
        "next_day_direction": predicted,
        "next_day_horizon": "next trading day",
        "amount": _num(latest.get("amount"), 0),
        "amount_ratio20": ratio,
        "amount_ratio60": _num(latest.get("amount_ratio60")),
        "amount_ratio20_pctl": pctl,
        "volume_slope5": slope,
        "breadth_pct": _num(breadth_pct),
        "score": {
            "total": composite,
            "level": components.get("level"),
            "trend": components.get("trend"),
            "breadth": components.get("breadth"),
            "state": components.get("state"),
            "weights": {"level": "40%", "trend": "25%", "breadth": "20%", "state": "15%"},
        },
        "conclusion": conclusion,
        "risk": risk,
    }
    history = []
    for _, row in features.tail(30).iterrows():
        history.append({
            "date": str(pd.to_datetime(row["date"]).date()),
            "close": _num(row["close"]),
            "pct_chg": _num(row.get("pct_chg")),
            "amount": _num(row.get("amount"), 0),
            "ratio20": _num(row.get("amount_ratio20")),
            "state": str(row.get("volume_state")),
        })
    return market, {"history": history}


def build_payload(bundle, industry_history: pd.DataFrame | None = None) -> dict:
    """纯计算，不做任何文件写入。industry_history 为逐日累积的行业快照。"""
    generated = datetime.now(timezone.utc).isoformat()
    if bundle.market.empty:
        return {
            "generated_at": generated, "status": "no_data", "stale": False,
            "warning": bundle.warning, "source": bundle.source,
            "market": {}, "breadth": bundle.breadth or {}, "chart": {"history": []},
            "indices": {}, "industries_top": [], "industries_bottom": [], "industries_score": [],
            "backtest": {}, "news": [], "policy_expectations": [],
            "review": {"observations": 0, "hit_rate": None},
        }
    features = market_features(bundle.market)
    market, chart = _market(features, bundle.breadth)
    industries = bundle.industries if bundle.industries is not None else pd.DataFrame()
    top = industries.sort_values("pct_chg", ascending=False) if not industries.empty else industries
    bottom = industries.sort_values("pct_chg", ascending=True) if not industries.empty else industries
    score_input = industry_history if industry_history is not None and not industry_history.empty else industries
    industries_score = []
    if score_input is not None and not score_input.empty and "pct_chg" in score_input:
        try:
            scored = score_industries(score_input)
            industries_score = _records(scored, ["industry", "score", "signal", "ret5", "ret20", "share_change5", "breadth", "pct_chg"], limit=10)
        except Exception:
            industries_score = []
    backtest = {}
    for horizon in (1, 5):
        result = backtest_volume(features, horizon=horizon)
        if result.observations:
            backtest[f"h{horizon}"] = asdict(result)
    return {
        "generated_at": generated, "status": "ok", "stale": False,
        "warning": bundle.warning, "source": bundle.source,
        "market": market, "breadth": bundle.breadth or {}, "chart": chart,
        "indices": bundle.indices or {},
        "industries_top": _records(top, ["industry", "pct_chg", "breadth", "amount"]),
        "industries_bottom": _records(bottom, ["industry", "pct_chg", "breadth", "amount"]),
        "industries_score": industries_score,
        "backtest": backtest,
        "news": bundle.news or [],
        "policy_expectations": bundle.policy_expectations or [],
        "review": {"observations": 0, "hit_rate": None},
    }


def write_outputs(payload: dict, output_dir: str | Path) -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    cleaned = _clean(payload)
    (out / "latest.json").write_text(json.dumps(cleaned, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (out / "report.md").write_text(render_markdown(cleaned), encoding="utf-8")
    (out / "index.html").write_text(_HTML, encoding="utf-8")


def build_report(bundle, output_dir: str | Path) -> dict:
    """兼容旧入口：构建 payload 并落盘。"""
    payload = build_payload(bundle)
    write_outputs(payload, output_dir)
    return payload


# ---------- Markdown ----------

def _fmt_pct(v, signed=True) -> str:
    if _num(v) is None:
        return "—"
    return f"{v:+.2f}%" if signed else f"{v:.2f}%"


def _fmt_money(v) -> str:
    value = _num(v)
    if value is None:
        return "—"
    if abs(value) >= 1e12:
        return f"{value / 1e12:.2f}万亿"
    if abs(value) >= 1e8:
        return f"{value / 1e8:.0f}亿"
    return f"{value:.0f}"


def _fmt(v, pattern="{:.2f}") -> str:
    return pattern.format(v) if _num(v) is not None else "—"


def render_markdown(x: dict) -> str:
    if x.get("status") != "ok" or not x.get("market"):
        return f"# 量潮罗盘\n\n暂无可验证行情数据，系统不会生成虚假结论。\n\n> {x.get('warning') or ''}\n"
    m, b, review = x["market"], x.get("breadth") or {}, x.get("review") or {}
    lines = [f"# 量潮罗盘｜{m['date']}", ""]
    if x.get("stale"):
        lines += [f"> ⚠️ {x.get('warning') or '本次数据源未更新，以下为最近一个已验证交易日的数据。'}", ""]
    elif x.get("warning"):
        lines += [f"> 数据源提示：{x['warning']}", ""]
    lines += [
        "## 核心结论", "",
        f"**{m['conclusion']}。**", "",
        f"- 明日方向（下一交易日）:{ {'up': '看多', 'down': '看空', 'flat': '中性'}.get(m.get('next_day_direction') or m.get('predicted_direction'), '不判断')}",
        f"风险提示:{m['risk']}。", "",
        "## 成交量与市场宽度", "",
        f"- 上证指数:{_fmt(m.get('close'))}({_fmt_pct(m.get('pct_chg'))})",
        f"- 两市成交额:{_fmt_money(m.get('amount'))}(20日均量比 {_fmt(m.get('amount_ratio20'))}, 近一年分位 {_fmt(m.get('amount_ratio20_pctl'), '{:.0%}')})",
        f"- 量能状态:{m.get('volume_state_label') or m.get('volume_state')} | 量能总分:{m['score'].get('total') if m['score'].get('total') is not None else '—'}/100",
        f"- 上涨/下跌/平盘:{b.get('up', '—')}/{b.get('down', '—')}/{b.get('flat', '—')}", "",
    ]
    if review.get("observations"):
        lines += [
            "## 模型复盘", "",
            f"- 已验证样本:{review['observations']}(待验证 {review.get('pending', 0)})",
            f"- 次日方向胜率:{_fmt(review.get('hit_rate'), '{:.1%}')} | 基线(始终看多):{_fmt(review.get('baseline_up_rate'), '{:.1%}')} | 超额:{_fmt(review.get('edge'), '{:+.1%}')}",
            "",
        ]
    backtest = (x.get("backtest") or {}).get("h5")
    if backtest and backtest.get("observations"):
        lines += [
            "## 历史回测(5日窗口, 窗口重叠样本相关, 仅供研究)", "",
            f"- 样本:{backtest['observations']} | 方向胜率:{_fmt(backtest.get('hit_rate'), '{:.1%}')} | 基线:{_fmt(backtest.get('baseline_hit_rate'), '{:.1%}')} | 超额:{_fmt(backtest.get('edge'), '{:+.1%}')}",
            "",
        ]
    if x.get("industries_score"):
        lines += ["## 行业量能评分 Top 10", ""]
        lines += [f"- {r['industry']}:{_fmt(r.get('score'), '{:.1f}')} 分｜{r.get('signal') or ''}" for r in x["industries_score"]]
        lines += [""]
    lines += ["## 行业涨跌 Top 10", ""]
    lines += [f"- {r['industry']}:{_fmt_pct(r.get('pct_chg'))}｜宽度 {_fmt(r.get('breadth'), '{:.0f}')}%" for r in x["industries_top"]] or ["- 行业数据暂不可用"]
    lines += ["", "## 行业涨跌 Bottom 10", ""]
    lines += [f"- {r['industry']}:{_fmt_pct(r.get('pct_chg'))}｜宽度 {_fmt(r.get('breadth'), '{:.0f}')}%" for r in x["industries_bottom"]] or ["- 行业数据暂不可用"]
    lines += ["", "## 政策/数据预期", ""]
    lines += [f"- {r['item']}:{r['expected_time']}｜{r['certainty']}" for r in x.get("policy_expectations", [])] or ["- 暂无"]
    lines += ["", "---", "", "所有结论为历史数据上的研究输出，不构成投资建议。", ""]
    return "\n".join(lines)


# ---------- HTML（纯静态单文件，无外部依赖） ----------

_HTML = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>量潮罗盘｜每日大盘研究</title>
<style>
:root{
  --bg:#f4f6f8;--card:#ffffff;--line:#e3e7eb;--grid:#eef0f2;
  --text:#1d2733;--muted:#69788a;
  --accent:#2d6cdf;--accent-soft:#eef4ff;--accent-line:#d9e6ff;
  /* A股语义色:红涨绿跌。图形标记色经过色觉障碍区分度校验 */
  --up:#e8737d;--dn:#0a6b45;
  --up-text:#c2414b;--dn-text:#0a6b45;
  --warn-bg:#fff8ed;--warn-line:#d97706;--warn-text:#79521c;
  --shadow:0 1px 2px rgba(29,39,51,.04);
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#14161a;--card:#1d2026;--line:#2b2f36;--grid:#262a31;
    --text:#e6e9ed;--muted:#93a0af;
    --accent:#6ea8ff;--accent-soft:#1d2738;--accent-line:#2b3c58;
    --up:#e36470;--dn:#0d744d;
    --up-text:#f0808a;--dn-text:#3dbd8d;
    --warn-bg:#2b2211;--warn-line:#d97706;--warn-text:#e8b56b;
    --shadow:none;
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
  font:14px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",Arial,sans-serif}
main{max-width:1280px;margin:auto;padding:24px 24px 52px}
.header{display:flex;align-items:flex-end;justify-content:space-between;gap:12px;
  border-bottom:1px solid var(--line);padding-bottom:16px;margin-bottom:18px;flex-wrap:wrap}
h1{font-size:26px;letter-spacing:1px;margin:0;font-weight:650}
h2{font-size:15px;margin:0 0 14px;font-weight:650}
.subtitle,.muted{color:var(--muted)}
.banner{background:var(--warn-bg);border:1px solid var(--warn-line);border-left-width:4px;
  color:var(--warn-text);border-radius:6px;padding:10px 14px;margin-bottom:16px}
.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:16px}
.card{grid-column:span 4;background:var(--card);border:1px solid var(--line);
  padding:18px;border-radius:8px;box-shadow:var(--shadow)}
.wide{grid-column:span 6}
.full{grid-column:span 12}
.span8{grid-column:span 8}
.conclusion{border-top:3px solid var(--accent)}
.conclusion strong{display:block;font-size:22px;line-height:1.4;margin:6px 0 10px}
.risk{background:var(--warn-bg);border-left:3px solid var(--warn-line);
  padding:9px 12px;color:var(--warn-text);border-radius:0 4px 4px 0}
.kpi{font-size:26px;font-weight:650}
.kpi small{font-size:13px;color:var(--muted);font-weight:400}
.num{font-variant-numeric:tabular-nums}
.row{display:flex;justify-content:space-between;align-items:center;gap:8px;
  border-bottom:1px solid var(--grid);padding:7px 0}
.row:last-child{border:0}
.pos{color:var(--up-text)}
.neg{color:var(--dn-text)}
.tag{display:inline-block;color:var(--accent);background:var(--accent-soft);
  border:1px solid var(--accent-line);padding:1px 8px;border-radius:4px;font-size:12px}
.chip{display:inline-block;padding:1px 8px;border-radius:4px;font-size:12px;
  background:var(--accent-soft);border:1px solid var(--accent-line);color:var(--accent)}
.meter{height:8px;border-radius:4px;background:var(--grid);overflow:hidden;margin:8px 0 2px}
.meter i{display:block;height:100%;border-radius:4px;background:var(--accent)}
.bar{flex:1;height:6px;border-radius:3px;background:var(--grid);overflow:hidden;min-width:60px}
.bar i{display:block;height:100%;background:var(--accent);border-radius:3px}
table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:8px 6px;border-bottom:1px solid var(--grid)}
th{color:var(--muted);font-weight:500;font-size:12px}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.indices{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.index-tile{border:1px solid var(--line);border-radius:6px;padding:10px 12px}
.index-tile .kpi{font-size:19px}
.news{padding:8px 0;border-bottom:1px solid var(--grid)}
.news:last-child{border:0}
.news a{color:var(--text);text-decoration:none}
.news a:hover{color:var(--accent)}
.news small{display:block;color:var(--muted)}
.empty{color:var(--muted);padding:10px 0}
.foot{color:var(--muted);font-size:12px;margin-top:20px;line-height:1.8}
.chart-wrap{position:relative}
.chart-wrap svg{display:block;width:100%;height:auto}
.tooltip{position:absolute;pointer-events:none;background:var(--card);border:1px solid var(--line);
  border-radius:6px;padding:8px 10px;font-size:12px;line-height:1.7;box-shadow:0 4px 14px rgba(0,0,0,.12);
  opacity:0;transition:opacity .08s;min-width:150px;z-index:5}
.tooltip b{font-variant-numeric:tabular-nums}
.tooltip .tt-row{display:flex;justify-content:space-between;gap:14px}
.legend{display:flex;gap:16px;color:var(--muted);font-size:12px;margin-top:6px;flex-wrap:wrap}
.legend i{display:inline-block;width:14px;height:0;border-top:2px solid var(--accent);vertical-align:middle;margin-right:5px}
.legend .k-up{border-top:6px solid var(--up);height:0;width:10px}
.legend .k-dn{border:1.5px solid var(--dn);height:4px;width:8px;background:none}
.legend .k-ma{border-top:2px solid var(--muted)}
@media(max-width:960px){
  .card,.wide,.full,.span8{grid-column:span 12}
  .header{display:block}
  .header .muted{margin-top:6px}
  main{padding:16px 12px 40px}
}
</style>
</head>
<body>
<main>
  <header class="header">
    <div>
      <h1>量潮罗盘</h1>
      <div class="subtitle">A股每日大盘｜成交量、市场宽度、行业轮动与政策预期</div>
    </div>
    <div id="updated" class="muted">正在读取数据…</div>
  </header>
  <div id="banner"></div>
  <div id="app" class="grid"><section class="card full">正在加载…</section></div>
  <div class="foot">
    系统按最近交易日生成；周末自动使用周五收盘数据。所有排名、评分与信号均为历史数据上的研究输出，不构成投资建议。<br>
    量能状态与分数说明:0—30 明显偏弱；30—50 存量博弈；50—70 中性；70—85 改善；85—100 过热/放量。分数不是买卖指令。
  </div>
</main>
<script>
'use strict';
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = v => (v == null || Number.isNaN(Number(v))) ? null : Number(v);
const pct = v => num(v) == null ? '—' : (num(v) > 0 ? '+' : '') + num(v).toFixed(2) + '%';
const cls = v => num(v) == null ? '' : (num(v) >= 0 ? 'pos' : 'neg');
const fx = (v, d=2) => num(v) == null ? '—' : num(v).toFixed(d);
const money = v => {
  const x = num(v);
  if (x == null) return '—';
  if (Math.abs(x) >= 1e12) return (x/1e12).toFixed(2) + '万亿';
  if (Math.abs(x) >= 1e8) return (x/1e8).toFixed(0) + '亿';
  return x.toFixed(0);
};
const safeLink = u => /^https?:\/\//i.test(String(u ?? '')) ? String(u) : '#';
const empty = '<div class="empty">当前数据源暂未返回内容，下一交易日自动重试。</div>';

function scoreRow(label, val, weight){
  const v = num(val);
  return `<div class="row"><span>${label} <small class="muted">${weight}</small></span>
    <span class="bar"><i style="width:${v == null ? 0 : Math.max(0, Math.min(100, v))}%"></i></span>
    <b class="num">${v == null ? '—' : v.toFixed(0)}</b></div>`;
}

function industryRows(xs){
  return (xs || []).map(x => `<tr><td>${esc(x.industry)}</td>
    <td class="num ${cls(x.pct_chg)}">${pct(x.pct_chg)}</td>
    <td class="num">${num(x.breadth) == null ? '—' : num(x.breadth).toFixed(0) + '%'}</td>
    <td class="num">${money(x.amount)}</td></tr>`).join('');
}

function scoreRows(xs){
  return (xs || []).map(x => `<tr><td>${esc(x.industry)}</td>
    <td class="num"><b>${fx(x.score, 1)}</b></td>
    <td>${esc(x.signal || '')}</td>
    <td class="num ${cls(x.ret5)}">${pct(x.ret5)}</td>
    <td class="num ${cls(x.ret20)}">${pct(x.ret20)}</td>
    <td class="num">${num(x.breadth) == null ? '—' : num(x.breadth).toFixed(0) + '%'}</td></tr>`).join('');
}

/* ---------- 30日量价走势图:纯内联 SVG,价格线 + 成交额柱(红涨实心/绿跌空心) ---------- */
function niceTicks(min, max, n){
  const span = max - min;
  if (!(span > 0)) return [min];
  const step0 = span / n, mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => span / s <= n) || step0;
  const ticks = [];
  for (let t = Math.ceil(min / step) * step; t <= max + 1e-9; t += step) ticks.push(t);
  return ticks;
}

function drawChart(hostId, hist){
  const host = document.getElementById(hostId);
  const H = (hist || []).filter(h => num(h.close) != null);
  if (H.length < 2){ host.innerHTML = empty; return; }
  const W = 860, PH = 210, GAP = 30, VH = 92, AX = 26, TOTAL = PH + GAP + VH + AX;
  const padL = 8, padR = 52;
  const plotW = W - padL - padR;
  const xs = i => padL + plotW * (i / (H.length - 1));
  const closes = H.map(h => num(h.close));
  let cMin = Math.min(...closes), cMax = Math.max(...closes);
  const cPad = (cMax - cMin) * 0.08 || cMax * 0.01;
  cMin -= cPad; cMax += cPad;
  const yc = v => PH - (v - cMin) / (cMax - cMin) * PH;
  const amounts = H.map(h => num(h.amount));
  const aMax = Math.max(...amounts.filter(v => v != null), 0) * 1.08 || 1;
  const ya = v => PH + GAP + VH - v / aMax * VH;
  const barW = Math.max(2, Math.min(18, plotW / H.length - 2));

  const endYLabel = Math.max(10, Math.min(PH - 4, yc(closes[closes.length - 1])));
  let g = '';
  for (const t of niceTicks(cMin, cMax, 4)){
    g += `<line x1="${padL}" y1="${yc(t)}" x2="${padL + plotW}" y2="${yc(t)}" stroke="var(--grid)" stroke-width="1"/>`;
    if (Math.abs(yc(t) - endYLabel) > 14){  // 与线末数值标签重叠的刻度不画文字
      g += `<text x="${W - padR + 6}" y="${yc(t) + 4}" font-size="11" fill="var(--muted)" class="num">${t >= 1000 ? Math.round(t).toLocaleString() : t.toFixed(0)}</text>`;
    }
  }
  const line = H.map((h, i) => `${i ? 'L' : 'M'}${xs(i).toFixed(1)},${yc(num(h.close)).toFixed(1)}`).join('');
  const area = line + `L${xs(H.length - 1).toFixed(1)},${PH}L${xs(0).toFixed(1)},${PH}Z`;
  let bars = '', ma = '';
  H.forEach((h, i) => {
    const a = num(h.amount);
    if (a != null){
      const up = num(h.pct_chg) == null || num(h.pct_chg) >= 0;
      const x = xs(i) - barW / 2, y = ya(a), hgt = Math.max(1, PH + GAP + VH - y);
      bars += up
        ? `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${hgt.toFixed(1)}" rx="2" fill="var(--up)"/>`
        : `<rect x="${(x + 0.75).toFixed(1)}" y="${y.toFixed(1)}" width="${(barW - 1.5).toFixed(1)}" height="${hgt.toFixed(1)}" rx="2" fill="none" stroke="var(--dn)" stroke-width="1.5"/>`;
    }
    const r = num(h.ratio20);
    if (a != null && r){
      const m = a / r;
      ma += `${ma ? 'L' : 'M'}${xs(i).toFixed(1)},${ya(Math.min(m, aMax)).toFixed(1)}`;
    }
  });
  let xlabels = '';
  const lab = Math.max(1, Math.round(H.length / 5));
  H.forEach((h, i) => {
    if (i % lab === 0 || i === H.length - 1){
      const anchor = i === 0 ? 'start' : (i === H.length - 1 ? 'end' : 'middle');
      xlabels += `<text x="${xs(i).toFixed(1)}" y="${TOTAL - 8}" font-size="11" fill="var(--muted)" text-anchor="${anchor}" class="num">${esc(h.date.slice(5))}</text>`;
    }
  });
  const last = H[H.length - 1];
  const endLabel = `<circle cx="${xs(H.length - 1).toFixed(1)}" cy="${yc(num(last.close)).toFixed(1)}" r="4" fill="var(--accent)" stroke="var(--card)" stroke-width="2"/>
    <text x="${W - padR + 6}" y="${endYLabel + 4}" font-size="12" font-weight="600" fill="var(--text)" class="num">${num(last.close).toFixed(0)}</text>`;

  host.innerHTML = `<svg viewBox="0 0 ${W} ${TOTAL}" role="img" aria-label="最近30个交易日上证指数收盘与两市成交额">
    ${g}
    <path d="${area}" fill="var(--accent)" opacity="0.08"/>
    <path d="${line}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
    ${bars}
    ${ma ? `<path d="${ma}" fill="none" stroke="var(--muted)" stroke-width="1.5" opacity="0.9"/>` : ''}
    ${endLabel}
    <line id="xh" x1="0" y1="0" x2="0" y2="${PH + GAP + VH}" stroke="var(--muted)" stroke-width="1" opacity="0"/>
    ${xlabels}
    <rect id="hit" x="${padL}" y="0" width="${plotW}" height="${PH + GAP + VH}" fill="transparent"/>
  </svg>
  <div class="tooltip" id="tt"></div>
  <div class="legend">
    <span><i></i>上证指数收盘</span>
    <span><i class="k-up"></i>成交额(当日上涨)</span>
    <span><i class="k-dn"></i>成交额(当日下跌)</span>
    <span><i class="k-ma"></i>20日均量</span>
  </div>`;

  const svg = host.querySelector('svg'), hit = host.querySelector('#hit'),
        xh = host.querySelector('#xh'), tt = host.querySelector('#tt');
  const stateLabel = {FLOOD:'洪峰放量', EXPAND:'温和放量', STABLE:'量能平稳', SHRINK:'温和缩量', DROUGHT:'地量', UNKNOWN:'数据不足'};
  function show(evt){
    const rect = svg.getBoundingClientRect();
    const mx = (evt.clientX - rect.left) / rect.width * W;
    const i = Math.max(0, Math.min(H.length - 1, Math.round((mx - padL) / plotW * (H.length - 1))));
    const h = H[i];
    xh.setAttribute('x1', xs(i)); xh.setAttribute('x2', xs(i)); xh.setAttribute('opacity', '0.5');
    tt.textContent = '';
    const mk = (label, value, klass) => {
      const row = document.createElement('div'); row.className = 'tt-row';
      const l = document.createElement('span'); l.className = 'muted'; l.textContent = label;
      const b = document.createElement('b'); b.textContent = value; if (klass) b.className = klass;
      row.append(l, b); tt.append(row);
    };
    const head = document.createElement('div'); head.style.fontWeight = '600'; head.textContent = h.date; tt.append(head);
    mk('收盘', fx(h.close), '');
    mk('涨跌', pct(h.pct_chg), cls(h.pct_chg));
    mk('成交额', money(h.amount), '');
    mk('量比(20日)', fx(h.ratio20), '');
    mk('量能状态', stateLabel[h.state] || h.state || '—', '');
    const left = Math.min(Math.max(evt.clientX - rect.left + 14, 4), rect.width - 170);
    tt.style.left = left + 'px';
    tt.style.top = Math.max(4, evt.clientY - rect.top - 20) + 'px';
    tt.style.opacity = '1';
  }
  hit.addEventListener('pointermove', show);
  hit.addEventListener('pointerleave', () => { tt.style.opacity = '0'; xh.setAttribute('opacity', '0'); });
}

/* ---------- 页面渲染 ---------- */
fetch('latest.json').then(r => r.json()).then(x => {
  document.getElementById('updated').textContent = '数据生成:' + new Date(x.generated_at).toLocaleString('zh-CN');
  if (x.stale || (x.status === 'ok' && x.warning)){
    document.getElementById('banner').innerHTML =
      `<div class="banner">${x.stale ? '⚠️ ' : ''}${esc(x.warning)}</div>`;
  }
  if (x.status !== 'ok'){
    document.getElementById('app').innerHTML =
      `<section class="card full"><h2>数据状态</h2><div class="risk">${esc(x.warning || '暂无数据')}</div></section>`;
    return;
  }
  const m = x.market || {}, ix = x.indices || {}, b = x.breadth || {}, s = m.score || {},
        rv = x.review || {}, bt = (x.backtest || {}).h5 || {};
  const dirLabel = {up: '看多', down: '看空', flat: '中性'};
  const total = num(b.up) != null && num(b.total) ? num(b.total) : null;
  const breadthBar = total
    ? `<div class="meter" title="上涨占比"><i style="width:${(num(b.up) / total * 100).toFixed(1)}%;background:var(--up)"></i></div>`
    : '';

  document.getElementById('app').innerHTML = `
  <section class="card span8 conclusion">
    <h2>最近交易日核心结论 <span class="tag num">${esc(m.date)}</span>
      <span class="chip">${esc(m.volume_state_label || m.volume_state || '')}</span></h2>
    <strong>${esc(m.conclusion)}</strong>
    <p>上证指数 <b class="num">${fx(m.close)}</b> <span class="num ${cls(m.pct_chg)}">${pct(m.pct_chg)}</span>
      ｜两市成交额 <b class="num">${money(m.amount)}</b>
      ${num(m.amount_ratio20_pctl) != null ? `<span class="muted">(近一年量能分位 ${(num(m.amount_ratio20_pctl) * 100).toFixed(0)}%)</span>` : ''}
      ｜明日方向(下一交易日):<b>${dirLabel[m.next_day_direction ?? m.predicted_direction] || '不判断'}</b></p>
    <div class="risk">风险提示:${esc(m.risk)}</div>
  </section>
  <section class="card">
    <h2>量能评分</h2>
    <div class="kpi num">${s.total ?? '—'}<small>/100</small></div>
    <div class="meter"><i style="width:${num(s.total) ?? 0}%"></i></div>
    ${scoreRow('量能水平', s.level, '40%')}
    ${scoreRow('量能趋势', s.trend, '25%')}
    ${scoreRow('市场宽度', s.breadth, '20%')}
    ${scoreRow('状态稳定性', s.state, '15%')}
  </section>
  <section class="card full">
    <h2>最近 30 个交易日｜价格与量能</h2>
    <div class="chart-wrap" id="chart"></div>
  </section>
  <section class="card">
    <h2>市场涨跌家数</h2>
    <div class="kpi num"><span class="pos">${b.up ?? '—'}</span> <small>涨</small> /
      <span class="neg">${b.down ?? '—'}</span> <small>跌</small></div>
    ${breadthBar}
    <div class="row"><span>平盘</span><b class="num">${b.flat ?? '—'}</b></div>
    <div class="row"><span>样本总数</span><b class="num">${b.total ?? '—'}</b></div>
    <div class="row"><span>上涨占比</span><b class="num">${fx(m.breadth_pct, 1)}%</b></div>
  </section>
  <section class="card">
    <h2>模型复盘(次日方向)</h2>
    ${rv.observations
      ? `<div class="kpi num">${(num(rv.hit_rate) * 100).toFixed(0)}<small>% 胜率｜${rv.observations} 样本</small></div>
        <div class="row"><span>基线(始终看多)</span><b class="num">${num(rv.baseline_up_rate) == null ? '—' : (num(rv.baseline_up_rate) * 100).toFixed(0) + '%'}</b></div>
        <div class="row"><span>相对基线超额</span><b class="num ${cls(rv.edge)}">${num(rv.edge) == null ? '—' : (num(rv.edge) > 0 ? '+' : '') + (num(rv.edge) * 100).toFixed(1) + '%'}</b></div>
        <div class="row"><span>近20次胜率</span><b class="num">${num(rv.recent_hit_rate) == null ? '—' : (num(rv.recent_hit_rate) * 100).toFixed(0) + '%'}</b></div>
        <div class="row"><span>待验证</span><b class="num">${rv.pending ?? 0}</b></div>`
      : `<div class="empty">复盘样本积累中:每个交易日记录一次预测,次日自动回填实际方向。</div>`}
  </section>
  <section class="card">
    <h2>历史回测(5日窗口)</h2>
    ${bt.observations
      ? `<div class="kpi num">${num(bt.hit_rate) == null ? '—' : (num(bt.hit_rate) * 100).toFixed(0)}<small>% 方向胜率｜${bt.observations} 样本</small></div>
        <div class="row"><span>基线胜率</span><b class="num">${num(bt.baseline_hit_rate) == null ? '—' : (num(bt.baseline_hit_rate) * 100).toFixed(0) + '%'}</b></div>
        <div class="row"><span>相对基线超额</span><b class="num ${cls(bt.edge)}">${num(bt.edge) == null ? '—' : (num(bt.edge) > 0 ? '+' : '') + (num(bt.edge) * 100).toFixed(1) + '%'}</b></div>
        <div class="row"><span>看多样本胜率</span><b class="num">${num(bt.long_hit_rate) == null ? '—' : (num(bt.long_hit_rate) * 100).toFixed(0) + '%'}</b></div>
        <div class="row"><span>看空样本胜率</span><b class="num">${num(bt.short_hit_rate) == null ? '—' : (num(bt.short_hit_rate) * 100).toFixed(0) + '%'}</b></div>
        <div class="muted" style="margin-top:8px;font-size:12px">5日窗口互相重叠,样本存在序列相关,结果偏乐观,仅供研究。</div>`
      : `<div class="empty">历史样本不足,暂不输出回测。</div>`}
  </section>
  <section class="card full">
    <h2>主要指数</h2>
    <div class="indices">
      ${Object.entries(ix).map(([k, v]) => `<div class="index-tile">
        <div class="muted">${esc(k)}</div>
        <div class="kpi num">${fx(v.close)}</div>
        <span class="num ${cls(v.pct_chg)}">${pct(v.pct_chg)}</span></div>`).join('') || empty}
    </div>
  </section>
  ${(x.industries_score || []).length ? `<section class="card full">
    <h2>行业量能评分 Top 10 <small class="muted">相对强度+份额变化+宽度,历史快照积累后逐步生效</small></h2>
    <table><tr><th>行业</th><th class="num">评分</th><th>信号</th><th class="num">5日</th><th class="num">20日</th><th class="num">宽度</th></tr>
    ${scoreRows(x.industries_score)}</table>
  </section>` : ''}
  <section class="card wide">
    <h2>最近交易日行业涨跌 Top 10</h2>
    ${(x.industries_top || []).length
      ? `<table><tr><th>行业</th><th class="num">涨跌</th><th class="num">上涨宽度</th><th class="num">成交额</th></tr>${industryRows(x.industries_top)}</table>`
      : empty}
  </section>
  <section class="card wide">
    <h2>最近交易日行业涨跌 Bottom 10</h2>
    ${(x.industries_bottom || []).length
      ? `<table><tr><th>行业</th><th class="num">涨跌</th><th class="num">上涨宽度</th><th class="num">成交额</th></tr>${industryRows(x.industries_bottom)}</table>`
      : empty}
  </section>
  <section class="card wide">
    <h2>政策与数据预期</h2>
    ${(x.policy_expectations || []).length
      ? x.policy_expectations.map(p => `<div class="news"><b>${esc(p.item)}</b>
          <small>预计:${esc(p.expected_time)}｜${esc(p.certainty)}｜依据:${esc(p.basis)}</small></div>`).join('')
      : empty}
  </section>
  <section class="card wide">
    <h2>新闻与地缘影响抽象</h2>
    ${(x.news || []).length
      ? x.news.map(n => `<div class="news"><a href="${esc(safeLink(n.link))}" target="_blank" rel="noreferrer noopener">
          <b>${esc(n.theme)}</b>｜${esc(n.title)}</a>
          <small>影响:${esc(n.impact)}｜置信度:${esc(n.confidence)}｜${esc(n.published)}</small></div>`).join('')
      : empty}
  </section>`;
  drawChart('chart', (x.chart || {}).history);
}).catch(() => {
  document.getElementById('app').innerHTML =
    '<section class="card full"><div class="risk">数据读取失败,请刷新页面。</div></section>';
});
</script>
<script>
// 盘中每 15 分钟重新读取一次 Actions 生成的数据；收盘后不再轮询。
setInterval(() => {
  const parts = new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai', weekday:'short', hour:'2-digit', minute:'2-digit', hour12:false}).formatToParts(new Date());
  const weekday = parts.find(x => x.type === 'weekday')?.value || '';
  const hour = Number(parts.find(x => x.type === 'hour')?.value || 0);
  const minute = Number(parts.find(x => x.type === 'minute')?.value || 0);
  const tradingDay = !['周六','周日','Sat','Sun'].includes(weekday);
  if (tradingDay && (hour > 9 || (hour === 9 && minute >= 0)) && (hour < 15 || (hour === 15 && minute === 0))) location.reload();
}, 15 * 60 * 1000);
</script>
</body>
</html>
'''
