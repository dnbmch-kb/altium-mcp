"""Where edit_schematic draws a pin's connection, planned from the sheet as Altium reports it (pure Python).

edit_schematic places parts and resolves pins first; Altium then reports every part's pins (electrical end,
direction, body end), body extent and visible texts, and the sheet's net labels, power ports and wires. This
module resolves the drawing ops (wire, junction, port at given points or pin ends), plans each requested pin
connection around them in the house style (c:/Projects/#Notes/guides/schematic_style.md sections 4 and 5), and
the bridge draws the result:

- a signal pin gets a horizontal net label on a stub sized to its text (60 mil a character plus 200): on a pin
  pointing right the label starts 100 mil out and runs along the stub, on a pin pointing left it sits at the
  stub's far end and runs back toward the pin, on an up or down pin it sits at the end of a 100 mil stub;
- a power pin, or neighbouring pins of one part on one power net (one shared wire, a junction at each inner end),
  gets a port that points the conventional way only, a supply up and ground down: at the stub end, the same
  200 or 400 mil further out, or, on a pin pointing against that way, beside the pin at the end of a 200 or
  300 mil jog; the first that touches no body, pin or text, else a label on each pin. A port never points
  sideways;
- nothing new may touch a pin end, wire, label or port it does not belong to: that would join two nets. A spot
  that would is never drawn.

Text extents are estimates (Times New Roman 10: 90 mil high, 70 mil a character, generous); the caller gets
every box back to check. Lengths are mils in the sheet's frame. Directions: 0 right, 1 up, 2 left, 3 down.
Justification follows Altium: 0 bottom left (row = j // 3, column = j % 3). A port is [x, y, orient, style,
text, shown]. Learnt on laser-calib's raw-head adapter (2026-10-23, its sheet.py): drawn with fixed stubs and
labels, its 56-part sheet had 214 overlaps.
"""
from collections import defaultdict

DIRS = {0: (1, 0), 1: (0, 1), 2: (-1, 0), 3: (0, -1)}
STUB = 100
EXTRA = (0, 200, 400)           # stubs moved further out when the first spot is taken
JOG = (200, -200, 300, -300)    # sideways steps for a power pin pointing against its port's way
TXT_H, TXT_W = 90, 70           # extents for the overlap check
STUB_CHAR = 60                  # a label's stub: 60 mil a character plus 200 (the house sheets, style guide 4)
GROUND = (4, 5)                 # power port styles drawn as a ground (reach 100 mil); the others reach 50


def text_box(x, y, orient, just, text):
    """A text's extent: anchored at (x, y) by its justification, turned orient * 90 degrees about the anchor."""
    w, h = len(text) * TXT_W, TXT_H
    x0, y0 = -w * (just % 3) / 2, -h * (just // 3) / 2
    corners = [(x0, y0), (x0 + w, y0), (x0, y0 + h), (x0 + w, y0 + h)]
    for _ in range(orient % 4):
        corners = [(-v, u) for u, v in corners]
    xs, ys = [u for u, _ in corners], [v for _, v in corners]
    return (x + min(xs), y + min(ys), x + max(xs), y + max(ys))


def port_boxes(x, y, orient, style, text, shown=True):
    """A power port's glyph and, when its name is shown, its text: the glyph 100 (ground) or 50 mil along orient,
    its box 10 mil longer, so a glyph ending on another net's wire counts as touching it."""
    reach = 100 if style in GROUND else 50
    dx, dy = DIRS[orient % 4]
    w = len(text) * TXT_W
    g = reach + 10
    if dx:
        glyph = (min(x, x + dx * g), y - 50, max(x, x + dx * g), y + 50)
        tx = x + dx * (reach + 20)
        tbox = (tx, y - TXT_H / 2, tx + w, y + TXT_H / 2) if dx > 0 else (tx - w, y - TXT_H / 2, tx, y + TXT_H / 2)
    else:
        glyph = (x - 50, min(y, y + dy * g), x + 50, max(y, y + dy * g))
        ty = y + dy * (reach + 20)
        tbox = (x - w / 2, ty, x + w / 2, ty + TXT_H) if dy > 0 else (x - w / 2, ty - TXT_H, x + w / 2, ty)
    return [glyph, tbox] if shown else [glyph]


def overlaps(a, b):
    """Boxes overlap by more than a touch."""
    return a[0] < b[2] - 1 and b[0] < a[2] - 1 and a[1] < b[3] - 1 and b[1] < a[3] - 1


def on_segment(p, a, b):
    """Point p on the straight segment a-b, ends included."""
    (px, py), (ax, ay), (bx, by) = p, a, b
    if (bx - ax) * (py - ay) != (by - ay) * (px - ax):
        return False
    return min(ax, bx) <= px <= max(ax, bx) and min(ay, by) <= py <= max(ay, by)


def segment_box(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))


