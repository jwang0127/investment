"""测试全部离线：封死 socket，任何漏 mock 的联网用例直接失败而不是偷偷联网。"""

import socket

import pytest


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise RuntimeError("network blocked in tests")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
