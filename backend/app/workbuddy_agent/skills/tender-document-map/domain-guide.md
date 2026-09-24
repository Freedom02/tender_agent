# Eight-Domain Guide

## Routing Table

| Domain | Include | Exclude | Route |
|---|---|---|---|
| `technical` | Product, platform, system, service capability, parameters, implementation, delivery quality, technical response | Scoring rules, post-award contract duties, template-only text | `technical_processor` |
| `qualification` | Entity qualification, license, certification, experience, personnel, finance, tax, social security, credit, consortium eligibility | Scoring-only evidence, post-award qualification, evaluator procedure | `qualification_processor` |
| `commercial` | Price, payment, delivery, service period, warranty, location, commercial deviation, cost obligations | Contract-only clauses, scoring formulas | `display_processor` |
| `evaluation` | Score items, weights, points, scoring rules, bonus/deduction, required scoring evidence | A mandatory eligibility condition stated independently of scoring | `evaluation_processor` |
| `contract` | Contract template, post-award performance, breach, termination, dispute, contract settlement | Bid-stage submission or eligibility obligations | `ignored_contract` |
| `template` | Bid letter, response form, price schedule, deviation table, declaration, authorization, qualification form, technical response table | Ordinary instruction text without a fillable or reusable form | `template_processor` |
| `project_info` | Project identity, scope overview, budget, deadlines, deposit, submission, electronic bid, consortium, delivery milestones | Enterprise capability requirements | `display_processor` |
| `rejection` | Invalid bid, rejected response, bid cancellation, refusal, disqualification, unacceptable conditions, hard red lines | Benign mentions, post-award contract consequences unless explicitly bid-stage | `rejection_processor` |

## Technical

Use `fine_extract` for a leaf range. Capture the smallest range that preserves
the requirement and its condition. Keep a table row with its table ID and row
coordinate. A heading such as “采购内容” is usually a container; map its
technical children instead of sending the whole chapter to matching.

## Qualification

Separate “must possess before bidding” from “may receive points”. A license
required for eligibility is qualification. A similar project worth two points
is evaluation unless the document separately makes it a mandatory threshold.

## Commercial

Preserve the source range and display it. Do not route it into enterprise
matching merely because it contains “provide”, “support”, or “certificate”.
Payment, warranty, delivery, and price conditions are project constraints.

## Evaluation

Preserve criterion name, score/weight, scoring text, required evidence, and
source table rows. Do not convert score evidence into a hard qualification
requirement. It may later feed a separate evidence planning step.

## Contract

Mark post-award contract text as ignored. Keep its location and reason so a
future contract-risk workflow can re-enable it. If a mixed section contains
both bid-stage rules and contract text, split the ranges.

## Template

A template is reusable or fillable source material, not merely a section with
the word “format”. Create a `TemplateLocator` with source IDs, download
eligibility, writing-agent usability, and the requested fidelity. If the same
form contains substantive technical or qualification requirements, add a
secondary domain but keep `template_processor` as the primary route for the
form body.

## Project Info

Use a document-wide overlay when facts are scattered. Typical fields include
project name/number, purchaser, scope, quantities, budget, maximum price,
deadlines, bid bond, performance bond, fees, delivery, service term,
submission mode, file naming, signatures, seals, consortium, and
subcontracting. This overlay is display/context only.

## Rejection

Search the entire document for rejection terms, then read enough context to
identify trigger, condition, exception, stage, and consequence. Emit a
`RejectionCandidate` for every source hit that matters. A phrase such as
“投标无效” or “废标” is never silently downgraded to ordinary display text.
