"""Install isolated dependencies across the design service boundaries."""
from Floor_engine_server import whole_home_design, design_store, design_schema, design_images, design_provider, design_structure, design_modeling, design_exports


def patch_design(monkeypatch, name, value):
    found = False
    for module in (whole_home_design, design_store, design_schema, design_images, design_provider, design_structure, design_modeling, design_exports):
        if hasattr(module, name):
            monkeypatch.setattr(module, name, value)
            found = True
    if not found:
        raise AttributeError(name)
