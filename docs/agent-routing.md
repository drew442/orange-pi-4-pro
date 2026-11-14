# Cost-conscious agent routing

## Defaults and escalation

The repository defaults to **GPT-6.1 Sol low** for routine coordination. An
explicit session choice can override it. The focused child default remains
Luna low. All roles use explicit settings, with no inherited expensive fallback.

| Role | Model / effort | Work and escalation trigger |
| --- | --- | --- |
| Coordinator | Sol 6.1 / low | Follow the finite plan, inspect compact script results, integrate clear bounded changes; use medium when competing requirements need planning |
| extractor | Luna / low | Narrow extraction, inventories and straightforward fact-based edits when delegation saves total work |
| implementer | Sol 6.1 / low | Clear existing interfaces, small scripts/configuration changes and focused regressions |
| integrator | Sol 6.1 / medium | Ambiguous multi-component changes, dependency boundaries, unfamiliar build integration or a diagnosis that low could not resolve |
| reviewer | Sol 6.1 / medium | Normal independent review of actual consequential diffs and relevant callers; no automatic high effort for every review |
| safety_reviewer | Sol 6.1 / high | Difficult firmware behavior, lock/transaction invariants, data-loss logic, conflicting evidence or a specific unresolved medium-review question |
| specialist | Astra / low | Deliberate escalation for an unresolved architectural/firmware question; increase effort only for a stated need |

Shell/Python tools do deterministic transforms, hashing and tests without an
agent assignment. Low is a starting policy for clearly scoped work, not a reason
to keep retrying an unsuitable task. Classify a failed attempt, supply missing
inputs or fix the environment, allow one justified correction, then escalate with
the exact question and failed evidence. Return to the lower tier after resolving
it. No automatic max/ultra or parallel multi-model review of every task.

## Why the generation change matters

Official [model guidance](https://learn.chatgpt.com/docs/models) says reasoning
levels do not map exactly across model generations. Its low setting suits
well-scoped work; medium adds planning depth and high suits harder tradeoffs.
The [selection guide](https://developers.openai.com/api/docs/guides/model-selection)
treats task recommendations as starting points and recommends the lightest setting
that meets the acceptance criteria. Checked 14 November 2026.

We therefore reduce old blanket Sol-medium/high assignments. We do **not** claim
a universal benchmark equivalence between Sol6.1 low and Sol6 medium, or between
new medium and old high. Judge real accepted tasks and repair work. No duplicate
benchmark campaign is needed before beta; use an occasional bounded comparison
only when an actual quality/cost question is unresolved. The stable acceptance
criteria do not change merely because the model setting changes.

## Delegation and context

Zero or one child is normal; at most two active, one writer per file, no nested
delegation. The coordinator must not duplicate a worker's investigation. Delegate
only when cheaper inference, isolation or independent review outweighs handoff
and integration cost. Independent review is required for consequential boot,
firmware-writing, disk-selection and rollback changes, not routine successful
logs or unrelated documentation edits. Retain a review when its reviewed bytes
and assumptions remain valid.

Use fresh narrow contexts for unrelated tasks. Where the runtime exposes explicit
spawn controls, select model/effort and a fresh-context option. Full-history forks
can inherit parent settings; naming a role in prose does not select its TOML.
Runtime model/effort metadata is the evidence when available. The CLI's observed
`max_depth=1` was V1-only and ignored by V2; instructions prohibit descendants,
but do not pretend that flag is a universal enforcement mechanism.

A handoff contains: outcome; owned paths; exact relevant source/evidence; decisions
already made; open question; checks; finite stop condition. Aim for a returned
summary below 800 tokens, with exceptions for necessary evidence. Keep full logs
in ignored `out/` or `tmp/`, not the conversation. Read only the current status and
relevant guide; retrieve exact historical receipts as needed.

After a long session reaches a safe checkpoint, persist the small current state
and resume in a fresh coordinator session. Account for hardware/jobs before doing
so. Do not reset after every command or rebuild history each turn. Large context
is not proof that every input token was uncached or billed at the same rate.

## Measuring actual usage

The low defaults take effect in a new trusted session; verify `/status` and
`/debug-config` once after adoption, not after every task. Accumulated context and
account-wide lifetime/peak totals do not identify project-specific cost.

For already captured Codex exec JSONL, summarize without reading successful
conversation/tool bodies into another model:

```sh
python3 scripts/summarize-agent-usage.py out/task.jsonl \
  --output out/task-usage.json --model gpt-6.1-sol --effort low
```

The script counts observed `turn.completed` events, separates cached input and
reasoning output when reported, ignores cumulative counters, and marks missing
or malformed measurements unknown with known subtotals. Explicit event metadata
is separate from supplied/unverified labels. Cross-file deduplication requires explicit thread identity; IDs without a
thread are scoped to each input stream. Turns without IDs cannot be deduplicated. No prompts, account/session identifiers or event bodies are
included in the summary. The documented event format is in
[non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode).

Record acceptance, retries and machine elapsed time with the task's ordinary
receipt when useful. Optimize total cost per accepted change, including reviews
and repairs. Script wall time, operator delay and model usage are different
quantities. Do not infer a cash saving from account aggregates or equate API
prices with subscription accounting. Unknown actual child settings stay unknown.
