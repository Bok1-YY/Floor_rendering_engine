"""Patch test collaborators at both route and workflow boundaries."""
from Floor_engine_server import routes_jobs, job_service


def patch_jobs(monkeypatch, name, value):
    found = False
    for module in (routes_jobs, job_service):
        if hasattr(module, name):
            monkeypatch.setattr(module, name, value)
            found = True
    if not found:
        raise AttributeError(name)
