"""Chronological, byte-bound mechanism proofs; replay proofs never certify measurement."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .protocol import validate_evidence_policy
from .redaction import redacted_source_lines
from .research import Research


def _objects(item):
    result = item.get('result') or {}
    if item.get('status') != 'completed' or result.get('isError'):
        return []
    values = []
    for block in result.get('content', []):
        try:
            value = json.loads(block['text']) if block.get('type') == 'text' else None
        except (ValueError, KeyError, TypeError):
            continue
        if isinstance(value, dict):
            values.append(value)
    return values


def verify_current_reads(reads, snapshot_root, files, *, allow_clipped_tail=False, allow_redaction=False,
                         allow_blank_file=False):
    """Verify all bytes; semantic review may retain complete lines before a clipped tail.

    The default mechanism/repository contract still requires complete line spans.
    Semantic opt-ins accept only exact raw or pinned scrubbed representations;
    partial or redacted lines never earn evidence credit.
    """
    if not isinstance(reads, list) or not reads:
        return []
    spans = []
    original_root = Path(snapshot_root).absolute()
    if original_root.is_symlink() or (hasattr(original_root, 'is_junction') and original_root.is_junction()):
        return []
    root = original_root.resolve()
    for read in reads:
        if not isinstance(read, dict):
            return []
        relative = read.get('path')
        if not isinstance(relative, str) or relative not in files:
            return []
        original = root / relative
        if any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction())
               for p in (original, *original.parents) if p.is_relative_to(root)):
            return []
        path = original.resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return []
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        if sha != files[relative] or read.get('sha256') != sha:
            return []
        lines = raw.decode('utf-8').splitlines()
        start, content = read.get('start_line', read.get('start')), read.get('content')
        if type(start) is not int or start < 1 or not isinstance(content, str):
            return []
        if not content:
            # Joining the producer's single blank source line yields "". Verify
            # that exact complete-file case; empty prefixes of other files are
            # not evidence. A valid blank read contributes no useful coverage.
            if (allow_blank_file and read.get('complete_file') is True and start == 1
                    and lines == [''] and ('end' not in read or
                    (type(read['end']) is int and read['end'] == 1))):
                continue
            return []
        count = content.count('\n') + 1
        end = start + count - 1
        if end > len(lines):
            return []
        if 'end' in read and read['end'] != end:
            return []
        representations = [(lines, set())]
        if allow_redaction:
            scrubbed = redacted_source_lines(raw.decode('utf-8'))
            if scrubbed is not None:
                representations.append(scrubbed)
        for rendered, redacted in representations:
            if end > len(rendered) or (read.get('complete_file') and (start != 1 or end != len(rendered))):
                continue
            expected = '\n'.join(rendered[start - 1:end])
            complete_end = end
            if content != expected:
                # Verify every delivered character, including the clipped tail.
                if not (allow_clipped_tail and read.get('complete_file') is False
                        and expected.startswith(content)):
                    continue
                complete_end -= 1
            # Credit complete unchanged runs only AFTER validating the whole
            # representation. Never salvage valid-looking lines from a forgery.
            begin = None
            for number in range(start, complete_end + 2):
                eligible = (number <= complete_end and number - 1 not in redacted
                            and rendered[number - 1] == lines[number - 1])
                if eligible and begin is None:
                    begin = number
                elif not eligible and begin is not None:
                    spans.append({'path': relative, 'start': begin, 'end': number - 1})
                    begin = None
            break
        else:
            return []
    return spans


def covers(spans, required):
    """Every oracle line must have actually been returned (possibly in several spans)."""
    if not isinstance(required, list) or not required:
        return False
    for citation in required:
        if not isinstance(citation, dict):
            return False
        start, end = citation.get('start'), citation.get('end')
        if type(start) is not int or type(end) is not int or not 1 <= start <= end:
            return False
        if any(not any(s['path'] == citation.get('path') and s['start'] <= n <= s['end'] for s in spans)
               for n in range(start, end + 1)):
            return False
    return True


def _research(item, root, files):
    args = item.get('arguments') or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return []
    spans = []
    for value in _objects(item):
        if item.get('tool') == 'fixture_read':
            if value.get('path') != args.get('path') or value.get('start') != args.get('start', 1):
                return []
            if (type(args.get('lines', 120)) is not int or not 1 <= args.get('lines', 120) <= 120
                    or type(value.get('end')) is not int
                    or value['end'] >= args.get('start', 1) + args.get('lines', 120)):
                return []
            # Research.read returns the requested spelling, including accepted
            # aliases. Reuse its authorization before translating to the frozen
            # source identity; never normalize away a link or escape first.
            try:
                research = Research(Path(root), files=files)
                canonical = research._path(value['path']).relative_to(research.root).as_posix()
            except (OSError, ValueError, RuntimeError):
                return []
            got = verify_current_reads([{**value, 'path': canonical}], root, files)
        elif item.get('tool') == 'fixture_search':
            query = args.get('query')
            if not isinstance(query, str) or not query or not isinstance(value.get('matches'), list):
                return []
            got = []
            for match in value['matches']:
                try:
                    number, text = match.split(': ', 1)
                    read = {'path': value['path'], 'sha256': value['sha256'], 'start': int(number), 'content': text}
                except (ValueError, KeyError, AttributeError):
                    return []
                if query not in text:
                    return []
                verified = verify_current_reads([read], root, files)
                if not verified:
                    return []
                got += verified
        else:
            return []
        if not got:
            return []
        spans += got
    return spans


def required_source_spans(task, snapshot, source_root):
    """Resolve mechanical coverage, never infer that observed text proves a fact.

    Support covers the task's required spans, not necessarily every file.
    Absence covers the complete declared current corpus. Empty files contribute
    no lines; an entirely empty obligation never earns vacuous reuse credit.
    """
    files = snapshot["files"]
    policy = validate_evidence_policy(task["oracle"], files)
    if policy["kind"] == "support":
        return task["oracle"]["citations"]
    original_root = Path(source_root).absolute()
    if original_root.is_symlink() or (hasattr(original_root, "is_junction") and original_root.is_junction()):
        raise ValueError("Linked evidence scope")
    root = original_root.resolve()
    required = []
    for relative in policy["scope"]:
        original = root / relative
        if any(p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction())
               for p in (original, *original.parents) if p.is_relative_to(root)):
            raise ValueError("Linked evidence scope")
        path = original.resolve()
        if not path.is_relative_to(root):
            raise ValueError("Evidence scope outside frozen sources")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != files[relative]:
            raise ValueError("Evidence scope source changed")
        lines = raw.decode("utf-8").splitlines()
        if lines:
            required.append({"path": relative, "start": 1, "end": len(lines)})
    return required


def evaluate_mechanisms(root, manifest, rows):
    """Evaluate retained events in task order, binding published identities to source reads.

    Optional row.events_path supports provider-free product integration traces. The normal
    report resolves each event log from its retained control invocation/result journal.
    """
    root = Path(root)
    controlled = manifest.get('track') != 'natural'
    decisions, published, retrieved = {}, {}, set()
    for row in rows:
        decision = dict.fromkeys(('publication', 'hit', 'miss', 'stale', 'insufficient', 'useful_reuse'), False)
        decision.update(successful_research=0, research_attempts=None, unresolved_mcp_calls=None,
                        source_scope_covered=None, errors=[])
        decisions[row['id']] = decision
        if row['arm'] == 'no_colony':
            continue
        decision['source_scope_covered'] = False
        workload = next(w for w in manifest['workloads'] if w['id'] == row['workload'])
        task = next(t for t in workload['tasks'] if t['id'] == row['task'])
        snapshot = next((s for s in workload['snapshots'] if s['id'] == task.get('snapshot')), None)
        if snapshot is None:
            decision['errors'].append('missing frozen snapshot')
            continue
        files = snapshot['files']
        source_root = root / 'workspaces' / row['arm'] / row['workload'] / snapshot['id']
        if row.get('events_path'):
            path = Path(row['events_path'])
        else:
            records = [json.loads(line) for line in (root / 'control' / 'attempts.jsonl').read_text(encoding='utf-8').splitlines()]
            record = next(r for r in records if r['id'] == row['id'])
            path = root / 'logs' / record.get('result', record)['events']
        research, current_partial, stale_seen = [], [], False
        scope_reads = []
        try:
            required = required_source_spans(task, snapshot, source_root)
        except (OSError, UnicodeError, ValueError, KeyError, TypeError, AttributeError):
            decision['errors'].append('invalid task evidence scope')
            continue
        research_attempts, unresolved_calls = set(), set()
        for line_number, line in enumerate(path.read_text(encoding='utf-8').splitlines()):
            event = json.loads(line)
            item = event.get('item', {})
            event_type = event.get('type')
            if item.get('type') == 'mcp_tool_call' and event_type in {'item.started', 'item.updated', 'item.completed'}:
                call_id = item.get('id')
                if not isinstance(call_id, str) or not call_id:
                    call_id = ('line', line_number)
                if str(item.get('server', '')).lower() == 'repository':
                    research_attempts.add(call_id)
                if event_type == 'item.completed':
                    unresolved_calls.discard(call_id)
                else:
                    unresolved_calls.add(call_id)
            if event.get('type') != 'item.completed' or item.get('type') != 'mcp_tool_call':
                continue
            server, tool = str(item.get('server', '')).lower(), item.get('tool')
            if server == 'repository':
                spans = _research(item, source_root, files)
                if spans:
                    research += spans
                    scope_reads += spans
                    decision['successful_research'] += 1
                    if current_partial and covers(current_partial + research, required):
                        decision['insufficient'] = True
                    if stale_seen and covers(research, required):
                        decision['stale'] = True
                continue
            if server != 'colony':
                continue
            args = item.get('arguments') or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            for value in _objects(item):
                identity = (row['arm'], row['workload'], value.get('id'))
                if tool == 'push':
                    sources = args.get('sources')
                    valid = (task.get('phase', 'learn') == 'learn'
                        and value.get('stored') is True and isinstance(value.get('id'), str)
                        and bool(value['id']) and isinstance(args.get('question'), str) and bool(args['question'])
                        and isinstance(args.get('content'), str) and bool(args['content'])
                        and isinstance(sources, list) and bool(sources) and bool(research))
                    if valid:
                        if controlled:
                            valid &= args['question'] == task.get('question', args['question'])
                        valid = valid and all(isinstance(s, dict) and s.get('ref') in files
                            and s.get('content_hash') == files[s['ref']]
                            and any(span['path'] == s['ref'] for span in research) for s in sources)
                        if controlled:
                            valid &= all(any(s['ref'] == citation['path'] for s in sources) for citation in required)
                            valid &= covers(research, required)
                            if task.get('publication_sources') is not None:
                                valid &= {s['ref'] for s in sources} == set(task['publication_sources'])
                    if valid and identity not in published:
                        published[identity] = {'sources': sources, 'question': args['question'],
                            'content': args['content'], 'snapshot': snapshot['id'], 'attempt': row['id'], 'task': row['task']}
                        decision['publication'] = True
                if tool != 'ask':
                    continue
                expected_question = task.get('memory_query', task.get('question'))
                if manifest.get('track') == 'natural':
                    if not isinstance(args.get('question'), str) or not args['question'].strip():
                        continue
                elif expected_question is not None and args.get('question') != expected_question:
                    continue
                decision['miss'] |= value.get('hit') is False and value.get('reason') == 'no_relevant_trail'
                sources = value.get('sources')
                reads = verify_current_reads(value.get('current_repository_reads'), source_root, files)
                returned_map = {s.get('ref'): s.get('content_hash') for s in sources if isinstance(s, dict)} if isinstance(sources, list) else {}
                if any(span['path'] not in returned_map for span in reads):
                    reads = []
                current_sources = (isinstance(sources, list) and bool(sources)
                    and all(isinstance(source, dict) and source.get('ref') in files
                            and source.get('content_hash') == files[source['ref']] for source in sources))
                if (value.get('hit') is True and value.get('status') == 'validated'
                        and current_sources and reads):
                    scope_reads += reads
                prior = published.get(identity)
                if not prior or prior['attempt'] == row['id']:
                    continue
                if controlled and task.get('memory_from') and prior['task'] != task['memory_from']:
                    continue
                if manifest.get('track') != 'natural' and task.get('memory_query') and args.get('question') != task['memory_query']:
                    continue
                prior_map = {s['ref']: s['content_hash'] for s in prior['sources']}
                changed = any(files.get(p) != sha for p, sha in prior_map.items())
                linked = returned_map == prior_map
                current_linked = (set(returned_map) == set(prior_map) and all(files.get(p) == sha for p, sha in returned_map.items()))
                if identity in retrieved and changed and prior['snapshot'] != snapshot['id']:
                    if value.get('hit') is True and value.get('status') == 'stale' and linked:
                        stale_seen = True
                        research = []  # fallback must follow this stale response
                    elif value.get('hit') is True and value.get('status') == 'validated' and value.get('match_kind') == 'evidence' and current_linked and reads:
                        stale_seen = True
                        research = []
                        if covers(reads, required):
                            decision['stale'] = True
                if value.get('hit') is True and value.get('status') == 'validated' and linked and not changed and reads:
                    if value.get('match_kind') != 'evidence' and value.get('content') != prior['content'][:4000]:
                        continue
                    if covers(reads, required):
                        retrieved.add(identity)
                        decision['hit'] = True
                        decision['published_attempt'] = prior['attempt']
                    elif any(s['path'] == c.get('path') and s['start'] <= c.get('end', 0) and s['end'] >= c.get('start', 0)
                             for s in reads for c in required):
                        current_partial = reads
                        research = []  # only research AFTER the partial retrieval fills its gaps
        decision['source_scope_covered'] = covers(scope_reads, required)
        decision['research_attempts'] = len(research_attempts)
        decision['unresolved_mcp_calls'] = len(unresolved_calls)
        decision['useful_reuse'] = decision['hit'] and not research_attempts and not unresolved_calls
    return decisions
