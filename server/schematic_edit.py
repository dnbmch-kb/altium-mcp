"""Spec lines for the bridge's schematic editor (edit_schematic, replace_component, annotate_schematic).

Pure Python: validates the caller's ops and turns them into the pipe-delimited lines
AltiumScript/schematic_edit.pas reads. Every error is a ValueError naming the op.
Coordinates are mils. A component is referenced as "uid:XXXXXXXX" (its UNIQUEID) or "des:R5".
"""
from pathlib import Path

DEFAULT_POWER = {"GND": (5, 3), "3V3": (2, 1), "5V": (2, 1)}   # net -> (port style, orientation)
SUPPLY_STYLE = (2, 1)                                          # bar, pointing up
GROUND_STYLE = (5, 3)                                          # signal ground, pointing down
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
    style, orient = power.get(net, (-1, -1))
    return f"{kind}|{pin}|{net}|{style}|{orient}"


def _link_fields(op, what):
    return "|".join(_text(op[k], f"{what} {k}") if op.get(k) not in (None, "") else ""
                    for k in ("link_library", "db_table", "design_item", "footprint", "comment"))


def _part_block(op, what, power):
    lines = []
    for name, value in (op.get("parameters") or {}).items():
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
    power = power_table(power_nets)
    lines = [f"SHEET|{path}"] + [f"POWERNET|{n}|{s}|{o}" for n, (s, o) in power.items()]
    for i, op in enumerate(ops):
        kind = op.get("op")
        what = f"op {i} ({kind})"
        if kind not in _REQUIRED:
            raise ValueError(f"{what}: unknown op; known: {', '.join(sorted(_REQUIRED))}")
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
            style, orient = power.get(text, SUPPLY_STYLE)
            lines.append(f"LABEL_TO_PORT|{_text(op['uid'], what + ' uid')}|{text}|{style}|{orient}")
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
            if op.get("part") is not None:                # one part of a multi-part symbol (2 = B, ...)
                part = op["part"]
                if isinstance(part, bool) or not isinstance(part, int) or part < 1:
                    raise ValueError(f"{what}: part must be a whole number from 1: {part!r}")
                line += f"|{part}"
            lines.append(line)
            lines += _part_block(op, what, power) + ["END"]
        elif kind == "replace":
            lines.append("REPLACE|{}|{}|{}|{}|{}|{}".format(
                _comp(op["comp"], what + " comp"), _schlib(op["symbol_library"], what),
                _text(op["lib_ref"], what + " lib_ref"),
                int(op["orient"]) % 4 if "orient" in op else -1, 1 if op.get("mirror") else 0, _link_fields(op, what)))
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
            lines.append(f"RELINK|{_comp(op['comp'], what + ' comp')}|{_link_fields(op, what)}")
            lines += _part_block(dict(op, pins={}, no_erc=()), what, power) + ["END"]
    if save:
        lines.append("SAVE")
    return lines


def unbound_models(placed):
    """'R1 PCBLIB RES_0402' for every model of a placed part linked to an IntLib or DbLib that the bridge read back
    without "Use Component Library": the ECO then searches every installed library for it."""
    out = []
    for part in placed:
        if part.get("library", "").lower().endswith((".intlib", ".dblib")):
            out += [f"{part.get('designator')} {m.get('type')} {m.get('name')}" for m in part.get("models", [])
                    if not m.get("use_component_library")]
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
