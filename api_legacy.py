"""Deprecated import path; new code imports api or providers directly."""
from .api import *
from . import api as _api

def __getattr__(name):
    return getattr(_api, name)
