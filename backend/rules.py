"""
rules.py — keyword rule packs used by the rule-based engine and as
hints for the LLM.

GENERAL_RULES apply to every legal document. Each document type in
doc_types.py adds its own extra pack (employment, rental, terms of
service). The keyword layer only proposes candidates; with USE_LLM=true
the LLM makes the final call.
"""

import re

# ---------------------------------------------------------------------------
# General commercial-contract rules (moved here from parser.py, unchanged)
# ---------------------------------------------------------------------------

GENERAL_RULES = {
    "Unlimited Liability": {
        "keywords": [
            "unlimited liability", "no cap on liability", "without limitation",
            "liable for any and all", "not subject to any liability cap",
            "in no event shall liability be limited",
        ],
        "risk_level": "High",
        "explanation": "This clause exposes the party to uncapped financial liability without any maximum ceiling or loss limit.",
        "why_it_matters": "A single operational dispute or legal claim could result in catastrophic financial damages exceeding the contract value.",
        "suggested_action": "Negotiate a mutual liability cap tied to fees paid over the preceding 12 months, and exclude indirect or consequential damages.",
    },
    "Indemnification": {
        "keywords": [
            "indemnify", "hold harmless", "defend and indemnify", "shall indemnify and defend",
            "indemnification", "indemnitee",
        ],
        "risk_level": "High",
        "explanation": "Obligates one party to defend, hold harmless, and reimburse the other for third-party claims, legal fees, and liabilities.",
        "why_it_matters": "Broad indemnification language can force you to pay for legal defense and settlements even when faults are shared or minor.",
        "suggested_action": "Ensure indemnification is reciprocal and strictly restricted to proven third-party IP infringement or gross negligence.",
    },
    "Non-Compete / Non-Solicit": {
        "keywords": [
            "non-compete", "shall not compete", "non-solicitation", "restrictive covenant",
            "shall not solicit", "covenant not to compete",
        ],
        "risk_level": "High",
        "explanation": "Restricts business activities, client engagements, or the recruiting of personnel during and after the contract term.",
        "why_it_matters": "Impedes commercial mobility, client relationships, and business development across markets.",
        "suggested_action": "Remove broad non-competes or narrow non-solicitation to direct client poaching for a maximum of 6 to 12 months.",
    },
    "Termination Risk": {
        "keywords": [
            "terminate immediately", "sole discretion", "without cause", "no refund",
            "terminate for convenience without", "immediate termination",
        ],
        "risk_level": "High",
        "explanation": "Grants one party the power to abruptly terminate the contract without cause or without a reasonable cure period.",
        "why_it_matters": "Creates business vulnerability, operational disruptions, and forfeiture of prepaid fees or investments.",
        "suggested_action": "Require mutual termination rights with at least 30 to 60 days written notice and a 30-day cure period for material breach.",
    },
    "IP Assignment": {
        "keywords": [
            "assigns all right, title", "work made for hire", "intellectual property shall belong",
            "exclusive property of", "hereby assigns all right",
        ],
        "risk_level": "High",
        "explanation": "Transfers all created intellectual property, code, designs, or inventions exclusively to the other party.",
        "why_it_matters": "You may surrender rights to background intellectual property, reusable libraries, and proprietary trade tools.",
        "suggested_action": "Carve out pre-existing background IP and grant a non-exclusive commercial license instead of full assignment.",
    },
    "Auto-Renewal": {
        "keywords": [
            "automatically renew", "auto-renew", "shall renew unless", "evergreen term", "automatic renewal",
        ],
        "risk_level": "Medium",
        "explanation": "The contract automatically extends for successive terms unless written notice is given within a narrow window.",
        "why_it_matters": "Missing the notification deadline locks you into another full term with ongoing payment obligations.",
        "suggested_action": "Require written renewal reminders 30–60 days prior to expiry, or change the mechanism to manual opt-in renewal.",
    },
    "Arbitration / Dispute Resolution": {
        "keywords": [
            "binding arbitration", "waive the right to a jury", "arbitration shall be final",
            "waive jury trial", "mandatory arbitration",
        ],
        "risk_level": "Medium",
        "explanation": "Mandates private binding arbitration and waives the right to court hearings and jury trials.",
        "why_it_matters": "Arbitration procedures can be costly with limited avenues for appeal or discovery compared to public court.",
        "suggested_action": "Ensure the arbitration venue is neutral and arbitration fees are split evenly between parties.",
    },
    "Hidden Fees": {
        "keywords": [
            "additional fees", "processing fee", "subject to change without notice",
            "late payment penalty", "at current standard rates", "additional charges may apply",
        ],
        "risk_level": "Medium",
        "explanation": "Allows unannounced price hikes, supplementary processing surcharges, or unilateral fee increases.",
        "why_it_matters": "Leads to unpredictable operational costs and unexpected invoice creep over time.",
        "suggested_action": "Mandate that any extra charges require prior written approval, and cap annual fee increases to a fixed 3–5%.",
    },
    "Payment Terms": {
        "keywords": [
            "due immediately", "net 10", "net 15", "interest at the rate of", "late charge of", "failure to pay within",
        ],
        "risk_level": "Medium",
        "explanation": "Imposes tight payment windows or heavy interest penalties on past-due balances.",
        "why_it_matters": "Short settlement cycles can strain cash flow and trigger avoidable penalty fees during administrative review.",
        "suggested_action": "Standardize to Net 30 or Net 45 days, with interest accruing only after written notice of delinquency.",
    },
    "Data Privacy": {
        "keywords": [
            "personal data", "data protection", "gdpr", "ccpa", "security breach",
            "data processing", "confidential customer data",
        ],
        "risk_level": "Medium",
        "explanation": "Governs the processing, handling, and security obligations for personal and sensitive corporate data.",
        "why_it_matters": "Regulatory non-compliance carries severe fines and third-party liability in the event of a security incident.",
        "suggested_action": "Include a standard Data Processing Addendum (DPA) specifying data protection standards and 48-hour breach notification.",
    },
    "Confidentiality Issues": {
        "keywords": [
            "confidential information", "non-disclosure", "shall remain confidential",
            "proprietary information", "trade secrets",
        ],
        "risk_level": "Low",
        "explanation": "Imposes confidentiality covenants restricting the disclosure or use of proprietary business information.",
        "why_it_matters": "Standard in commercial contracts, but overly broad scopes can restrict ordinary business communication.",
        "suggested_action": "Verify confidentiality is mutual with standard exclusions (publicly known, independently developed).",
    },
}

