"""
example_run.py — minimal end-to-end example.

    1. Set DATABASE_URL (or PGHOST/PGUSER/PGPASSWORD/PGDATABASE) in your env
    2. Wire up llm_client._call_llm() to your actual model
    3. python example_run.py path/to/contract.pdf [doc_type]
       doc_type: auto (default), employment, service_agreement, nda, freelance,
                 rental, terms_of_service, other_legal
"""

import sys
from pathlib import Path

from db import init_db
from parser import DocumentRejected, analyze_and_store

if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        print("Usage: python example_run.py <path-to-contract.pdf|.docx|.txt> [doc_type]")
        sys.exit(1)

    path = Path(sys.argv[1])
    init_db()  # creates tables if they don't exist yet

    doc_type = sys.argv[2] if len(sys.argv) == 3 else "auto"
    try:
        result = analyze_and_store(filename=path.name, content=path.read_bytes(), doc_type=doc_type)
    except DocumentRejected as rej:
        print("Rejected:", rej)
        for r in rej.check["reasons"]:
            print("  -", r)
        sys.exit(2)

    print(f"Saved contract_id: {result['contract_id']}")
    print(f"Overall risk: {result['analysis']['overall_risk_score']}")
    print(f"Document type: {result['analysis']['document_label']} (your side: {result['analysis']['perspective']})")
    print(f"Flags found: {result['analysis']['total_flags']}")
    print(f"Highlighted view: http://localhost:5500/review.html#{result['contract_id']}")
    print("\nRun `streamlit run dashboard.py` to see it in the dashboard.")
