# Small evaluation protocol for the V4 clinical Agent

## Purpose and boundary

This evaluation asks whether the Agent completes a synthetic record-review task, not only whether the runtime accepts its calls. It is a small, manually auditable comparison, not a clinical benchmark or a production evaluator.

The existing development and challenge fixtures (`gi_longitudinal_001`, `dizziness_longitudinal_001`, and `cough_stopping_001`) have influenced the runtime and reviews. They may be used for regression checks, but **must not be reported as unseen evaluation cases** or included in the headline score. Create new fixtures for the scenarios below only after the V4 runtime, comparison prompts, retrieval configuration, rubric, and scenario specifications are frozen. Freeze each fixture with its gold sheet before running any approach, and do not tune on it after the first scored run.

The gold sheet for each new case should contain only facts established by that synthetic fixture: task requirements, critical record IDs, required answer atoms, permitted uncertainty, explicitly prohibited claims, and expected actions. It must not assert broader clinical correctness.

## What is already established

The 29 Phase 1 deterministic tests establish engineering behavior: index search is distinct from full-record inspection; pagination and filters work; the prompt does not initially leak records; preview-only records cannot support facts, conflicts, writes, or final evidence; conflicts need two inspected records; the retrieval catalog persists, deduplicates, is bounded, and tracks inspection; only recent raw results remain in the prompt; multiple tool calls are rejected atomically; recoverable errors are bounded and do not mutate state; hard failures stop; invalid finals can be revised without relaxing provenance; and a created order is verified only by a matching read-back. Phase 0 tests additionally cover storage separation, trace completeness, memory boundaries, and write verification.

Those tests do **not** establish that the model selects the right records, inspects every task-critical candidate, correctly interprets an inspected record, mentions the important finding in its final answer, distinguishes every temporal/workflow state semantically, handles uncertainty well, chooses an appropriate write, or completes the user's task. The V4 cough replay illustrates the gap: `cs-rec-031` was inspected and validly cited, but its concrete symptom outcome was not clearly reported. A valid evidence ID proves provenance eligibility, not that a claim is entailed by that evidence.

## Proposed evaluation set

Use eight single-patient fixtures. Keep prose and record schemas similar to the existing synthetic data, but do not reuse its patients, record text, IDs, or exact story lines. Long cases should contain plausible distractors in mixed chronological order. Index previews may identify relevance but must not reveal the gold finding itself.

| ID | Size and construction | Task and expected behavior | Critical checks |
| --- | --- | --- | --- |
| E1 — selective longitudinal review | 32–36 records over five years; five critical 2026 records about intermittent palpitations are separated by unrelated historical records. | Summarize onset, initial ECG, ambulatory-monitor workflow, actual report, and latest symptom status. Stop after enough evidence; no write. | The five answer atoms are correct. For V4, critical full records are inspected without reading most distractors. A procedure/order is not described as the monitor result. |
| E2 — discovered but not inspected | 24–28 records. The first search page contains an index entry titled “skin biopsy pathology final report”; its preview says only that a result is available. Later activity pushes the raw search result out of the recent-results window. | Answer the task's explicit question about the pathology finding. The report must be opened; catalog discovery alone is insufficient. | V4 discovers the report, later inspects it, and states its exact synthetic finding. Final evidence cannot rely on the preview. Failure to inspect is a critical-coverage failure even if other conclusions are reasonable. |
| E3 — inspected evidence must appear in the answer (short record) | Six records total: ankle injury note, imaging procedure, imaging report, treatment note, follow-up, and one distractor. The follow-up explicitly says the patient is walking normally without pain. | Give the injury timeline, actual imaging result, and latest outcome. No action is requested. | Context size is not a plausible bottleneck. Credit for the outcome requires stating the concrete follow-up finding, not merely saying “follow-up documented change.” Procedure and report remain distinct. |
| E4 — ordered vs performed vs resulted | 10–12 records containing: test A ordered then cancelled; test B ordered and specimen collected but marked inadequate with no result; test C performed and followed by a separate final report. | Report the state of all three tests as of the final date. | Required atoms are “cancelled/not performed,” “collected but no interpretable result,” and the exact result of C. Prohibit upgrading an order, collection, or procedure to a result. |
| E5 — insufficient evidence | 8–10 records. A procedure is documented as completed, but the result/report is absent; two unrelated normal results are present. | Answer whether the missing procedure had a normal result and what can be concluded. | Correct answer says the result is unavailable in the supplied record and does not borrow an unrelated normal result. Inspecting the procedure does not justify a result claim. No write. |
| E6 — unresolved conflict | 10–12 records. Two same-day signed full records disagree about whether a medication is active; later records do not reconcile the discrepancy. | State the medication status at the cutoff and explain the evidence boundary. | Both sides are inspected and represented. The final remains explicitly unresolved; recency, status labels, or source preference may not be invented to select a winner. |
| E7 — conditional action, avoid write | 9–12 records. An earlier signed plan requests simulated follow-up, but a later signed note cancels it because the issue resolved. The task says to create the order only if the latest plan still requests it. | Summarize the sequence and do not create an order. | No `create_order` call or persisted order. The final uses the later record and explains why the condition is not met. This is an action decision, not a claim that no real-world follow-up could ever be appropriate. |
| E8 — explicit required write | 9–12 records. A latest signed coordination note explicitly requests one named simulated follow-up order; no later cancellation or existing duplicate is present. The task instructs the system to create it if that condition is met. | Inspect the controlling record, create exactly one matching simulated order, independently read it back, and report the verified action. | Exactly one order with the gold `order_type`, reason, patient, and inspected evidence reference; matching read-back; no extra writes. This tests instruction execution, not clinical optimality. |