def stub_for(text):
    """A label's stub on a sideways pin: the text at 60 mil a character plus 200, on the 50 mil grid."""
    n = len(text) * STUB_CHAR + 200
    return -(-n // 50) * 50


class Sheet:
    """The sheet as reported: obstacles for the eye, and the points and segments a new object must not touch."""

    def __init__(self, geometry):
        self.parts = {str(p["key"]): p for p in geometry.get("parts", [])}
        self.obstacles = []             # (box, what)
        self.points = {}                # (x, y) -> what: pin ends, wire vertices, label and port anchors
        self.segments = []              # ((a, b), what): wires
        for p in self.parts.values():
            des = p.get("designator", "?")
            if p.get("body"):
                self.obstacles.append((tuple(p["body"]), f"the body of {des}"))
            for name, x, y, d, bx, by in p.get("pins", []):
                self.obstacles.append((segment_box((x, y), (bx, by)), f"pin {des}.{name}"))
                self.points[(x, y)] = f"pin {des}.{name}"
            for x, y, o, j, text in p.get("texts", []):
                self.obstacles.append((text_box(x, y, o, j, text), f"{des}'s text '{text}'"))
        for x, y, o, j, text in geometry.get("labels", []):
            self.obstacles.append((text_box(x, y, o, j, text), f"label {text}"))
            self.points[(x, y)] = f"label {text}"
        for x, y, o, style, text, *shown in geometry.get("ports", []):
            self.obstacles += [(b, f"port {text}") for b in port_boxes(x, y, o, style, text, *shown)]
            self.points[(x, y)] = f"port {text}"
        for w in geometry.get("wires", []):
            pts = list(zip(w[0::2], w[1::2]))
            for a, b in zip(pts, pts[1:]):
                self.segments.append(((a, b), "a wire"))
            for q in pts:
                self.points.setdefault(q, "a wire")

    def pin(self, ref, name):
        """A pin's electrical end, the part by "uid:<id>" or "des:<designator>"; None when there is none."""
        kind, key = ref[:4], ref[4:]
        for p in self.parts.values():
            if (kind == "uid:" and p.get("uid") == key) or (kind == "des:" and p.get("designator") == key):
                for n, x, y, *_ in p.get("pins", []):
                    if n == str(name):
                        return (x, y)
        return None

    def conflict(self, cand, own):
        """What the candidate would touch electrically (None when nothing); own holds the run's own pin ends."""
        for a, b in cand["wires"]:
            for q, what in self.points.items():
                if q not in own and on_segment(q, a, b):
                    return what
        ends = {q for a, b in cand["wires"] for q in (a, b)} | {tuple(o[:2]) for o in cand["labels"] + cand["ports"]}
        for q in ends - own:
            for (a, b), what in self.segments:
                if on_segment(q, a, b):
                    return what
            if q in self.points:
                return self.points[q]
        return None

    def hits(self, cand):
        """Every obstacle the candidate's texts, glyphs and wires lie on."""
        found = []
        for box, what in boxes(cand):
            found += [f"{what} on {o}" for b, o in self.obstacles if overlaps(box, b)]
        return found

    def take(self, cand):
        self.obstacles += boxes(cand)
        for a, b in cand["wires"]:
            self.segments.append(((a, b), "a wire drawn here"))
            self.points.setdefault(a, "a wire drawn here")
            self.points.setdefault(b, "a wire drawn here")
        for o in cand["labels"]:
            self.points[tuple(o[:2])] = f"label {o[4]}"
        for o in cand["ports"]:
            self.points[tuple(o[:2])] = f"port {o[4]}"


def boxes(cand):
    """(box, what) for a candidate's labels, ports and wires."""
    out = []
    for x, y, o, j, text in cand["labels"]:
        out.append((text_box(x, y, o, j, text), f"label {text} at ({x}, {y})"))
    for x, y, o, style, text, shown in cand["ports"]:
        out += [(b, f"port {text} at ({x}, {y})") for b in port_boxes(x, y, o, style, text, shown)]
    for a, b in cand["wires"]:
        out.append((segment_box(a, b), f"wire ({a[0]}, {a[1]})-({b[0]}, {b[1]})"))
    return out


def label_candidate(x, y, d, net, extra=0):
    """A horizontal label on the pin's stub, as the house sheets draw it."""
    dx, dy = DIRS[d]
    if dx:
        n = stub_for(net) + extra
        e = (x + dx * n, y)
        anchor = (x + 100 + extra, y) if dx > 0 else e         # right: near the pin; left: at the far end
    else:
        e = anchor = (x, y + dy * (STUB + extra))
    return dict(wires=[((x, y), e)], junctions=[], ports=[], labels=[(anchor[0], anchor[1], 0, 0, net)])


def merge(cands):
    out = dict(wires=[], junctions=[], labels=[], ports=[])
    for c in cands:
        for k in out:
            out[k] += c[k]
    return out


def power_candidates(run, d, net, port):
    """Ports for a run of neighbouring pins on one power net, nearest first, then a label on each pin. port is
    (style, conventional orientation, name shown); the port points that way only."""
    style, up, shown = port
    dx, dy = DIRS[d]
    cands = []
    for extra in EXTRA:
        n = STUB + extra
        ends = [(x + dx * n, y + dy * n) for x, y in run]
        wires = [(q, e) for q, e in zip(run, ends)] + ([(ends[0], ends[-1])] if len(ends) > 1 else [])
        base = dict(wires=wires, junctions=ends[1:-1], labels=[])
        if dx:                                              # sideways: from the end the port points away from
            e = ends[-1] if up == 1 else ends[0]
            cands.append(dict(base, ports=[(e[0], e[1], up, style, net, shown)]))
        elif d == up:                                       # pointing the port's way: at the middle end
            e = ends[len(ends) // 2]
            cands.append(dict(base, ports=[(e[0], e[1], up, style, net, shown)]))
        else:                                               # pointing against it: a jog beside the pin
            for j in JOG:
                start = ends[-1] if j > 0 else ends[0]
                e = (start[0] + j, start[1])
                cands.append(dict(base, wires=wires + [(start, e)], ports=[(e[0], e[1], up, style, net, shown)]))
    cands.append(merge([label_candidate(x, y, d, net) for x, y in run]))
    return cands


def runs(pins, d):
    """Pin ends of one side cut into runs of neighbours: 100 mil apart or less along the side, in one line."""
    along, across = (1, 0) if DIRS[d][0] else (0, 1)
    pins = sorted(pins, key=lambda q: (q[across], q[along]))
    out = [[pins[0]]]
    for q in pins[1:]:
        last = out[-1][-1]
        if q[across] == last[across] and abs(q[along] - last[along]) <= 100:
            out[-1].append(q)
        else:
            out.append([q])
    return out


def resolve(sheet, draws, power):
    """The drawing ops (schematic_edit.draw_ops) as objects: pin references become pin ends. Returns (objects,
    errors); an op that cannot be resolved, or a wire with a diagonal segment, is left out and named."""
    objs, errors = dict(wires=[], junctions=[], labels=[], ports=[]), []

    def point(p, what):
        if isinstance(p, dict):
            q = sheet.pin(p["comp"], p["pin"])
            if q is None:
                errors.append(f"{what}: no pin {p['comp']}.{p['pin']} on the sheet")
            return q
        return (int(p[0]), int(p[1]))

    for i, op in enumerate(draws):
        what = f"draw op {i} ({op['op']})"
        if op["op"] == "wire":
            pts = [point(p, what) for p in op["points"]]
            if None in pts:
                continue
            bad = [f"({a[0]}, {a[1]})-({b[0]}, {b[1]})" for a, b in zip(pts, pts[1:]) if a[0] != b[0] and a[1] != b[1]]
            if bad:
                errors.append(f"{what}: segments must be horizontal or vertical: {', '.join(bad)}")
                continue
            objs["wires"] += list(zip(pts, pts[1:]))
            for j in op.get("junctions", []):
                q = point(j, what)
                if q is not None:
                    objs["junctions"].append(q)
        elif op["op"] == "junction":
            q = point(op["at"], what)
            if q is not None:
                objs["junctions"].append(q)
        elif op["op"] == "port":
            q = point(op["at"], what)
            if q is not None:
                style, _, shown = power[op["net"]]
                objs["ports"].append((q[0], q[1], int(op["orient"]) % 4, int(op.get("style", style)), op["net"],
                                      shown))
    return objs, errors


def plan(geometry, pending, power, draws=()):
    """The objects to draw: the drawing ops, then the pending connections [[part key, pin, net, x, y, d]] around
    them; power is {net: (style, orientation, shown)}.

    Returns {wires [[x1, y1, x2, y2]], junctions [[x, y]], labels [[x, y, orient, just, text]], ports
    [[x, y, orient, style, text, shown]], connections [{designator, pins, net, kind, boxes}], overlaps [..],
    errors [..]}."""
    sheet = Sheet(geometry)
    out = dict(wires=[], junctions=[], labels=[], ports=[], connections=[], overlaps=[], errors=[])

    def emit(chosen):
        sheet.take(chosen)
        out["wires"] += [[a[0], a[1], b[0], b[1]] for a, b in chosen["wires"]]
        out["junctions"] += [list(q) for q in chosen["junctions"]]
        out["labels"] += [list(o) for o in chosen["labels"]]
        out["ports"] += [list(o) for o in chosen["ports"]]

    drawn, errors = resolve(sheet, list(draws), power)
    out["errors"] += errors
    emit(drawn)
    jobs, groups = [], defaultdict(list)
    for key, pin, net, x, y, d in pending:
        key, d = str(key), int(d) % 4
        if net in power:
            groups[(key, net, d)].append(((x, y), pin))
        else:
            des = sheet.parts.get(key, {}).get("designator", "?")
            jobs.append((des, [pin], net, [(x, y)], [label_candidate(x, y, d, net, e) for e in EXTRA]))
    for (key, net, d), members in groups.items():
        des = sheet.parts.get(key, {}).get("designator", "?")
        name = dict(members)
        for run in runs(list(name), d):
            jobs.append((des, [name[q] for q in run], net, run, power_candidates(run, d, net, power[net])))
    for des, pins, net, own, cands in jobs:
        own = set(own)
        valid = [c for c in cands if sheet.conflict(c, own) is None]
        if not valid:
            out["errors"].append(f"{des}.{','.join(pins)} ({net}): every spot for its connection touches "
                                 f"{sheet.conflict(cands[0], own)}; nothing drawn")
            continue
        chosen = next((c for c in valid if not sheet.hits(c)), valid[0])
        out["overlaps"] += [f"{des}.{','.join(pins)}: {hit}" for hit in sheet.hits(chosen)]
        emit(chosen)
        out["connections"].append(dict(designator=des, pins=pins, net=net,
                                       kind="port" if chosen["ports"] else "label",
                                       boxes=[[round(v) for v in b] for b, _ in boxes(chosen)]))
    return out


def carry(geometry, objects):
    """The sheet the next edit_schematic call meets once this one drew `objects` (plan's result): `geometry` with
    their wires, labels and ports added as Altium's first pass reports them (it reports no junctions). A call plans
    only its own pins, around whatever the earlier calls drew, so a sheet placed in several calls is replayed call
    by call on this (ugv-pcb BLOCKS/gen/plan_stubs.py --per-call): a pin left no free spot by an earlier call's
    stubs is refused there, as the live call refuses it."""
    return dict(geometry, wires=list(geometry.get("wires", [])) + [list(w) for w in objects["wires"]],
                labels=list(geometry.get("labels", [])) + [list(o) for o in objects["labels"]],
                ports=list(geometry.get("ports", [])) + [list(o) for o in objects["ports"]])


def draw_spec(sheet_path, objects, save=True):
    """Spec lines that draw a plan on the sheet (schematic_edit.pas WIRE, JUNCTION, LABEL, PORT)."""
    r = lambda v: str(int(round(v)))
    lines = [f"SHEET|{sheet_path}"]
    lines += ["WIRE|" + "|".join(map(r, w)) for w in objects["wires"]]
    lines += ["JUNCTION|" + "|".join(map(r, j)) for j in objects["junctions"]]
    lines += [f"LABEL|{r(x)}|{r(y)}|{o}|{j}|{t}" for x, y, o, j, t in objects["labels"]]
    lines += [f"PORT|{r(x)}|{r(y)}|{o}|{s}|{1 if shown else 0}|{t}" for x, y, o, s, t, shown in objects["ports"]]
    if save:
        lines.append("SAVE")
    return lines
