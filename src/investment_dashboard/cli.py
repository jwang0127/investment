from __future__ import annotations

import argparse
import json
from pathlib import Path

from .data_sources import fetch_free_data
from .journal import append_prediction, summarize_journal
from .news_sources import abstract_news, fetch_news
from .notifier import send_feishu
from .report import build_report


def main() -> None:
    parser = argparse.ArgumentParser(description="生成量潮罗盘每日大盘看板")
    parser.add_argument("--days", type=int, default=120)
    parser.add_argument("--output", default="public")
    args = parser.parse_args()
    bundle = fetch_free_data(args.days)
    bundle.news = fetch_news()
    bundle.news = abstract_news(bundle.news)
    payload = build_report(bundle, args.output)
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
