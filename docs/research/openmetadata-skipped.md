# OpenMetadata: the three things we marked "skip", studied in depth

> Quoted material © Collate and the OpenMetadata authors, Apache-2.0 (spec, service), https://github.com/open-metadata/OpenMetadata at d77e1fe709b4.

Source: `open-metadata/OpenMetadata` at HEAD `d77e1fe709b4`, sparse clone in
`research3/openmetadata/om-sparse`. Paths below are relative to that clone unless they start with
`backend/` or `frontend/`, which are ours. Licensing: `openmetadata-spec`, `openmetadata-service`,
`openmetadata-ui` and `openmetadata-mcp` are Apache-2.0, so ideas and schema shapes can be reused
freely. Nothing here depends on `ingestion/` (Collate Community License).

This follows up `OPENMETADATA-DEEP.md` §9 and §14, which marked three things as skip: the Flowable
workflow engine, auto-approval, and Elasticsearch/OpenSearch. Each section below covers how the
feature works, with verbatim JSON and code, then strengths, weaknesses, a verdict, and a design
sketch with effort.

**Summary of verdicts**

| # | Subject | Verdict | What we take | Effort |
|---|---|---|---|---|
| 1 | Governance workflow engine (Flowable/BPMN, JSON graph, editor) | **Adapt, small part.** Skip the engine and the editor | A declarative **review policy** per proposal kind and risk tier, evaluated inside `decide_proposal`. Transition hooks through our outbox events. OM's own Feb-2026 ADR reaches the same split: the repository owns transitions and the engine only follows | M |
| 2 | Auto-approval | **Skip the rules, adopt three guards and two features** | N-of-M thresholds per risk tier; "no eligible reviewer" means *assign admins without the requester* or *wait*, never approve; automation proposals always go to a person; the requester is stripped from the assignee list as data; restore to the last *durably* approved version. We need no rollback, because we hold changes until approval (OM's "Enforce" mode is our only mode) | S–M |
| 3 | Elasticsearch/OpenSearch search | **Skip the cluster, adapt four ideas** | Per-asset field boost config with exact/phrase/fuzzy match types; usage and tier boosts; RBAC compiled into the query instead of post-filtering; one vector per chunk with a fingerprint so unchanged text is not re-embedded; a metadata `semantic_search` tool. All of it on Postgres FTS + `pg_trgm` + Qdrant (or pgvector), with RRF we already have | M |

---

## 1. The governance workflow engine

### 1.1 Shape of a workflow

A workflow is an entity (`workflowDefinition`, `openmetadata-spec/.../governance/workflows/workflowDefinition.json`)
with `{name, config {storeStageStatus, lifecycle?}, trigger, nodes[], edges[], deployed, suspended}`.
An edge is `{from, to, condition?}`, where `condition` is a string matched against the source node's
`result` variable: `"true"`/`"false"` for checks, or a transition id such as `"approve"`, `"reject"`
or `"ack"` for user tasks.

**Triggers** (`elements/triggers/*.json`, enum `eventBasedEntity | noOp | periodicBatchEntity`):

- `eventBasedEntity.config`: `entityTypes[]`, `events: Created|Updated`, `exclude[]` (do not fire if
  only these fields changed), `include[]` (must be among the changed fields, takes priority),
  `filter` (JSONLogic, a string or a per-entity-type map, evaluated against the full entity), and
  `approvalMode: Default | Enforce`. The schema describes the two modes this way: *"Default runs it
  after the edit is published. Enforce holds the edit for review: the workflow's user approval task
  publishes it when approved and discards it when rejected."* (this distinction matters in §2).
- `periodicBatchEntity.config`: `schedule`, `entityTypes[]`, `filters` (a search query, so batch
  triggers depend on Elasticsearch), `batchSize` (default 500).
- `noOp`: no trigger. The workflow is started by the Task system when a task of the matching type is
  created ("Task V2").

**Node subtypes** (`elements/nodeSubType.json`, 20 values): `startEvent`, `endEvent`,
`parallelGateway`, `userApprovalTask`, `createRecognizerFeedbackApprovalTask`,
`checkEntityAttributesTask` (JSONLogic), `checkChangeDescriptionTask` (which fields were
added/updated/deleted, `AND|OR`), `setEntityAttributeTask {fieldName, fieldValue}`,
`setGlossaryTermStatusTask`, `setEntityCertificationTask {certification: Gold|Silver|Bronze}`,
`rollbackEntityTask`, `dataCompletenessTask {fieldsToCheck[], qualityBands[{name,minimumScore}]}`
(outputs `completenessScore`, `qualityBand`), `collectMetadataTask {stage, targetStage, fields[],
conditions, taskAssignees, revision: request|resubmit}`, `createAndRunIngestionPipelineTask`,
`runAppTask`, `sinkTask {sinkType: git|webhook|httpEndpoint, syncMode, outputFormat: yaml|json,
entityFilter, batchMode}` (branches `success|failure`), `createAndRunAIAutomationTask {template,
shouldRun, waitForCompletion, timeoutSeconds}` (branches `success|failure`), `policyAgentTask
{waitForCompletion, timeoutSeconds, accessType}` (branches `granted|manual|denied`, or
`revoked|…` for revocation), and the recognizer-feedback apply/reject tasks.

**`userApprovalTask.config`** (`elements/nodes/userTask/userApprovalTask.json`):

| Field | Meaning |
|---|---|
| `assignees {addReviewers=true, addOwners=false, addDomainOwners, candidates[], emptyAssigneeStrategy: none\|assignAdmins\|wait}` | Who gets the task. Teams are expanded to their users |
| `approvalThreshold` (1), `rejectionThreshold` (1) | Votes needed to approve or reject |
| `stageId`, `stageDisplayName`, `taskStatus`, `assigneeStrategy` | Stage labels stored on the task |
| `transitionMetadata[{id, label, targetStageId, targetTaskStatus, resolutionType, formRef, requiresComment}]` | The buttons. Edge conditions name these ids |
| `expiryTimer {durationVariable \| dateVariable, transitionId, closeAsResolution}` | An interrupting boundary timer that fires a transition |
| `allowPartialDecisions` | In Enforce mode, approve or reject change by change |
| `lifecycleMove {stage, targetStage}` | The approval gates a lifecycle stage move rather than a diff |

### 1.2 The seeded workflows, measured

`openmetadata-service/src/main/resources/json/data/governance/workflows/` holds 19 files. Their
trigger types, node and edge counts, and non-start/end node types:

```
AIAssetApprovalWorkflow          eventBasedEntity 12 12 check, setAttr, userApproval
GlossaryTermApprovalWorkflow     eventBasedEntity 40 39 check, rollback, setAttr, userApproval
MetricApprovalWorkflow           eventBasedEntity 23 22 check, rollback, setAttr, userApproval
RecognizerFeedbackReviewWorkflow eventBasedEntity  6  5 recognizer approval/apply/reject
IncidentLifecycleWorkflow        noOp 5 8   userApproval (3 stages)
IncidentResolutionTaskWorkflow   noOp 5 8   userApproval
TestCaseResolutionTaskWorkflow   noOp 5 9   userApproval
GenericIncidentTaskWorkflow      noOp 6 8   userApproval (4 stages)
CustomTask, DataQualityReviewTask, DescriptionUpdateTask, DomainUpdateTask, GenericReviewTask,
OwnershipUpdateTask, PipelineReviewTask, RequestApprovalTask, SuggestionTask, TagUpdateTask,
TierUpdateTask                   noOp 4 3   one userApprovalTask: approve | reject
```

So 15 of the 19 are a single human task wrapped in a graph. Only four use the event trigger. None
of the seeded workflows uses `parallelGateway`, `sinkTask`, `createAndRunAIAutomationTask`,
`policyAgentTask`, `dataCompletenessTask` or `setEntityCertificationTask`. Those exist for workflows
users author and for Collate's AutoPilot, which an ADR names `AutoPilotWorkflow.json`. It is not in
the OSS tree.

**DataQualityReviewTaskWorkflow** (verbatim; eleven seeds are this file with a different name):

```json
{
  "name": "DataQualityReviewTaskWorkflow",
  "config": { "storeStageStatus": true },
  "trigger": { "type": "noOp", "config": {}, "output": ["relatedEntity", "updatedBy"] },
  "nodes": [
    { "type": "startEvent", "subType": "startEvent", "name": "TaskStart" },
    { "type": "userTask", "subType": "userApprovalTask", "name": "TaskReview",
      "config": {
        "assignees": { "addReviewers": true, "addOwners": false, "candidates": [] },
        "approvalThreshold": 1, "rejectionThreshold": 1,
        "stageId": "review", "stageDisplayName": "Review", "taskStatus": "Open",
        "assigneeStrategy": "reviewers-and-assignees",
        "transitionMetadata": [
          { "id": "approve", "label": "Approve", "targetStageId": "approved",
            "targetTaskStatus": "Approved", "resolutionType": "Approved",
            "formRef": "approve", "requiresComment": false },
          { "id": "reject", "label": "Reject", "targetStageId": "rejected",
            "targetTaskStatus": "Rejected", "resolutionType": "Rejected",
            "formRef": "reject", "requiresComment": true } ] },
      "inputNamespaceMap": { "relatedEntity": "global" } },
    { "type": "endEvent", "subType": "endEvent", "name": "ApprovedEnd" },
    { "type": "endEvent", "subType": "endEvent", "name": "RejectedEnd" }
  ],
  "edges": [
    { "from": "TaskStart", "to": "TaskReview" },
    { "from": "TaskReview", "to": "ApprovedEnd", "condition": "approve" },
    { "from": "TaskReview", "to": "RejectedEnd", "condition": "reject" }
  ]
}
```

**IncidentLifecycleWorkflow** (verbatim, with the repeated `assignees` blocks shortened to `…`):
the stages are modelled as `userApprovalTask` nodes whose transitions are stage moves, and the
graph has cycles.

```json
{
  "name": "IncidentLifecycleWorkflow",
  "trigger": { "type": "noOp", "config": {}, "output": ["relatedEntity", "updatedBy"] },
  "nodes": [
    { "type": "startEvent", "name": "IncidentStart" },
    { "subType": "userApprovalTask", "name": "NewStage", "config": {
        "assignees": { "addReviewers": true, "addOwners": true, "candidates": [] },
        "approvalThreshold": 1, "rejectionThreshold": 1,
        "stageId": "new", "taskStatus": "Open", "assigneeStrategy": "owners-and-assignees",
        "transitionMetadata": [
          { "id": "ack", "label": "Acknowledge", "targetStageId": "ack", "targetTaskStatus": "InProgress" },
          { "id": "assign", "label": "Assign", "targetStageId": "assigned", "targetTaskStatus": "InProgress" },
          { "id": "resolve", "label": "Resolve", "targetStageId": "resolved", "targetTaskStatus": "Completed",
            "resolutionType": "Completed", "formRef": "resolve", "requiresComment": true } ] } },
    { "subType": "userApprovalTask", "name": "AckStage", "config": { "stageId": "ack", "taskStatus": "InProgress", "…": "…",
        "transitionMetadata": [ { "id": "assign", "…": "…" }, { "id": "resolve", "requiresComment": true, "…": "…" } ] } },
    { "subType": "userApprovalTask", "name": "AssignedStage", "config": { "stageId": "assigned", "…": "…",
        "transitionMetadata": [ { "id": "reassign", "targetStageId": "assigned" }, { "id": "resolve", "…": "…" } ] } },
    { "type": "endEvent", "name": "ResolvedEnd" }
  ],
  "edges": [
    { "from": "IncidentStart", "to": "NewStage" },
    { "from": "NewStage", "to": "AckStage", "condition": "ack" },
    { "from": "NewStage", "to": "AssignedStage", "condition": "assign" },
    { "from": "NewStage", "to": "ResolvedEnd", "condition": "resolve" },
    { "from": "AckStage", "to": "AssignedStage", "condition": "assign" },
    { "from": "AckStage", "to": "ResolvedEnd", "condition": "resolve" },
    { "from": "AssignedStage", "to": "AssignedStage", "condition": "reassign" },
    { "from": "AssignedStage", "to": "ResolvedEnd", "condition": "resolve" }
  ]
}
```

`TestCaseResolutionTaskWorkflow` is the same graph plus `AckStage -new-> NewStage`. The four-stage
`GenericIncidentTaskWorkflow` is `Open → {Pending, InProgress} → Resolved → Closed`.

**MetricApprovalWorkflow**: the trigger, every node with its config, and every edge, verbatim in
substance:

```text
trigger: {"type":"eventBasedEntity","config":{"entityTypes":["metric"],"events":["Created","Updated"],
          "exclude":["entityStatus"],"include":[],"filter":{}},"output":["relatedEntity","updatedBy"]}

CheckMetricHasReviewers           check  {"and":[{"some":[{"var":"reviewers"},{"!=":[{"var":"fullyQualifiedName"},null]}]}]}
CheckIfMetricUpdatedByIsReviewer  check  {"and":[{"isReviewer":{"var":"updatedBy"}}]}
CheckMetricIsReadyToBeReviewed    check  {"and":[{"!!":[{"var":"description"}]}]}
CheckIfMetricIsNew                check  {"and":[{"==":[{"var":"version"},0.1]}]}
ApproveMetric / ApprovalForUpdates userApprovalTask (reviewers, threshold 1/1, approve | reject[requiresComment])
RollbackMetricChanges             rollbackEntityTask {}
Set…Status… (×7)                  setEntityAttributeTask {"fieldName":"status","fieldValue":"Approved|In Review|Draft|Rejected"}

MetricCreated -> CheckMetricHasReviewers
CheckMetricHasReviewers -false-> SetMetricStatusToApproved -> ApprovedEnd           # nobody to ask: approved
CheckMetricHasReviewers -true-> CheckIfMetricUpdatedByIsReviewer
CheckIfMetricUpdatedByIsReviewer -true-> SetMetricStatusToApprovedByReviewer -> AutoApprovedByReviewerEnd
CheckIfMetricUpdatedByIsReviewer -false-> CheckMetricIsReadyToBeReviewed
CheckMetricIsReadyToBeReviewed -false-> SetMetricStatusToDraft -> DraftEnd
CheckMetricIsReadyToBeReviewed -true-> CheckIfMetricIsNew
CheckIfMetricIsNew -true-> SetMetricStatusToInReview -> ApproveMetric
CheckIfMetricIsNew -false-> SetMetricStatusToInReviewForUpdate -> ApprovalForUpdates
ApproveMetric -approve-> SetMetricStatusToApprovedAfterApproval -> ApprovedEndAfterApproval
ApproveMetric -reject-> SetMetricStatusToRejected -> RejectedEnd
ApprovalForUpdates -approve-> SetMetricStatusToApprovedAfterReview -> ApprovalEnd
ApprovalForUpdates -reject-> RollbackMetricChanges -> RollbackEnd
```

**GlossaryTermApprovalWorkflow** (40 nodes, 39 edges) is the metric graph plus an automation
branch in front of it. The front of it, verbatim:

```json
{ "type": "automatedTask", "subType": "checkEntityAttributesTask",
  "name": "CheckIfGlossaryTermIsAutomationProposal",
  "config": { "rules": "{\"and\":[{\"==\":[{\"var\":\"provider\"},\"automation\"]},{\"!=\":[{\"var\":\"entityStatus\"},\"Approved\"]}]}" },
  "inputNamespaceMap": { "relatedEntity": "global" } },
…
{ "type": "userTask", "subType": "userApprovalTask", "name": "ApproveAutomationProposal",
  "displayName": "Approve the Automation's Proposal",
  "config": { "assignees": { "addReviewers": true, "addOwners": false, "candidates": [],
                             "emptyAssigneeStrategy": "assignAdmins" },
              "approvalThreshold": 1, "rejectionThreshold": 1, "…": "approve | reject(requiresComment)" },
  "output": ["updatedBy"] }
```

```text
GlossaryTermCreated -> CheckIfGlossaryTermIsAutomationProposal
  -false-> CheckGlossaryTermHasReviewers …(the metric graph)…
  -true->  CheckIfAutomationProposalUpdatedByIsReviewer
             -true->  SetAutomationProposalStatusToApprovedByReviewer -> AutomationProposalApprovedByReviewerEnd
             -false-> CheckIfAutomationProposalIsNew
                        -true->  …InReview -> ApproveAutomationProposal -approve-> Approved | -reject-> Rejected
                        -false-> …InReview(Update) -> ApproveAutomationProposalUpdate
                                     -approve-> Approved | -reject-> RollbackAutomationProposalChanges
```

`AIAssetApprovalWorkflow` (aiApplication, llmModel, mcpServer) acts only when
`entityStatus == "In Review"`. With no reviewers it does nothing (`NoActionEnd`), a reviewer's own
edit is approved, and anyone else's edit goes to a 1-of-N reviewer task.

### 1.3 How a workflow runs

**Compilation.** `flowable/MainWorkflow.java` first runs `new WorkflowGraph(def).validate()`, which
checks that every `inputNamespaceMap` variable is output by a node that can *reach* this one (a
BFS, so transitive inputs are allowed). It then turns each node into a Flowable BPMN `SubProcess`
through `elements/NodeFactory.java` and each edge into a `SequenceFlow` whose condition is
`${<node>_result == '<condition>'}`. It adds an error boundary event that routes runtime exceptions
to an `Error` end event, which carries `terminate` (ADR 2026-10-22 "a workflow run never ends with a
branch waiting at a join": before that, a failing branch inside a fork left the instance `RUNNING`
forever). `flowable/TriggerWorkflow.java` compiles the trigger into a *separate* process that
starts on a signal and calls the main one as a `CallActivity`. Every workflow is therefore two BPMN
process definitions.

**Event to process.** `WorkflowEventConsumer implements Destination<ChangeEvent>` subscribes to
OM's own change-event stream. It computes the signal `"<entityType>-<eventType>"`, applies
`exclude`/`include`/`filter`, skips any change by `governance-bot` (the workflow's own writes, so
it cannot loop), and skips every other bot change, *except* a bot's create of an entity whose
`provider` is `automation` (ADR 2026-10-22 "agents' glossary-term proposals are decided through the
approval workflow"). It then calls `WorkflowHandler.triggerWithSignal(signal, variables)` with
`relatedEntity` (an entity link plus the immutable id) and `updatedBy`.

**A user task inside the BPMN** (`elements/nodes/userTask/UserApprovalTask.java`, 693 lines):

```
SubProcess
  Start → SetApprovalAssigneesImpl (service task) → ExclusiveGateway "hasAssigneesGateway"
            ├─ ${hasAssignees}  → UserTask [listeners: SetCandidateUsersImpl, CreateTask,
            │                               ApprovalTaskCompletionValidator]
            │                       + optional interrupting timer → ExpireOnTimerImpl
            └─ ${!hasAssignees} → AutoApproveServiceTaskImpl         ("No assignees", default flow)
  → (Enforce mode) ResolveHeldChangeImpl: publish or discard the held change → End
  + termination boundary event
```

Two comments in that file explain why the entry path is forced synchronous:
*"Force sync execution on the approval subprocess so the entry path (SetApprovalAssigneesImpl →
user task creation → CreateTask listener) runs on the caller's thread inside the current
transaction. Without this the async job executor picks up the continuation after POST /resolve
returns, which races with client reads and subsequent writes."* An async engine had to be made
synchronous at its most visible step.

**Tasks.** `CreateTask.java` (1 560 lines) creates OM's own `Task` entity (the inbox item) from the
Flowable user task. It writes the thresholds onto it and seeds the vote lists:

```java
delegateTask.setVariable("approvalThreshold", catalogCounts ? 1 : taskApprovalThreshold);
delegateTask.setVariable("rejectionThreshold", catalogCounts ? 1 : rejectionThreshold);
delegateTask.setVariable("approversList", new ArrayList<String>());
delegateTask.setVariable("rejectersList", new ArrayList<String>());
```

There is also an "all assignees" approval mode:

```java
static int resolveApprovalThreshold(String approvalMode, int nodeThreshold, List<EntityReference> assignees) {
  return WorkflowStartVariables.APPROVAL_MODE_ALL_ASSIGNEES.equals(approvalMode)
      ? Math.max(nodeThreshold, assignees == null ? 0 : assignees.size())   // a team is one entry
      : nodeThreshold;
}
```

A resolve call (`TaskRepository` → `WorkflowHandler`) appends the voter to `approversList` or
`rejectersList` and tries to complete the Flowable task. `ApprovalTaskCompletionValidator` (a
`complete` listener) throws until a threshold is met:

```java
if (approvalThreshold <= 1 && rejectionThreshold <= 1) return;     // single approval
boolean approvalThresholdMet = approvalCount >= approvalThreshold;
boolean rejectionThresholdMet = rejectionCount >= rejectionThreshold;
if (approvalThresholdMet || rejectionThresholdMet) return;
throw new RuntimeException(String.format(
    "MULTI_APPROVAL_THRESHOLD_NOT_MET: Task requires %d approvals but only has %d", …));
```

Nothing says *which* threshold should win if both are met at once, and an exception is used for
control flow.

When a task is rejected, the reviewer's comment and name become the global variables
`rejectionComment` and `rejectedBy`, so a later `collectMetadataTask {revision: request}` can open a
"Revise and resubmit" task, and the next edit resubmits it (ADR 2026-10-21). When an approved task
changes an entity, the change is recorded as that entity's own change event, by the approver, with
`ChangeSource.SUGGESTED` kept for agent suggestions (ADR 2026-10-30). That ADR exists because the
REST response filter recorded only the *task's* event, so alerts, the audit log and the feed missed
approved description, owner and domain changes. It is a consequence of running approvals through
an engine that is separate from the entity write.

**Change requests and lifecycle.** In Enforce mode the edit becomes a change request: `mutationOp`
with `baseValue`, conflict detection, and an append-only `approvalDecision`. This was covered in
`OPENMETADATA-DEEP.md` §9. `config.lifecycle {entityType, gates[{stage, checks[], conditions, task}]}`
is a guided playbook that a compiler expands into `collectMetadataTask` + `userApprovalTask
{lifecycleMove}` nodes.

### 1.4 Versioning, migration and operations

- **Redeploy.** Saving a definition deploys new BPMN versions of the trigger and the main process.
  Running instances stay on their old version, which is Flowable's model. The ADR
  "a workflow redeploy deploys before it drains" records a bug where draining old versions before
  deploying left a window with no start-signal subscription, so entity events in that window started
  nothing. Old `ACT_RE_PROCDEF` versions are drained 100 at a time. In migration context the drain
  is skipped, because *"customers with long-running approval instances accumulate tens of thousands
  of versions… running the drain per iteration turns migrate into a multi-hour job"*
  (`WorkflowHandler.java`).
- **Seed migration.** *"Seeding never updates a stored workflow"*. Changing default behaviour (for
  example adding `emptyAssigneeStrategy: assignAdmins`) needs a data-migration step that patches
  every stored definition in place and keeps what an admin changed (ADR 2026-10-22). Every change to
  a default is therefore a migration.
- **Tables.** These are Flowable's own: `ACT_RE_*` (definitions), `ACT_RU_*` (executions, tasks,
  variables, jobs, timer jobs, event subscriptions), `ACT_HI_*` (async history), plus OM's
  `workflow_instance_time_series` and `workflow_instance_state_time_series`. `IdempotentDdl*` wraps
  Flowable's DDL so schema creation can be repeated.
