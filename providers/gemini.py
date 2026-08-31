"""Stable Gemini provider boundary; implementations remain compatible through api.py."""

def generate(*args, **kwargs):
    from ..api import call_gemini_generate
    return call_gemini_generate(*args, **kwargs)


def edit(*args, **kwargs):
    from ..api import call_gemini_edit
    return call_gemini_edit(*args, **kwargs)


def analyze_style(*args, **kwargs):
    from ..api import analyze_style_image
    return analyze_style_image(*args, **kwargs)


__all__ = ['analyze_style', 'edit', 'generate']
