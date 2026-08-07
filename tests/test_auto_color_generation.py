import asyncio
from types import SimpleNamespace

import numpy as np
from PIL import Image

from Floor_engine_server import config, records, routes_jobs, server_helpers, server_state
from Floor_engine_server.models import ensure_model_runs, new_job


def test_auto_color_workflow_scope():
    for mode in ('纯效果图 (生成全新空间)', '地板替换', '宠物友好', '参照模式', 'Omakase'):
        assert routes_jobs.supports_auto_color_match(mode)
    for mode in ('墙板模式', '自由创作 (自定义提示词/多图)', ''):
        assert not routes_jobs.supports_auto_color_match(mode)


def test_auto_color_config_defaults_on(monkeypatch):
    monkeypatch.setattr(config, 'load_config', lambda: {})
    assert config.get_auto_color_match_enabled() is True

    monkeypatch.setattr(config, 'load_config', lambda: {'auto_color_match_enabled': False})
    assert config.get_auto_color_match_enabled() is False


def test_auto_color_helper_changes_only_segmented_floor(tmp_path, monkeypatch):
    src = Image.new('RGB', (80, 60), (40, 80, 180))
    src.paste(Image.new('RGB', (80, 30), (190, 80, 45)), (0, 30))
    ref_path = tmp_path / 'floor.png'
    Image.new('RGB', (30, 30), (80, 150, 75)).save(ref_path)
    raw_path = tmp_path / 'raw.jpg'
    src.save(raw_path)

    mask = np.zeros((60, 80), dtype=bool)
    mask[30:, :] = True
    result = SimpleNamespace(
        mask=mask,
        confidence=0.91,
        status='ok',
        warnings=[],
        model='mobile_sam',
    )
    monkeypatch.setattr(routes_jobs, 'segment_floor', lambda *args, **kwargs: (src, result))

    out, metadata, err = routes_jobs._auto_color_match_generated(
        src, str(raw_path), str(ref_path))

    assert err == ''
    assert metadata['operation'] == 'auto_color_match'
    assert metadata['mask_coverage'] == 0.5
    assert np.array_equal(np.asarray(out)[:25], np.asarray(src)[:25])
    assert not np.array_equal(np.asarray(out)[40:], np.asarray(src)[40:])


def _run_generated_model(monkeypatch, *, color_success):
    job = new_job('test', '12:00', 'b2')
    job.workflow_mode = '纯效果图 (生成全新空间)'
    job.model_targets = ['b2']
    ensure_model_runs(job)
    api_image = Image.new('RGB', (24, 18), (160, 90, 50))
    corrected_image = Image.new('RGB', (24, 18), (90, 140, 70))
    writes = []

    monkeypatch.setattr(
        routes_jobs,
        'call_image_generate',
        lambda *args, **kwargs: (api_image, None, 'google'),
    )
    monkeypatch.setattr(routes_jobs, 'save_api_result_jpg', lambda *args, **kwargs: 'raw.jpg')
    monkeypatch.setattr(routes_jobs, 'save_api_result_png', lambda *args, **kwargs: 'corrected.png')
    monkeypatch.setattr(routes_jobs, 'record_usage', lambda *args, **kwargs: None)

    def write_record(*args, **kwargs):
        writes.append((args, kwargs))
        return 'raw-result-id' if 'API 原图' in args[1] else 'corrected-result-id'

    monkeypatch.setattr(routes_jobs, 'api_write_to_record', write_record)
    if color_success:
        monkeypatch.setattr(
            routes_jobs,
            '_auto_color_match_generated',
            lambda *args: (
                corrected_image,
                {'operation': 'auto_color_match', 'variant': 'auto_color_corrected'},
                '',
            ),
        )
    else:
        monkeypatch.setattr(
            routes_jobs,
            '_auto_color_match_generated',
            lambda *args: (None, {'mask_confidence': 0.2}, '未识别到可靠地板区域'),
        )
    monkeypatch.setattr(server_state, 'model_semaphores', {'b2': asyncio.Semaphore(1)})

    result = asyncio.run(routes_jobs._generate_one_model(
        job,
        'model-id',
        'prompt',
        'b2',
        'Nano Banana 2',
        api_key='key',
        pnp='floor.png',
        ims='4K',
        ar='4:3',
        rp=None,
        sref=None,
        bevel_ref=None,
        jpt='record.json',
        rid='record-id',
        should_cancel=lambda: False,
        auto_color_match=True,
    ))
    return job, writes, result


def test_generate_keeps_raw_and_makes_corrected_current(monkeypatch):
    job, writes, (path, err) = _run_generated_model(monkeypatch, color_success=True)

    assert err == ''
    assert path == 'corrected.png'
    assert job.b2_paths == ['raw.jpg', 'corrected.png']
    assert job.b2_path == 'corrected.png'
    run = job.model_runs['b2']
    assert run['base_path'] == 'raw.jpg'
    assert run['settings']['auto_color_status'] == 'done'
    assert len(writes) == 2
    assert writes[0][0][1].endswith('API 原图')
    assert writes[1][0][1].endswith('自动校色')
    assert writes[1][0][6] == 'raw-result-id'


def test_auto_color_failure_is_nonfatal_and_keeps_raw(monkeypatch):
    job, writes, (path, err) = _run_generated_model(monkeypatch, color_success=False)

    assert err == ''
    assert path == 'raw.jpg'
    assert job.b2_paths == ['raw.jpg']
    assert job.b2_path == 'raw.jpg'
    assert job.model_runs['b2']['status'] == 'done'
    assert job.model_runs['b2']['settings']['auto_color_status'] == 'failed'
    assert len(writes) == 1


def test_job_view_exposes_api_original(tmp_path, monkeypatch):
    raw = tmp_path / 'raw.jpg'
    corrected = tmp_path / 'corrected.png'
    raw.write_bytes(b'raw')
    corrected.write_bytes(b'corrected')
    monkeypatch.setattr(server_helpers, 'MAIN_OUTPUT_DIR', str(tmp_path))

    job = new_job('test', '12:00', 'b2')
    job.model_targets = ['b2']
    job.b2_paths = [str(raw), str(corrected)]
    job.b2_idx = 1
    job.b2_path = str(corrected)
    ensure_model_runs(job)
    job.model_runs['b2']['base_path'] = str(raw)
    job.model_runs['b2']['settings'] = {
        'auto_color_match_enabled': True,
        'auto_color_status': 'done',
        'auto_color_error': '',
    }

    view = server_helpers.job_view(job)['model_runs']['b2']
    assert view['url'].endswith('/corrected.png')
    assert view['api_original_url'].endswith('/raw.jpg')
    assert view['auto_color_status'] == 'done'


def test_record_result_can_link_to_api_original(tmp_path):
    record_path = tmp_path / 'record.json'
    records.save_records_file(str(record_path), [{'id': 'r1', 'results': []}])
    image = Image.new('RGB', (12, 10), (80, 90, 100))

    result_id = records.api_write_to_record(
        image,
        'Nano Banana Pro · 自动校色',
        str(record_path),
        'r1',
        metadata={'variant': 'auto_color_corrected'},
        source_result_id='raw-id',
        comment='自动校色 (12×10)',
    )

    entry = records.load_records_file(str(record_path))[0]['results'][0]
    assert result_id == entry['result_id']
    assert entry['source_result_id'] == 'raw-id'
    assert entry['comment'] == '自动校色 (12×10)'
