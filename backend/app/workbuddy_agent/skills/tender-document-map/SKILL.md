---
name: tender-document-map
description: Locate and classify the business ranges of tender, procurement, bid, and response documents into a validated DocumentMap with source-safe routing. Use when an agent must inspect a parsed tender document, find technical requirements, qualification review, commercial terms, evaluation criteria, templates, project facts, contract exclusions, or rejection risks, especially when headings are inconsistent, ranges are non-contiguous, or sections mix several purposes.
---

# Tender Document Map

## Mission

Build the first-pass map of where each business purpose lives. Do not extract
enterprise requirements, decide whether a company satisfies them, or rewrite
the source document. Downstream processors consume the ranges after validation.

The source of truth is the current parsed `DocumentIR`. Read it only through
the current-run MCP tools. Never ask for, construct, or infer a local file
path.

## Required Workflow

### 1. Bind and inspect

Before reading content, confirm the current `documentId` and
`documentVersionId`. Call:

1. `inspect_document`
2. `list_section_candidates`

Use the inspection result to choose a bounded reading plan. Check document
size, section count, tables, parser warnings, template signals, evaluation
signals, and whether pagination is required.

### 2. Understand the full document

Read the complete normalized document, not only headings:

- Use `read_document_full` page by page when the document is small enough.
- Continue with `nextCursor` until `hasMore=false`.
- For large documents, read every page in the same run and use
  `read_document_range` for uncertain or high-risk areas.
- Use `search_document` for rejection terms, project fields, template signals,
  and cross-section terms.
- Use `read_table_range` when a table may contain technical requirements,
  qualification checks, scoring rules, or a response template.

Headings are evidence, not a requirement. A range is valid when its body,
table structure, neighboring sections, and source coordinates support the
classification.

### 3. Create ranges and overlays

Produce one or more `DocumentMapSection` records for each discovered range.
Use non-contiguous `ranges` when the same business purpose appears in separate
parts of the document. Use overlays for document-wide concerns:

- `project_info` with `processingMode=full_scan`;
- `rejection` with `processingMode=full_scan`;
- a template/technical or template/qualification overlap when a response form
  also contains substantive requirements.

Each range must carry real `sectionIds`, `sourceSegmentIds`, `tableIds`, or a
valid source anchor. Preserve the exact source IDs returned by tools.

### 4. Resolve boundaries

Read neighboring content when any of these are unclear:

- a heading could be a container or a leaf;
- a scoring criterion resembles a qualification requirement;
- a template title is adjacent to ordinary instructions;
- a contract section is mixed with pre-bid obligations;
- a rejection phrase has an exception, condition, or post-award context;
- one range appears to belong to more than one domain.

If the boundary remains unclear, call `record_unresolved_issue` with the source
references and keep `needsHumanReview=true`. Do not silently choose the more
convenient route.

### 5. Save and validate

Before saving:

- every source ID exists in the current document;
- `contract` is in `ignoredSections`, has `ignored=true`, and uses
  `processingMode=ignore`;
- `template` has a `TemplateLocator`;
- `technical` and `qualification` leaf ranges use `fine_extract`;
- `rejection` uses `rejection_processor`;
- `project_info` never uses a technical or qualification matching route;
- hard rejection candidates remain candidates or require human review;
- high-risk search hits are covered by a map range or an explicit unresolved
  issue.

Call `save_document_map` only after the map is complete. If validation fails,
repair the map using the returned source IDs; never invent replacements.

## Processing Modes

| Mode | Use |
|---|---|
| `fine_extract` | Technical and qualification leaf ranges sent to downstream clause/evidence processors |
| `display` | Commercial, evaluation, and contextual sections shown or summarized without enterprise matching |
| `template_extract` | Forms, response tables, declarations, bid letters, and downloadable templates |
| `full_scan` | Project-wide facts and rejection candidates |
| `ignore` | Post-award contract content preserved for location but excluded from the current review |

The primary route is a processing contract, not a confidence label. Secondary
domains may preserve useful overlap, but they do not override the primary
route.

## Output Discipline

Return only the stable map artifact and concise run summaries. Do not return
hidden reasoning, raw tool logs, or the entire document in `DocumentMap`.
Record useful but out-of-contract observations with `record_candidate_finding`.
Both unresolved issues and candidate findings default to `retention=run_only`.
They become Skill candidates only after human confirmation, anonymization,
evaluation against a golden set, and approval.

Read [domain-guide.md](domain-guide.md) for the eight-domain taxonomy.
Read [boundary-policy.md](boundary-policy.md) before resolving mixed or
ambiguous ranges. Use the JSON files in `examples/` as compact classification
patterns, not as a substitute for source evidence.