- **History.** `setAsyncHistoryEnabled(true)`, `setEnableHistoryCleaning`,
  `setCleanInstancesEndedAfter(cleanAfterNumberOfDays)`. OM's own run tables were never cleaned:
  *"A busy install reached 7M state rows, and every read that looked runs up by entity… scanned the
  table and timed out."* The fix keeps each entity's latest run forever and expires the rest after
  30 days, but only once no `ACT_RU_EXECUTION` row with that business key is alive (ADR 2026-10-22).
- **Concurrency.** A configurable async executor (`corePoolSize`, `maxPoolSize`, `queueSize`,
  `jobLockTimeInMillis`). On MySQL, `READ_COMMITTED` isolation is forced because REPEATABLE_READ
  *"gap / next-key locks deadlock Flowable's concurrent ACT_RU_* runtime writes"*.
  `BoundedLockWaitCommand` uses `SET LOCAL lock_timeout` because Postgres lock waits are otherwise
  unbounded. The connection pool has 4 connections of headroom over the executor for Flowable's own
  threads. A fair `Semaphore(MAX_CONCURRENT_TASK_RESOLUTIONS = 8)` sits in front of task
  resolution, because *"a burst blocked in MyBatis popConnection past the client timeout and stranded
  tasks in review."* There are `UnlockJobSql` and `UnlockExecutionSql` to free stuck jobs.
  `BatchParallelism` caps batch nodes at a quarter of the DB pool.
