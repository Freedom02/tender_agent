# Boundary Policy

## Primary Route

Assign one primary route per map section. Preserve overlaps with
`secondaryDomains`, `contextSectionIds`, or separate overlay sections. Never
use multiple processors implicitly.

Priority when text is mixed:

1. hard rejection signal;
2. post-award contract exclusion;
3. reusable template/form;
4. technical or qualification leaf;
5. evaluation;
6. commercial;
7. project context.

This priority is a review order, not permission to overwrite a clearly
supported domain. Split the source when two purposes have different downstream
handling.

## Containers and Leaves

- `role=container`: preserve context and do not fine-extract the whole parent.
- `role=leaf`: route the smallest actionable section or table range.
- `role=overlay`: document-wide or cross-cutting concern such as project facts
  or rejection scanning.

Do not force every segment into exactly one map section. Unclassified segments
are a signal for another read or an explicit unresolved issue.

## Mixed Sections

For a section containing heading, instructions, table, and form text:

1. read the section and the next/previous sibling;
2. identify the reusable form boundary;
3. split form rows from explanatory requirements when source IDs permit;
4. give the form `template` as primary and preserve technical/qualification as
   secondary only when the row itself states a substantive requirement;
5. record an issue when the boundary cannot be resolved.

## Evaluation Versus Qualification

Use `evaluation` when the text describes points, weights, score bands,
bonuses, deductions, or evidence used to earn a score. Use `qualification`
only for a mandatory threshold, eligibility condition, or required submission.
“提供类似项目得 2 分” is evaluation, not an automatic qualification.

## Contract Versus Bid Requirement

“中标后”, “合同签订后”, “履约”, “违约”, “合同解除”, and “争议解决”
usually indicate `contract`. A bid-stage requirement that happens to mention
future performance remains technical or commercial if it controls bid
response. Split mixed ranges and keep only the contract range ignored.

## Rejection Context

For each rejection hit, establish:

- whether it applies before bid opening, during evaluation, or after award;
- what trigger activates it;
- whether an exception or cure exists;
- whether it applies to the bid, response, qualification, price, or submission;
- whether it is a hard risk requiring human confirmation.

If any of these are unknown, retain the candidate and create an
`UnresolvedIssue`. Never mark a hard signal as rejected solely because the
surrounding text is incomplete.

## Source Integrity

Only use IDs returned by MCP tools. Source locators may supplement IDs but do
not replace them when IDs are available. A map section without a source is
invalid. A source from another document version is invalid.

For tables, preserve `tableId`, row range, cell index when available, and the
table's neighboring section. For templates, preserve the original artifact
association and state when only text/table fidelity is available.

## Read More Or Stop

Read more when:

- the top two classifications are close;
- a range crosses a heading or table boundary;
- a hard rejection term lacks its condition;
- a template locator ends in the middle of a table;
- a high-risk segment is unclassified;
- parser warnings affect the candidate range.

Stop when:

- all eight domain coverage checks are satisfied;
- all high-risk hits have a source-backed range or unresolved issue;
- all templates and contracts have explicit routes;
- the map passes `save_document_map`;
- remaining observations are only useful-but-out-of-contract candidates.

## Retention

`record_unresolved_issue` and `record_candidate_finding` default to
`run_only`. Do not promote them to tenant or global Skill knowledge during a
document run. Promotion requires human review, anonymization, golden-set
evaluation, and an approved version change.
