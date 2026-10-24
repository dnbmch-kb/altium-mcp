"""Spec lines for the bridge's schematic editor (edit_schematic, replace_component, annotate_schematic).

Pure Python: validates the caller's ops and turns them into the pipe-delimited lines
AltiumScript/schematic_edit.pas reads. Every error is a ValueError naming the op.
Coordinates are mils. A component is referenced as "uid:XXXXXXXX" (its UNIQUEID) or "des:R5".
"""
from pathlib import Path

# net -> (port style, orientation, name shown), the house sheets (#Notes/guides/schematic_style.md section 5):
# GND is power ground (4) pointing down with its name hidden, a supply a bar (2) pointing up with its name shown
DEFAULT_POWER = {"GND": (4, 3, False), "3V3": (2, 1, True), "5V": (2, 1, True)}
SUPPLY_STYLE = (2, 1, True)
GROUND_STYLE = (4, 3, True)                                    # another ground net keeps its name
DRAW_OPS = ("wire", "junction", "port")                        # drawn in the second pass, from the reported pins
CONNECTIONS = ("PIN", "MAP", "ATTACH")                         # spec lines whose connection sheet_plan places

_REQUIRED = {
    "delete": ("uid",),
    "delete_component": ("uid",),
    "rename_label": ("uid", "text"),
    "rename_net": ("old", "new"),
    "port_rename": ("uid", "new"),
    "label_to_port": ("uid", "text"),
    "attach_pin": ("comp", "pin", "net"),
    "detach_pin": ("comp", "pin"),
    "no_erc_pins": ("comp", "pins"),
    "place": ("designator", "symbol_library", "lib_ref", "x", "y"),
    "replace": ("comp", "symbol_library", "lib_ref", "pin_map"),
    "relink": ("comp",),
    "attach_entry": ("symbol", "entry", "net"),
    "wire": ("points",),
    "junction": ("at",),
    "port": ("net", "at", "orient"),
}


def _text(value, what):
    s = str(value)
    if "|" in s or "\n" in s or "\r" in s:
        raise ValueError(f"{what} may not contain '|' or a line break: {s!r}")
    if s == "":
        raise ValueError(f"{what} is empty")
    return s


def _comp(ref, what):
    s = _text(ref, what)
    if not (s.startswith("uid:") or s.startswith("des:")) or len(s) < 5:
        raise ValueError(f"{what} must be 'uid:<UNIQUEID>' or 'des:<designator>': {s!r}")
    return s


def _schlib(path, what):
    p = Path(str(path))
    if p.suffix.lower() != ".schlib" or not p.is_absolute() or not p.is_file():
        raise ValueError(f"{what} must be the full path of an existing .SchLib (the symbol is copied from it; "
                         f"link_library names the library the part then links to): {path}")
    return str(p)


def _int(value, what):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise ValueError(f"{what} must be a whole number of mils: {value!r}")
    return str(int(value))


def power_table(power_nets=None):
    """Nets drawn as power ports: GND styles as ground, everything else as a supply bar."""
    table = dict(DEFAULT_POWER)
    for net in power_nets or ():
        net = _text(net, "power net")
        table.setdefault(net, GROUND_STYLE if net.upper().startswith("GND") else SUPPLY_STYLE)
    return table


def _net_line(kind, pin, net, power):
    pin = "|".join(_text(p, "pin") for p in str(pin).split("|"))
    net = _text(net, f"net of pin {pin}")
    style, orient, _ = power.get(net, (-1, -1, True))
    return f"{kind}|{pin}|{net}|{style}|{orient}"


def _point(p, what):
    """A drawing op's point: [x, y] in mils or {"comp": "uid:..."|"des:...", "pin": name}, checked."""
    if isinstance(p, dict):
        if "comp" not in p or "pin" not in p:
            raise ValueError(f"{what}: a pin point needs comp and pin: {p!r}")
        return {"comp": _comp(p["comp"], what + " comp"), "pin": _text(p["pin"], what + " pin")}
    if not isinstance(p, (list, tuple)) or len(p) != 2:
        raise ValueError(f"{what}: a point is [x, y] or {{comp, pin}}: {p!r}")
    return [int(_int(p[0], what + " x")), int(_int(p[1], what + " y"))]


