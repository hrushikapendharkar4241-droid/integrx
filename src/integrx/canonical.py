"""Transform partner formats into one canonical model.

Whatever arrives (X12, EDIFACT, EPCIS XML or CSV), the rest of the platform only sees these documents:
  order      : po_number, order_date, buyer, ship_to, lines[]
  shipment   : shipment_id, po_number, ship_date, ship_to, items[], containers[]
  inventory  : rows[]
  ack        : acknowledged group/message and whether it was accepted
"""
from __future__ import annotations

import csv
import io

from lxml import etree
from pdoc.parsers import edifact, x12
from pdoc.rules import epcis_rules

BOM = b"\xef\xbb\xbf"


class TransformError(Exception):
    pass


def _text(data: bytes) -> str:
    return (data[3:] if data.startswith(BOM) else data).decode("utf-8", errors="replace")


def _x12_date(v: str) -> str | None:
    v = v.strip()
    return f"{v[:4]}-{v[4:6]}-{v[6:8]}" if len(v) == 8 and v.isdigit() else None


def from_x12(data: bytes) -> dict:
    ic = x12.parse(_text(data))
    st = ic.find("ST")
    if not st:
        raise TransformError("no ST segment")
    code = st[0].get(1)
    segs = ic.segments
    isa = segs[0]
    sender = isa.get(6).strip()
    if code == "850":
        beg = ic.find("BEG")[0]
        ship_to = next((s for s in segs if s.tag == "N1" and s.get(1) == "ST"), None)
        buyer = next((s for s in segs if s.tag == "N1" and s.get(1) == "BY"), None)
        lines = [{"line": s.get(1), "item": s.get(7), "qty": float(s.get(2)), "uom": s.get(3),
                  "unit_price": float(s.get(4) or 0)} for s in ic.find("PO1")]
        return {"doc_type": "order", "source_format": "X12 850", "sender": sender, "po_number": beg.get(3),
                "order_date": _x12_date(beg.get(5)), "buyer": buyer.get(2) if buyer else None,
                "ship_to": {"name": ship_to.get(2), "id": ship_to.get(4)} if ship_to else None, "lines": lines}
    if code == "856":
        bsn = ic.find("BSN")[0]
        prf = ic.find("PRF")
        dtm = next((s for s in ic.find("DTM") if s.get(1) == "011"), None)
        ship_to = next((s for s in segs if s.tag == "N1" and s.get(1) == "ST"), None)
        items = []
        for lin in ic.find("LIN"):
            sn1 = next((s for s in segs[lin.index:] if s.tag == "SN1"), None)
            items.append({"item": lin.get(3), "qty": float(sn1.get(2)) if sn1 else None, "serials": [],
                          "lot": None, "expiry": None})
        return {"doc_type": "shipment", "source_format": "X12 856", "sender": sender, "shipment_id": bsn.get(2),
                "po_number": prf[0].get(1) if prf else None, "ship_date": _x12_date(dtm.get(2)) if dtm else None,
                "ship_to": {"name": ship_to.get(2), "id": ship_to.get(4)} if ship_to else None,
                "items": items, "containers": []}
    if code == "997":
        ak1 = ic.find("AK1")
        ak9 = ic.find("AK9")
        return {"doc_type": "ack", "source_format": "X12 997", "sender": sender,
                "acknowledges": {"functional_id": ak1[0].get(1), "group": ak1[0].get(2)} if ak1 else None,
                "accepted": bool(ak9) and ak9[0].get(1) == "A"}
    raise TransformError(f"no canonical mapping for X12 {code}")


