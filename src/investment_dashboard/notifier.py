"""可选的飞书群机器人推送。Webhook 只从环境变量读取；推送失败不影响日报生成。"""

from __future__ import annotations

import os
import sys

import requests


def send_feishu(text: str) -> bool:
    webhook = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
    if not webhook:
        return False
    try:
        response = requests.post(webhook, json={"msg_type": "text", "content": {"text": text}}, timeout=15)
        response.raise_for_status()
        # 飞书对签名错误/限频等返回 HTTP 200 + 非零业务码，raise_for_status 抓不到。
        data = response.json() if response.content else {}
        code = data.get("code", data.get("StatusCode"))
        if code not in (0, None):
            print(f"飞书推送被拒绝：code={code} msg={data.get('msg') or data.get('StatusMessage')}", file=sys.stderr)
            return False
        return True
    except Exception as exc:
        # 异常文本可能带完整 webhook URL，脱敏后再进公开日志。
        print(f"飞书推送失败（不影响报告生成）：{type(exc).__name__}: {str(exc).replace(webhook, '***')}", file=sys.stderr)
        return False
