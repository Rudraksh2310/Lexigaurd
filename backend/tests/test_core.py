"""Core pipeline tests. Run from backend/:  python -m pytest tests -q"""
import json
import pathlib

import pytest

import llm_client
import parser as P
from doc_gate import rule_based_check
from highlighter import Locator, finalize_flags, compute_overall
from report import build_docx, build_pdf

SAMPLES = pathlib.Path(__file__).resolve().parents[2] / "samples"


def read(name):
    return (SAMPLES / name).read_text(encoding="utf-8")


def test_resume_is_rejected_without_override():
    with pytest.raises(P.DocumentRejected) as e:
        P.analyze_and_store("cv.txt", read("resume_sample.txt").encode(), use_llm=False, force=True)
    assert e.value.check["verdict"] == "not_legal" and not e.value.check["can_override"]


def test_uncertain_document_can_be_forced():
    text = read("borderline_letter.txt")
    assert rule_based_check(text)["verdict"] == "uncertain"
    out = P.analyze_and_store("x.txt", text.encode(), use_llm=False, doc_type="service_agreement", force=True)
    assert out["analysis"]["document_type"] == "service_agreement"


def test_offer_letter_detected_as_employment_with_bond_flag():
    out = P.analyze_and_store("offer.txt", read("offer_letter_sample.txt").encode(), use_llm=False)
    a = out["analysis"]
    assert a["document_type"] == "employment" and a["perspective"] == "employee"
    cats = {f["category"] for f in a["flags"]}
    assert {"Service Bond / Training Cost Recovery", "Original Documents Retention", "Termination Without Reason"} <= cats


def test_every_flag_offset_matches_its_quote():
    for name in ("sample_contract.txt", "offer_letter_sample.txt"):
        out = P.analyze_and_store(name, read(name).encode(), use_llm=False)
        text = out["document"]["text"]
        for f in out["analysis"]["flags"]:
            assert text[f["start"]:f["end"]] == f["clause_text"]


def test_negation_is_not_flagged():
    cands = P.find_candidate_clauses("This Agreement shall not automatically renew at the end of the term.")
    assert not any("Auto-Renewal" in c["suspected_categories"] for c in cands)


def test_locator_tolerates_spacing_quotes_and_case():
    loc = Locator("The parties\n  waive the right to a jury trial.")
    assert loc.find("the PARTIES waive the right to a “jury trial”") is not None
    assert Locator("abc").find("completely invented sentence here") is None


def test_llm_output_is_normalised_grounded_and_rescored(monkeypatch):
    text = read("sample_contract.txt")
    fake = {"contract_summary": "s", "overall_risk_score": "Low", "flags": [
        {"category": "auto-renewal", "risk_level": "HIGH", "clause_text": "shall automatically renew for successive terms"},
        {"category": "Nonsense", "risk_level": "??", "clause_text": "Vendor shall pay Customer one million dollars."},
    ]}
    monkeypatch.setattr(llm_client, "_call_llm", lambda s, u: json.dumps(fake))
    monkeypatch.setattr("doc_gate.llm_check", lambda t: None)
    a = P.analyze_and_store("c.txt", text.encode(), use_llm=True)["analysis"]
    assert a["engine"] == "llm" and a["total_flags"] == 1 and a["grounding_dropped"] == 1
    assert a["flags"][0]["category"] == "Auto-Renewal" and a["flags"][0]["risk_level"] == "High"
    assert a["overall_risk_score"] == "High"  # recomputed, not the model's "Low"


def test_overall_rule():
    assert compute_overall([{"risk_level": "Low"}, {"risk_level": "Medium"}]) == "Medium"


def test_reports_build():
    out = P.analyze_and_store("offer.txt", read("offer_letter_sample.txt").encode(), use_llm=False)
    data = {"filename": "offer.txt", "uploaded_at": None, "document_text": out["document"]["text"], **out["analysis"]}
    assert build_pdf(data)[:4] == b"%PDF"
    assert build_docx(data)[:2] == b"PK"
