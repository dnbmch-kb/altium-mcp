"""Where edit_schematic draws a pin's connection, planned from the sheet as Altium reports it (pure Python).

edit_schematic places parts and resolves pins first; Altium then reports every part's pins (electrical end,
direction, body end), body extent and visible texts, and the sheet's net labels, power ports and wires. This
module plans each requested connection from that, and the bridge draws the plan:

- a signal pin gets a 100 mil stub outward and a net label ending at the stub, on the pin's own side: left-
  justified on a pin pointing right, right-justified on one pointing left, turned 90 degrees on an up or down pin
  (reading up from the stub end, or ending at it on a down pin);
- a power pin, or neighbouring pins of one part on one power net (one shared wire, a junction at each inner end),
  gets the first of these that touches no body, pin or text: a port the conventional way (supplies up, ground
  down) or the pin's way at the stub end, the same 200 or 400 mil further out, else a net label on each pin; a
  sideways power pin with another pin of its side within 150 mil takes a label, there is no room for a port;
- nothing new may touch a pin end, wire, label or port it does not belong to: that would join two nets. A spot
  that would is never drawn; a signal pin tries the longer stubs too.

Port and text extents are estimates (Times New Roman 10: 90 mil high, 70 mil per character, generous); the
caller gets every box back to check. Lengths are mils in the sheet's frame. Directions: 0 right, 1 up, 2 left,
3 down. Justification follows Altium: 0 bottom left, 2 bottom right (row = j // 3, column = j % 3).
Learnt on laser-calib's raw-head adapter (2026-10-23, hw/raw_head_adapter/altium_native/sheet.py): drawn the old
way, its 56-part sheet had 214 overlaps, JP1's labels on its own body among them.
"""
from collections import defaultdict

DIRS = {0: (1, 0), 1: (0, 1), 2: (-1, 0), 3: (0, -1)}
STUB = 100
EXTRA = (0, 200, 400)           # longer stubs tried when the first spot is taken
TXT_H, TXT_W = 90, 70
GROUND = 5                      # power port style drawn as a ground (reaches 100 mil); the others reach 50