- **Failure handling.** `WorkflowFailureListener` (a global Flowable event listener) marks
  instances FAILED. `WorkflowInstanceTerminator`, `WorkflowStopRequests` and
  `SubWorkflowFailureListener` handle stop and cleanup. Retries are Flowable's per-job retries.
- **Scale (Feb-2026 ADR).** `adr-incident-manager-governance-workflows.md` sizes the incident case
  at *"10K–75K concurrent open incidents"*. A BPMN UserTask costs *"~5–10"* runtime rows per open
  task, against *"~2"* for a message subscription. Over a lifetime that is *"~225K correlations"*
  for 75K incidents with three transitions each. The ADR lists the risks as *"ACT_RU growth"*,
  *"Process orphaning"*, and *"MainWorkflow compiler must support cycles. Today it assumes a
  DAG"*.

### 1.5 The Task Lifecycle Node ADR (27 Feb 2026, status Proposed)

Its decision: a new `taskLifecycleNode` that **does not use Flowable's UserTask**. *"The node uses
OpenMetadata tasks as the source of truth… Repository stays in the critical path. All transitions
are synchronous. Flowable is notified after the fact. If Flowable is down, transitions still
succeed."* The node config:

```json
{ "type": "taskLifecycleNode",
  "config": { "template": "incident", "statuses": ["New","Ack","Assigned","Resolved"],
              "terminal": ["Resolved"], "responsibles": { "source": "tableOwner" }, "ttl": "P30D" } }
```

