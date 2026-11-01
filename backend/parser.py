"""
parser.py — LexiGuard AI document parsing pipeline.

Flow:
    extract_text()               -> raw text from PDF/DOCX/TXT
    check_document()             -> is this a legal document? which type? (doc_gate.py)
    find_candidate_clauses()     -> keyword pre-filter using the type's rule packs
    extract_rule_based_verdicts()-> deterministic analysis (default, no LLM)
    get_llm_verdicts()           -> (optional, USE_LLM=true) LLM verdict engine
    finalize_flags()             -> ground quotes, attach highlight offsets, score (highlighter.py)
    save_analysis()              -> writes Contract + RedFlag rows to Postgres

Call analyze_and_store() to run the whole pipeline end-to-end.
"""

import io
import os
import re

from pypdf import PdfReader
from docx import Document

from db import Contract, RedFlag, SessionLocal
from llm_client import get_llm_verdicts
from rules import GENERAL_RULES, keyword_hit
from doc_types import AUTO, DOC_TYPES, VALID_CHOICES, rules_for, categories_for, resolve_perspective
from doc_gate import check_document, rejection_message
from highlighter import finalize_flags

# Backwards-compatible names (dashboard.py / older code may import these)
RISK_RULES = GENERAL_RULES
RISK_KEYWORDS = {cat: data["keywords"] for cat, data in RISK_RULES.items()}

MIN_TEXT_CHARS = 200


class DocumentRejected(ValueError):
    """Raised when the upload doesn't look like a legal document.
    `.check` holds the doc_gate result so the API can explain why."""

    def __init__(self, check: dict):
        self.check = check
        super().__init__(rejection_message(check))


# ---------------------------------------------------------------------------
# 1. Text extraction
# ---------------------------------------------------------------------------

def _docx_text(content: bytes) -> str:
    """Paragraphs AND tables, in document order (offer letters often keep
    salary, bond and notice terms in tables)."""
    doc = Document(io.BytesIO(content))
    lines = []
    for block in doc.element.body.iterchildren():
        tag = block.tag.rsplit("}", 1)[-1]
        if tag == "p":
            lines.append("".join(t.text or "" for t in block.iter() if t.tag.endswith("}t")))
        elif tag == "tbl":
            for row in block.iter():
                if row.tag.endswith("}tr"):
                    cells = []
                    for cell in row.iterchildren():
                        if cell.tag.endswith("}tc"):
                            cells.append(" ".join(
                                "".join(t.text or "" for t in p.iter() if t.tag.endswith("}t"))
                                for p in cell.iter() if p.tag.endswith("}p")
                            ).strip())
                    lines.append(" | ".join(c for c in cells if c))
    return "\n".join(lines)


def extract_text(filename: str, content: bytes) -> str:
    lower = filename.lower()
    if lower.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            raise ValueError("This PDF is password-protected. Remove the password and upload it again.")
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if lower.endswith(".docx"):
        return _docx_text(content)
    if lower.endswith(".txt"):
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return content.decode("cp1252", errors="ignore")
    raise ValueError("Unsupported file type. Upload a PDF, DOCX, or TXT.")


# ---------------------------------------------------------------------------
# 2. Candidate clause detection
# ---------------------------------------------------------------------------

_ABBREVIATIONS = ["Rs", "No", "Nos", "Inc", "Ltd", "Pvt", "Co", "Corp", "Mr", "Mrs", "Ms", "Dr", "Sr", "Jr",
                  "Sec", "Art", "Cl", "Para", "viz", "e.g", "i.e", "etc", "approx", "St", "vs", "Govt", "Dept"]
_ABBR_RE = re.compile(r"\b(" + "|".join(re.escape(a) for a in _ABBREVIATIONS) + r")\.", re.IGNORECASE)
_DOT = "․"  # placeholder for protected full stops (same length, so offsets don't move)
_NUM_PREFIX = re.compile(r"^\s*(?:(?:Section|Article|Clause|Schedule|Annexure)\s+)?[0-9IVXivx]+(?:\.[0-9]+)*[.)]?\s*", re.IGNORECASE)


def _is_heading(line: str) -> bool:
    """Short, title-cased lines such as '4. Service Bond' or
    'Section 5. Indemnification and Defense.'"""
    s = line.strip()
    if not s or len(s) > 90 or len(s.split()) > 10 or s.endswith((":", ",")):
        return False
    rest = _NUM_PREFIX.sub("", s)
    # "4. Licence fee and deposit": numbered, short, no full stop at the end
    if rest != s and rest and len(rest.split()) <= 7 and not s.endswith((".", ";")) and rest[0].isupper():
        return True
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'’-]*", rest) if len(w) > 3]
    return bool(words) and all(w[0].isupper() for w in words)


