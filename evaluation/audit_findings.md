# Audit findings (re-audit of the existing implementation, 2026-10-25)

Method: the previous reports were not trusted. Every check below was re-executed (`python code/run_all.py`; logs in
`evaluation/logs/`). Official contract = `AGENTS.md` §6 and `problem_statement.md`. Gold (`sample_output.csv`) was used only
for scoring, never by the predictor. "Hidden" = the 518 requests in `dataset/deletion_requests.csv` (no labels exist).

## 0. Inventory (generated: `evidence_diagnostics.md`, `linkage_confidence.csv`)
94 manifest files = 28 json, 26 log, 24 sql, 11 png, 4 pdf, 1 txt; none missing on disk, none unlisted. 92 parse into
observations; 2 yield none (`evidence_27` legal-hold register PDF, `evidence_90` empty export). 10 files have no link to any hidden
request (sample-only or decoys). Resource ids use the prefixes account/search/support/analytics/audit_record/data_flow/backup/export/
marketing/object/tombstone/legal_hold. All timestamps are `...Z` (UTC); the parser also normalises offsets and naive values to UTC.

## 1. Contract verification (independent validator `code/validate.py`)
Checked on the final `output.csv`: header `case_id,deletion_status,steps_json`; 518 unique rows sorted by case_id; valid JSON
arrays; exactly the 7 step keys; allowed phase/operation/state pairs; consecutive step_no; phase then operation-rank then
system_id then resource_id order; evidence_id empty for proposed remediation and future verification; non-empty for findings
and for every COMPLETE verification; COMPLETE has verification only; NOT_APPLICABLE is exactly `[]`. Result: structural OK.
Semantic layer (cited ids exist, not captured/occurring after audit_at, resource mentioned, policy treatment, finding ->
remediation -> verification consistency): 0 errors, 13 warnings (see F13).

## 2. Issues found

