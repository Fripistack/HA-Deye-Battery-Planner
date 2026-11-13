"""Laad de rekenmodules los, zonder Home Assistant."""
import importlib.util
import sys
import types
from pathlib import Path

MAP = Path(__file__).resolve().parent.parent / "custom_components" / "batterij_planner_deye"


def laad(naam: str):
    spec = importlib.util.spec_from_file_location(f"bpd_{naam}", MAP / f"{naam}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def laad_profiel():
    """profiel.py met minimale stand-ins voor de Home Assistant-imports."""
    if "homeassistant" not in sys.modules:
        stubs = {
            "homeassistant": {},
            "homeassistant.core": {"HomeAssistant": object},
            "homeassistant.helpers": {},
            "homeassistant.helpers.storage": {"Store": object},
        }
        for naam, attrs in stubs.items():
            m = types.ModuleType(naam)
            m.__dict__.update(attrs)
            sys.modules[naam] = m
    pakket = types.ModuleType("bpd")
    pakket.__path__ = [str(MAP)]
    sys.modules["bpd"] = pakket
    const = types.ModuleType("bpd.const")
    const.DOMAIN = "batterij_planner_deye"
    sys.modules["bpd.const"] = const
    spec = importlib.util.spec_from_file_location("bpd.profiel", MAP / "profiel.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod
