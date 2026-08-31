"""Text/style-analysis provider boundary."""

def omakase(*args, **kwargs):
    from ..api import call_omakase_scenes
    return call_omakase_scenes(*args, **kwargs)


__all__ = ['omakase']