def text_box(x, y, orient, just, text):
    """A text's extent: anchored at (x, y) by its justification, turned orient * 90 degrees about the anchor."""
    w, h = len(text) * TXT_W, TXT_H
    x0, y0 = -w * (just % 3) / 2, -h * (just // 3) / 2
    corners = [(x0, y0), (x0 + w, y0), (x0, y0 + h), (x0 + w, y0 + h)]
    for _ in range(orient % 4):
        corners = [(-v, u) for u, v in corners]
    xs, ys = [u for u, _ in corners], [v for _, v in corners]
    return (x + min(xs), y + min(ys), x + max(xs), y + max(ys))


def port_boxes(x, y, orient, style, text):
    """A power port's glyph and its text: the glyph 100 (ground) or 50 mil along orient, 50 to either side."""
    reach = 100 if style == GROUND else 50
    dx, dy = DIRS[orient % 4]
    w = len(text) * TXT_W
    if dx:
        glyph = (min(x, x + dx * reach), y - 50, max(x, x + dx * reach), y + 50)
        tx = x + dx * (reach + 20)
        tbox = (tx, y - TXT_H / 2, tx + w, y + TXT_H / 2) if dx > 0 else (tx - w, y - TXT_H / 2, tx, y + TXT_H / 2)
    else:
        glyph = (x - 50, min(y, y + dy * reach), x + 50, max(y, y + dy * reach))
        ty = y + dy * (reach + 20)
        tbox = (x - w / 2, ty, x + w / 2, ty + TXT_H) if dy > 0 else (x - w / 2, ty - TXT_H, x + w / 2, ty)
    return [glyph, tbox]


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
        for x, y, o, style, text in geometry.get("ports", []):
            self.obstacles += [(b, f"port {text}") for b in port_boxes(x, y, o, style, text)]
            self.points[(x, y)] = f"port {text}"
        for w in geometry.get("wires", []):
            pts = list(zip(w[0::2], w[1::2]))
            for a, b in zip(pts, pts[1:]):
                self.segments.append(((a, b), "a wire"))
            for q in pts:
                self.points.setdefault(q, "a wire")

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
    for x, y, o, style, text in cand["ports"]:
        out += [(b, f"port {text} at ({x}, {y})") for b in port_boxes(x, y, o, style, text)]
    for a, b in cand["wires"]:
        out.append((segment_box(a, b), f"wire ({a[0]}, {a[1]})-({b[0]}, {b[1]})"))
    return out


def label_candidate(x, y, d, net, extra=0):
    """A stub outward from the pin end and a label ending at it, on the pin's side."""
    n = STUB + extra
    e = (x + DIRS[d][0] * n, y + DIRS[d][1] * n)
    return dict(wires=[((x, y), e)], junctions=[], ports=[],
                labels=[(e[0], e[1], 1 if d in (1, 3) else 0, 2 if d in (2, 3) else 0, net)])


def merge(cands):
    out = dict(wires=[], junctions=[], labels=[], ports=[])
    for c in cands:
        for k in out:
            out[k] += c[k]
    return out


def power_candidates(run, d, net, style, conventional):
    """Ports for a run of neighbouring pins on one power net, nearest first, then a label on each pin."""
    dx, dy = DIRS[d]
    cands = []
    for extra in EXTRA:
        n = STUB + extra
        ends = [(x + dx * n, y + dy * n) for x, y in run]
        wires = [(q, e) for q, e in zip(run, ends)] + ([(ends[0], ends[-1])] if len(ends) > 1 else [])
        for orient in ((conventional, d) if dx else (d,)):
            if not dx:
                e = ends[-1]
            elif orient == d:
                e = ends[len(ends) // 2]                    # the pin's way: from the middle of the shared wire
            else:
                e = ends[-1] if orient == 1 else ends[0]    # up from the top end, down from the bottom one
            cands.append(dict(wires=wires, junctions=ends[1:-1], labels=[], ports=[(e[0], e[1], orient, style, net)]))
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


def plan(geometry, pending, power):
    """The objects to draw for pending connections [[part key, pin, net, x, y, d]]; power is {net: (style, orient)}.

    Returns {wires [[x1, y1, x2, y2]], junctions [[x, y]], labels [[x, y, orient, just, text]], ports
    [[x, y, orient, style, text]], connections [{designator, pins, net, kind, boxes}], overlaps [..], errors [..]}."""
    sheet = Sheet(geometry)
    out = dict(wires=[], junctions=[], labels=[], ports=[], connections=[], overlaps=[], errors=[])
    jobs, groups = [], defaultdict(list)
    for key, pin, net, x, y, d in pending:
        key, d = str(key), int(d) % 4
        if net in power:
            groups[(key, net, d)].append(((x, y), pin))
        else:
            des = sheet.parts.get(key, {}).get("designator", "?")
            jobs.append((des, [pin], net, [(x, y)], [label_candidate(x, y, d, net, e) for e in EXTRA]))
    for (key, net, d), members in groups.items():
        part = sheet.parts.get(key, {})
        name = dict(members)
        side = [(x, y) for _, x, y, pd, _, _ in part.get("pins", []) if pd % 4 == d]
        style, conventional = power[net]
        for run in runs(list(name), d):
            crowded = DIRS[d][0] and len(run) == 1 and any(
                q != run[0] and q[0] == run[0][0] and abs(q[1] - run[0][1]) <= 150 for q in side)
            cands = ([merge([label_candidate(x, y, d, net) for x, y in run])] if crowded
                     else power_candidates(run, d, net, style, conventional))
            jobs.append((part.get("designator", "?"), [name[q] for q in run], net, run, cands))
    for des, pins, net, own, cands in jobs:
        own = set(own)
        valid = [c for c in cands if sheet.conflict(c, own) is None]
        if not valid:
            out["errors"].append(f"{des}.{','.join(pins)} ({net}): every spot for its connection touches "
                                 f"{sheet.conflict(cands[0], own)}; nothing drawn")
            continue
        chosen = next((c for c in valid if not sheet.hits(c)), valid[0])
        out["overlaps"] += [f"{des}.{','.join(pins)}: {hit}" for hit in sheet.hits(chosen)]
        sheet.take(chosen)
        out["wires"] += [[a[0], a[1], b[0], b[1]] for a, b in chosen["wires"]]
        out["junctions"] += [list(q) for q in chosen["junctions"]]
        out["labels"] += [list(o) for o in chosen["labels"]]
        out["ports"] += [list(o) for o in chosen["ports"]]
        out["connections"].append(dict(designator=des, pins=pins, net=net,
                                       kind="port" if chosen["ports"] else "label",
                                       boxes=[[round(v) for v in b] for b, _ in boxes(chosen)]))
    return out


def draw_spec(sheet_path, objects, save=True):
    """Spec lines that draw a plan on the sheet (schematic_edit.pas WIRE, JUNCTION, LABEL, PORT)."""
    r = lambda v: str(int(round(v)))
    lines = [f"SHEET|{sheet_path}"]
    lines += ["WIRE|" + "|".join(map(r, w)) for w in objects["wires"]]
    lines += ["JUNCTION|" + "|".join(map(r, j)) for j in objects["junctions"]]
    lines += [f"LABEL|{r(x)}|{r(y)}|{o}|{j}|{t}" for x, y, o, j, t in objects["labels"]]
    lines += [f"PORT|{r(x)}|{r(y)}|{o}|{s}|{t}" for x, y, o, s, t in objects["ports"]]
    if save:
        lines.append("SAVE")
    return lines
