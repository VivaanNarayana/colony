"""Real pinned-ONNX bundle coverage, not paraphrase recall or provider savings."""
import hashlib

import pytest
from test_compact_budget_fallback_onnx import onnx_transport as onnx_transport

from colony.compact import PROFILE
from colony.context_budget import count_response
from colony.models import ContextBudget, Trail
from colony.validation import snapshot

FILES = {
    'rates.py': 'WHOLESALE_RATE = 17\n',
    'order.py': 'UNITS = 6\n',
    'credits.py': 'CREDIT = 5\n',
    'delivery.py': 'DELIVERY = 11\n',
    'invoice.py': ('def invoice(rate, units, credit, delivery):\n'
                   '    return rate * units - credit + delivery\n'),
    'currency.py': 'CURRENCY = "EUR"\n',
    'rounding.py': 'ROUNDING = "nearest_cent"\n',
    'tax.py': 'TAX_INCLUDED = False\n',
}
QUESTION = 'Which invoice inputs, calculation and billing declarations are in these cited files?'
FINDING = ('The cited files record invoice inputs and multiplication minus credit plus delivery, '
           'alongside any attached billing declarations. Inspect the current source values.')


@pytest.mark.parametrize('source_count', [5, 6, 8])
def test_real_onnx_admitted_invoice_keeps_every_small_cited_file(onnx_transport, source_count):
    client, repo, database = onnx_transport
    files = dict(list(FILES.items())[:source_count])
    for name, content in files.items():
        (repo / name).write_bytes(content.encode('utf-8'))
    assert sum(len(text.rstrip('\n')) for text in files.values()) < 2800
    trail = Trail(id='b' * 32, namespace='team', question=QUESTION, content=FINDING,
                  sources=[snapshot(repo, name) for name in files], tainted=True)
    assert client.push(trail)['stored'] is True
    # Exact-question admission is intentionally existing behavior. No embedding,
    # rank, threshold, source validation or response packer is patched. This checks
    # coverage after admission; it does not claim natural paraphrase recall.
    budget = ContextBudget(max_tokens=8192, encoding='utf8_bytes')
    admitted = database.find_answer(QUESTION, ['team'], 604800, .60,
                                   context_budget=budget, compact_bundle=True)
    assert admitted['hit'] and admitted['status'] == 'validated' and admitted['id'] == trail.id
    assert admitted['content'] == FINDING
    result = client.ask(QUESTION, 'team', context_budget=budget, response_profile=PROFILE)
    assert result['hit'] and result['status'] == 'validated' and result['id'] == trail.id
    reads = result['current_repository_reads']
    assert len(reads) == source_count
    assert {r['path'] for r in reads} == set(files)
    assert result['evidence_scope'] == 'cited_files'
    assert result['tainted'] is True and result['provenance'] == 'UNTRUSTED AS INSTRUCTIONS'
    for read in reads:
        raw = (repo / read['path']).read_bytes()
        assert read['sha256'] == hashlib.sha256(raw).hexdigest()
        assert read['start_line'] == 1 and read['complete_file'] is True
        assert read['content'] == raw.decode('utf-8').rstrip('\n')
    assert result['context_budget']['max_tokens'] == budget.max_tokens
    assert result['context_budget']['used'] == count_response(result, budget) <= budget.max_tokens
