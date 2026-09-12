"""Patch provider dependencies across explicitly imported client boundaries."""
from Floor_engine_server import api
from Floor_engine_server.providers import shared, gemini, fal, inpaint, comfyui, analysis, diagnostics, dispatch


def patch_provider(monkeypatch, name, value):
    found = False
    for module in (api, shared, gemini, fal, inpaint, comfyui, analysis, diagnostics, dispatch):
        if hasattr(module, name):
            monkeypatch.setattr(module, name, value)
            found = True
    if not found:
        raise AttributeError(name)