# ---------------------------------------------------------------------------
# Employment / job-joining documents (offer letter, appointment letter,
# employment agreement, service bond). Judged from the employee's side.
# ---------------------------------------------------------------------------

EMPLOYMENT_RULES = {
    "Original Documents Retention": {
        "keywords": [
            "original certificates", "original documents", "original degree", "original marksheet",
            "retain the original", "submit the original", "deposit the original",
        ],
        "risk_level": "High",
        "explanation": "The employer asks to keep your original educational or identity documents during employment.",
        "why_it_matters": "Holding your originals makes it hard to switch jobs or apply elsewhere, and loss or damage is hard to remedy.",
        "suggested_action": "Offer self-attested copies and in-person verification of originals instead of handing them over.",
    },
    "Service Bond / Training Cost Recovery": {
        "keywords": [
            "service bond", "bond period", "bond amount", "training cost", "cost of training",
            "liquidated damages", "shall be liable to pay the company", "shall pay the company a sum",
            "minimum period of service", "serve the company for a minimum",
        ],
        "risk_level": "High",
        "explanation": "You must stay for a fixed minimum period, or pay the employer a set amount if you leave early.",
        "why_it_matters": "Leaving for a better offer or personal reasons could cost you a large lump sum, which can trap you in the job.",
        "suggested_action": "Ask for a shorter bond, an amount reduced month by month, and a written basis for the training cost claimed.",
    },
    "Notice Period": {
        "keywords": [
            "notice period", "months' notice", "months notice", "days' notice", "days notice",
            "salary in lieu of notice", "pay in lieu of notice", "in lieu of notice",
        ],
        "risk_level": "Medium",
        "explanation": "Sets how much advance notice you must give before resigning, and what you pay if you leave sooner.",
        "why_it_matters": "A long or one-sided notice period can delay a new job or cost you salary to buy it out.",
        "suggested_action": "Ask for the same notice period on both sides and the option to buy out or waive notice.",
    },
    "Probation & Confirmation": {
        "keywords": [
            "probation", "probationary period", "extend the probation", "extend your probation",
        ],
        "risk_level": "Low",
        "explanation": "Your job is not confirmed until a probation period ends, and the employer may be able to extend it.",
        "why_it_matters": "During probation you can usually be let go on short notice, and open-ended extensions prolong that uncertainty.",
        "suggested_action": "Ask for a fixed probation length, a limit on extensions, and written reasons if it is extended.",
    },
    "Salary Deductions / Withholding": {
        "keywords": [
            "deduct from your salary", "deducted from your salary", "deduct from the salary",
            "withhold your salary", "withhold salary", "withhold the full and final",
            "forfeit", "recover from your dues", "adjusted against your salary",
        ],
        "risk_level": "Medium",
        "explanation": "Allows the employer to deduct money from your pay or hold back your final settlement.",
        "why_it_matters": "Your last salary, bonus, or settlement could be reduced or delayed with little say from you.",
        "suggested_action": "Ask that deductions be listed specifically, agreed in writing, and that final dues are paid by a fixed date.",
    },
    "Transfer & Relocation": {
        "keywords": [
            "transfer you to any", "transferred to any", "any of its offices", "any location",
            "relocate", "any branch", "deputed to",
        ],
        "risk_level": "Low",
        "explanation": "The employer can move you to another city, office, or associated company.",
        "why_it_matters": "A transfer can force relocation of your family or a longer commute with no extra pay.",
        "suggested_action": "Ask for relocation support, advance notice, and your consent for transfers outside your city.",
    },
    "Moonlighting / Exclusivity": {
        "keywords": [
            "whole time and attention", "exclusively devote", "shall not engage in any other",
            "any other employment", "part-time", "freelance", "moonlighting",
        ],
        "risk_level": "Medium",
        "explanation": "Stops you from doing any other work, side business, or freelancing while employed.",
        "why_it_matters": "Personal projects, teaching, or open-source work could be treated as a breach.",
        "suggested_action": "Ask for an exception for unpaid, non-competing activities done outside working hours.",
    },
    "IP Assignment (Employee)": {
        "keywords": [
            "whether or not during working hours", "outside working hours", "during or after working hours",
            "all inventions", "conceived or developed", "shall be the sole property of the company",
        ],
        "risk_level": "High",
        "explanation": "Everything you create, possibly including personal projects outside work, becomes the employer's property.",
        "why_it_matters": "You could lose rights to side projects, apps, or research unrelated to your job.",
        "suggested_action": "Limit assignment to work done in the course of employment and list your prior or personal projects as excluded.",
    },
    "Termination Without Reason": {
        "keywords": [
            "without assigning any reason", "without any reason", "without notice", "at any time without",
            "terminate your services", "summarily terminate",
        ],
        "risk_level": "High",
        "explanation": "The employer can end your job without giving a reason or notice.",
        "why_it_matters": "You may lose your income suddenly, often while still bound by a long notice period yourself.",
        "suggested_action": "Ask for termination terms that match your own notice period, with notice pay if notice is not given.",
    },
}