| # | Sev | Issue | Evidence | Root cause | Correction |
|---|---|---|---|---|---|
| F1 | High | 11 COMPLETE cases had a VERIFY step with empty `evidence_id` (case_64, 69, 70, 213, 215, 218, 219, 221, 335, 336, 339) | validator rule "COMPLETE verification without evidence" | the only log line proving "account not found" sits in a file captured after `audit_at` and was (correctly) refused as a citation, leaving nothing | `state.py::_omission_cite` cites an enumerating snapshot captured in time that omits the record; last resort is the resource's own `activity_id`. All 11 now cite `evidence_72/81/93`. |
| F2 | Med | First fix (citing an activity id) cited a FAILED deletion event as proof of absence | case_213 -> activity_2353 (FAILED) | operational results are claims | replaced by F1's snapshot citation; activity id kept only as fallback |
| F3 | Med | Old validator was structural only; it could not detect wrong treatment, unsupported or future citations, orphan findings | review of `validate.py` | single-layer check | rewritten, independent of the planner (imports nothing from `deletion_audit`), separate structural and semantic layers, 17 mutation tests (1 clean baseline + 16 corruptions) prove it rejects corrupted files |
| F4 | Med | Intent classifier mishandled negation outside English (de "nicht löschen", es "no quiero eliminar", fr "ne supprimez pas"), Chinese deletion verbs, "Do not remove anything", mixed clauses, injected instructions in request_text | 5/17 probe phrases wrong before the fix | regex cues without scope | negation window per language, CJK suffix negation, French `ne ... pas`, sentence-level separation of a preservation clause, sanitiser that drops sentences addressed to the agent. Decisions on all 550 official requests unchanged (0 diffs) |
| F5 | Med (latent) | A required retained record (audit log / legal hold) that is missing at audit_at would have been "verified" as PRESENT | `planner.assess` returned ok for any state | no guard | `Assessment.verifiable`: such a record gets no verification step. Never occurs in the data (416 audit records and 8 legal-hold records all PRESENT). The spec has no operation that can repair it |
| F6 | Low (latent) | A deleted backup or data flow would be reported as an active risk | `assess` compared with BLOCKED/DISABLED only | no guard | ABSENT now satisfies BLOCKED/DISABLED. Never occurs in the data |
| F7 | High risk (unfixable) | 378 of 1,811 linked resources are inferred, not proven: 260 accounts (152 with one eligible worker event, 108 with 2-3) and 118 anonymised analytics rows | `evidence_diagnostics.md` | the accounts / analytics rows carry no identifiers; only timing in `system_activity.csv` links them | not turned into facts silently: provenance of every link is exported to `linkage_confidence.csv`. Held-out test (`linkage_holdout.py`): single-candidate links 127/129 = 98.4% correct, 2-3 candidates 75/123 = 61.0% (pessimistic: every request competes, identity-linked accounts not excluded). Expect errors among the 108 ambiguous accounts |
| F8 | Info | `evidence_27` legal-hold register PDF yields no observations | pdftotext shows ORD-... rows for user_08/12/17/21/24/29 | the register lists order numbers, while the policy resources are `legal_hold_*`/`audit_record_*` ids | no resource can be derived from it; the sample gold of those users contains no order resource either (checked for evaluation only). Left unparsed on purpose |
| F9 | Info | `evidence_90` is an empty export | 0 rows | - | correctly treated as proving nothing (an empty export cannot prove absence) |
| F10 | Info | Event time vs capture time | per-format table in §3 | - | state is taken from the observation's own event time (<= audit_at); only files captured <= audit_at may be cited. Ablation `--strict-capture` (ignore late-captured files entirely) drops sample sequence accuracy 0.906 -> 0.750 and changes 121 hidden rows, `--no-omission` drops it to 0.531 (239 rows). Both rules stay |
| F11 | Low | Evaluator reported only status/sequence/step/field | `evaluate.py` | coarse | rewritten: per-status P/R/F1 (micro+macro), step micro+macro, resource, operation/state/evidence accuracy, phase order, schema validity |
| F12 | Low | Usage report was hand-written | `usage_report.md` | no instrumentation | `deletion_audit/usage.py::METER` + `main.py` generates `usage_run.json` / `usage_report.md` from the real run |
| F13 | Low | 13 validator warnings | 3 citations do not literally name the resource: case_33 export_1842 (evidence_18, PDF), case_46 tombstone_17 (evidence_42, PNG), case_61 object_19 (evidence_46, PNG); the other 10 warnings: 10 cases without account verification (33, 35, 39, 45, 50, 52, 53, 55, 61, 63) | these resources are bound to a person by a catalog `LINK_RESOURCE_IDENTITY` event + OCR, not by an id printed on the page; their accounts are not observable | kept, flagged as OCR/catalog-inferred |
| F14 | Info | sample case_04 comes out `COMPLETE` with `[]` (gold has account_004 + analytics_901) | evaluation | hand-made case: no identifier and the activity falls outside the timing window | not special-cased (no case shortcuts). Hidden set has no COMPLETE row with empty steps |
| F15 | Info | 2 audit_record ids of sample cases 18 and 20 exist nowhere in participant data | evaluation | unobtainable | omitted, never guessed |
| F16 | Low | OCR re-ran on every run (6 s) | timing | no cache | content-hash cache `.cache/ocr` (excluded from the zip); cold run still verified byte-identical |

## 3. Event time vs observation (capture) time per format
- log files: line timestamp = event time; manifest `captured_at` = capture time (can be later than the lines).
- SQL snapshots: `snapshot_scope.captured_at` per origin system (else the header snapshot time) = event time; manifest time = capture. Replicas of the same table are compared at the same instant; the worst copy wins.
- JSON bundles: `snapshots[].captured_at` per (data_store, collection); exports: `exported_at`; ledger rows: `recorded_at`.
- txt ledger / PDF / PNG: ledger timestamp, otherwise the manifest time; PDF/PNG facts are bound to a person only through catalog link events (stamped 7 min after the artifact).
- Observations older than `requested_at` belong to a previous request and are ignored; equal instants: severity order PRESENT/EXPOSED/ACTIVE beat ABSENT/REDACTED.

## 4. Not changed because evidence says it is right
Operation order, remediation order, policy mapping (DELETE/REDACT/ANONYMIZE/RETAIN, backups -> BLOCKED, flows -> DISABLED) are
read from `deletion_rules.csv` and the specification and validated by both samples and mutation tests.