Each status is exposed to the parent graph, so users can attach hooks with ordinary edges:

```json
{ "from": "incident", "to": "notifySlack", "condition": { "status": "Assigned" } },
{ "from": "notifySlack", "to": "incident" }
```

It rejects "Resolution through Flowable" because *"Puts Flowable in the critical path"*. The
implementation slice that followed (`openspec/changes/incident-lifecycle-workflow/design.md`) went
further. `storeInternal()` persists the record and broadcasts through the synchronous
`EntityLifecycleEventDispatcher`. Short-lived, signal-started processes run the generic
`openTask`/`closeTask` nodes (Task → Delegate → Impl layering with no Flowable imports in the Impl),
and every step is idempotent. Meanwhile the seeded `IncidentLifecycleWorkflow` shown above models
stages as `userApprovalTask` + `transitionMetadata`. Two designs are live at once.

**Lesson for us:** OM's own architects, at 10K–75K open items, moved the source of truth *out of*
the BPM engine and kept it in the domain table, with the engine reduced to a hook runner. That is
what our inbox already is: `inbox.proposal` / `inbox.hitl_request` are the source of truth,
`decide_proposal` is the synchronous transition, and outbox events (`proposal.decided`,
`proposal.step_decided`) drive the owning worker.

### 1.6 The editor UI

`openmetadata-ui/.../components/WorkflowDefinitions/WorkflowBuilder/`: a **ReactFlow** canvas
(`WorkflowCanvas.tsx`, `CustomNodes.tsx`, `StraightEdge.tsx`), a palette (`WorkflowSidebar`), a
per-node config side panel (`NodeConfigSidebar`, `NodeFormSidebar`). The per-type forms are
`UserApprovalForm`, `CheckConditionForm`, `CheckChangeDescriptionForm`, `SetActionForm`,
`RevertBackForm`, `DataCompletenessForm`, `SinkTaskForm`, `CollectMetadataForm`, a fallback
`SchemaBasedNodeForm` on `@rjsf` (JSON-Schema forms), `TriggerConfigSection`,
`EventTriggerFilterSection`, `CronExpressionBuilder` and `ApprovalModeSection` (Default/Enforce).
A `ConnectionConditionModal` picks the edge condition. A `ConditionBuilder` with
`QueryBuilderWidget` (the same advanced-search builder Explore uses) writes JSONLogic. There is also
`WorkflowExecutionHistory`, and a per-entity "Workflow" tab on glossary terms
(`WorkflowHistory.component.tsx`). A `WorkflowModeContext` disables the forms for read-only or seeded
definitions.

### 1.7 Strengths

1. One abstraction covers approvals, incident triage, certification, lifecycle playbooks and
   automation chains. Customers change review paths without a release.
2. The edge-condition-equals-transition-id convention is simple and readable.
3. The useful parts are small and declarative: `assignees` with an empty-assignee strategy,
   approve/reject thresholds, `requiresComment` per transition, `expiryTimer` with a transition id,
   `exclude`/`include` changed-field filters, and Default vs Enforce.
4. History per entity is visible in the UI.

### 1.8 Weaknesses (from their own code and ADRs)

1. **Operational weight.** It needed: a second connection pool, a semaphore against pool exhaustion,
   forced synchronous gateways, lock-timeout wrappers, MySQL isolation overrides, unlock SQL,
   version drains, a migration-time special case, retention for millions of rows, and orphan
   cleanup.
2. **Two sources of truth.** The Task entity and the Flowable task drift apart. Hence the ADRs on
   change events, on task closure "both sides, idempotent", and on lost signal subscriptions.
3. **Most workflows are trivial.** 15 of 19 seeds are one approve/reject node, which a status column
   expresses.
4. **Defaults are migrations.** Seeds are never updated, so every behaviour fix ships a data
   migration that has to merge with admin edits.
5. **The DAG assumption** blocks lifecycles with loops. The ADR calls cycle support the *"biggest
   technical risk"*.
6. **Default mode publishes before approval** and relies on rollback (§2). A governance engine that
   lets an unapproved metric definition be live, even briefly, conflicts with our invariant that the
   LLM proposes and deterministic checks plus a human decide.
7. **The JVM-only stack** (Flowable 7.2, JUEL, MyBatis). In our Python services the equivalent would
   be a second runtime or a rewrite.

### 1.9 Verdict: adapt the declarative policy, skip the engine and the editor

We already have the engine OM is moving toward: a domain table that owns its state machine,
synchronous transitions in `decide_proposal` (`backend/services/api/modules/inbox/routes.py`), an
outbox for hooks (ADR-06), Temporal for timers and schedules, and the owning worker applying the
decision (`backend/services/worker_agent/proposals.py`, `metric_changes.py`). What we lack is
OM's declarative layer: today the review path for each kind is code. `requires_compliance` →
a second step with the `compliance` capability (BR-119), BR-28's eval gate, the data-product
creator rule (BR-175) and the edit-restarts-chain rule (BR-62) are all `if`s in `decide_proposal`.

**Design sketch: `inbox.review_policy`**

```sql
CREATE TABLE inbox.review_policy (
  id uuid PRIMARY KEY, org_id uuid NOT NULL,
  data_space_id uuid,                        -- NULL = org default
  proposal_kind text NOT NULL,               -- metric_change, synonym, relationship, knowledge_page, …
  impact_level text NOT NULL,                -- low | medium | high  (inbox.proposal.impact_level)
  version int NOT NULL, status text NOT NULL,-- draft | active | retired; one active per key (partial unique)
  spec jsonb NOT NULL,
  created_by uuid, approved_by uuid,         -- a policy change is itself a proposal (kind review_policy)
  CHECK (approved_by IS DISTINCT FROM created_by)
);
```

```json
{
  "steps": [
    { "id": "review",  "assignees": { "capability": "context_edit", "owners": true },
      "approve_threshold": 1, "reject_threshold": 1,
      "distinct_from": ["proposer", "editors"], "reject_requires_note": true },
    { "id": "control", "assignees": { "capability": "compliance" },
      "approve_threshold": 1, "distinct_from": ["proposer", "previous_steps"],
      "when": { "requires_compliance": true } }
  ],
  "gates": ["eval_regressions_reviewed"],
  "no_eligible_reviewer": "assign_admins",        // assign_admins | wait ; never "approve"
  "expiry": { "after": "P30D", "transition": "expired" },
  "escalate": { "after": "P3D", "to_capability": "space_admin" }
}
```

- **Executed by:** `decide_proposal`, synchronously in its transaction. It reads the active policy
  for `(kind, impact_level, data_space)`, computes the current step from the `proposal_review` rows
  (the code already derives `accepted_before` and `editors`), checks the `distinct_from` sets with
  `two_people()`, counts votes against the thresholds, and either stays `pending_review` (and
  re-points `hitl_request.assignee_capability` to the next step, as BR-119 does today) or moves to
  `accepted`/`rejected`. When the policy is resolved at proposal creation, the worker stamps
  `review_policy_id` + `version` on `inbox.proposal`, so a policy edit never changes the rules
  under an open proposal. This is Flowable's "running instances stay on the old version", without
  any process versions.
