"""Laad de rekenmodules los, zonder Home Assistant."""
import importlib.util
import sys
from pathlib import Path

MAP = Path(__file__).resolve().parent.parent / "custom_components" / "batterij_planner_deye"


def laad(naam: str):
    spec = importlib.util.spec_from_file_location(f"bpd_{naam}", MAP / f"{naam}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod
