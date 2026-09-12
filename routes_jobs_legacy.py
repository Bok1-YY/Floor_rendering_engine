"""Deprecated job-route import path; use routes_jobs or job_service."""
from .routes_jobs import *
from . import routes_jobs as _routes

def __getattr__(name):
    return getattr(_routes, name)
