# -*- coding: utf-8 -*-
"""usage_stats 直接单测:计数累计、失败计数、线路区分、成本估算与未定价标记。

原先 record_usage/load_usage_summary 只被 test_non_color_features 间接覆盖;
拆分成独立模块后补齐最小直测。
"""
from Floor_engine_server import usage_stats


def _use_tmp_file(tmp_path, monkeypatch):
    monkeypatch.setattr(usage_stats, "_USAGE_STATS_FILE", str(tmp_path / "usage.json"))


def test_record_usage_accumulates_ok_and_fail(tmp_path, monkeypatch):
    _use_tmp_file(tmp_path, monkeypatch)
    usage_stats.record_usage("纯效果图 (生成全新空间)", "Nano Banana Pro", "google", True)
    usage_stats.record_usage("纯效果图 (生成全新空间)", "Nano Banana Pro", "google", True)
    usage_stats.record_usage("纯效果图 (生成全新空间)", "Nano Banana Pro", "google", False)
    summary = usage_stats.load_usage_summary()
    row = next(r for r in summary["rows"] if r["model"] == "Pro" and r["provider"] == "google")
    assert row["ok"] == 2
    assert row["fail"] == 1


def test_record_usage_separates_providers(tmp_path, monkeypatch):
    _use_tmp_file(tmp_path, monkeypatch)
    usage_stats.record_usage("纯效果图", "Nano Banana 2", "google", True)
    usage_stats.record_usage("纯效果图", "Nano Banana 2", "fal", True)
    rows = usage_stats.load_usage_summary()["rows"]
    providers = {r["provider"] for r in rows if r["model"] == "B2"}
    assert providers == {"google", "fal"}


def test_cost_estimate_uses_prices_and_marks_unpriced(tmp_path, monkeypatch):
    _use_tmp_file(tmp_path, monkeypatch)
    usage_stats.record_usage("纯效果图", "Nano Banana Pro", "google", True)
    usage_stats.record_usage("纯效果图", "Nano Banana 2", "google", True)
    summary = usage_stats.load_usage_summary(prices={"Pro": 0.5})
    pro = next(r for r in summary["rows"] if r["model"] == "Pro")
    b2 = next(r for r in summary["rows"] if r["model"] == "B2")
    assert pro["cost"] == 0.5
    assert b2["cost"] is None          # 未配单价 → None,不瞎猜
    assert summary["totals"]["cost"] == 0.5
    assert summary["totals"]["unpriced_ok"] == 1   # 有未定价行,总额只是下限


def test_load_summary_survives_corrupt_file(tmp_path, monkeypatch):
    f = tmp_path / "usage.json"
    f.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(usage_stats, "_USAGE_STATS_FILE", str(f))
    summary = usage_stats.load_usage_summary()
    assert summary["rows"] == []       # 坏文件不炸,当空账本


def test_record_usage_never_raises(tmp_path, monkeypatch):
    # 落盘路径不可写也不允许拖垮生图主流程(全程吞异常是刻意设计)
    monkeypatch.setattr(usage_stats, "_USAGE_STATS_FILE", "/nonexistent-dir/usage.json")
    usage_stats.record_usage("纯效果图", "Nano Banana Pro", "google", True)