- **Timers:** `expires_at` exists (BR-29). Escalation is a Temporal schedule (we already run
  schedules there) that scans `hitl_request` older than `escalate.after`. No per-proposal timers and
  no workflow engine.
- **Hooks:** consumers of `proposal.step_decided` / `proposal.decided` on the outbox. This covers
  what the Task Lifecycle Node ADR wants (hooks on any transition) and needs no graph.
- **Not built:** a canvas, arbitrary node graphs, branching on entity JSONLogic. If a customer needs
  a branch, it is a `when:` predicate over a closed set of proposal fields (`impact_level`,
  `requires_compliance`, `kind`, `target_service`, eval regressions), never free JSONLogic over the
  entity.
- **UI:** a table editor per kind × risk tier on the admin pages next to `SelfApprovalSwitch.tsx`,
  and `ApprovalChainPage.tsx` reading the step list from the stamped policy instead of inferring it.
- **Effort: M.** One table + migration, policy resolution in the worker, a refactor of
  `decide_proposal` to follow the spec, defaults seeded to reproduce today's behaviour exactly (with
  `test_two_people` and the BR-119/BR-28 tests as the guard), and one admin page.

---

## 2. Auto-approval

### 2.1 Every path by which OM approves without a second person

| # | Path | Where | Condition |
|---|---|---|---|
| A | **No reviewers → Approved** | `CheckGlossaryTermHasReviewers` / `CheckMetricHasReviewers` `-false->` `Set…StatusToApproved` | JSONLogic `{"some":[{"var":"reviewers"},{"!=":[{"var":"fullyQualifiedName"},null]}]}` is false. For metrics this is the *default* path: a metric has no reviewers unless someone adds them |
| B | **Updater is a reviewer → Approved** | `CheckIf…UpdatedByIsReviewer` `-true->` `…ApprovedByReviewer` → `AutoApprovedByReviewerEnd` | `{"isReviewer":{"var":"updatedBy"}}`. Team membership counts (see below) |
| C | **Reviewer edits an automation's proposal → Approved** | `CheckIfAutomationProposalUpdatedByIsReviewer` | the same `isReviewer`. A reviewer's *edit* of an agent's draft term is that reviewer's approval |
| D | **The user task finds no assignees → AutoApprove** | `UserApprovalTask` gateway `${!hasAssignees}` → `AutoApproveServiceTaskImpl` | Only when `emptyAssigneeStrategy == none` (the default) *and* the task is not workflow-managed |
| E | **A change request only its requester could review** | `AutoApproveServiceTaskImpl` → `ApprovalDecisionService.recordAutomaticApproval` | Enforce mode with no eligible reviewer and strategy `none` |
| F | **AI assets with no reviewers** | `AIAssetApprovalWorkflow` `CheckAIAssetHasReviewers -false-> NoActionEnd` | Not approved, but nothing happens either: the asset stays as it is |

The custom operator (`rules/LogicOps.java` registers it, `rules/JsonLogicUtils.evaluateUserInRole`
implements it), verbatim in substance:

```java
// {"isReviewer": { var: "updatedBy"} }
Object reviewersObj = entityMap.get(role);                 // "reviewers" (or "owners" for isOwner)
for (EntityReference ref : reviewers)                       // direct reviewer
  if ("user".equals(ref.getType()) && updatedBy.equals(ref.getName())) return true;
User user = Entity.getEntityByName(Entity.USER, updatedBy, "teams", Include.ALL);
for (EntityReference ref : reviewers)                       // member of a reviewer team
  if ("team".equals(ref.getType()) && user.getTeams().stream().anyMatch(t -> ref.getId()…)) return true;
} catch (Exception e) { /* log error if needed */ }       // a lookup failure reads as "not a reviewer"
```

**Self-approval prevention exists, but only for path D.** `SetApprovalAssigneesImpl.java`:

```java
// Prevent self-approval: remove the requester from the assignees. … A non-workflow-managed task under
// the "none" strategy keeps the requester when no one else is available, so the task stays actionable;
// "assignAdmins" and "wait" have a fallback of their own below … A change request is never given back
// to its requester.
boolean removedRequester = assigneeList.removeAll(requesterEntityLinks);
if (removedRequester && assigneeList.isEmpty() && !workflowManagedTask && !reviewsChangeRequest
    && emptyAssigneeStrategy == NONE) {
  assigneeList.addAll(preRemovalAssignees);          // the requester gets their own task back
}
// ASSIGN_ADMINS routes to all platform admins, excluding the requester so self-approval can never happen.
// NONE leaves the list empty, so the approval node approves automatically.
if (assigneeList.isEmpty() && assignAdmins) { admins.removeAll(requesterEntityLinks); assigneeList.addAll(admins); }
// An explicit admin fallback must never turn into implicit approval when no eligible admin remains
// (for example, the requester is the sole admin). Keep an open task.
boolean hasAssignees = workflowManagedTask || emptyAssigneeStrategy != NONE || !assigneeList.isEmpty();
```

The requester is read from both `updatedBy` (entity workflows) and `taskUpdatedBy` (task
workflows). A code comment cites a Copilot review of 2026-08-04 that found a self-approval leak
when the variable was not a String.

So under `none`, the default for most nodes, a sole reviewer who is also the requester **gets the
task back and may approve their own change**. This happens only after paths A and B have already
approved most self-edits at the graph level, before any task exists.

### 2.2 The audit trail OM leaves

- Paths A, B and C: the entity gets a new version whose `entityStatus` changed to `Approved`, written
  by `governance-bot` impersonating the user (`ChangeActor.system(updatedBy, GOVERNANCE_BOT)`). The
  Flowable history shows which end event was reached (`AutoApprovedByReviewerEnd`, `ApprovedEnd`).
  When `storeStageStatus: true`, `workflow_instance_state_time_series` holds the stage. No
  "self-approval" record is written, and no task exists to show who approved.
- Path D: `taskRepository.closeApprovalTaskForEntity(fqn, "system", "Task auto-approved: " + reason)`,
  where `reason` is `"Submitter is a reviewer"` or `"No reviewers configured"`. The node variable
  `autoApprovalReason` is set as well.
- Path E: an `approvalDecision` recorded by the system (`recordAutomaticApproval`) so that Commit
  publishes it.

So the audit is an end-event name, a log line and a system-closed task. There is nothing a
compliance reader could query as "approvals where approver = author".

### 2.3 How rollback works

`RollbackEntityImpl` (322 lines). On reject of an *update*:

1. `findMostRecentApprovedVersion`: walk the entity's earlier versions newest-first. Take the first
   one that is Approved **and** *durably* approved:

   ```java
   // A non-reviewer edit inherits Approved until its asynchronous workflow marks it In Review.
   // Only an actual approval event (or reviewer-authored change) is safe to restore later.
   boolean hasDurableApproval = nullOrEmpty(entity.getReviewers())
       || recordsApprovalTransition(entity)   // that version's change set entityStatus → Approved
       || wasUpdatedByReviewer(entity);
   ```
2. If one is found, compute a JSON patch from the current version to that version and apply it as
   `governance-bot` impersonating the rejecting reviewer. Set `rollbackAction=rollback`,
   `rollbackFromVersion`, `rollbackToVersion`.
3. If none is found, patch only `entityStatus = Rejected` on the current version.

The comment in step 1 states the core problem: in **Default mode the edit is already published**.
Between the PATCH and the workflow's `Set…InReview`, the edited term or metric is visible and still
marked Approved, and search indexes it. A rollback is a new version, so history shows
`v0.3 (bad edit) → v0.4 (restore of v0.2)`. Anything that read v0.3 in between (a dashboard, an
agent answer, a cache) is not recalled. Enforce mode (change requests with `baseValue` and conflict
states) is OM's fix, and it is opt-in per trigger.

### 2.4 How OM reconciles this with governance

OM treats a reviewer as a **trusted editor**, not as a second pair of eyes. Review exists to stop
*non-reviewers* from changing governed terms, and reviewers' own edits are not reviewed. A
glossary or metric without reviewers is ungoverned by choice. The 2026-10-22 ADR is the one place
where they tightened this: an *automation's* term with no reviewers no longer auto-approves; it
goes In Review and falls back to the admins (`assignAdmins`). The reason given: *"which is every
glossary an agent creates"*. AI-generated content forced the two-person rule onto exactly the
case where nobody had configured one.

