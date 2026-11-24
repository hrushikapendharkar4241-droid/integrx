"""Rule-based failure classification.

Every failure gets a code, a severity (P1 highest), a runbook, and a flag saying whether replaying the
archived original can fix it. Payload problems are classified from Payload Doctor rule IDs; transport and
downstream problems from the error the pipeline saw. There is no AI here: these are explicit rules.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Classification:
    code: str
    severity: str
    title: str
    runbook: str
    replayable: bool  # True = our side can fix it and replay; False = partner must send a corrected file
    customer_impact: str


RB_AS2_CERT = "RB-01-as2-certificate-expiry.md"
RB_ACK = "RB-02-missing-acknowledgement.md"
RB_SFTP = "RB-03-sftp-authentication-failure.md"
RB_PAYLOAD = "RB-04-payload-validation-failure.md"
RB_DOWNSTREAM = "RB-05-downstream-timeout.md"

CATALOGUE: dict[str, Classification] = {c.code: c for c in [
    # Not replayable: a message whose signature could not be verified must not be trusted after the fact.
    # The archived copy is kept as evidence; the partner resends once the new certificate is installed.
    Classification("AS2_CERT_INVALID", "P1", "AS2 partner certificate invalid or expired", RB_AS2_CERT, False,
                    "Every AS2 message from this partner is rejected until the certificate is renewed."),
    Classification("AS2_SECURITY_ERROR", "P2", "AS2 decryption or signature failure", RB_AS2_CERT, False,
                    "Messages from this partner cannot be opened or trusted; nothing is processed."),
    Classification("AS2_MDN_NOT_DELIVERED", "P2", "Asynchronous MDN could not be delivered to partner", RB_ACK, False,
                    "We processed the document, but the partner has no receipt and may resend it (duplicate risk)."),
    Classification("ACK_NOT_SENT", "P3", "Functional acknowledgement (997/CONTRL) not sent", RB_ACK, True,
                    "The partner cannot confirm we accepted the document and may chase or resend it."),
    Classification("SFTP_AUTH_FAILURE", "P2", "Partner cannot authenticate to SFTP", RB_SFTP, False,
                    "The partner cannot upload files, so no documents arrive from them."),
    Classification("INVALID_XML", "P3", "Payload is not well-formed XML", RB_PAYLOAD, False,
                    "The document was rejected; the partner must send a corrected file."),
    Classification("DUPLICATE_SERIAL", "P2", "Duplicate serial number in serialized shipment data", RB_PAYLOAD, False,
                    "Receiving cannot verify the shipment; the goods may be quarantined until corrected data arrives."),
    Classification("AGGREGATION_MISMATCH", "P2", "Case/pallet contents do not match commissioned units", RB_PAYLOAD, False,
                    "Receiving cannot verify the shipment hierarchy; goods may be held at the dock."),
    Classification("EPCIS_DATA_ERROR", "P3", "Serialization (EPCIS) data incomplete or invalid", RB_PAYLOAD, False,
                    "The shipment data was rejected; the partner must send corrected EPCIS data."),
    Classification("EDI_ENVELOPE_ERROR", "P3", "EDI envelope/control numbers invalid or file truncated", RB_PAYLOAD, False,
                    "The interchange was rejected as a whole; none of its documents were processed."),
    Classification("EDI_MAPPING_ERROR", "P3", "EDI business data missing or invalid", RB_PAYLOAD, False,
                    "The document was rejected; the partner's mapping must be fixed and the file resent."),
    Classification("PARTNER_REJECTED_ACK", "P2", "Partner acknowledgement reports a rejection", RB_PAYLOAD, False,
                    "A document we sent was refused by the partner and was not processed on their side."),
    Classification("CSV_DATA_ERROR", "P3", "CSV file has invalid or missing data", RB_PAYLOAD, False,
                    "Rows with errors were not loaded; the partner must correct and resend the file."),
    Classification("FILE_FORMAT_ERROR", "P3", "File empty, unreadable or in an unknown format", RB_PAYLOAD, False,
                    "Nothing in the file could be processed."),
    Classification("TRANSFORM_ERROR", "P3", "Valid payload could not be mapped to the canonical model", RB_PAYLOAD, True,
                    "The document passed validation but could not be mapped; it is held until the mapping is fixed."),
    Classification("DOWNSTREAM_TIMEOUT", "P2", "Downstream order service timed out", RB_DOWNSTREAM, True,
                    "The document was received and valid but not delivered to the order system."),
    Classification("DOWNSTREAM_SERVER_ERROR", "P2", "Downstream order service returned a server error", RB_DOWNSTREAM, True,
                    "The document was received and valid but not delivered to the order system."),
    Classification("DOWNSTREAM_AUTH_FAILURE", "P2", "Downstream order service rejected our credentials", RB_DOWNSTREAM, True,
                    "No documents can be delivered to the order system until credentials are fixed."),
    Classification("DOWNSTREAM_REJECTED", "P3", "Downstream order service rejected the document", RB_DOWNSTREAM, False,
                    "The order system refused this document; business data must be corrected."),
    Classification("UNKNOWN", "P3", "Unclassified failure", RB_PAYLOAD, False,
                    "The document was not processed; investigate the timeline."),
]}

ENVELOPE_RULES = {"X12-001", "X12-002", "X12-003", "X12-004", "X12-005", "X12-006", "X12-007", "X12-010",
                  "X12-011", "EDI-001", "EDI-002", "EDI-003", "EDI-004", "EDI-005", "EDI-012"}


def from_rule(rule_id: str) -> Classification:
    """Classify a payload failure from the first error-level Payload Doctor rule."""
    if rule_id == "XML-001":
        return CATALOGUE["INVALID_XML"]
    if rule_id == "EPC-004":
        return CATALOGUE["DUPLICATE_SERIAL"]
    if rule_id == "EPC-005":
        return CATALOGUE["AGGREGATION_MISMATCH"]
    if rule_id.startswith(("EPC-", "XML-")):
        return CATALOGUE["EPCIS_DATA_ERROR"]
    if rule_id in ("X12-025", "EDI-011"):
        return CATALOGUE["PARTNER_REJECTED_ACK"]
    if rule_id in ENVELOPE_RULES:
        return CATALOGUE["EDI_ENVELOPE_ERROR"]
    if rule_id.startswith(("X12-", "EDI-")):
        return CATALOGUE["EDI_MAPPING_ERROR"]
    if rule_id.startswith("CSV-"):
        return CATALOGUE["CSV_DATA_ERROR"]
    if rule_id.startswith("GEN-"):
        return CATALOGUE["FILE_FORMAT_ERROR"]
    return CATALOGUE["UNKNOWN"]


def from_downstream(status_code: int | None, timed_out: bool) -> Classification:
    if timed_out:
        return CATALOGUE["DOWNSTREAM_TIMEOUT"]
    if status_code in (401, 403):
        return CATALOGUE["DOWNSTREAM_AUTH_FAILURE"]
    if status_code is None or status_code >= 500:
        return CATALOGUE["DOWNSTREAM_SERVER_ERROR"]
    return CATALOGUE["DOWNSTREAM_REJECTED"]


def from_as2(detail: str) -> Classification:
    d = (detail or "").lower()
    if "certificate" in d:
        return CATALOGUE["AS2_CERT_INVALID"]
    return CATALOGUE["AS2_SECURITY_ERROR"]


def get(code: str) -> Classification:
    return CATALOGUE.get(code, CATALOGUE["UNKNOWN"])
