# Clinical Agent Explorer — Project Context

## 1. Purpose

Build a small, local, synthetic clinical-agent harness that makes the complete data flow easy to inspect:

```text
mock patient database
→ tools
→ agent prompt/context
→ run state
→ patient-level working memory
→ trace and final output
```

The project exists to make agent behavior understandable through concrete runs. Phase 1 should answer questions such as: What did the agent read? What did each tool return? What entered the next prompt? What changed in state? What was remembered for a later run?

It is not an attempt to build a production clinical agent or a successor to the old controller.

## 2. What Phase 1 Should Build

Create a minimal, runnable harness with:

- one synthetic gastroenterology patient and one focused task;
- a small file-backed mock database;
- a few explicit read tools and one reversible mock write tool;
- a simple bounded tool-calling loop;
- one serializable run-state object;
- patient-level working memory that persists across runs;
- append-only tool and state traces;
- saved prompt/context snapshots and a human-readable final result;
- a second run showing how prior memory is used without treating it as clinical truth.

Prefer plain Python and transparent files. Add a framework only if it makes the data flow easier to inspect.

## 3. Mock Gastroenterology Scenario

Use one fictional patient, `GI-001`, with recurrent epigastric symptoms and a deliberately small longitudinal record. The fixture should contain enough information to require multiple reads but remain understandable by inspection:

- demographics and encounter dates;
- recurrent epigastric pain/dyspepsia;
- regular NSAID exposure documented in a medication list or note;
- mild iron-deficiency anemia shown by dated laboratory results;
- an older endoscopy or gastroenterology note;
- a positive *H. pylori* result or treatment history;
- no clearly documented test of cure;
- at least one older or superseded fact so chronology matters.

The task is to review the available record, produce a concise evidence-based assessment, identify unresolved questions, and create one mock follow-up action such as an *H. pylori* test-of-cure order. The action must be read back before it is called verified.

This scenario is a data-flow demonstration, not a test of autonomous diagnosis. All people, identifiers, dates, and records must be synthetic.

## 4. Phase 1 Implementation

Use a deliberately small loop:

1. Load the task, empty initial run state, and relevant patient-memory entries.
2. Build and save the exact context sent to the model.
3. Let the model request one permitted tool call or return a final response.
4. Validate the tool name and arguments, execute it, and append a tool-call trace.
5. Add returned record IDs to run state and save a state-change event.
6. Repeat until a final response, an explicit error, or a small iteration limit.
7. For a write, store a mock receipt and use a separate read tool to verify the stored result.
8. Save the final response and append only useful follow-up information to patient memory.

Keep the model-facing context small and visible. It may contain the task, selected memory entries, current state summary, available tools, and tool results. Do not hide essential behavior behind an orchestration framework.

A practical file layout is:

```text
mock_db/
  patients.jsonl
  clinical_records.jsonl
  orders.jsonl
memory/
  patient_memory.jsonl
runs/<run_id>/
  run_state.json
  tool_calls.jsonl
  state_events.jsonl
  prompt_snapshots.jsonl
  final.md
```

## 5. Minimal Contracts

These contracts are intentionally small. Fields may be added only when a concrete Phase 1 run needs them.

### Clinical record

```json
{
  "record_id": "rec-001",
  "patient_id": "GI-001",
  "record_type": "lab|medication|note|procedure|result",
  "clinical_time": "2026-01-12T09:00:00Z",
  "source": "synthetic_fixture",
  "data": {}
}
```

`clinical_time` is when the clinical event occurred. Tool-call time belongs in the trace and must not replace it.

### Tools

Phase 1 needs only tools equivalent to:

```text
get_patient(patient_id)
search_records(patient_id, record_type?, from_time?, to_time?)
create_order(patient_id, order_type, reason, evidence_refs)
get_order(patient_id, order_id)
```

Every call returns structured JSON. Reads return stable record IDs. A write returns a receipt, but the action becomes `verified` only after `get_order` reads back a matching stored order. Invalid arguments and empty results must be represented explicitly.

### Run state

```json
{
  "run_id": "run-001",
  "patient_id": "GI-001",
  "task": "...",
  "status": "running|complete|stopped|error",
  "step": 0,
  "retrieved_record_ids": [],
  "working_summary": "",
  "unresolved_questions": [],
  "proposed_actions": [],
  "verified_actions": []
}
```

