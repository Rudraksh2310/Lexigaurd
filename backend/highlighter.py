"""
highlighter.py — turns flags into highlight positions in the document.

  locate_quote()     finds a flag's quoted clause in the source text even
                     when spacing, line breaks, curly quotes or dashes
                     differ, and returns character offsets in the ORIGINAL
                     text. This is also the grounding check: a flag whose
                     quote can't be found is dropped.
  finalize_flags()   validates/normalises flags, attaches offsets, drops
                     ungrounded ones, and recomputes counts + overall score
                     in code (never trusted from the model).
  segments()         splits the text into plain / highlighted runs, merging
                     overlapping flags. Used by the PDF/DOCX report; the
                     web page does the same thing in JavaScript.
"""

_CHAR_MAP = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "‑": "-", " ": " ",
}

LEVELS = ("High", "Medium", "Low")
LEVEL_RANK = {"High": 3, "Medium": 2, "Low": 1}


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Lower-case, unify quotes/dashes, collapse whitespace. Returns the
    normalised string and, for each of its characters, the index of the
    original character it came from."""
    out, idx = [], []
    prev_space = True  # also strips leading whitespace
    for i, ch in enumerate(text):
        ch = _CHAR_MAP.get(ch, ch)
        if ch == '"':
            continue  # models often add or drop quotation marks around terms
        if ch.isspace():
            if prev_space:
                continue
            out.append(" ")
            idx.append(i)
            prev_space = True
        else:
            out.append(ch.lower())
            idx.append(i)
            prev_space = False
    return "".join(out), idx


def _normalize(s: str) -> str:
    return _normalize_with_map(s)[0].strip()


class Locator:
    """Build once per document, then call find() for each quote."""

    def __init__(self, text: str):
        self.text = text
        self.norm, self.idx = _normalize_with_map(text)
        self._used: dict[str, int] = {}  # quote -> next search position

    def _to_original(self, ns: int, ne: int) -> tuple[int, int]:
        return self.idx[ns], self.idx[ne - 1] + 1

    def find(self, quote: str) -> tuple[int, int] | None:
        q = _normalize(quote or "").strip(" .\"'")
        if len(q) < 8:
            return None
        start_from = self._used.get(q, 0)

        pos = self.norm.find(q, start_from)
        if pos == -1 and start_from:
            pos = self.norm.find(q)  # same quote reused: fall back to first hit
        if pos != -1:
            self._used[q] = pos + len(q)
            return self._to_original(pos, pos + len(q))

        # Fallback for paraphrased middles / "..." elisions: anchor on the
        # first and last few words and accept if the span length is sane.
        if len(q) >= 60:
            head, tail = q[:45], q[-30:]
            if "..." in q:
                parts = [p.strip() for p in q.split("...") if p.strip()]
                head, tail = parts[0][:45], parts[-1][-30:]
            h = self.norm.find(head)
            while h != -1:
                window_end = h + int(len(q) * 1.6) + 40
                t = self.norm.find(tail, h + len(head) - 1, window_end)
                if t != -1:
                    return self._to_original(h, t + len(tail))
                h = self.norm.find(head, h + 1)
        return None


def normalize_level(level) -> str:
    s = str(level or "").strip().lower()
    for lv in LEVELS:
        if s.startswith(lv.lower()):
            return lv
    return "Medium"  # unknown -> conservative middle, per the rubric


def normalize_category(cat, allowed: list[str]) -> str:
    s = str(cat or "").strip()
    lookup = {a.lower(): a for a in allowed}
    if s.lower() in lookup:
        return lookup[s.lower()]
    # tolerate "Non-Compete" vs "Non-Compete / Non-Solicit" etc.
    for a in allowed:
        if s and (s.lower() in a.lower() or a.lower().split(" /")[0] in s.lower()):
            return a
    return "Other"


def compute_overall(flags: list[dict]) -> str:
    """Deterministic rule (project reference M12): High if any High,
    else Medium if any Medium, else Low."""
    levels = {f["risk_level"] for f in flags}
    if "High" in levels:
        return "High"
    if "Medium" in levels:
        return "Medium"
    return "Low"


def finalize_flags(analysis: dict, text: str, allowed_categories: list[str]) -> dict:
    loc = Locator(text)
    kept, dropped = [], 0
    for raw in analysis.get("flags", []) or []:
        if not isinstance(raw, dict):
            dropped += 1
            continue
        span = loc.find(raw.get("clause_text", ""))
        if span is None:
            dropped += 1
            continue
        start, end = span
        kept.append({
            "category": normalize_category(raw.get("category"), allowed_categories),
            "risk_level": normalize_level(raw.get("risk_level")),
            # show exactly what's in the document, not the model's version
            "clause_text": text[start:end],
            "section_reference": (str(raw["section_reference"])[:128] if raw.get("section_reference") else None),
            "plain_english_explanation": str(raw.get("plain_english_explanation") or "").strip(),
            "why_it_matters": str(raw.get("why_it_matters") or "").strip(),
            "suggested_action": str(raw.get("suggested_action") or "").strip(),
            "start": start,
            "end": end,
        })

    kept.sort(key=lambda f: (f["start"], -LEVEL_RANK[f["risk_level"]]))
    counts = {lv: sum(1 for f in kept if f["risk_level"] == lv) for lv in LEVELS}
    if dropped:
        print(f"[LexiGuard] grounding: dropped {dropped} of {dropped + len(kept)} flags (quote not found in source)")

    analysis["flags"] = kept
    analysis["total_flags"] = len(kept)
    analysis["severity_counts"] = counts
    analysis["overall_risk_score"] = compute_overall(kept) if kept else "Low"
    analysis["grounding_dropped"] = dropped
    return analysis


def segments(text: str, flags: list[dict]) -> list[dict]:
    """Split text into runs. Each run: {text, flags: [indexes], level}.
    Overlapping flags share a run; the run takes the highest severity."""
    bounds = {0, len(text)}
    for f in flags:
        if f.get("start") is not None and f.get("end") is not None:
            bounds.update((f["start"], f["end"]))
    points = sorted(b for b in bounds if 0 <= b <= len(text))
    runs = []
    for a, b in zip(points, points[1:]):
        if a == b:
            continue
        covering = [i for i, f in enumerate(flags)
                    if f.get("start") is not None and f["start"] <= a and f["end"] >= b]
        level = max((flags[i]["risk_level"] for i in covering), key=LEVEL_RANK.get, default=None)
        runs.append({"text": text[a:b], "flags": covering, "level": level})
    return runs
