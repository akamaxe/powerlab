"""Project persistence: ``.psn`` files (UTF-8 JSON).

A project is the network plus everything around it that is not physics: the
parametric bindings, the drawing layout, the study cases and the view state.
Keeping those in parallel sections rather than inside the component dataclasses
is what lets the core stay Qt-free and the file stay readable in a diff.

Only the ``network`` section is required. A file without ``layout`` still opens;
:func:`auto_layout` produces a left-to-right ladder, which suits radial
single-line diagrams far better than a spring layout.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields as dc_fields
from pathlib import Path
from typing import Any

import networkx as nx

from .cases import Case
from .components import Bus, TYPE_REGISTRY, buses_of, is_series
from .errors import ValidationError
from .network import Network
from .params import Parameters

FORMAT = "psa-project"
VERSION = 2


def _v1_to_v2(data: dict) -> dict:
    """Version 1 kept one options dict per study; version 2 has study cases."""
    data = dict(data)
    studies = data.pop("studies", None) or {}
    data["cases"] = [{"id": key, "study": key, "options": dict(opts or {})}
                     for key, opts in studies.items()]
    data["version"] = 2
    return data


#: Migrations from an older file version to the next one.
MIGRATIONS: dict[int, Any] = {1: _v1_to_v2}

GRID = 10


# --------------------------------------------------------------------------- #
# Project
# --------------------------------------------------------------------------- #


@dataclass
class Project:
    """Everything a ``.psn`` file holds."""

    network: Network = field(default_factory=Network)
    params: Parameters = field(default_factory=Parameters)
    layout: dict[str, dict] = field(default_factory=dict)
    wires: dict[str, list[list[float]]] = field(default_factory=dict)
    cases: list[Case] = field(default_factory=list)
    view: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    path: str | None = None
    grid: int = GRID

    def ensure_layout(self) -> None:
        """Fill in positions for anything that does not have one yet."""
        missing = [o.id for o in self.network if o.id not in self.layout]
        if missing:
            self.layout.update(auto_layout(self.network, skip=set(self.layout)))

    def apply_parameters(self, run_script: bool = False):
        """Evaluate the parametric bindings into the network.

        The linked script is only executed when ``run_script`` is true, which
        callers must tie to an explicit user action. Otherwise the variables it
        produced last time are reused; right after opening there are none, so a
        binding that needs them fails and keeps the value saved in the file.
        """
        issues = []
        if run_script and self.params.script_path:
            base = Path(self.path).parent if self.path else None
            issues = list(self.params.reload_script(base))
        return issues + self.params.apply(self.network)


# --------------------------------------------------------------------------- #
# Complex helpers
# --------------------------------------------------------------------------- #


def _enc(value: Any) -> Any:
    if isinstance(value, complex):
        return [value.real, value.imag]
    return value


def _dec(value: Any, like: Any) -> Any:
    if isinstance(like, complex):
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return complex(float(value[0]), float(value[1]))
        return complex(value)
    return value


# --------------------------------------------------------------------------- #
# Serialisation
# --------------------------------------------------------------------------- #


def network_to_dict(net: Network) -> dict:
    buses = []
    for b in net.buses.values():
        buses.append({f.name: _enc(getattr(b, f.name)) for f in dc_fields(b)})
    comps = []
    for c in net.components.values():
        d = {"type": c.TYPE}
        d.update({f.name: _enc(getattr(c, f.name)) for f in dc_fields(c)})
        comps.append(d)
    return {
        "s_base_mva": net.s_base_mva,
        "f_hz": net.f_hz,
        "base_ref": {"bus": net.base_ref_bus, "u_base_kv": net.base_ref_u_kv},
        "buses": buses,
        "components": comps,
    }


def network_from_dict(data: dict) -> Network:
    ref = data.get("base_ref") or {}
    net = Network(
        s_base_mva=float(data.get("s_base_mva", 10.0)),
        f_hz=float(data.get("f_hz", 50.0)),
        base_ref_bus=ref.get("bus", ""),
        base_ref_u_kv=float(ref.get("u_base_kv", 0.0) or 0.0),
    )
    for raw in data.get("buses", []):
        net.add(_build(Bus, raw))
    for raw in data.get("components", []):
        type_name = raw.get("type")
        cls = TYPE_REGISTRY.get(type_name)
        if cls is None or cls is Bus:
            raise ValidationError(
                f"Unknown component type '{type_name}' in the project file.")
        net.add(_build(cls, raw))
    return net


def _build(cls, raw: dict):
    known = {f.name: f for f in dc_fields(cls)}
    kwargs = {}
    defaults = cls(id=raw.get("id", "?"))
    for name, f in known.items():
        if name not in raw:
            continue
        kwargs[name] = _dec(raw[name], getattr(defaults, name))
    kwargs["id"] = raw["id"]
    return cls(**kwargs)


def project_to_dict(proj: Project) -> dict:
    p = proj.params
    out = {
        "format": FORMAT,
        "version": VERSION,
        "meta": dict(proj.meta),
        "network": network_to_dict(proj.network),
        "parameters": {
            "script_path": p.script_path,
            "project_vars": [list(v) for v in p.project_vars],
            "bindings": dict(p.bindings),
        },
        "layout": {
            "grid": proj.grid,
            "items": proj.layout,
            "wires": proj.wires,
        },
        "cases": [c.to_dict() for c in proj.cases],
        "view": proj.view,
    }
    if proj.network.meta:
        out["meta"] = {**proj.network.meta, **out["meta"]}
    return out


def project_from_dict(data: dict, path: str | None = None) -> Project:
    fmt = data.get("format")
    if fmt != FORMAT:
        raise ValidationError(
            f"Not a PowerLab project file (found format '{fmt}').")
    version = int(data.get("version", 1))
    while version < VERSION:
        migrate = MIGRATIONS.get(version)
        if migrate is None:
            raise ValidationError(
                f"Project file version {version} cannot be upgraded to "
                f"{VERSION}.")
        data = migrate(data)
        version = int(data.get("version", version + 1))
    if version > VERSION:
        raise ValidationError(
            f"This project was written by a newer version of PowerLab "
            f"(file version {version}, this build understands {VERSION}).")

    if "network" not in data:
        raise ValidationError("The project file has no 'network' section.")

    net = network_from_dict(data["network"])
    meta = dict(data.get("meta") or {})
    net.meta = dict(meta)

    raw_params = data.get("parameters") or {}
    params = Parameters(
        script_path=raw_params.get("script_path"),
        project_vars=[(str(a), str(b)) for a, b in
                      (raw_params.get("project_vars") or [])],
        bindings={str(k): str(v) for k, v in
                  (raw_params.get("bindings") or {}).items()},
    )

    raw_layout = data.get("layout") or {}
    known = {"format", "version", "meta", "network", "parameters", "layout",
             "cases", "view"}
    unknown = {k: v for k, v in data.items() if k not in known}
    if unknown:
        meta["_unknown"] = unknown

    return Project(
        network=net,
        params=params,
        layout=dict(raw_layout.get("items") or {}),
        wires={k: [list(pt) for pt in v]
               for k, v in (raw_layout.get("wires") or {}).items()},
        cases=[Case.from_dict(c) for c in (data.get("cases") or [])],
        view=dict(data.get("view") or {}),
        meta=meta,
        path=path,
        grid=int(raw_layout.get("grid", GRID)),
    )


# --------------------------------------------------------------------------- #
# File I/O
# --------------------------------------------------------------------------- #


def save(proj: Project, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(project_to_dict(proj), indent=2, ensure_ascii=False)
    # LF on every platform, so a project file diffs the same everywhere.
    p.write_text(text + "\n", encoding="utf-8", newline="\n")
    proj.path = str(p)
    return p


def load(path: str | Path, apply_params: bool = True,
         run_script: bool = False) -> Project:
    """Read a project. The linked variable script is NOT run unless asked to."""
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    proj = project_from_dict(data, str(p))
    if apply_params and (proj.params.bindings or proj.params.script_path):
        proj.apply_parameters(run_script=run_script)
    return proj


# --------------------------------------------------------------------------- #
# Automatic layout
# --------------------------------------------------------------------------- #


def auto_layout(net: Network, skip: set[str] | None = None,
                grid: int = GRID) -> dict[str, dict]:
    """Left-to-right ladder placement, snapped to the grid.

    Buses are laid out by their distance from the base reference bus, which
    turns a radial feeder into the familiar horizontal chain. Series branches
    sit midway between their buses; node shunts hang below theirs.
    """
    skip = skip or set()
    out: dict[str, dict] = {}
    step_x, step_y = 18 * grid, 9 * grid

    g = net.graph()
    root = net.base_ref_bus if net.base_ref_bus in g else (
        next(iter(g.nodes), None))
    if root is None:
        for i, bus in enumerate(net.buses.values()):
            out[bus.id] = {"x": 0, "y": i * step_y, "orientation": "V",
                           "length": 6 * grid}
        return {k: v for k, v in out.items() if k not in skip}

    depth = {}
    for comp in nx.connected_components(g):
        sub = g.subgraph(comp)
        seed = root if root in sub else min(sub.nodes)
        depth.update(nx.single_source_shortest_path_length(sub, seed))
    for n in g.nodes:
        depth.setdefault(n, 0)

    bus_half = 3 * grid          # half the default bar length
    lanes: dict[int, int] = {}
    pos: dict[str, tuple[float, float]] = {}
    for bus_id in net.buses:
        d = depth.get(bus_id, 0)
        lane = lanes.get(d, 0)
        lanes[d] = lane + 1
        pos[bus_id] = (d * step_x, lane * step_y)
        out[bus_id] = {
            "x": d * step_x, "y": lane * step_y,
            "orientation": "V", "length": 6 * grid,
        }

    for comp in net.components.values():
        bs = buses_of(comp)
        if is_series(comp) and len(bs) == 2 and bs[0] in pos and bs[1] in pos:
            x = (pos[bs[0]][0] + pos[bs[1]][0]) / 2.0
            y = (pos[bs[0]][1] + pos[bs[1]][1]) / 2.0
        elif bs and bs[0] in pos:
            x, y = pos[bs[0]]
            if comp.TYPE == "source":
                x -= step_x / 2.0
            else:
                # Clear the bar (half its length) plus the symbol's own lead,
                # or the arrow ends up drawn on top of the busbar.
                y += 3 * bus_half + 2 * grid
        else:
            x, y = 0.0, 0.0
        out[comp.id] = {
            "x": _snap(x, grid), "y": _snap(y, grid),
            "rotation": 0, "mirrored": False,
        }

    return {k: v for k, v in out.items() if k not in skip}


def _snap(v: float, grid: int) -> int:
    return int(round(v / grid) * grid)
