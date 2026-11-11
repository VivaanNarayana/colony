"""Portable source-review calibration, not Colony runtime or model-behavior tests.

These small test-owned call chains model exception propagation, dependency
injection and termination mistakes found during review. They import no benchmark
fixtures and make no claim about agent obedience, accuracy or token savings.
"""

from dataclasses import dataclass
from itertools import repeat

import pytest


class TemporaryFailure(Exception):
    pass


@dataclass(frozen=True)
class Outcome:
    retryable: bool


def _sender(outcomes, trace):
    outcomes = iter(outcomes)
    sent = 0

    def send():
        nonlocal sent
        trace.append(('send', sent))
        sent += 1
        outcome = next(outcomes)
        if isinstance(outcome, TemporaryFailure):
            raise outcome
        return outcome

    return send


def _retrying(send, limit):
    def invoke():
        for position in range(limit):
            try:
                return send()
            except TemporaryFailure:
                if position + 1 == limit:
                    raise

    return invoke


def _fanout(send, width):
    def invoke():
        for _ in range(width):
            outcome = send()
        return outcome

    return invoke


def _drive(invoke, limit, pause, trace):
    for position in range(limit):
        trace.append(('invoke', position))
        outcome = invoke()
        if not outcome.retryable or position + 1 == limit:
            return outcome
        trace.append(('wait', pause))


LIMITS = [(2, 1, 0), (5, 4, 17), (7, 5, 31)]


@pytest.mark.parametrize('outer,inner,pause', LIMITS)
def test_uncaught_dependency_exception_prevents_outer_retry(outer, inner, pause):
    trace = []
    failure = TemporaryFailure('original failure')
    invoke = _retrying(_sender(repeat(failure), trace), inner)
    with pytest.raises(TemporaryFailure) as caught:
        _drive(invoke, outer, pause, trace)
    assert caught.value is failure
    assert trace == [('invoke', 0)] + [('send', n) for n in range(inner)]
    assert sum(event == 'send' for event, _ in trace) != outer * inner


@pytest.mark.parametrize('outer,unselected_limit,pause', LIMITS)
def test_injected_callable_has_no_unselected_implementation_bound(outer, unselected_limit, pause):
    ordinary_trace, injected_trace = [], []
    outcome = Outcome(retryable=True)
    ordinary = _retrying(_sender(repeat(outcome), ordinary_trace), unselected_limit)
    injected_width = unselected_limit + 2
    injected = _fanout(_sender(repeat(outcome), injected_trace), injected_width)
    assert _drive(ordinary, outer, pause, ordinary_trace) is outcome
    assert _drive(injected, outer, pause, injected_trace) is outcome
    assert [item for item in ordinary_trace if item[0] != 'send'] == [
        item for item in injected_trace if item[0] != 'send'
    ]
    assert [n for event, n in ordinary_trace if event == 'send'] == list(range(outer))
    observed_sends = [n for event, n in injected_trace if event == 'send']
    assert observed_sends == list(range(outer * injected_width))
    assert len(observed_sends) > outer * unselected_limit


@pytest.mark.parametrize('outer,inner,pause', LIMITS)
@pytest.mark.parametrize('final_raises', [False, True])
def test_explicit_dependency_selection_supports_conditional_send_bound(
    outer, inner, pause, final_raises,
):
    trace = []
    failure = TemporaryFailure('last permitted send failed')
    terminal = failure if final_raises else Outcome(False)
    plan = ([failure] * (inner - 1) + [Outcome(True)]) * (outer - 1)
    plan += [failure] * (inner - 1) + [terminal]
    send = _sender(plan, trace)

    def configured_driver():
        # This call site selects the implementation. A definition elsewhere alone
        # would not justify transferring its limit to an arbitrary injected callable.
        return _drive(_retrying(send, inner), outer, pause, trace)

    if final_raises:
        with pytest.raises(TemporaryFailure) as caught:
            configured_driver()
        assert caught.value is failure
    else:
        assert configured_driver() is terminal
    assert trace == _full_trace(outer, inner, pause)
    assert sum(event == 'send' for event, _ in trace) == outer * inner
    assert sum(event == 'wait' for event, _ in trace) == outer - 1