def draw_ops(ops):
    """The wire, junction and port ops, checked, in order: edit_spec leaves them out, the second pass draws them
    once the pins they name are known."""
    out = []
    for i, op in enumerate(ops):
        kind = op.get("op")
        if kind not in DRAW_OPS:
            continue
        what = f"op {i} ({kind})"
        for key in _REQUIRED[kind]:
            if key not in op:
                raise ValueError(f"{what}: missing '{key}'")
        if kind == "wire":
            if not isinstance(op["points"], (list, tuple)) or len(op["points"]) < 2:
                raise ValueError(f"{what}: points needs two or more")
            out.append({"op": "wire", "points": [_point(p, what) for p in op["points"]],
                        "junctions": [_point(p, what) for p in op.get("junctions") or ()]})
        elif kind == "junction":
            out.append({"op": "junction", "at": _point(op["at"], what)})
        else:
            orient = op["orient"]
            if isinstance(orient, bool) or orient not in (0, 1, 2, 3):
                raise ValueError(f"{what}: orient is 0 right, 1 up, 2 left or 3 down: {orient!r}")
            port = {"op": "port", "net": _text(op["net"], what + " net"), "at": _point(op["at"], what),
                    "orient": orient}
            if "style" in op:
                port["style"] = int(op["style"])
            out.append(port)
    return out


def _footprints(op, what):
    """The footprint field: footprint, then alternate_footprints (a DbLib row's Footprint Ref 2 onward), ';'-joined;
    the part's footprint models become exactly these, the first current."""
    alternates = op.get("alternate_footprints") or ()
    if op.get("footprint") in (None, ""):
        if alternates:
            raise ValueError(f"{what}: alternate_footprints needs footprint, the current one")
        return ""
    if isinstance(alternates, str):
        raise ValueError(f"{what}: alternate_footprints is a list of footprint names")
    names = [_text(n, f"{what} footprint") for n in (op["footprint"], *alternates)]
    if any(";" in n for n in names) or len(set(names)) != len(names):
        raise ValueError(f"{what}: footprint names may not contain ';' or repeat: {names}")
    return ";".join(names)


def _link_fields(op, what):
    return "|".join(_footprints(op, what) if k == "footprint" else
                    _text(op[k], f"{what} {k}") if op.get(k) not in (None, "") else ""
                    for k in ("link_library", "db_table", "design_item", "footprint", "comment"))


def _footprint_library(op, what):
    """The PcbLib a part without a database row takes its footprint from, by full path, or "". A DbLib part
    without a row has none to take the footprint from: on Use Component Library the ECO reported Footprint Not
    Found (dss-r0-aiv CTRL, 47 passives, 2026-10-24). Sent as the last field of the op's line, so a spec without it
    reads as before."""
    path = op.get("footprint_library")
    if path in (None, ""):
        return ""
    if op.get("design_item") not in (None, ""):
        raise ValueError(f"{what}: footprint_library is for a part without a database row; design_item's row "
                         "names the footprint")
    if op.get("footprint") in (None, ""):
        raise ValueError(f"{what}: footprint_library needs footprint, the footprint's name in it")
    p = Path(str(path))
    if p.suffix.lower() != ".pcblib" or not p.is_absolute() or not p.is_file():
        raise ValueError(f"{what}: footprint_library must be the full path of an existing .PcbLib: {path}")
    return _text(p, what + " footprint_library")


def _description(op, what):
    """The part's own Description: the op's description, or a parameter named Description (any case). Altium keeps
    no parameter of that name - a DbLib column mapped to Description lands in the component description - so one
    written as a parameter never reached the saved sheet."""
    desc = None if op.get("description") in (None, "") else _text(op["description"], what + " description")
    for name, value in (op.get("parameters") or {}).items():
        if str(name).lower() == "description":
            value = _text(value, f"{what} parameter {name}")
            if desc is not None and desc != value:
                raise ValueError(f"{what}: description {desc!r} and parameter {name} {value!r} differ")
            desc = value
    return desc


def _part_block(op, what, power):
    desc = _description(op, what)
    lines = [] if desc is None else [f"DESCRIPTION|{desc}"]
    for name, value in (op.get("parameters") or {}).items():
        if str(name).lower() != "description":
            lines.append(f"PARAM|{_text(name, what + ' parameter name')}|{_text(value, what + ' parameter ' + name)}")
    pins = op.get("pins") or {}
    for pin, net in pins.items():
        lines.append(_net_line("PIN", pin, net, power))
    for pin in op.get("no_erc") or ():
        if pin in pins:
            raise ValueError(f"{what}: pin {pin} has a net and a No-ERC marker")
        lines.append(f"NOPIN|{_text(pin, 'pin')}")
    return lines


