"""每日预测留痕：让模型能在未来复盘，而不是只保留最后一次结论。"""

from __future__ import annotations

import json
from pathlib import Path


def append_prediction(path: str | Path, payload: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def summarize_journal(path: str | Path) -> dict:
    target = Path(path)
    if not target.exists():
        return {"observations": 0, "hit_rate": None}
    rows = []
    for line in target.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    checked = [r for r in rows if r.get("actual_direction") in ("up", "down") and r.get("predicted_direction") in ("up", "down")]
    hits = sum(r["actual_direction"] == r["predicted_direction"] for r in checked)
    return {"observations": len(checked), "hit_rate": round(hits / len(checked), 4) if checked else None}
