# -*- coding: utf-8 -*-
"""running_model_status_text 纯函数：运行中任务的分模型徽章串。"""
from floor_engine.models import JobRecord, running_model_status_text


def _job(**kw):
    base = dict(job_id='j', display_name='n', ts='t', status='running')
    base.update(kw)
    return JobRecord(**base)


def test_both_queued():
    j = _job(model_filter='both', b2_stage='排队中', pro_stage='排队中')
    assert running_model_status_text(j) == 'B2 排队中 · Pro 排队中'


def test_b2_done_pro_queued():
    # B2 已出图 → ✓；Pro 还在等槽 → 排队中（正是跨任务流水线下"B2 已能接力"的态）
    j = _job(model_filter='both', b2_path='/x/b2.jpg', pro_stage='排队中')
    assert running_model_status_text(j) == 'B2 ✓ · Pro 排队中'


def test_both_generating():
    # 有真实阶段文案(渲染/思考)且未出图、不含"排队" → 生成中
    j = _job(model_filter='both', b2_stage='渲染中', pro_stage='思考')
    assert running_model_status_text(j) == 'B2 生成中 · Pro 生成中'


def test_empty_stage_counts_as_generating():
    # 刚抢到槽、尚无阶段文案 → 视作生成中（不是排队中）
    j = _job(model_filter='both')
    assert running_model_status_text(j) == 'B2 生成中 · Pro 生成中'


def test_single_b2_queued():
    j = _job(model_filter='b2', b2_stage='排队中')
    assert running_model_status_text(j) == 'B2 排队中'


def test_single_pro_done():
    j = _job(model_filter='pro', pro_path='/x/pro.jpg')
    assert running_model_status_text(j) == 'Pro ✓'