# ---------------------------------------------------------------------------
# Rent / lease agreements, judged from the tenant's side
# ---------------------------------------------------------------------------

RENTAL_RULES = {
    "Lock-in Period": {
        "keywords": ["lock-in", "lock in period", "lock-in period", "shall not vacate before"],
        "risk_level": "Medium",
        "explanation": "You cannot leave before a set date without paying the remaining rent or a penalty.",
        "why_it_matters": "A job change or emergency move could cost you several months of rent.",
        "suggested_action": "Ask for a shorter lock-in or a fixed small penalty instead of the full remaining rent.",
    },
    "Security Deposit Forfeiture": {
        "keywords": ["security deposit", "forfeit the deposit", "deposit shall be forfeited", "non-refundable deposit"],
        "risk_level": "Medium",
        "explanation": "Sets how much deposit you pay and when the landlord may keep part or all of it.",
        "why_it_matters": "Vague deduction rules often lead to a deposit not being returned.",
        "suggested_action": "Ask for a refund deadline, itemised deductions, and an exclusion for normal wear and tear.",
    },
    "Rent Escalation": {
        "keywords": ["escalation", "increase the rent", "rent shall be increased", "enhanced by"],
        "risk_level": "Medium",
        "explanation": "Rent goes up by a set percentage or at the landlord's choice.",
        "why_it_matters": "Uncapped or frequent increases can make the home unaffordable mid-tenancy.",
        "suggested_action": "Cap increases to a fixed percentage once a year, written into the agreement.",
    },
}

