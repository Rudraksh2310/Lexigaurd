"""
doc_types.py — the document types a user can choose on the analyze
page, and what each one changes:

  * which keyword rule packs run (rules.py)
  * whose side risk is judged from (the "perspective")
  * which categories the LLM is told to look for

"auto" lets doc_gate.py pick the type from the text.
"""

from rules import GENERAL_RULES, EMPLOYMENT_RULES, RENTAL_RULES, TOS_RULES

GENERAL_CATEGORIES = list(GENERAL_RULES.keys())

DOC_TYPES = {
    "service_agreement": {
        "label": "Commercial / service agreement",
        "hint": "Vendor, client, MSA, SaaS, supply or partnership contracts",
        "perspectives": ["customer", "vendor"],
        "default_perspective": "customer",
        "extra_rules": {},
    },
    "employment": {
        "label": "Employment & joining documents",
        "hint": "Offer letter, appointment letter, employment agreement, service bond, internship letter",
        "perspectives": ["employee", "employer"],
        "default_perspective": "employee",
        "extra_rules": EMPLOYMENT_RULES,
    },
    "nda": {
        "label": "NDA / confidentiality agreement",
        "hint": "One-way or mutual non-disclosure agreements",
        "perspectives": ["receiving party", "disclosing party"],
        "default_perspective": "receiving party",
        "extra_rules": {},
    },
    "freelance": {
        "label": "Freelance / consulting agreement",
        "hint": "Independent contractor, consultant, statement of work",
        "perspectives": ["freelancer", "client"],
        "default_perspective": "freelancer",
        "extra_rules": {},
    },
    "rental": {
        "label": "Rent / lease agreement",
        "hint": "Residential or commercial lease, leave and licence",
        "perspectives": ["tenant", "landlord"],
        "default_perspective": "tenant",
        "extra_rules": RENTAL_RULES,
    },
    "terms_of_service": {
        "label": "Terms of service / privacy policy",
        "hint": "Website, app, or platform terms a user accepts",
        "perspectives": ["user"],
        "default_perspective": "user",
        "extra_rules": TOS_RULES,
    },
    "other_legal": {
        "label": "Other legal document",
        "hint": "Any other agreement, deed, or policy",
        "perspectives": ["signing party"],
        "default_perspective": "signing party",
        "extra_rules": {},
    },
}

AUTO = "auto"
VALID_CHOICES = [AUTO] + list(DOC_TYPES.keys())


def rules_for(doc_type: str) -> dict:
    """General rules plus the type's extra pack. Type-specific rules come
    first so, e.g., an employment 'Termination Without Reason' is preferred
    over the generic 'Termination Risk' for the same sentence."""
    info = DOC_TYPES.get(doc_type, DOC_TYPES["other_legal"])
    merged = dict(info["extra_rules"])
    merged.update(GENERAL_RULES)
    return merged


def categories_for(doc_type: str) -> list[str]:
    return list(rules_for(doc_type).keys()) + ["Other"]


def resolve_perspective(doc_type: str, requested: str | None) -> str:
    info = DOC_TYPES.get(doc_type, DOC_TYPES["other_legal"])
    if requested and requested.strip().lower() in info["perspectives"]:
        return requested.strip().lower()
    return info["default_perspective"]


def public_list() -> list[dict]:
    """Shape served by GET /api/document-types for the dropdown."""
    return [{"value": AUTO, "label": "Auto-detect", "hint": "Let LexiGuard work out the type",
             "perspectives": [], "default_perspective": None}] + [
        {"value": k, "label": v["label"], "hint": v["hint"],
         "perspectives": v["perspectives"], "default_perspective": v["default_perspective"]}
        for k, v in DOC_TYPES.items()
    ]