@pytest.mark.parametrize('outer,unselected_limit,pause', LIMITS)
@pytest.mark.parametrize('stop_after', [1, 2])
def test_injected_dependency_preserves_outer_early_stop_but_not_inner_bound(
    outer, unselected_limit, pause, stop_after,
):
    traces = []
    for width in (1, unselected_limit + 2):
        trace = []
        terminal = Outcome(False)
        plan = [Outcome(True)] * (width * (stop_after - 1)) + [terminal] * width
        invoke = _fanout(_sender(plan, trace), width)
        assert _drive(invoke, outer, pause, trace) is terminal
        assert sum(event == 'invoke' for event, _ in trace) == stop_after
        assert sum(event == 'wait' for event, _ in trace) == stop_after - 1
        assert sum(event == 'send' for event, _ in trace) == stop_after * width
        assert trace[-1][0] == 'send'  # No wait after the terminal result.
        traces.append(trace)
    assert [item for item in traces[0] if item[0] != 'send'] == [
        item for item in traces[1] if item[0] != 'send'
    ]
    assert sum(event == 'send' for event, _ in traces[1]) > stop_after * unselected_limit


def _full_trace(outer, inner, pause):
    expected = []
    for position in range(outer):
        expected.append(('invoke', position))
        expected.extend(('send', position * inner + offset) for offset in range(inner))
        if position + 1 < outer:
            expected.append(('wait', pause))
    return expected


@pytest.mark.parametrize('outer,inner,pause', LIMITS)
def test_final_permitted_send_can_raise_after_full_send_count(outer, inner, pause):
    trace = []
    failure = TemporaryFailure('last permitted send failed')
    plan = ([failure] * (inner - 1) + [Outcome(True)]) * (outer - 1) + [failure] * inner
    with pytest.raises(TemporaryFailure) as caught:
        _drive(_retrying(_sender(plan, trace), inner), outer, pause, trace)
    assert caught.value is failure
    assert trace == _full_trace(outer, inner, pause)
    assert sum(event == 'send' for event, _ in trace) == outer * inner
    assert trace[-1] == ('send', outer * inner - 1)


@pytest.mark.parametrize('outer,inner,pause', LIMITS)
@pytest.mark.parametrize('last_retryable', [False, True])
def test_final_return_need_not_request_another_retry(outer, inner, pause, last_retryable):
    trace = []
    failure = TemporaryFailure('intermediate send')
    final = Outcome(last_retryable)
    plan = ([failure] * (inner - 1) + [Outcome(True)]) * (outer - 1)
    plan += [failure] * (inner - 1) + [final]
    assert _drive(_retrying(_sender(plan, trace), inner), outer, pause, trace) is final
    assert trace == _full_trace(outer, inner, pause)
    assert sum(event == 'wait' for event, _ in trace) == outer - 1


@pytest.mark.parametrize('outer,inner,pause', LIMITS)
def test_terminal_result_stops_before_unused_allowance(outer, inner, pause):
    trace = []
    final = Outcome(False)
    assert _drive(_retrying(_sender([final], trace), inner), outer, pause, trace) is final
    assert trace == [('invoke', 0), ('send', 0)]


@pytest.mark.parametrize('outer,inner,pause', [(2, 3, 0), (5, 4, 17), (7, 5, 31)])
def test_identical_returned_responses_do_not_exclude_recovered_internal_failures(outer, inner, pause):
    """Even a known dependency can recover failures before each observed response."""
    outcome = Outcome(True)
    results, traces = [], []
    for failures_per_response in (0, inner - 1):
        trace, returned = [], []
        failure = TemporaryFailure('recovered before returning')
        plan = ([failure] * failures_per_response + [outcome]) * outer
        dependency = _retrying(_sender(plan, trace), inner)

        def invoke(dependency=dependency, returned=returned):
            response = dependency()
            returned.append(response)
            return response

        assert _drive(invoke, outer, pause, trace) is outcome
        assert trace == _full_trace(outer, failures_per_response + 1, pause)
        results.append(returned)
        traces.append(trace)
    assert results[0] == results[1] == [outcome] * outer
    assert [item for item in traces[0] if item[0] != 'send'] == [
        item for item in traces[1] if item[0] != 'send'
    ]
    assert sum(event == 'send' for event, _ in traces[0]) == outer
    assert sum(event == 'send' for event, _ in traces[1]) == outer * inner