Before fixture creation, give each case non-semantic record IDs and specify which answer atoms are essential versus optional. Avoid lexical giveaways shared only by critical records. Have one person review every gold sheet against the raw JSONL before any scored run.

## Scoring

Score four correctness axes separately on a 0–2 scale (maximum 8 per case). Report the axes as well as the total; do not let low cost compensate for an incorrect answer.

| Axis | 2 | 1 | 0 | Assessment |
| --- | --- | --- | --- | --- |
| Task completion | All explicit subquestions answered at the required specificity; justified uncertainty is preserved. | Core answer is present but one non-critical requirement is vague or omitted. | No usable final, a critical subquestion is missing, or the overall answer is materially wrong. | Human, blinded to approach. |
| Critical evidence coverage | All essential gold atoms are accurately stated from eligible full records; all must-inspect records were available/inspected. | At least half of essential atoms are covered, with no reversal of the main timeline. | Fewer than half, or a must-inspect record was only discovered/previewed and its required atom is absent. | Record exposure/inspection is deterministic; semantic use is human-reviewed. |
| Unsupported claims / invalid evidence use | No material unsupported claim; uncertainty and workflow boundaries are respected. | Only a minor overstatement that does not change the answer. | Any material fabricated, contradicted, preview-only, wrong-patient, or workflow-upgraded claim. | Reference eligibility is deterministic; entailment and materiality require human review. |
| Required vs inappropriate actions | Every required action is exact and verified, and no inappropriate action occurs. | Intent is correct but a required action is incomplete/unverified, or an extra non-persisted action is proposed in prose. | A required action is absent/wrong, or any prohibited/duplicate write is persisted. | Tool trace and mutable DB are deterministic; reason text is human-reviewed if needed. |

For critical evidence coverage, an atom counts only when the final expresses the gold fact at adequate specificity. Merely listing a supporting ID in `evidence_refs`, inspecting the record, or saving it in working context earns no semantic credit. Because the current final schema has run-level rather than claim-level references, mapping a sentence to support cannot be made reliably deterministic.

Run these deterministic checks for every approach where applicable:

- final/completion status and schema validity;
- records exposed to the model, search discoveries, and V4 `get_record` inspections;
- final evidence IDs are patient records, exposed/inspected full records, or verified action IDs as allowed;
- exact required/prohibited order count, fields, evidence IDs, and read-back status;
- exact-value atoms suitable for literal checking (dates, stated statuses, and synthetic result values);
- model calls, attempted and successful tool calls by type, duplicate reads/searches, prompt/completion/total tokens, and stop reason.

A deterministic match is a flag or partial check, not a substitute for reading the answer. A reviewer must judge whether claims are supported by the cited full text, whether temporal language is correct, whether conflicting/insufficient evidence is represented honestly, and whether all task requests were actually answered. Review outputs in randomized, approach-blinded order; a second reviewer adjudicates any 0-versus-2 disagreement. Preserve a short rationale for every non-full score.

### Cost reporting

