"""可选的飞书群机器人推送。Webhook 只从环境变量读取。"""

from __future__ import annotations

import os

import requests


def send_feishu(text: str) -> bool:
    webhook = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
    if not webhook:
        return False
    response = requests.post(webhook, json={"msg_type": "text", "content": {"text": text}}, timeout=15)
    response.raise_for_status()
    return True