def edit_spec(sheet, ops, power_nets=None, save=True):
    """Spec lines for one sheet. Ops run in order; a part's pins, parameters and pin map close with END."""
    path = Path(str(sheet))
    if path.suffix.lower() != ".schdoc" or not path.is_absolute() or not path.is_file():
        raise ValueError(f"sheet must be the full path of an existing .SchDoc: {sheet}")
    if not ops:
        raise ValueError("no ops given")
    draw_ops(ops)
    power = power_table(power_nets)
    lines = [f"SHEET|{path}"] + [f"POWERNET|{n}|{s}|{o}|{1 if shown else 0}" for n, (s, o, shown) in power.items()]
    for i, op in enumerate(ops):
        kind = op.get("op")
        what = f"op {i} ({kind})"
        if kind not in _REQUIRED:
            raise ValueError(f"{what}: unknown op; known: {', '.join(sorted(_REQUIRED))}")
        if kind in DRAW_OPS:
            continue
        for key in _REQUIRED[kind]:
            if key not in op:
                raise ValueError(f"{what}: missing '{key}'")
        if kind == "delete":
            lines.append(f"DELETE|{_text(op['uid'], what + ' uid')}")
        elif kind == "delete_component":
            keep = ",".join(_text(u, what + " keep") for u in op.get("keep") or ())
            lines.append(f"DELCOMP|{_text(op['uid'], what + ' uid')}|{1 if op.get('dead_ends', True) else 0}|{keep}")
        elif kind == "rename_label":
            lines.append(f"RENAME_LABEL|{_text(op['uid'], what + ' uid')}|{_text(op['text'], what + ' text')}")
        elif kind == "rename_net":
            lines.append(f"RENAME_NET|{_text(op['old'], what + ' old')}|{_text(op['new'], what + ' new')}")
        elif kind == "port_rename":
            lines.append(f"PORT_RENAME|{_text(op['uid'], what + ' uid')}|{_text(op['new'], what + ' new')}")
        elif kind == "label_to_port":
            text = _text(op["text"], what + " text")
            style, orient, shown = power.get(text, SUPPLY_STYLE)
            lines.append(f"LABEL_TO_PORT|{_text(op['uid'], what + ' uid')}|{text}|{style}|{orient}|{1 if shown else 0}")
        elif kind == "attach_pin":
            lines.append(f"ATTACH|{_comp(op['comp'], what + ' comp')}|" + _net_line("X", op["pin"], op["net"], power)[2:])
        elif kind == "detach_pin":
            lines.append(f"DETACH|{_comp(op['comp'], what + ' comp')}|{_text(op['pin'], what + ' pin')}")
        elif kind == "no_erc_pins":
            for pin in op["pins"]:
                lines.append(f"NOERC|{_comp(op['comp'], what + ' comp')}|{_text(pin, what + ' pin')}")
        elif kind == "attach_entry":
            lines.append(f"ENTRY|{_text(op['symbol'], what + ' symbol')}|{_text(op['entry'], what + ' entry')}|"
                         + _net_line("X", "entry", op["net"], power)[len("X|entry|"):])
        elif kind == "place":
            line = "PLACE|{}|{}|{}|{}|{}|{}|{}|{}".format(
                _text(op["designator"], what + " designator"), _schlib(op["symbol_library"], what),
                _text(op["lib_ref"], what + " lib_ref"), _int(op["x"], what + " x"), _int(op["y"], what + " y"),
                int(op.get("orient", 0)) % 4, 1 if op.get("mirror") else 0, _link_fields(op, what))
            part = op.get("part")
            if part is not None:                          # one part of a multi-part symbol (2 = B, ...)
                if isinstance(part, bool) or not isinstance(part, int) or part < 1:
                    raise ValueError(f"{what}: part must be a whole number from 1: {part!r}")
            footprint_library = _footprint_library(op, what)
            if part is not None or footprint_library:
                line += f"|{'' if part is None else part}"
            if footprint_library:
                line += f"|{footprint_library}"
            lines.append(line)
            lines += _part_block(op, what, power) + ["END"]
        elif kind == "replace":
            footprint_library = _footprint_library(op, what)
            lines.append("REPLACE|{}|{}|{}|{}|{}|{}".format(
                _comp(op["comp"], what + " comp"), _schlib(op["symbol_library"], what),
                _text(op["lib_ref"], what + " lib_ref"),
                int(op["orient"]) % 4 if "orient" in op else -1, 1 if op.get("mirror") else 0, _link_fields(op, what))
                + (f"|{footprint_library}" if footprint_library else ""))
            nets = {str(k): v for k, v in (op.get("nets") or {}).items()}
            if not op["pin_map"]:
                raise ValueError(f"{what}: empty pin_map")
            mapped = {}
            for old, new in op["pin_map"].items():
                new = _text(new, what + " new pin")
                if new not in nets:
                    raise ValueError(f"{what}: no net for new pin {new} (mapped from old pin {old}); "
                                     "give every mapped pin its net in 'nets'")
                # several old pins onto one new pin (an exposed pad's numbered copies): one stub, every old end named
                kind = "MAPOLD" if new in mapped else "MAP"
                mapped.setdefault(new, []).append(_text(old, what + " old pin"))
                lines.append(_net_line(kind, f"{old}|{new}", nets[new], power))
            extra = {k: v for k, v in nets.items() if k not in mapped}
            lines += _part_block(dict(op, pins=extra), what, power) + ["END"]
        elif kind == "relink":
            footprint_library = _footprint_library(op, what)
            lines.append(f"RELINK|{_comp(op['comp'], what + ' comp')}|{_link_fields(op, what)}"
                         + (f"|{footprint_library}" if footprint_library else ""))
            lines += _part_block(dict(op, pins={}, no_erc=()), what, power) + ["END"]
    if save:
        lines.append("SAVE")
    return lines


