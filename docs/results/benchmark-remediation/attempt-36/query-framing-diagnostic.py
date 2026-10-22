"""Independent fixed screening of imperative-to-question reranker framing.

No runtime change. No source, model, score, cutoff or benchmark edits.
Accept for implementation only if at least two existing misses become hits,
all positive best passages are relevant, and no unrelated passage crosses -5.
"""

import json
from pathlib import Path

from colony.retrieval import rank

cases = [
    (
        "Launch the local event collector.",
        "Section: Collector setup\n```sh\nevent-collector --listen localhost --spool events/\n```",
        "Section: Collector cleanup\n```sh\nevent-collector purge --all\n```",
    ),
    (
        "Start the background task worker.",
        "Section: Worker startup\n```sh\njob-runner worker --queue tasks --background\n```",
        "Section: Worker logs\n```sh\njob-runner logs --follow\n```",
    ),
    (
        "Run the static type checker.",
        "Section: Validation\n```sh\npython -m mypy app/\n```",
        "Section: Unit checks\n```sh\npython -m pytest tests/\n```",
    ),
    (
        "Install the optional image codecs.",
        "Section: Optional image support\n```sh\npython -m pip install 'picture[codecs]'\n```",
        "Section: Optional cloud support\n```sh\npython -m pip install 'picture[cloud]'\n```",
    ),
    (
        "Restore the encrypted backup.",
        "Section: Recovery\n```sh\nbackupctl restore --decrypt snapshot.enc\n```",
        "Section: Storage cleanup\n```sh\nbackupctl delete snapshot.enc\n```",
    ),
    (
        "Configure TLS for the gateway.",
        "Section: Gateway transport security\n```sh\ngate config set tls.cert gateway.pem tls.key gateway.key\n```",
        "Section: Gateway monitoring\n```sh\ngate config set metrics.port 9100\n```",
    ),
    (
        "Rotate the signing key.",
        "Section: Key inspection\n```sh\nsigner list-keys\n```",
        "Section: Key removal\n```sh\nsigner delete-key expired\n```",
    ),
    (
        "Deploy the billing service to production.",
        "Section: Testing\n```sh\nbill deploy --target staging\n```",
        "Section: Database creation\n```sh\nbill db create\n```",
    ),
]
rows = []
for i, (question, positive, negative) in enumerate(cases):
    framed = "How do I " + question[0].lower() + question[1:].rstrip(".!?") + "?"
    scores = {
        arm: rank(q, [positive, negative]) for arm, q in [("before", question), ("framed", framed)]
    }
    rows.append(
        {
            "question": question,
            "framed": framed,
            "passages": [positive, negative],
            "positive_case": i < 6,
            "scores": scores,
        }
    )
    print(json.dumps(rows[-1]), flush=True)
improved = sum(
    r["positive_case"] and r["scores"]["before"][0] < -5 <= r["scores"]["framed"][0] for r in rows
)
false_positives = [
    r["question"]
    for r in rows
    if r["scores"]["framed"][1] >= -5
    or (not r["positive_case"] and max(r["scores"]["framed"]) >= -5)
]
qualified = (
    improved >= 2
    and not false_positives
    and all(
        not r["positive_case"] or r["scores"]["framed"][0] > r["scores"]["framed"][1] for r in rows
    )
)
Path("docs/results/benchmark-remediation/attempt-36/query-framing-results.json").write_text(
    json.dumps(
        {
            "qualified_for_implementation": qualified,
            "improved_misses": improved,
            "false_positives": false_positives,
            "rows": rows,
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
    newline="\n",
)
raise SystemExit(0 if qualified else 1)