### 2.5 Our model, side by side

| Question | OpenMetadata | Data Agent |
|---|---|---|
| Is the change live before approval? | Yes in Default mode (rollback on reject); no in Enforce mode | **No.** The proposal is a diff in `inbox.proposal.diff`; the owning worker publishes only on `accepted` |
| Author approves own change | Yes if they are a reviewer (paths B, C); yes as the sole assignee under `none` | Only if `org.organization.allow_self_approval` is on. Enforced twice: `kernel/approval.py two_people()` in the API and the `org.two_people()` trigger in the database (migration `v3_0024`) |
| Nobody configured to review | Approved (A, D) | The request waits in Cần bạn for the capability (`hitl_request.assignee_capability`) |
| Audit of self-approval | End-event name and log line | One `audit` row per step taken on one's own work: `record_self_approval(step=…)` with steps `proposal.review`, `proposal.accept_edit`, `proposal.second_step`, `data_product.activate`, written in the approval's transaction |
| Multi-step | Thresholds within one task; chained nodes | Fixed chain: `requires_compliance` → second step by `compliance`, another person (BR-119); an edit restarts the chain and its author cannot accept it (BR-62) |
| N-of-M votes | `approvalThreshold`, `rejectionThreshold`, an "all assignees" mode | None. One accept per step |
| Agent-authored | Since Oct 2026: always reviewed, admins as fallback | `proposed_by` = agent or user, `excluded_approver_ids` in the HITL context |
| Evidence gate | None | BR-28: accepting with eval regressions needs `reviewed_regressions` |

### 2.6 Verdict: skip the auto-approval rules, adopt five guards and features

**Skip** A, B, C and D. Each one turns "nobody looked at this" into an Approved state, which is the
opposite of a trust label on every answer (V3 invariant 14). They also invert the two-person
default that our org switch is designed around. Rollback-on-reject has no use for us either: we
never publish before acceptance, which is OM's Enforce mode, and they had to add it later.

**Adopt / adapt:**

1. **Risk-tiered thresholds (S, inside the M of §1.9).** `approve_threshold` / `reject_threshold`
   per step in `review_policy`, keyed by `impact_level`. For example: `low` → 1 reviewer; `medium`
   → 1 reviewer + eval gate; `high` → 2 distinct reviewers, then the compliance step. Count distinct
   `reviewer_id` among plain `accept`s since the last edit or `request_changes`; `decide_proposal`
   already builds this list (`accepted_before`). Rejection: one reject with a note ends it (BR keeps
   4b). Let both thresholds be defined, but have an explicit precedence (a reject at any time wins)
   instead of OM's "whichever is met first".
2. **"No eligible reviewer" semantics (S).** Copy OM's three-valued strategy, minus `none`:
   `assign_admins` (space admins, excluding the proposer and the editors) or `wait` (an unassigned
   request, shown on the admin page as "no reviewer can take this"). Never approve. Today a proposal
   whose only holder of the capability is its proposer waits forever with no signal. Add a check
   when the request is created: if the eligible set minus the excluded ids is empty, apply the
   strategy and write an `inbox` event.
3. **The requester stripped from assignees, as data (S).** `excluded_approver_ids` already exists in
   `hitl_request.context` (`proposals.py` `_review_request`). Make Cần bạn exclude those ids
   *server-side* for every kind, so a self-review is never offered under the strict org setting,
   instead of being offered and then refused with a 403 by `two_people()`.
4. **The automation's human is excluded too (S, verify first).** OM's 2026-10 fix was about an
   agent's proposal being approved by the person who effectively caused it. Check that when a
   user's agent turn produces a proposal (synonym, metric_change, knowledge_page from
   `worker_agent/reviews.py` / `semantic_review.py`), the user whose turn it was is in
   `excluded_approver_ids` (or is `proposed_by`). Otherwise a user can ask the agent to propose and
   then approve it, which is a self-approval the audit never records.
5. **Restore target = the last durably approved version (S, for retro/rollback features).** We roll
   back by re-activating an earlier `metric_version` / `dataset_version`. OM's
   `isApprovedBaseline` rule is worth stating as ours: a version is a safe restore target only if
   its approval was recorded by a person other than its author, or by a logged self-approval. Do not
   restore to a version merely because its status column says active.

---

## 3. Search: how OpenMetadata uses Elasticsearch/OpenSearch

### 3.1 Indexes and mappings

- `openmetadata-spec/src/main/resources/elasticsearch/indexMapping.json` declares **70** logical
  indexes. Each has `indexName`, a mapping file template `/elasticsearch/%s/<x>_index_mapping.json`
  (`%s` = language), an `alias`, and `parentAliases`/`childAliases`, so a search on `database`
  includes its tables and `all` spans everything:

  ```json
  {"indexName":"table_search_index","indexMappingFile":"/elasticsearch/%s/table_index_mapping.json",
   "alias":"table","parentAliases":["database","databaseSchema","databaseService","all","dataAsset",
   "dataAssetEmbeddings"],"childAliases":["testSuite","testCase","testCaseResolutionStatus",
   "testCaseResult","tableColumn"]}
  ```
- **Languages**: there are five complete mapping sets: `en`, `jp`, `zh`, `ko`, `ru` (60 files each).
  The language is chosen once per deployment, not per field or per document. `en` `om_analyzer` =
  standard tokenizer + `word_delimiter(preserve_original)` + lowercase + asciifolding + `kstem` +
  `minimal_english`. `jp` adds `om_analyzer_jp` (kuromoji, needs the plugin). `zh`/`ko` use only a
  2–3-gram tokenizer plus a `letter` tokenizer. `ru` adds `russian_snowball`. **There is no
  Vietnamese**, and asciifolding over Vietnamese would fold `đ`/diacritics in a way our `norm()`
  does explicitly.
- **Table mapping, key fields (verbatim):**

  ```json
  "name": {"type":"text","analyzer":"om_analyzer","fields":{
      "keyword":{"type":"keyword","ignore_above":256,"normalizer":"lowercase_normalizer"},
      "ngram":{"type":"text","analyzer":"om_ngram"},                 // ngram 3..20
      "compound":{"type":"text","analyzer":"om_compound_analyzer"}}}, // split camelCase / snake / numerics
  "description": {"type":"text","analyzer":"om_analyzer","similarity":"boolean",
                  "term_vector":"with_positions_offsets"},
  "fullyQualifiedName": {"type":"keyword","normalizer":"lowercase_normalizer"},
  "fqnParts": {"type":"keyword"},
  "owners": {"type":"nested", …}, "tags": {"properties":{"tagFQN":{"type":"keyword", …}, "labelType", "source", "state"}}
  ```
  Other fields: `columns` (nested name/description/tags), `columnNames`, `columnNamesFuzzy`,
  `upstreamLineage` + `processedLineage` (lineage edges denormalized onto the node),
  `usageSummary`, `tier`, `certification`, `entityStatus`, `aliases`, `customPropertiesTyped`,
  `aiContext`, and for embeddings `textToEmbed`, `textToLLMContext`, `chunkIndex`, `chunkCount`,
  `parentId`, `fingerprint`. Note `"similarity":"boolean"` on descriptions: a long description does
  not outscore a name match by term frequency.

### 3.2 The indexing pipeline

1. **Live.** `events/lifecycle/handlers/SearchIndexHandler` is an `EntityLifecycleEventHandler`
   that runs *synchronously after commit on the request thread*: *"Search indexing must be visible to
   follow-up operations in the same request flow… never deferred to a background lane, which would
   make the entity eventually consistent and break read-your-write."* Bulk create/update use the
   bulk API and fall back to per-entity writes.
2. **Propagation.** A change on a parent (owner, domain, tag, a renamed FQN) is pushed into child
   documents with `update_by_query` scripts (`UpdateByQueryReconciler`, `SearchPropagationLimits`,
   ADR "synchronous update-by-query retries version conflicts"). Denormalization is the price of
   needing no joins.
3. **Failure.** A failed live write goes to the DB table `search_index_retry_queue`.
   `SearchIndexRetryWorker` claims rows, checks the cluster is reachable (backoff 5 s → 60 s), treats
   4xx except 429 as permanent, and reclaims rows stuck IN_PROGRESS for more than 10 minutes.