def unbound_models(placed):
    """'R1 PCBLIB RES_0402' for every model of a placed part linked to an IntLib or DbLib that the bridge read back
    on library Any - neither "Use Component Library" nor a library path: the ECO then searches every installed
    library for it."""
    out = []
    for part in placed:
        if part.get("library", "").lower().endswith((".intlib", ".dblib")):
            out += [f"{part.get('designator')} {m.get('type')} {m.get('name')}" for m in part.get("models", [])
                    if not m.get("use_component_library") and not m.get("library_path")]
    return out


def annotate_spec(sheets, assignments, save=True):
    """Spec lines for annotate_schematic: explicit {uid: designator}, every sheet opened first."""
    lines = []
    for sheet in sheets:
        path = Path(str(sheet))
        if path.suffix.lower() != ".schdoc" or not path.is_absolute() or not path.is_file():
            raise ValueError(f"sheet must be the full path of an existing .SchDoc: {sheet}")
        lines.append(f"SHEET|{path}")
    if not lines:
        raise ValueError("no sheets given")
    if not assignments:
        raise ValueError("no assignments given")
    seen = {}
    for uid, des in assignments.items():
        des = _text(des, f"designator for {uid}")
        if des.endswith("?"):
            raise ValueError(f"designator for {uid} is unannotated: {des}")
        if des in seen:
            raise ValueError(f"designator {des} given to both {seen[des]} and {uid}")
        seen[des] = uid
        lines.append(f"ASSIGN|{_text(uid, 'uid')}|{des}")
    if save:
        lines.append("SAVE")
    return lines


def auto_designators(components, prefix_override=None):
    """Designators for every '?' component, existing ones kept: per prefix, the next free numbers, ordered top to
    bottom, then left to right (larger Y first, as Altium's Y grows upward). components: [{uid, designator, x, y,
    lib_ref}] in sheet order. Returns {uid: designator} for the '?' ones only."""
    prefix_override = prefix_override or {}
    used = {}
    for c in components:
        d = c["designator"]
        if not d.endswith("?"):
            head = d.rstrip("0123456789")
            if head != d:
                used.setdefault(head, set()).add(int(d[len(head):]))
    todo = sorted((c for c in components if c["designator"].endswith("?")), key=lambda c: (-c["y"], c["x"]))
    out = {}
    for c in todo:
        prefix = prefix_override.get(c.get("lib_ref"), c["designator"][:-1])
        n = 1
        taken = used.setdefault(prefix, set())
        while n in taken:
            n += 1
        taken.add(n)
        out[c["uid"]] = f"{prefix}{n}"
    return out
