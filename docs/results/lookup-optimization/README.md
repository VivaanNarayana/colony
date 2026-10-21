# Lookup optimization: strict gate status

The PR remains draft. The latest complete 48-session trial passed import,
provenance and attempted-call compliance audits, with 24/24 correct answers in
both arms. It failed the gross-token gate: main used 1,155,825 tokens and the
feature used 1,173,889 (+1.6%). Subject time improved from 284.002s to 242.662s.
No failed gate is advisory; merge-review readiness remains false.

Research tools now expose remaining calls and exact source objects. The next
revision explicitly identifies complete-file reads and directs both arms to
finish once the facts suffice. The original questions, outer prompts, model,
six-call budget, native accounting and gates are unchanged. All previous failed
trials are retained, and the next comparison must run all 48 sessions again.

The runtime code and inference reductions are unchanged: local time 77.971s main
versus 19.700s feature, with 72 paired responses matching. Verification is 86
passing tests, one existing host symlink skip, and clean Ruff. Other feature work
remains paused.
