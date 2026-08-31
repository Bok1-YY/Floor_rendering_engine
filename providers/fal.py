"""Stable Fal provider boundary."""

def generate(*args, **kwargs):
    from ..api import call_fal_generate
    return call_fal_generate(*args, **kwargs)


def queue(*args, **kwargs):
    from ..api import _call_fal_queue_json
    return _call_fal_queue_json(*args, **kwargs)


__all__ = ['generate', 'queue']
