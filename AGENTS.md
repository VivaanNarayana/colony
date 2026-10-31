# Feature branch instructions

This checkout is reserved for the DRAFT automatic agent workflow integration plan.

- Expected repository: https://github.com/VivaanNarayana/colony
- Only permitted feature branch: `codex/automatic-agent-workflow-integration`
- Expected checkout: `C:/Users/b3vel/Documents/Codex/2026-10-31/https-github-com-vivaannarayana-colony-https/work/colony`
- Starting commit: `df5bdca1be52cf776ddecd59a259e0f35e88d353`
- Read `docs/plans/automatic-agent-workflow-integration.md` before continuing.

## Current authorization

Planning only. Keep the plan and any PR in DRAFT. Do not start implementation until the user explicitly requests it. The current allowed diff is this file and the draft plan. After implementation is requested, update the plan's exact feature-file boundary on this same branch before coding.

## Branch confinement

Before every mutating phase and again before commit/push, verify the absolute resolved checkout, repository/common Git directory, origin, exact non-detached branch, expected HEAD progression and explicit changed-path scope. If any check differs, stop mutations and report. Do not continue work on another branch or checkout, and do not automatically repair drift by checkout/switch, stash, reset, clean, merge, rebase or cherry-pick.

Stage explicit in-scope files only. The only permitted push destination for this feature is `HEAD:refs/heads/codex/automatic-agent-workflow-integration`. No force push, broad push, main writes, unrelated feature work, auto-merge, ready-for-review transition or merge. A request to continue this feature preserves these constraints unless the user explicitly changes them.

Use at most two subagents when delegated work is authorized, and do not use Astra. Give every subagent this branch and scope contract. The initial two reviewers were read-only GPT-6.1 Sol agents.

These instructions are temporary development constraints, not Git-enforced isolation. Before an explicitly authorized eventual merge, remove this temporary file on the same feature branch so it does not constrain main; retain the plan's historical scope record.