4. **Batch.** `apps/bundles/searchIndex/SearchIndexApp` (a Quartz app) with
   `ReindexingOrchestrator`, `DistributedIndexingStrategy` (multiple servers share partitions),
   `BulkSink` + `BulkCircuitBreaker` + `HeapBackpressure` + `BulkConnectionBudget` (ADR "bulk
   requests never outnumber search connections"), and `promotion/`: build a new physical index, then
   swap the alias (`RecreateIndexHandler`, `OrphanedIndexCleaner`). `RecreateWithEmbeddings` reuses
   stored vectors when only the mapping changed.
5. **Embeddings** (`search/vector/`). `VectorEmbeddingHandler` is another lifecycle handler.
   `VectorDocBuilder` builds the body text: description + column names by default, with per-type
   `BodyTextExtractor`s for queries, test cases, pages and context memories, and children phrases
   for containers. `TextChunkManager` cuts it at 380 words. Each chunk is its own document
   `<parentId>_<chunkIndex>` in a dedicated chunk index (`dataAssetEmbeddings`), with the filter
   fields copied onto it (entityType, deleted, tags, domains, tier). A `fingerprint` of the embedded
   text skips re-embedding when only metadata changed (*"rewrite the metadata around the stored
   vectors, at no embedding cost"*). `docVersion` triggers an additive mapping change plus a
   backfill. The clients are `OpenAIEmbeddingClient`, `BedrockEmbeddingClient`,
   `GoogleEmbeddingClient`, and `DjlEmbeddingClient` (a local model). The OpenSearch index uses
   `"knn": true`.

### 3.3 Building the keyword query

`json/data/settings/searchSettings.json` is the ranking configuration, editable by admins at runtime.
For each asset type it gives `searchFields[{field, boost, matchType: exact|phrase|fuzzy|standard}]`,
`fieldValueBoosts` (function_score on numeric fields), `termBoosts`, `scoreMode`, `boostMode`,
`highlightFields`, aggregations and `matchTypeBoostMultipliers`. For tables, verbatim:

```json
{"field":"displayName.keyword","boost":20.0,"matchType":"exact"},
{"field":"name.keyword","boost":20.0,"matchType":"exact"},
{"field":"name","boost":10.0,"matchType":"phrase"},
{"field":"name.ngram","boost":1.0,"matchType":"fuzzy"},
{"field":"name.compound","boost":8.0,"matchType":"standard"},
{"field":"description","boost":2.0,"matchType":"standard"},
{"field":"fqnParts","boost":5.0,"matchType":"standard"},
{"field":"columns.name.keyword","boost":2.0,"matchType":"exact"},
{"field":"columnNamesFuzzy","boost":1.5,"matchType":"standard"},
{"field":"aliases.keyword","boost":10.0,"matchType":"exact"},
"fieldValueBoosts":[{"field":"usageSummary.monthlyStats.count","factor":2.5e-05,"modifier":"log1p","missing":0},
                    {"field":"usageSummary.monthlyStats.percentileRank","factor":0.0025,"modifier":"none"}],
"scoreMode":"sum","boostMode":"multiply",
"matchTypeBoostMultipliers":{"exactMatchMultiplier":2.0,"phraseMatchMultiplier":1.5,"fuzzyMatchMultiplier":1.0}
```

The global `termBoosts` are additive priors: `Tier.Tier1 0.5`, `Tier2 0.3`, `entityStatus approved
0.12`, `in review 0.08`, `draft 0.05`, and `entityType table 0.3 … tableColumn 0.05`.
`OpenSearchSourceBuilderFactory` composes them into `function_score(bool(should: multi_match per
match type…), functions: field_value_factor + term filters)`. It runs staged ranking
(`RankingStage`: exact → phrase → `match_bool_prefix` prefix → fuzzy, with
`SearchUtils.getFuzziness` scaling with query length). A query that already uses Lucene syntax
(`LuceneQuerySyntax`) goes through `query_string` instead.

**Facets.** Global `aggregations` (terms on `serviceType`, `entityType`, `tier.tagFQN`,
`ownerDisplayName`, `domains.displayName.keyword`, `tags.tagFQN`, `classificationTags`,
`glossaryTags`, `certification.tagLabel.tagFQN`, database and schema names), per-asset ones, and
`maxAggregateSize: 10000`.

### 3.4 RBAC in search

`search/security/RBACConditionEvaluator` parses each policy rule's SpEL condition for the
search-relevant operations (`VIEW_BASIC`, `VIEW_ALL`, `ALL`) into an AST and **compiles it into the
search query**. `isOwner()` becomes a term on `owners.id` in {user, user's teams};
`matchAnyTag(…)` a terms query on `tags.tagFQN`; `hasDomain()` a terms query on domains;
`isReviewer`, `noOwner`, `inAnyTeam`, `hasAnyRole` and `matchAnyService*` are handled the same way;
deny rules become `must_not`. A ThreadLocal holds the caller so concurrent requests cannot compile
another user's `isOwner()`. Functions it cannot translate are logged once (capped at 128 names).
Two caveats:

- `globalSettings.enableAccessControl` is **`false` by default** in the seeded settings. Out of the
  box, keyword search returns everything the index holds.
- **Vector search does not use it.** `SemanticSearchTool` says so: *"Vector search indexes every
  entity and applies no access policy of its own, so results the caller may not view are dropped
  before anything is counted"*. It post-filters each hit through the authorizer and pages past the
  dropped hits. Only context memories get a visibility clause inside the kNN filter
  (`ContextMemorySearchVisibility`, fail-closed).

### 3.5 Hybrid keyword + vector with RRF

`OpenSearchVectorService.buildHybridRrfPipeline` (verbatim JSON it produces):

```json
{ "phase_results_processors": [ { "score-ranker-processor": {
    "combination": { "technique": "rrf", "rank_constant": 30,
                     "parameters": { "weights": [ <keywordWeight>, <semanticWeight> ] } } } } ] }
```

It is sent **inline** as the `search_pipeline` field of each request (an *ad-hoc* pipeline) rather
than stored with `PUT /_search/pipeline/hybrid-rrf`. The Javadoc explains: a stored pipeline needs
`cluster:admin/search/pipeline/put`, which a prefix-scoped tenant role lacks, and *"a named pipeline
is one cluster-global object, so on a shared cluster every tenant's reindex overwrote the previous
tenant's keyword/semantic weights"*. The weights come from search settings, so they apply on the
next query (UI `HybridWeightsSection.tsx`; migration `v210` "HybridWeightSwap"). The request body is
`{"query":{"hybrid":{"queries":[<keyword query>, {"knn":{"embedding":{"vector":[…],"k":K,
"filter":{"bool":{"must":[…]}}}}}]}}, "search_pipeline": {…}}` (shape taken from the health probe
`buildHybridSearchProbe` and `VectorSearchQueryBuilder.buildQuery`, documented *"Used by hybrid
search to embed as a sub-query inside a hybrid query"*). The code that sends the hybrid query at
search time is **not in the OSS tree**: no OSS caller uses `getHybridRrfPipelineDefinition()`, so it
belongs to Collate's AI search. This is OpenSearch-only (*"Skip silently for Elasticsearch"*). On
Elasticsearch, OM runs native `knn` with `num_candidates = clamp(k×2, 100, 10 000)`.

Note `rank_constant: 30` (we use `RRF_K = 60`). OM weights the two lists. We weight them equally.

### 3.6 `semantic_search` (MCP) and metadata NLQ

`openmetadata-mcp/.../tools/SemanticSearchTool.java` (633 lines). Parameters are `query, filters,
size (≤50), k (≤10 000, default 100), threshold, from, cursor, ignorePersonaScope`. It is **pure
kNN over the chunk index**, not hybrid. It returns one result per entity (the best chunk), compact
summaries (FQN strings instead of nested references, at most 60 column names, a
`columnNamesTruncated` flag, and `metricExpression`/`metricType`/`granularity` for metrics), and it
**rejects unknown or misplaced filter keys** instead of silently ignoring them (*"a wrong answer that
looks like a right one"*). `totalFound` is reported in entities, not chunks (*"eight matching tables
read as totalFound: 96"*). The sibling `search_metadata` is keyword search with `queryFilter` (raw
ES DSL), `fields` and aggregations. Its description carries hard-won usage rules (a `terms` query
on FQN to fetch many known entities in one call; `originEntityFQN` for a table's column tests).
`search/nlq/NLQService` (natural-language → ES query) is a no-op in OSS.

### 3.7 Lineage through search

Each node document carries its incoming edges (`upstreamLineage[{fromEntity.fqnHash, toEntity,
sqlQuery, columns…}]`). `OSLineageGraphBuilder` walks upstream and downstream by depth, with one
`terms` query on `fqnHash` per layer, `LineagePathPreserver` for filtered paths, size estimation
to choose a strategy, column-level filtering, and a 404 for a missing root (ADR 2026-10-27).
Lineage scale was validated separately (`docs/perf/lineage-scale-validation.md`). Graph queries are
served by the search cluster, not by the relational store.

### 3.8 Our side

- **Documents.** `backend/services/worker_agent/documents/retrieve.py`: pre-filter first (Data Space
  collections, purpose BR-59, ready docs, validity at `as_of` BR-109, OCR threshold BR-19, masked
  text only BR-46, injection-suspected excluded), then **two lists fused by RRF (`RRF_K = 60`, pool
  40)**: Qdrant neighbours (`documents/search.py`, `worker_document/indexing.py`) and Postgres FTS
  `to_tsvector('simple', chunk.text)` over the GIN index `chunk_text_idx` (*"no stemming dictionary
  fits Vietnamese, so words are matched as written"*). Then the TEI reranker, or a fallback on word
  overlap or dense similarity, with an explicit relevance bar (an empty result is E1's "not found",
  never a guess). It is evaluated by `make eval-retrieval` (recall@k, MRR, not-found accuracy,
  forbidden rate).
- **Semantic layer.** `context.synonym (phrase, phrase_norm, target_kind
  metric|dimension|measure|entity|value|column, status pending_review|active|rejected)` and
  `context.value_index (dimension_id, value_code, value_label, label_norm)`, matched in
  `worker_agent/understand/value_retrieval.py`: exact on normalized form, longest phrase first, then
  `pg_trgm` similarity for partial labels, never picking between two equal near-matches.
- **Catalog and semantic objects.** `ILIKE '%q%'` over table/schema/display name/description
  (`api/modules/catalog/routes.py` ≈ L1012), metric code and name (`context/routes_semantic.py`
  L174), entities and examples. There are FTS indexes on `knowledge_page` and `data_product`.
  Nothing is ranked, there is no fuzzy matching, no usage boost, and no metadata embeddings.

### 3.9 Strengths and weaknesses of OM's approach

**Strengths:** relevance tuning as configuration (per-field boosts, match types, usage and tier
priors) that admins change without reindexing; one query language for search, facets and
lineage; fast faceting over 70 index types; zero-downtime reindex by alias swap; embedding reuse by
fingerprint; RBAC compiled into the query rather than applied after paging; the honest-response
rules in the MCP tool.

**Weaknesses:** a second stateful cluster (or two, with the vector index) that must be kept in sync
with the database. It needs a retry queue, propagation scripts, a distributed reindexer, a circuit
breaker, heap backpressure, connection budgets and an orphaned-index cleaner, and every one of those
exists because of a production incident. Access control is off by default and absent from vector
search. The language is one per deployment, with no Vietnamese. Hybrid RRF is OpenSearch-only, and
its runtime caller is closed source. Denormalized lineage and ownership mean a parent change fans
out to `update_by_query` over children.

### 3.10 Verdict: skip the cluster, adapt four ideas onto Postgres + Qdrant

Our corpus per org is small by search-engine standards: thousands of datasets and columns, hundreds
of metrics, tens of thousands of chunks. Our invariants also want data-bearing search co-located
with row-level permission checks (invariant 13, "permissions checked before generation"). Postgres
FTS + `pg_trgm` + Qdrant already cover lexical, fuzzy and dense, and the RRF fusion is written and
evaluated. Adding OpenSearch would bring OM's whole sync apparatus (§3.2) and an analyzer stack that
has no Vietnamese.

Revisit only if: (a) one org's catalog passes roughly 1M searchable objects, (b) faceted browsing
over many fields becomes a primary screen, or (c) a customer mandates it. Even then, use
**ParadeDB/pg_search (BM25 in Postgres)** or pgvector before a separate cluster.

**Adapt (design sketch, effort M overall):**

1. **Metadata search with a ranking config (M).** Replace the `ILIKE`s with one
   `catalog.search_document` view or table per Data Space: `kind`, `ref_id`, `name`, `display_name`,
   `aliases[]` (from active `context.synonym`), `description`, `fqn_parts[]`, `tier`, `status`,
   `usage_30d`, a `norm()` column (same normalizer as `value_index.label_norm`), a `tsvector`
   ('simple', over the norm column), and a GIN `gin_trgm_ops` on name/display_name. Rank as OM does,
   in SQL:
   `score = 20·exact(norm_name) + 10·exact(alias) + 10·phrase(display) + 8·ts_rank(compound split
   of snake/camel) + 2·ts_rank(description) + 1·similarity(trgm)`, multiplied by
   `(1 + log1p(usage_30d)·k_usage)`, plus priors for `status = active/certified` and tier. Keep the
   weights in a JSON setting per org (`org.search_settings`), as `searchSettings.json` does. A
   compound field is just `regexp_replace` that splits `FactInternetSales` → `fact internet sales`
   at index time.
2. **RBAC inside the query (S).** Search only `data_space_id`s and datasets the principal may see,
   as a `WHERE` clause from the same capability/ACL resolution the routes already use. Never fetch,
   page and then drop (the shortcoming OM's semantic tool admits).
3. **Metadata embeddings, reusing documents' machinery (M).** One Qdrant collection per org (or
   pgvector on `search_document`) of `name + description + column names + synonyms`, chunked like
   `TextChunkManager` only for long descriptions, with a `fingerprint` (sha256 of the embedded text)
   stored on the row, so a tier or owner change does not re-embed. Fuse with the lexical list using
   the `retrieve.py` RRF. Add per-list weights (OM's `[keyword, semantic]`) as a setting and measure
   `k = 30` vs `60` on the eval set before changing anything. The writer is the outbox consumer that
   already reacts to catalog and semantic changes. A periodic reconciler compares fingerprints
   (OM's retry-queue lesson: a lost write must be found again without a full reindex).
4. **A `semantic_search` agent tool over metadata (S, after 3).** It returns compact hits (FQN
   strings, capped column lists with a truncation flag, a metric's expression), rejects unknown
   filters, and counts entities, not chunks. This helps schema linking for vague questions, where
   `value_index`/`synonym` exact matching finds nothing.

**Keep as is:** document retrieval (`retrieve.py`), whose pre-filters for validity, masking and
injection go beyond anything in OM; `value_index` + synonyms with exact-then-trigram matching and
no guessing between ties; lineage in Postgres, which is small, relational, and needs no fan-out.

**Not worth copying:** per-deployment language mapping sets, `query_string` pass-through of raw
Lucene or DSL to the agent (`search_metadata.queryFilter`), and denormalizing lineage into search
documents.

---

## 4. What changes in our backlog

| Item | Module | Effort | Depends on |
|---|---|---|---|
| `inbox.review_policy` + stamping on proposal + `decide_proposal` reading it; defaults equal to today | `api/modules/inbox`, `worker_agent/proposals.py`, alembic | M | none |
| Risk-tier thresholds (N distinct accepts) and explicit reject precedence | same | S | the item above |
| No-eligible-reviewer strategy (`assign_admins` / `wait`), server-side exclusion of `excluded_approver_ids` in Cần bạn | `api/modules/inbox`, `worker_agent/proposals.py` | S | none |
| Audit: agent-turn proposals exclude the turn's user from approving | `worker_agent/reviews.py`, `semantic_review.py` | S | verify first |
| Durable-approval rule for restore targets | `context` / `catalog` version activation | S | none |
| Ranked metadata search (`search_document`, trgm + FTS + usage/tier priors, org setting) replacing ILIKE | `api/modules/catalog`, `context` | M | none |
| Metadata embeddings with fingerprint + RRF fusion + `semantic_search` tool | `worker_agent`, Qdrant | M | ranked metadata search |
| Escalation schedule for stale review requests | `schedules/` (Temporal) | S | `review_policy` |

Explicitly **not** planned: Flowable or any BPMN engine, a workflow canvas, JSONLogic over entities,
auto-approve on "no reviewers" or "author is a reviewer", publish-then-rollback, and an
Elasticsearch/OpenSearch cluster.