This is execution state, not a comprehensive patient model. Save it after each meaningful transition.

### Patient memory

```json
{
  "memory_id": "mem-001",
  "patient_id": "GI-001",
  "created_at": "2026-01-12T10:00:00Z",
  "source_run_id": "run-001",
  "kind": "prior_work|unresolved|next_step",
  "text": "No test-of-cure result was found; check again on the next run.",
  "evidence_refs": ["rec-007"],
  "status": "active|resolved|superseded"
}
```

Patient memory stores the agent's prior work and open loops, not authoritative medical facts. A later run may use memory to decide what to query, but must re-read the mock database before asserting a current fact. Entries are append-only; later entries may resolve or supersede earlier ones.

### Trace event

```json
{
  "event_id": "evt-001",
  "run_id": "run-001",
  "timestamp": "2026-01-12T10:00:01Z",
  "event_type": "prompt|tool_call|tool_result|state_change|final",
  "step": 1,
  "tool_name": "search_records",
  "input": {},
  "output_ref": "tool_calls.jsonl#call-001",
  "state_delta": {}
}
```

The trace should record observable inputs, outputs, decisions, and state changes. Do not store hidden chain-of-thought. Large tool results may be stored once and referenced by ID.

## 6. Scope Boundaries

- Keep Phase 1 to one patient, one task family, and a small number of tools.
- Use local synthetic JSON/JSONL data; do not connect to a real EHR or use real patient data.
- Favor deterministic fixtures and inspectability over realism or scale.
- Patient memory and run state are separate: memory spans runs; state belongs to one run.
- The database remains the source of clinical truth. Memory is a navigation aid.
- Preserve source record IDs and clinical timestamps in summaries and actions.
- Represent missing information, conflicting records, failed tools, and unverified writes honestly.
- A successful tool receipt is not proof that a write is present; use read-back verification.
- Do not implement speculative abstractions until a concrete run demonstrates the need.

## 7. Using `cat-on-the-bench`

The old repository is read-only reference material:

```text
D:\ArrowBallad\cat-on-the-bench
```

Codex may inspect it, guided by the handoff `REFERENCE_NOTES.md`, for:

- the official PhysicianBench MiniAgent tool-calling loop;
- task and tool schemas;
- representative trajectories and artifacts;
- examples of write receipts and read-back verification;
- context-building and trace conventions;
- documented failures involving temporal evidence, context compression, requirement/gap bookkeeping, and cross-state references.

Do not modify the repository, place new implementation inside it, rerun expensive experiments by default, or copy its controller architecture wholesale. Reproduce only the smallest concepts needed for this project and record the exact reference path when old code materially influences a decision.

The proposed old-project abstractions such as `RawEvidenceRecord`, `EvidenceCard`, `BeliefClaim`, `WorkingSet`, and a large `ExecutionState` are design references, not Phase 1 requirements.

## 8. Acceptance Criteria

Phase 1 is complete when:

1. A fresh local run can execute the gastroenterology task from the synthetic fixtures.
2. A reviewer can follow every database read, tool result, prompt snapshot, and state transition from saved artifacts.
3. The final assessment cites only record IDs actually returned during the run and distinguishes current, older, missing, and unresolved information.
4. The mock order is not marked verified until a separate read returns matching stored data.
5. The first run creates a useful unresolved or next-step memory entry.
6. A second run loads that memory, uses it to guide retrieval, rechecks the database, and resolves or supersedes it when appropriate.
7. The final output, run state, trace, and patient memory agree about task and action status.
8. The harness stops cleanly on invalid tool input, tool failure, or iteration limit and records the reason.
9. No file in `cat-on-the-bench` or any synced read-only reference directory is changed.

## 9. Explicit Non-goals

Phase 1 will not build:

- a production clinical decision-support system;
- a real FHIR server integration or hospital workflow;
- support for real PHI;
- a broad gastroenterology knowledge base;
- multi-patient scale, multi-agent orchestration, or a web UI;
- a generalized planner, requirement/gap engine, evidence ontology, belief-state engine, or working-set optimizer;
- autonomous high-risk prescribing or real external side effects;
- a recreation of the old LangGraph controller;
- benchmark optimization or claims of improved clinical quality;
- elaborate retrieval, vector search, or long-term memory infrastructure.

Phase 1 should remain small enough that one person can inspect an entire run and explain every state change.
