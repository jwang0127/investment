from datetime import date

from investment_dashboard.news_sources import _clean_title, abstract_news, fetch_news, policy_expectations


def test_policy_pmi_uses_real_month_end():
    rows = policy_expectations(date(2026, 6, 26))
    pmi = rows[0]
    assert "2026-06-30" in pmi["expected_time"]  # 6 月没有 31 号
    assert "6月" in pmi["item"]


def test_policy_lpr_not_skipped_before_20th():
    rows = policy_expectations(date(2026, 7, 10))
    lpr = rows[1]
    assert "2026-07-20" in lpr["expected_time"]  # 本月 20 日未过，最近一次就在本月
    rows_late = policy_expectations(date(2026, 7, 25))
    assert "2026-08-20" in rows_late[1]["expected_time"]


def test_policy_trade_data_labels_current_month():
    rows = policy_expectations(date(2026, 7, 10))
    trade = rows[2]
    assert trade["item"].startswith("7月")  # 下月上旬公布的是本月数据
    assert "2026-08" in trade["expected_time"]


def test_clean_title_strips_media_suffix():
    assert _clean_title("央行宣布降准0.5个百分点 - 新浪财经") == "央行宣布降准0.5个百分点"
    assert _clean_title("无后缀标题") == "无后缀标题"


def test_abstract_news_classification():
    rows = [
        {"title": "央行宣布降准释放流动性 - 某媒体", "category": "政策"},
        {"title": "证监会发布新监管规则", "category": "政策"},
        {"title": "半导体产业迎来新机遇", "category": "市场"},
        {"title": "中东地缘局势升级", "category": "市场"},
        {"title": "普通市场消息", "category": "市场"},
    ]
    result = abstract_news(rows)
    themes = [r["theme"] for r in result]
    assert themes == ["流动性", "监管政策", "产业主题", "地缘政治", "市场"]
    assert result[0]["title"] == "央行宣布降准释放流动性"  # 展示用标题去掉媒体后缀
    assert result[0]["raw_title"].endswith("某媒体")


def test_fetch_news_offline_degrades_with_warning():
    warnings: list[str] = []
    rows = fetch_news(date(2026, 7, 24), warnings)  # conftest 已封死网络
    assert rows == []
    assert len(warnings) == 2  # 两条 RSS 查询各报一次失败
    assert all("抓取失败" in w for w in warnings)
