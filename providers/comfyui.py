"""Stable ComfyUI provider boundary."""

def inpaint(*args, **kwargs):
    from ..api import call_comfyui_inpaint
    return call_comfyui_inpaint(*args, **kwargs)


__all__ = ['inpaint']
