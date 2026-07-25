from __future__ import annotations

import argparse
import json
from pathlib import Path

from .data_sources import fetch_free_data
from .journal import append_prediction, load_last_success, summarize_journal
from .news_sources import abstract_news, fetch_news, policy_expectations
from .notifier import send_feishu
from .report import build_report


def main() -> None:
    parser = argparse.ArgumentParser(description="生成量潮罗盘每日大盘看板")
    parser.add_argument("--days", type=int, default=120)
    parser.add_argument("--output", default="public")
    args = parser.parse_args()
    bundle = fetch_free_data(args.days)
    as_of = __import__("pandas").to_datetime(bundle.market["date"]).max().date() if not bundle.market.empty else None
    bundle.news = fetch_news(as_of)
    bundle.news = abstract_news(bundle.news)
    bundle.policy_expectations = policy_expectations(as_of) if as_of else []
    payload = build_report(bundle, args.output)
    if payload["status"] == "no_data":
        previous = load_last_success(Path("data") / "prediction_journal.jsonl")
        if previous:
            previous["status"] = "ok"
            previous["stale"] = True
            previous["warning"] = f"本次数据源未更新，沿用最近已验证交易日 {previous['market'].get('date')} 数据：{bundle.warning}"
            Path(args.output, "latest.json").write_text(json.dumps(previous, ensure_ascii=False, indent=2), encoding="utf-8")
            payload = previous
    journal = Path("data") / "prediction_journal.jsonl"
    append_prediction(journal, payload)
    payload["journal"] = summarize_journal(journal)
    payload["review"] = payload["journal"]
    Path(args.output, "latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if payload["status"] == "ok":
        send_feishu(
            f"量潮罗盘\n日期：{payload.get('date')}\n量能状态：{payload.get('volume_state')}\n"
            f"量能分数：{payload.get('volume_signal')}\n数据源：{payload.get('source')}\n"
            f"历史复盘样本：{payload['journal']['observations']}"
        )
    print(f"status={payload['status']} source={payload['source']} output={args.output}")


if __name__ == "__main__":
    main()
