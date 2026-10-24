"""Fixed independent screen of real enclosing-scope labels on split Python passages.

Labels identify actual enclosing definitions; no invented descriptions or answers.
Accept only with >=2 recovered relevant passages, no lost supported positives,
and all unrelated controls below unchanged -5 cutoff. No runtime edit yet.
"""

import json
from pathlib import Path

from colony.retrieval import rank

cases = [
    (
        "How is the refund charge computed?",
        "Billing.compute_refund_charge",
        "    return max(0, amount - processing_fee)",
        "Billing.audit_tax_codes",
        "    return sorted(set(code for code in codes if code))",
    ),
    (
        "How are tenant permissions checked?",
        "check_tenant_permissions",
        "    if tenant not in grant.tenants:\n        raise PermissionError('denied')\n    return True",
        "check_project_status",
        "    if project not in registry:\n        raise ValueError('unknown')\n    return True",
    ),
    (
        "How are upload checksums verified?",
        "verify_upload_checksum",
        "    actual = hashlib.sha256(data).hexdigest()\n    if actual != expected:\n        raise ValueError('changed')",
        "summarize_worker_status",
        "    return {'active': len(workers), 'queued': len(queue)}",
    ),
    (
        "What does the cursor expiry check do?",
        "check_cursor_expiry",
        "    if now > expires_at:\n        raise ValueError('expired')\n    return token",
        "check_schema_version",
        "    if current != expected:\n        raise ValueError('unsupported')\n    return token",
    ),
    (
        "How is a backup name generated?",
        "generate_backup_name",
        "    return prefix + '-' + timestamp.strftime('%Y%m%d')",
        "generate_api_header",
        "    return {'Accept': 'application/json'}",
    ),
    (
        "How does the message decoder handle invalid input?",
        "Message.decode",
        "    try:\n        return json.loads(data)\n    except ValueError:\n        return None",
        "Queue.enqueue",
        "    try:\n        queue.append(item)\n    except MemoryError:\n        return None",
    ),
]
rows = []
for question, scope, body, other_scope, other_body in cases:
    before = rank(question, [body, other_body])
    passages = [f"Python scope: {scope}\n{body}", f"Python scope: {other_scope}\n{other_body}"]
    after = rank(question, passages)
    rows.append(
        {
            "question": question,
            "scopes": [scope, other_scope],
            "bodies": [body, other_body],
            "before": before,
            "after": after,
        }
    )
    print(json.dumps(rows[-1]), flush=True)
recovered = sum(r["before"][0] < -5 <= r["after"][0] for r in rows)
qualified = recovered >= 2 and all(
    r["after"][1] < -5
    and r["after"][0] > r["after"][1]
    and (r["before"][0] < -5 or r["after"][0] >= -5)
    for r in rows
)
Path("docs/results/benchmark-remediation/attempt-37/scope-model-results.json").write_text(
    json.dumps(
        {"qualified_for_implementation": qualified, "recovered": recovered, "rows": rows}, indent=2
    )
    + "\n",
    encoding="utf-8",
    newline="\n",
)
raise SystemExit(0 if qualified else 1)
