> **Historical record — not active instructions or release gates.**
> Superseded by the [manual beta scope](../../beta-scope.md) and
> [current status](../../implementation-status.md). Original observations retain
> their recorded configuration and limitations.

# Cost conscious agent routing

## Configuration and activation

`.codex/config.toml` sets Sol medium as coordinator and Luna low as the default child. Custom roles live in `.codex/agents/`. Start a new Codex session in this trusted repository to load project settings; existing sessions need not reload them. Do not change global configuration or permissions. Keep root `AGENTS.md` concise and current; place detailed routing guidance here.

These defaults are a starting policy, not a demonstrated cost saving. OpenAI recommends comparing models on identical representative tasks, fixing acceptance criteria first, and measuring review and repair time as well as completion time. Keep the lightest setting that passes. See [model selection](https://developers.openai.com/api/docs/guides/model-selection).

Codex documents project agent TOML files and model/effort overrides. Children can inherit expensive parent settings when no override is supplied. Role instructions are not permission boundaries. See [subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents) and [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).

## Routing policy

The coordinator must request delegation for qualifying tasks, rather than spawning agents for every command. Prefer shell tools for deterministic transforms and checks. Delegate only when isolation, cheaper inference or independent review is likely to outweigh handoff and integration cost.

| Role | Model and effort | Project assignments | Initial context target |
| --- | --- | --- | --- |
| extractor | gpt-6-luna / low | Extract pinned board settings, summarize a selected log, update docs from verified facts | 2–6k tokens |
| implementer | gpt-6.1-sol / medium | Rootfs recipes, bounded provisioning logic, meaningful tests | 6–16k tokens |
| reviewer | gpt-6.1-sol / high | Installer device safety, SD write paths, update/rollback, KVM acceptance checks | 8–20k tokens |
| specialist | gpt-6-astra / high | Unresolved EL2/firmware behavior, contradictory boot evidence, architectural dead ends | 12–32k tokens |

Coordinator owns architecture and release gates. Use Luna low for straightforward work, not novel kernel patches or destructive disk logic. For a clear but reasoning-heavy extraction, explicitly use Luna high; if a custom role fixes low effort, spawn with explicit settings without that role and include its instructions. For an implementation ambiguity, first try Sol high with the same bounded evidence. Use Astra only when unresolved risk or failed reasoning justifies the escalation. Do not run multiple models on every task or automatically select max/ultra.

Work packages A and B from the hypervisor plan combine cheap source extraction with coordinator/specialist decisions. C uses the implementer. D requires implementer plus independent reviewer. E uses extraction for evidence tables and documentation, with coordinator signoff. No model tier may waive physical KVM, SSD boot or SD-write validation.

## Context handoffs

The table gives project working targets, not model context limits or enforced quotas. Count instructions, tool schemas, source excerpts, images and anticipated outputs when estimating context. Verify the selected runtime's actual limit; reserve at least 25% for tool results and completion. If essential dependencies exceed a target, enlarge deliberately or split along a real interface, never omit correctness-critical information to meet a number.

Use fresh context by default. In a runtime exposing `collaboration.spawn_agent`, set `fork_turns="none"`, `model` and `reasoning_effort` explicitly, and paste the matching role instructions into the task. That API may not expose named custom roles; merely mentioning a role does not select its TOML. Full-history forks in this environment inherit parent model and effort, defeating cheap routing. Other clients should use their documented named-agent selection and verify effective settings.

Pass paths and bounded excerpts, not the whole manual, repository or chat. Include upstream URLs with relevant sections and pinned revisions. Give access to dependencies and tell the worker what it must read; fresh contexts do not inherit unstated decisions. Store large logs under ignored `tmp/` and return relevant line references. Aim for returned summaries below 800 tokens, with exceptions for necessary evidence. Continue an existing child only for the same task; start fresh for unrelated work.

Use this handoff template:

```text
Role / explicit model / effort:
Outcome and acceptance criteria:
Project invariants: A733; 2 GB SD; 250 GB NVMe; no routine SD writes;
  real KVM guest required; preserve compatible firmware/kernel/DTB.
Owned files (exclusive write scope, or read-only):
Required source paths, revisions and relevant excerpts:
Decisions already made and assumptions still open:
Dependencies / interfaces / work already attempted:
Checks to run and evidence to return:
Context target / output target:
Stop and report if scope expands, sources conflict or acceptance cannot be tested.
Do not delegate, commit, push or flash devices as part of this assignment.
```

## Concurrency and escalation

Limit active children to two; one is usually enough. Parallelize independent reads or disjoint files only. The coordinator must not duplicate a worker's task while it runs. Children may not spawn descendants. Custom instructions enforce these workflow rules; they do not create a sandbox.

After a failed attempt, classify the cause: missing inputs, tool/environment failure, or reasoning failure. Supply missing evidence or fix the environment before buying a stronger model. Allow one targeted correction at the same tier when justified, then escalate with the failed attempt and exact error. Do not loop on unchanged inputs. Hardware absence remains an evidence gap regardless of model strength.

For boot firmware, partitioning and rollback, require an independent Sol high review and relevant executable checks before integration. The coordinator inspects all diffs and confirms results cover the requirement. A clean review is not proof of hardware behavior.

## Calibration and cost accounting

During the first implementation tasks, record a small representative set: board-config extraction, build recipe change, installer device-selection fix, and boot-log diagnosis. For each, define expected facts or executable checks before assignment. Establish a coordinator-reviewed baseline, then compare a lower tier on the same inputs for task types where quality is uncertain. Limit duplicate runs to calibration or unresolved high-impact questions.

Record model, effort, estimated/actual input and output tokens when exposed, cache usage when exposed, elapsed time, retries, reviewer repair time, and acceptance outcome. Use actual account prices or usage accounting; do not equate API pricing with subscription billing. Report unknown costs as unknown. Optimize total cost per accepted task, including coordinator and review work. Keep quality gates fixed and change one variable at a time. Recalibrate after model or major workflow changes; do not assume a larger context window improves reliability.

## Verification

Parse all TOML files and run `git diff --check` after edits. In a fresh trusted Codex session, confirm the selected coordinator and role models/efforts in session metadata, then run one bounded read-only extraction. A role name or a model's self-report is insufficient proof of the effective model. If the client ignores custom roles, use explicit spawn parameters and the handoff above. If the model is unavailable, report it and select an available tier deliberately; never silently fall back to an expensive inherited default.

Initial check (2026-11-04): Codex CLI 0.166.0 accepted configuration loading through `codex features list`; Python `tomllib` parsed all five configuration files. An explicit fresh-context `gpt-6-luna` / `low` spawn extracted the board, boot alternatives and KVM requirement. It initially overstated the 1.8 GB installer target as mandatory; one focused correction restored the target-versus-hard-limit distinction. This is limited evidence for reviewed extraction, not an implementation benchmark or measured savings. Automatic named-role selection in a new client session and effective model metadata remain activation checks; the current session used explicit spawn parameters.
