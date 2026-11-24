"""Build functional acknowledgements: X12 997 and EDIFACT CONTRL, accepted or rejected."""
from __future__ import annotations

from datetime import datetime, timezone

from pdoc.parsers import edifact, x12


def _now() -> datetime:
    return datetime.now(timezone.utc)


def build_997(original: bytes, accepted: bool, control: int, our_id: str, error_segment: tuple[str, int] | None = None) -> bytes:
    """Acknowledge every transaction set in the first functional group of `original`."""
    ic = x12.parse(original.decode("utf-8", errors="replace"))
    isa, gs = ic.segments[0], ic.find("GS")[0]
    sets = ic.find("ST")
    now = _now()
    ctl = f"{control:09d}"
    partner = isa.get(6).strip()
    segs = [
        ["ISA", "00", " " * 10, "00", " " * 10, "ZZ", our_id.ljust(15), isa.get(5), partner.ljust(15),
         now.strftime("%y%m%d"), now.strftime("%H%M"), "U", "00401", ctl, "0", isa.get(15), ">"],
        ["GS", "FA", our_id, partner, now.strftime("%Y%m%d"), now.strftime("%H%M"), str(control), "X", "004010"],
        ["ST", "997", "0001"],
        ["AK1", gs.get(1), gs.get(6)],
    ]
    for st in sets:
        segs.append(["AK2", st.get(1), st.get(2)])
        if not accepted and error_segment:
            tag, position = error_segment
            segs.append(["AK3", tag, str(position), "", "8"])  # 8 = segment has data element errors
        segs.append(["AK5", "A" if accepted else "R"] + ([] if accepted else ["5"]))
    n = str(len(sets))
    segs.append(["AK9", "A" if accepted else "R", n, n, n if accepted else "0"])
    segs.append(["SE", str(len(segs) - 2 + 1), "0001"])
    segs.append(["GE", "1", str(control)])
    segs.append(["IEA", "1", ctl])
    return "".join("*".join(s) + "~\n" for s in segs).encode()


def build_contrl(original: bytes, accepted: bool, control: int, our_id: str) -> bytes:
    ic = edifact.parse(original.decode("utf-8", errors="replace"))
    unb = ic.segments[0]
    now = _now()
    action = "7" if accepted else "4"  # 7 = acknowledged, 4 = rejected
    body = [f"UCI+{unb.el(5)}+{unb.el(2, 0)}:{unb.el(2, 1) or 'ZZ'}+{unb.el(3, 0)}:{unb.el(3, 1) or 'ZZ'}+{action}'"]
    for unh in ic.find("UNH"):
        msg_id = ":".join(unh.elements[2])
        body.append(f"UCM+{unh.el(1)}+{msg_id}+{action}'")
    lines = ["UNA:+.? '",
             f"UNB+UNOC:3+{our_id}:ZZ+{unb.el(2, 0)}:ZZ+{now:%y%m%d}:{now:%H%M}+{control}'",
             "UNH+1+CONTRL:D:3:UN'", *body, f"UNT+{len(body) + 2}+1'", f"UNZ+1+{control}'"]
    return ("\n".join(lines) + "\n").encode()