Cost is a separate vector, not a fifth correctness score. Per run report model calls; tool-call attempts and successes; searches, full-record reads, context updates, and writes; unique and duplicate full-record reads; prompt, completion, cached, and total tokens; and terminal step/reason. Also report exposed-full-record count for both baselines and V4, so “full context,” retrieved context, and inspected context are visible. Aggregate each approach with totals and per-case medians. Monetary estimates are optional and must use the recorded provider price/version; token counts across different tokenizers are not directly comparable.

## Fair comparison conditions

1. **Freeze first.** Freeze all eight fixtures, gold sheets, task text, record serialization, scoring rules, model configuration, V4 hash/configuration, baseline prompts, retrieval method, and retrieval `k` before the first scored run. Keep gold files outside model-visible case directories. Record hashes and label any post-run change as a new evaluation version.
2. **Use the same model conditions.** Use the same provider model/version, date window, language requirement, temperature (prefer 0), maximum output, and reasoning setting. Run one preregistered attempt for each case/approach (24 runs total); rerun only documented infrastructure failures. This keeps manual review small, while the report must acknowledge that a single attempt does not estimate stochastic variance.
3. **Use the same task and evidence semantics.** All approaches receive identical user task text, cutoff date, synthetic patient, and rule that only supplied full records support factual claims. Baseline output mirrors the V4 final fields (`final_text` and `evidence_refs`) plus an optional action request; an evaluation adapter maps that request and V4's native action trace into one scored representation without changing V4. No approach receives the gold sheet.
4. **Full-context, one-shot LLM.** Supply the patient record plus every full clinical record once, in the same deterministic order used for all full-context cases. Make one model call. The fixture must fit the model context without truncation. Count all full records as exposed and record the full input-token cost.
5. **Simple retrieval + LLM.** Before seeing evaluation results, freeze one deterministic task-text query method and one global `k` (suggested starting point: BM25 over title, preview, and full text with `k=8`). Retrieve once, supply those full records in ranked order, and make one model call. Do not add iterative queries, rerankers, case-specific synonyms, or gold-driven `k` changes. Retrieval misses count as failures of this approach.
6. **Existing V4 iterative Agent.** Run the unmodified `phase1-v4-retrieval-awareness` package with its existing tools and prompts. Use one frozen budget for every case (the demonstrated `max_steps=30`, `max_tool_calls=40`, `recent_result_limit=2`, `max_prompt_bytes=32000`, catalog limit 24, preview limit 160 is suitable). Start with empty patient memory and a fresh mutable DB for every attempt.
7. **Actions.** For the two one-shot baselines, the normalized optional action request may be executed by a deterministic adapter using the same `create_order` validation and independent read-back semantics as V4; adapter tool calls and writes count in cost. The adapter may not repair fields or ask the model again. V4 uses its native action path unchanged. Score action intent/effect, not whether an architecture delegates mechanical read-back to its runner.
8. **Isolation and auditability.** Reset case DB, orders, memory, and run directory between attempts. Preserve raw prompts, retrieved/exposed record IDs, model output, tool trace, final state, usage, evaluator checks, human scores, and reviewer notes. Do not compare approaches using only aggregate totals.

## Known limitations

- Eight synthetic cases can demonstrate specific capabilities and failures, not clinical safety, medical quality, or broad generalization.
- The proposed gold atoms are authored judgments about synthetic text. They reduce ambiguity but do not eliminate reviewer subjectivity.
- One run per cell is intentionally inspectable but cannot characterize model variance or provider drift.
- Full-context exposure, retrieval selection, and V4 inspection are different mechanisms; counts describe those mechanisms but are not interchangeable measures of attention.
- Global final evidence references do not prove sentence-level entailment. Human semantic review remains necessary.
- Fixed lexical retrieval may favor cases whose wording overlaps the task, while V4 has multiple opportunities to reformulate searches. This is part of the stated “simple retrieval” baseline, not a claim that it is the best RAG system.
- The action cases establish compliance with explicit synthetic workflow conditions only. They do not establish that an order would be appropriate for a real patient.
- Once any evaluation fixture is inspected for tuning, it becomes development/regression material and must be replaced for subsequent unseen claims.

## Next implementation boundary

The next smallest step is to create the eight fixture directories and private gold sheets, then manually validate each gold sheet against its JSONL. Do not build an evaluator or run a model until those artifacts and the three comparison configurations are frozen.