def from_edifact(data: bytes) -> dict:
    ic = edifact.parse(_text(data))
    unh = ic.find("UNH")
    if not unh:
        raise TransformError("no UNH segment")
    mtype = unh[0].el(2, 0)
    sender = ic.segments[0].el(2, 0)

    def dtm(q: str) -> str | None:
        for s in ic.find("DTM"):
            if s.el(1, 0) == q:
                v = s.el(1, 1)
                return f"{v[:4]}-{v[4:6]}-{v[6:8]}" if len(v) >= 8 else v
        return None

    def lines() -> list[dict]:
        out, current = [], None
        for s in ic.segments:
            if s.tag == "LIN":
                current = {"line": s.el(1), "item": s.el(3, 0), "qty": None}
                out.append(current)
            elif s.tag == "QTY" and current is not None:
                current["qty"] = float(s.el(1, 1))
        return out

    nad = {s.el(1): s.el(2, 0) for s in ic.find("NAD")}
    bgm = ic.find("BGM")
    if mtype == "ORDERS":
        return {"doc_type": "order", "source_format": "EDIFACT ORDERS", "sender": sender,
                "po_number": bgm[0].el(2) if bgm else None, "order_date": dtm("137"), "buyer": nad.get("BY"),
                "ship_to": {"name": None, "id": nad.get("DP") or nad.get("ST")},
                "lines": [{**ln, "uom": None, "unit_price": None} for ln in lines()]}
    if mtype == "DESADV":
        return {"doc_type": "shipment", "source_format": "EDIFACT DESADV", "sender": sender,
                "shipment_id": bgm[0].el(2) if bgm else None, "po_number": None, "ship_date": dtm("11"),
                "ship_to": {"name": None, "id": nad.get("ST") or nad.get("DP")},
                "items": [{"item": ln["item"], "qty": ln["qty"], "serials": [], "lot": None, "expiry": None}
                          for ln in lines()], "containers": []}
    if mtype == "CONTRL":
        uci = ic.find("UCI")
        return {"doc_type": "ack", "source_format": "EDIFACT CONTRL", "sender": sender,
                "acknowledges": {"interchange": uci[0].el(1)} if uci else None,
                "accepted": bool(uci) and uci[0].el(4) in ("7", "8")}
    raise TransformError(f"no canonical mapping for EDIFACT {mtype}")


def from_epcis(data: bytes) -> dict:
    root = epcis_rules.parse(data[3:] if data.startswith(BOM) else data)
    ns = {"cbvmda": epcis_rules.NS_MDA}
    events = epcis_rules._events(root)
    lots: dict[str, dict] = {}
    for ev in events:
        if epcis_rules._child_text(ev, "bizStep") == epcis_rules.COMMISSIONING:
            lot = ev.find(".//cbvmda:lotNumber", ns)
            exp = ev.find(".//cbvmda:itemExpirationDate", ns)
            for urn in epcis_rules._epcs(ev, "epcList"):
                lots[urn] = {"lot": lot.text if lot is not None else None, "expiry": exp.text if exp is not None else None}
    from pdoc.gs1 import parse_epc

    by_gtin: dict[str, dict] = {}
    for urn, meta in lots.items():
        epc = parse_epc(urn)
        if not epc.gtin:
            continue
        item = by_gtin.setdefault(epc.gtin, {"item": None, "gtin": epc.gtin, "qty": 0, "serials": [],
                                             "lot": meta["lot"], "expiry": meta["expiry"]})
        item["qty"] += 1
        item["serials"].append(epc.serial)
    shipping = next((ev for ev in events if epcis_rules._child_text(ev, "bizStep") == epcis_rules.SHIPPING), None)
    po = None
    dest = None
    ship_date = None
    containers: list[str] = []
    if shipping is not None:
        ship_date = (epcis_rules._child_text(shipping, "eventTime") or "")[:10] or None
        containers = [u for u in epcis_rules._epcs(shipping, "epcList") if u.startswith("urn:epc:id:sscc:")]
        for el in shipping.iter():
            if not isinstance(el.tag, str):
                continue
            name = etree.QName(el).localname
            if name == "bizTransaction" and (el.get("type") or "").endswith(":po"):
                po = (el.text or "").rsplit(":", 1)[-1]
            elif name == "destination" and el.get("type") == epcis_rules.OWNING_PARTY:
                dest = (el.text or "").strip()
    return {"doc_type": "shipment", "source_format": "EPCIS 1.2", "sender": None,
            "shipment_id": containers[0].rsplit(":", 1)[-1] if containers else None, "po_number": po,
            "ship_date": ship_date, "ship_to": {"name": None, "id": dest},
            "items": list(by_gtin.values()), "containers": containers}


def from_csv(data: bytes) -> dict:
    text = _text(data)
    from pdoc.rules.csv_rules import sniff

    rows = list(csv.DictReader(io.StringIO(text), delimiter=sniff(text)))
    return {"doc_type": "inventory", "source_format": "CSV", "rows": rows}


def transform(fmt: str, data: bytes) -> dict:
    try:
        if fmt == "x12":
            return from_x12(data)
        if fmt == "edifact":
            return from_edifact(data)
        if fmt == "epcis-xml":
            return from_epcis(data)
        if fmt == "csv":
            return from_csv(data)
    except TransformError:
        raise
    except Exception as e:  # a mapping bug must become a classified failure, not a crash
        raise TransformError(f"{type(e).__name__}: {e}") from e
    raise TransformError(f"no canonical mapping for format {fmt}")


def business_key(doc: dict) -> str | None:
    return doc.get("po_number") or doc.get("shipment_id")