def _split_into_clauses(text: str) -> list[dict]:
    """Splits text into sentence-sized clauses, remembering the heading
    each one sits under (used as section_reference).

    Returns [{"text": clause with whitespace collapsed, "section": heading or None}]."""
    protected = _ABBR_RE.sub(lambda m: m.group(1) + _DOT, text)
    paragraphs, current, heading = [], [], None
    for line in protected.splitlines():
        if not line.strip():
            if current:
                paragraphs.append(("\n".join(current), heading))
                current = []
        elif _is_heading(line):
            if current:
                paragraphs.append(("\n".join(current), heading))
                current = []
            heading = re.sub(r"\s+", " ", line).strip().rstrip(".").replace(_DOT, ".")
        else:
            current.append(line)
    if current:
        paragraphs.append(("\n".join(current), heading))

    clauses = []
    for para, head in paragraphs:
        for sent in re.split(r"(?<=[.;])\s+(?=[A-Z0-9(\"“])", para):
            c = re.sub(r"\s+", " ", sent).strip().replace(_DOT, ".")
            if len(c) > 20 and len(c.split()) >= 5:
                clauses.append({"text": c, "section": head})
    return clauses


def _extract_section_ref(clause: str) -> str | None:
    match = re.search(r"\b(Section|Article|Clause|Paragraph|Sec\.)\s+([0-9]+(\.[0-9]+)*|[A-Z]+)\b", clause, re.IGNORECASE)
    if match:
        return match.group(0).strip()
    match_num = re.match(r"^\s*([0-9]+(\.[0-9]+)+)\b", clause)
    if match_num:
        return f"Section {match_num.group(1).strip()}"
    return None


def find_candidate_clauses(text: str, rules: dict | None = None) -> list[dict]:
    """Scans clauses against risk keywords. Categories are ordered by how
    many of their keywords matched (most specific first)."""
    rules = rules or GENERAL_RULES
    candidates = []
    for i, cl in enumerate(_split_into_clauses(text)):
        lower = cl["text"].lower()
        scored = []
        for order, (cat, rule) in enumerate(rules.items()):
            hits = sum(1 for kw in rule["keywords"] if keyword_hit(lower, kw))
            if hits:
                scored.append((-hits, order, cat))
        if scored:
            candidates.append({
                "clause_text": cl["text"],
                "suspected_categories": [c for _, _, c in sorted(scored)],
                "section_reference": _extract_section_ref(cl["text"]) or cl["section"],
                "clause_index": i,
            })
    return candidates


# ---------------------------------------------------------------------------
# 3. Rule-based analysis engine (No LLM required)
# ---------------------------------------------------------------------------

def extract_rule_based_verdicts(filename: str, text: str, candidates: list[dict],
                                rules: dict | None = None, doc_label: str = "document",
                                perspective: str = "signing party") -> dict:
    rules = rules or GENERAL_RULES
    flags, seen, last_index = [], set(), None
    for cand in candidates:
        clause = cand.get("clause_text", "").strip()
        if not clause or clause in seen:
            continue
        cat = next((c for c in cand.get("suspected_categories", []) if c in rules), None)
        if not cat:
            continue
        seen.add(clause)
        idx = cand.get("clause_index")
        prev = flags[-1] if flags else None
        # Next sentence about the same issue under the same heading -> one longer highlight
        if (prev and prev["category"] == cat and idx is not None and last_index == idx - 1
                and prev["section_reference"] == cand.get("section_reference")):
            prev["clause_text"] += " " + clause
            last_index = idx
            continue
        rule = rules[cat]
        flags.append({
            "category": cat,
            "risk_level": rule["risk_level"],
            "clause_text": clause,
            "section_reference": cand.get("section_reference") or _extract_section_ref(clause),
            "plain_english_explanation": rule["explanation"],
            "why_it_matters": rule["why_it_matters"],
            "suggested_action": rule["suggested_action"],
        })
        last_index = idx

    cats = sorted({f["category"] for f in flags})
    highs = sum(1 for f in flags if f["risk_level"] == "High")
    meds = sum(1 for f in flags if f["risk_level"] == "Medium")
    if flags:
        summary = (
            f"Rule-based review ({doc_label}, {perspective}'s side) found {len(flags)} clause(s) worth a look, "
            f"in: {', '.join(cats[:4])}{' and others' if len(cats) > 4 else ''}. "
            f"{highs} High and {meds} Medium risk."
        )
    else:
        summary = f"Rule-based review ({doc_label}, {perspective}'s side) found no standard risk clauses."
    return {"contract_summary": summary, "flags": flags}


# ---------------------------------------------------------------------------
# 4. Persist to Postgres
# ---------------------------------------------------------------------------

