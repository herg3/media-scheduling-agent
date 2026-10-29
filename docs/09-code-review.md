> 本文是AI子Agent代码审查原始记录，不代表候选人本人审查。原发现与定向复查按时间保留。

# Final whole-branch review

Reviewed committed implementation base e644925..f255721 and docs/01-requirements.md, docs/07-contracts.md. Read-only source review plus two isolated, temporary-database reproductions using work/agent-venv/Scripts/python.exe. No network, no full-suite rerun, no source changes.

## Findings

### P2 — Invalidate saved pending proposal when a later slot observation proves it occupied

Location: src/scheduling_agent/tools.py:110-119; src/scheduling_agent/evidence.py:28-33.

Trigger: model successfully saves pending 19:00, another operation occupies 19:00, model refreshes query_slots for 19:00–20:00 (successful response available_slots=[]), then finishes. The refresh updates queried_slots but never invalidates last_save.is_current. Final output compares only material/rule versions and proposed time, therefore reports task_status=awaiting_confirmation, draft.is_current=true, reason_code=null and asks the user to confirm the known-unavailable time. Reproduced with real SQLite and ToolContext. Counts remain one pending draft and zero draft slots; the final message is misleading, although confirmation-time transaction guards prevent unauthorized reservation.

Contract: docs/07 says an old proposal newly occupied must have is_current=false. Invalidate against explicit occupied_slots from successful refresh (do not infer conflict merely from six-result truncation), and provide an incomplete/conflict result or require a fresh saved proposal. Failed query refresh should also avoid implying availability has just been verified.

### P2 — Final state read failure discards already committed results and trace

Location: src/scheduling_agent/evidence.py:20; src/scheduling_agent/cli.py:93-94.

Trigger: after a normal successful save and its post-commit readback, read_draft raises sqlite3.OperationalError during final item_result construction. Reproduced by a MockModel subclass injecting the failure when returning finish. run_agent raises OperationalError; DB counts are drafts=1,draft_slots=1 but no output.json or trace.jsonl is written. CLI's generic exception branch then emits an input-style envelope with items=[] / TOOL_UNAVAILABLE, discarding the material, successful save evidence, and trace. For a batch, previous completed results are also lost.

Contract: INV-10 / FR-16 require preserving successful items; evidence must distinguish already committed state from unavailable current verification. Catch final read failures at item-result generation, retain trustworthy last_save/read observations and item identifiers, explicitly mark unknown current facts/is_current=null where needed, and still export trace. Do not rewrite this as a never-executed items=[] request error. write_run's separate readback-export catch does not address this earlier failure.

## Verdict

No P1 found. Core authorization and transaction design is sound on inspection: model cannot mint confirmation, confirmation binds draft/time/revision/hash, facts and occupancy are rechecked in a transaction, original request fingerprint is immutable, unique constraints guard draft and slot duplication, pending drafts do not reserve, and saved reserved drafts cannot silently migrate. Execution is bounded by model/tool counts and elapsed-time checks. Evaluation expectations are outside model context and databases are isolated per case.

Spec verdict: needs two targeted corrections before unconditional acceptance, both affecting truthful output rather than unauthorized writes. Quality verdict: strong prototype safeguards and evidence separation; not production scheduling/authentication. This review does not certify live case pass counts or the full test suite; those runs are owned by the parent task. Default system Python was too old for the package, so reproduction used the existing project virtual environment.

## Targeted re-review of f255721..fbb4b75

Both original P2 findings are resolved in the reviewed correction; the original findings above remain as historical review evidence. No new blocking P1/P2 was identified in this targeted diff review.

- The query_slots change invalidates last_save.is_current only when explicit occupied_slots includes the saved proposal. It records SLOT_CONFLICT while retaining the historically accurate saved status and draft ID. It does not mistake a truncated candidate list or an unrelated query range for proof of conflict.
- Final item_result readback now catches SQLite/DomainError failures, preserves a previously observed successful save snapshot, and emits FINAL_READBACK_UNAVAILABLE at item and run level. Existing unknown/failed save ledgers retain precedence, so the fallback does not upgrade an unverified save to success.
- The two added guardrail tests directly cover the original reproduction scenarios. Read the implementation review record documenting 2 failing regression tests before repair and 23 passing targeted tests afterward. This reviewer did not rerun those tests during the targeted re-review.
- Dynamic evaluation injection now occupies the actual first proposed time and writes before/after database evidence. Its regression forces an initial 20:00 proposal and checks that 20:00 is the injected conflict. E07 still requires the same draft ID, an increased revision, a changed suggestion and no reservation. E09 checks the safety boundary rather than requiring one particular model response strategy. Expected results remain outside model inputs.

Re-review scope: source diff f255721..fbb4b75, tests/test_guardrails.py, tests/test_evaluation.py, changed E07/E09 fixtures and expectations, and work/briefs/core-review.md. Read-only review; no network, full-suite rerun or business-source modifications. Scoped specification/quality verdict: acceptable after these fixes for synthetic local scheduling drafts. This review does not replace full offline tests or live evaluation.

Parent-reported status at report update, not independently verified by this reviewer: 74 offline tests passing; final-01 live evaluation 12/13, with E13 safely stopping without retry; stability re-evaluation still underway. These results must be attributed to the parent task's actual test/evaluation evidence, not to this review.

## Prompt/schema-description re-review of 0e0004d

Reviewed git show for commit 0e0004dd68859db931dca5265af59deab870626d. Actual diff: three added prompt lines and one changed expected_revision field-description line, across prompts.py and tools.py (4 insertions, 1 deletion). No execution-routing, repository, authorization, confirmation, budget, or evaluation assertion code changed.

No actionable P1/P2 found in this scoped change. Describing expected_revision as the current observed revision agrees with the repository optimistic-concurrency contract. Re-reading get_draft after REVISION_CONFLICT refreshes evidence rather than inventing a version. Describing a single retry for a query_slots observation marked retryable=true stays within the existing execution budgets and identical-attempt limit; it neither grants permission to reserve a slot nor bypasses the save gates. The model still chooses actual actions from live observations: these are general protocol/recovery instructions, not case-ID routing, replayed outputs, or a deterministic substitute for live decisions.

Minor wording boundary: the description's example that saving current revision 1 produces revision 2 applies to an actual update; an unchanged idempotent save may retain revision 1. This does not alter the instructed argument (pass observed current revision) or introduce a correctness blocker. Runtime return values remain authoritative.

Parent attributes this clarification to retained stability-01 failures (E07 wrong version argument and E13 early stop). This review verifies only the small committed change and its consistency with previously reviewed guards, not the underlying live failure artifacts or improved model reliability. Parent reports final-02 full live evaluation and stability-02 (four scenarios, three repetitions each) are underway on this commit; no pass rate is asserted here. Old failures are reported as retained. No network calls, test reruns, or source edits were performed for this re-review; only this report was appended.
