# -*- coding: utf-8 -*-
"""GPT Image 2 provider 合同：fal 队列字段与 OpenAI multipart 不得回退/混用。"""
from __future__ import annotations

import base64
import io

from PIL import Image

from Floor_engine_server import api


def _png(path, color):
    Image.new('RGB', (16, 16), color).save(path)
    return str(path)


def _png_b64(color=(1, 2, 3)) -> str:
    buffer = io.BytesIO()
    Image.new('RGB', (16, 16), color).save(buffer, 'PNG')
    return base64.b64encode(buffer.getvalue()).decode('ascii')


def test_fal_gpt_image_2_edit_uses_actual_endpoint_and_custom_size(tmp_path, monkeypatch):
    first = _png(tmp_path / 'rgb.png', (10, 20, 30))
    second = _png(tmp_path / 'depth.png', (40, 50, 60))
    mask = _png(tmp_path / 'mask.png', (255, 255, 255))
    observed = {}

    monkeypatch.setattr(api, 'load_config', lambda: {'fal_api_key': 'fal-test'})

    def fake_queue(key, endpoint, payload, **kwargs):
        observed.update(key=key, endpoint=endpoint, payload=payload, kwargs=kwargs)
        return {'images': [{'url': 'data:image/png;base64,' + _png_b64()}]}, None

    monkeypatch.setattr(api, '_call_fal_queue_json', fake_queue)
    image, error = api.call_gpt_image_edit(
        '', 'preserve ERP', [first, second], mask_image_path=mask,
        provider='fal', endpoint='openai/gpt-image-2/edit', model_id='gpt-image-2',
        size='3840x1920', resume_handle={'request_id': 'resume-1'})
    assert error is None and image is not None
    assert observed['endpoint'] == 'openai/gpt-image-2/edit'
    payload = observed['payload']
    assert len(payload['image_urls']) == 2
    assert payload['image_size'] == {'width': 3840, 'height': 1920}
    assert payload['mask_image_url'].startswith('data:image/png;base64,')
    assert 'mask_url' not in payload
    assert payload['sync_mode'] is False and payload['output_format'] == 'png'
    assert observed['kwargs']['resume_handle'] == {'request_id': 'resume-1'}


def test_openai_gpt_image_2_edit_is_multipart_with_image_array(tmp_path, monkeypatch):
    first = _png(tmp_path / 'rgb.png', (10, 20, 30))
    second = _png(tmp_path / 'depth.png', (40, 50, 60))
    mask = _png(tmp_path / 'mask.png', (255, 255, 255))
    observed = {}

    class Response:
        status_code = 200
        text = ''

        @staticmethod
        def json():
            return {'data': [{'b64_json': _png_b64()}]}

    monkeypatch.setattr(api, 'load_config', lambda: {'openai_api_key': 'openai-test', 'proxy': ''})

    def fake_post(url, **kwargs):
        observed.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr(api._req, 'post', fake_post)
    image, error = api.call_gpt_image_edit(
        '', 'preserve ERP', [first, second], mask_image_path=mask,
        provider='openai', model_id='gpt-image-2-2026-04-21', size='3840x1920')
    assert error is None and image is not None
    assert observed['url'] == api.OPENAI_IMAGE_EDITS_URL
    assert observed['data']['model'] == 'gpt-image-2-2026-04-21'
    assert observed['data']['size'] == '3840x1920'
    assert [field for field, _ in observed['files']] == ['image[]', 'image[]', 'mask']
    assert observed['headers'] == {'Authorization': 'Bearer openai-test'}


def test_gpt_image_edit_never_cross_provider_fallback(tmp_path, monkeypatch):
    source = _png(tmp_path / 'rgb.png', (10, 20, 30))
    monkeypatch.setattr(api, 'load_config', lambda: {'fal_api_key': 'fal-test'})
    monkeypatch.setattr(api, '_call_fal_queue_json', lambda *args, **kwargs: (None, 'queue failed'))
    openai_called = False

    def forbidden_openai(*args, **kwargs):
        nonlocal openai_called
        openai_called = True
        raise AssertionError('fal 失败后不得自动跨 provider')

    monkeypatch.setattr(api._req, 'post', forbidden_openai)
    image, error = api.call_gpt_image_edit(
        '', 'preserve ERP', [source], provider='fal',
        endpoint='openai/gpt-image-2/edit', size='3840x1920')
    assert image is None and error == 'queue failed'
    assert openai_called is False


def test_fal_flux_canny_edit_binds_two_inputs_and_control_params(tmp_path, monkeypatch):
    rgb = tmp_path / 'rgb-padded.png'
    control = tmp_path / 'canny-padded.png'
    Image.new('RGB', (2048, 960), (120, 120, 120)).save(rgb)
    Image.new('RGB', (2048, 960), (0, 0, 0)).save(control)
    observed = {}
    monkeypatch.setattr(api, 'load_config', lambda: {'fal_api_key': 'fal-test'})

    def fake_queue(key, endpoint, payload, **kwargs):
        observed.update(key=key, endpoint=endpoint, payload=payload, kwargs=kwargs)
        return {'images': [{'url': 'data:image/png;base64,' + _png_b64()}]}, None

    monkeypatch.setattr(api, '_call_fal_queue_json', fake_queue)
    image, error = api.call_fal_flux_canny_edit(
        '', 'preserve every canny line', str(rgb), str(control),
        size='2048x960', seed=12345, strength=.62,
        control_lora_strength=1.25, num_inference_steps=32,
        guidance_scale=3.0, endpoint='fal-ai/flux-control-lora-canny/image-to-image')
    assert error is None and image is not None
    assert observed['endpoint'] == 'fal-ai/flux-control-lora-canny/image-to-image'
    payload = observed['payload']
    assert payload['image_size'] == {'width': 2048, 'height': 960}
    assert payload['image_url'].startswith('data:image/png;base64,')
    assert payload['control_lora_image_url'].startswith('data:image/png;base64,')
    assert payload['seed'] == 12345 and payload['strength'] == .62
    assert payload['control_lora_strength'] == 1.25
    assert payload['num_inference_steps'] == 32 and payload['guidance_scale'] == 3.0
    assert payload['num_images'] == 1 and payload['sync_mode'] is False