# ---------------------------------------------------------------------------
# Terms of service / privacy policies, judged from the user's side
# ---------------------------------------------------------------------------

TOS_RULES = {
    "Unilateral Changes": {
        "keywords": [
            "modify these terms at any time", "change these terms at any time", "amend these terms",
            "without prior notice", "at our sole discretion",
        ],
        "risk_level": "Medium",
        "explanation": "The provider can change the rules at any time, often just by updating a web page.",
        "why_it_matters": "Prices, data use, or your rights can change without you actively agreeing.",
        "suggested_action": "Check for advance notice of changes and a right to cancel and get a refund if you disagree.",
    },
    "Class Action Waiver": {
        "keywords": ["class action", "class-action", "representative action"],
        "risk_level": "High",
        "explanation": "You give up the right to join with other users in a group lawsuit.",
        "why_it_matters": "Small individual claims become impractical to pursue on your own.",
        "suggested_action": "Look for an opt-out window for the waiver and use it if offered.",
    },
    "Data Sharing": {
        "keywords": ["share your information", "share your personal", "third parties", "sell your", "affiliates and partners"],
        "risk_level": "Medium",
        "explanation": "Your data may be shared with other companies, partners, or advertisers.",
        "why_it_matters": "Personal information can spread beyond the service you signed up for.",
        "suggested_action": "Check the opt-out settings and whether data is sold or only shared for service delivery.",
    },
}


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------

_NEGATORS = ("not", "no", "never", "neither", "nor", "without any")


def _compile(keyword: str) -> re.Pattern:
    """Word-boundary pattern so 'net 15' doesn't match 'net 150'."""
    escaped = re.escape(keyword.lower()).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])")


_PATTERN_CACHE: dict[str, re.Pattern] = {}


def keyword_hit(clause_lower: str, keyword: str) -> bool:
    """True if the keyword is present and not directly negated
    ("shall not automatically renew" is not an auto-renewal risk)."""
    pat = _PATTERN_CACHE.get(keyword)
    if pat is None:
        pat = _PATTERN_CACHE[keyword] = _compile(keyword)
    for m in pat.finditer(clause_lower):
        before = clause_lower[max(0, m.start() - 25): m.start()].split()
        # Negation within the three words before the keyword
        if any(w.strip(",;:") in _NEGATORS for w in before[-3:]):
            continue
        return True
    return False
