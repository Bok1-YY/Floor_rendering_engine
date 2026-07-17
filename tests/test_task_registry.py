# -*- coding: utf-8 -*-
"""TaskRegistry 单测:trim 只删终态、cancel-all 代次、on_evict/on_persist 回调、
reserve 预留槽、newest_first 顺序语义 —— 这些是 jobs/previews/inpaints 三套
原实现收敛后的公共契约,任何行为漂移都会破坏队列/预览/修补的既有语义。
"""
from Floor_engine_server.task_registry import TaskRegistry


def _reg(**kw):
    defaults = dict(max_entries=5, is_terminal=lambda e: e.get('status') in ('done', 'failed'))
    defaults.update(kw)
    return TaskRegistry('t', **defaults)


def test_add_get_pop_roundtrip():
    r = _reg()
    r.add('a', {'status': 'running'})
    assert r.get('a') == {'status': 'running'}
    assert r.pop('a') == {'status': 'running'}
    assert r.get('a') is None
    assert r.pop('missing') is None


def test_trim_only_removes_oldest_terminal():
    r = _reg(max_entries=3)
    r.add('old-done', {'status': 'done'})
    r.add('running', {'status': 'running'})
    r.add('new-done', {'status': 'done'})
    r.add('x', {'status': 'running'})   # 超限 1 → 只删最旧终态 old-done
    assert r.get('old-done') is None
    assert r.get('new-done') is not None
    assert r.get('running') is not None


def test_trim_never_removes_inflight_even_over_limit():
    r = _reg(max_entries=2)
    for i in range(4):
        r.add(f'run-{i}', {'status': 'running'})
    assert len(r) == 4   # 终态不足时容忍超限,in-flight 永不删


def test_trim_reserve_frees_extra_slot():
    r = _reg(max_entries=3)
    for i in range(3):
        r.add(f'done-{i}', {'status': 'done'})
    r.trim(reserve=1)
    assert len(r) == 2
    assert r.get('done-0') is None   # 删的是最旧的


def test_trim_evict_callback_and_cancel_cleanup():
    evicted = []
    r = _reg(max_entries=1, on_evict=evicted.append)
    r.add('a', {'status': 'done'})
    r.request_cancel('a')
    r.add('b', {'status': 'running'})   # 挤掉 a
    assert evicted == [{'status': 'done'}]
    assert not r.is_cancelled('a')      # 取消标记随条目清理


def test_cancel_single_and_clear():
    r = _reg()
    r.request_cancel('a')
    assert r.is_cancelled('a')
    r.clear_cancelled('a')
    assert not r.is_cancelled('a')


def test_cancel_all_generation_semantics():
    r = _reg()
    old_gen = r.generation
    assert not r.is_cancelled('a', old_gen)
    r.bump_generation()
    assert r.is_cancelled('a', old_gen)          # 捕获旧代次的 in-flight 任务自行退出
    assert not r.is_cancelled('a', r.generation)  # 新任务捕获新代次,不受影响
    assert not r.is_cancelled('a', None)          # 不带代次 = 只看单任务取消集合


def test_newest_first_snapshot_and_replace():
    r = _reg(newest_first=True)
    r.add('first', {'status': 'running', 'n': 1})
    r.add('second', {'status': 'running', 'n': 2})
    snap = r.snapshot()
    assert [e['n'] for e in snap] == [2, 1]       # 最新在前(原 insert(0) 语义)
    r.replace([('x', {'n': 10}), ('y', {'n': 20})])   # 公开顺序:最新在前
    assert [e['n'] for e in r.snapshot()] == [10, 20]


def test_update_fields_and_view():
    r = _reg()
    r.add('a', {'status': 'running', 'stage': ''})
    assert r.update_fields('a', stage='生成中') is True
    assert r.update_fields('missing', stage='x') is False
    snap = r.view('a')
    snap['stage'] = '篡改'
    assert r.get('a')['stage'] == '生成中'   # view 是拷贝,不影响本体
    assert r.view('missing') is None


def test_persist_passes_snapshot_and_noop_without_callback():
    seen = []
    r = _reg(on_persist=seen.append, newest_first=True)
    r.add('a', {'status': 'running', 'n': 1})
    r.add('b', {'status': 'running', 'n': 2})
    r.persist()
    assert [e['n'] for e in seen[0]] == [2, 1]
    _reg().persist()   # 无 on_persist → 空操作不抛


def test_locked_composite_with_trim():
    r = _reg(max_entries=2)
    r.add('done-old', {'status': 'done'})
    r.add('done-new', {'status': 'done'})
    with r.locked() as entries:
        r.trim_locked(reserve=1)          # locked 块内允许免锁 trim
        assert 'done-old' not in entries  # reserve=1 → 限额 1 → 超限 1 → 清最旧终态
        assert 'done-new' in entries
        entries['new'] = {'status': 'running'}
    assert r.get('new') is not None
