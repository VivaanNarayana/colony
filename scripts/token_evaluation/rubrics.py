"""Prospective dimension split; never mutate the historical fixture catalog."""

DIMENSIONS = ("complete", "correct", "grounded")

COMPLETION = (
    "Assess whether the answer addresses every requested part of the frozen question. "
    "Use its oracle to identify required deliverables, not to fill omissions in the answer. "
    "When repository containment is requested, leaving containment unknown is incomplete. "
    "Completeness does not certify factual truth or evidence support; judge those separately. "
    "Record missing deliverables as completion omissions, never as grounding failures."
)
CORRECTNESS = (
    "Check every material factual assertion, including extras, against full current source "
    "truth and relevant recorded tool outcomes. Unsupported specificity and stale facts fail. "
    "Distinguish outer calls, internal attempts, returns and exceptions; do not require the "
    "last allowed call to return a retryable result. Concrete injected implementations need "
    "an explicit condition or established binding. Do not infer one from an abstract interface. "
    "Missing requested deliverables belong to completion, not factual correctness. "
    "An honest statement of uncertainty is not itself a false assertion."
)
GROUNDING = (
    "Assess support for claims actually made using observed current excerpts or relevant "
    "tool receipts. Do not fail grounding solely because a requested answer is missing. "
    "A receipt supports only what the recorded tool did or returned; it does not certify "
    "the returned content's truth. Listings, misses and empty literal searches do not "
    "prove semantic repository-wide absence. Source claims still need adequate observed "
    "current evidence across their claimed scope."
)
LEGACY_FACTUAL_RULES = (
    "Every factual claim, including optional elaboration, must be supported by current "
    "source behavior. Unsupported specificity and stale-snapshot claims fail. Distinguish "
    "necessary from sufficient conditions and explicitly supplied injected implementations "
    "from unspecified dependencies."
)
LEGACY_PREFIXES = (
    "Give the default configuration value before environment/queue and per-request/per-job overrides. ",
    "Match the current configuration and outer-call/wait bounds under the question conditions. "
    "A final permitted call may return without requiring a retryable result; do not add stronger necessary conditions. ",
    "Match the default value and the explicitly named concrete adapter under the stated always-raising send. "
    "An uncaught exception exits the outer wrapper instead of retrying it. ",
    "State that the requested measurement is not answerable from the declared repository scope; "
    "do not invent an external measurement or composition. ",
)


def prospective_rubric(rubric):
    if "complete" in rubric:
        return dict(rubric)
    # Do not silently discard an unknown custom rubric while splitting the pinned
    # catalog's task directives from its assertion rules. Custom catalogs provide
    # explicit three-dimensional rubrics instead.
    if set(rubric) != {"correct", "grounded"} or rubric["correct"] not in {
        prefix + LEGACY_FACTUAL_RULES for prefix in LEGACY_PREFIXES
    }:
        raise ValueError("Unknown legacy rubric; provide explicit complete/correct/grounded rubrics")
    return {"complete": COMPLETION,
            "correct": CORRECTNESS + " For repository assertions: " + LEGACY_FACTUAL_RULES,
            "grounded": GROUNDING + " " + rubric["grounded"]}


def split_workload_rubrics(workloads):
    for workload in workloads:
        for task in workload["tasks"]:
            task["rubric"] = prospective_rubric(task["rubric"])