def save_analysis(filename: str, raw_text_length: int, analysis: dict,
                  document_text: str | None = None, document_type: str | None = None,
                  perspective: str | None = None, user_id=None) -> tuple[str, list[str]]:
    """Writes Contract + RedFlag rows. Returns (contract id, flag ids in order)."""
    session = SessionLocal()
    try:
        flags = analysis.get("flags", [])
        counts = {lv: sum(1 for f in flags if f.get("risk_level") == lv) for lv in ("High", "Medium", "Low")}

        contract = Contract(
            user_id=user_id,
            engine=analysis.get("engine"),
            filename=filename,
            contract_summary=analysis.get("contract_summary"),
            overall_risk_score=analysis.get("overall_risk_score"),
            total_flags=len(flags),
            raw_text_length=raw_text_length,
            high_count=counts["High"],
            medium_count=counts["Medium"],
            low_count=counts["Low"],
            document_type=document_type,
            perspective=perspective,
            document_text=document_text,
        )
        session.add(contract)
        session.flush()

        rows = []
        for f in flags:
            row = RedFlag(
                contract_id=contract.id,
                category=f.get("category", "Other")[:64],
                risk_level=f.get("risk_level", "Low"),
                clause_text=f.get("clause_text", ""),
                section_reference=f.get("section_reference"),
                plain_english_explanation=f.get("plain_english_explanation"),
                why_it_matters=f.get("why_it_matters"),
                suggested_action=f.get("suggested_action"),
                start_offset=f.get("start"),
                end_offset=f.get("end"),
            )
            session.add(row)
            rows.append(row)

        session.commit()
        return str(contract.id), [str(r.id) for r in rows]
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 5. End-to-end pipeline
# ---------------------------------------------------------------------------

def _use_llm_default() -> bool:
    """USE_LLM=true/false in .env; if it isn't set, use the LLM whenever a key is configured."""
    flag = os.getenv("USE_LLM")
    if flag is None or not flag.strip():
        from llm_client import llm_available
        return llm_available()
    return flag.strip().lower() in ("true", "1", "yes")


def analyze_document(filename: str, content: bytes, use_llm: bool | None = None,
                     doc_type: str = AUTO, perspective: str | None = None,
                     force: bool = False) -> dict:
    """Everything except saving. Raises ValueError for unreadable files and
    DocumentRejected for non-legal documents."""
    if doc_type not in VALID_CHOICES:
        raise ValueError(f"Unknown document type '{doc_type}'.")
    if use_llm is None:
        use_llm = _use_llm_default()

    text = extract_text(filename, content)
    if len(text.strip()) < MIN_TEXT_CHARS:
        raise ValueError(
            "Couldn't read enough text from this file. If it's a scanned PDF or a photo, "
            "upload a text-based PDF, DOCX, or TXT instead."
        )

    check = check_document(text, use_llm=use_llm)
    if not check["is_legal"] and not (force and check["can_override"]):
        raise DocumentRejected(check)

    resolved_type = doc_type if doc_type != AUTO else (check.get("detected_type") or "other_legal")
    info = DOC_TYPES[resolved_type]
    side = resolve_perspective(resolved_type, perspective)
    rules = rules_for(resolved_type)
    categories = categories_for(resolved_type)

    candidates = find_candidate_clauses(text, rules)

    analysis, engine = None, "rules"
    if use_llm:
        try:
            analysis = get_llm_verdicts(full_text=text, candidate_clauses=candidates,
                                        doc_label=info["label"], perspective=side, categories=categories)
            engine = "llm"
        except Exception as err:
            print(f"[LexiGuard] LLM call failed or unconfigured ({type(err).__name__}: {err}); using rule-based engine.")
    if analysis is None:
        analysis = extract_rule_based_verdicts(filename, text, candidates, rules, info["label"], side)

    analysis = finalize_flags(analysis, text, categories)
    analysis.update({
        "document_type": resolved_type,
        "document_label": info["label"],
        "perspective": side,
        "engine": engine,
        "doc_check": {k: check[k] for k in ("verdict", "detected_label", "confidence", "reasons", "method")},
        "type_mismatch": (doc_type != AUTO and check.get("detected_type") not in (None, "other_legal", doc_type)),
    })
    return {"text": text, "analysis": analysis}


def analyze_and_store(filename: str, content: bytes, use_llm: bool | None = None,
                      doc_type: str = AUTO, perspective: str | None = None,
                      force: bool = False, user_id=None) -> dict:
    result = analyze_document(filename, content, use_llm, doc_type, perspective, force)
    text, analysis = result["text"], result["analysis"]

    contract_id, flag_ids = save_analysis(
        filename, raw_text_length=len(text), analysis=analysis, document_text=text,
        document_type=analysis["document_type"], perspective=analysis["perspective"], user_id=user_id,
    )
    for f, fid in zip(analysis["flags"], flag_ids):
        f["id"] = fid
    return {"contract_id": contract_id, "analysis": analysis, "document": {"text": text}}
