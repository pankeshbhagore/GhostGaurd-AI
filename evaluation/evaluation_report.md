# Evaluation report (all numbers measured in this session; reproduce with `python code/run_all.py`)

## 1. Scope and honesty notes
- Official gold exists only for the 32 public samples (`dataset/sample_output.csv`). The pipeline was developed while looking at
  these samples, so the numbers are optimistic. **No hidden-set accuracy is known.**
- The samples contain 0 `NOT_APPLICABLE` cases, so that path is verified only by synthetic tests and by the fact that the 10
  predicted NOT_APPLICABLE hidden requests equal the 10 `CLASSIFY_REQUEST` events of `system_activity.csv` (a consistency
  signal, not a label).
- Gold is read only by `code/evaluate.py` and the held-out experiment; the predictor never opens it.

## 2. Official sample results (32 cases) - `evaluation/sample_metrics.md/json`
| Metric | Value |
|---|---|
| Case-level status accuracy | 1.000 (32/32) |
| Status F1 COMPLETE / INCOMPLETE (support 10 / 22), macro | 1.000 / 1.000 / 1.000 |
| Sequence accuracy (exact ordered steps) | 0.906 (29/32) |
| Step-object precision / recall / F1 (micro) | 1.000 / 0.979 / 0.990 |
| Step F1, macro over cases | 0.966 |
| Resource precision / recall / F1 | 1.000 / 0.964 / 0.982 |
| Operation / state / evidence accuracy on matched steps | 1.000 / 1.000 / 1.000 |
| Field accuracy phase, system_id, resource_id, operation, state, evidence_id | 1.000 each |
| Field accuracy step_no | 0.979 (shifts after a missing step) |
| Phase-order accuracy / schema validity | 1.000 / 1.000 |

Losses (4 missing steps in 3 cases): sample case_04 (hand-made, no identifiers; we return COMPLETE with `[]`), and one
`audit_record_*` each in cases 18 and 20 whose ids exist nowhere in participant data. No wrong step is emitted (precision 1.0).

## 3. Full run on the 518 requests
INCOMPLETE 356, COMPLETE 152, NOT_APPLICABLE 10; 3,256 steps; 1,248 citations. Independent validator: structural OK, semantic
0 errors, 13 warnings (OCR/catalog-bound artifacts, see `audit_findings.md` F13). Two consecutive runs (the first with the OCR
cache disabled) are byte-identical: sha256 `e2d83396...c67f4c` (`logs/5_reproducibility.json`).
Evidence coverage: 94/94 files discovered, 92 parsed, 2 empty by nature, 10 not linked to any hidden request.

## 4. Ablations (sample metrics / hidden rows that change)
| Variant | Sample seq. acc. | Sample step F1 | Hidden rows changed | Hidden status changed |
|---|---|---|---|---|
| final | 0.906 | 0.990 | - | - |
| `--no-omission` (absence only from explicit "not found") | 0.531 | 0.951 | 239 | 0 |
| `--strict-capture` (ignore files captured after audit_at) | 0.750 | 0.976 | 121 | 1 |

## 5. Uncertainty of inferred links (`linkage_holdout.py`, `linkage_confidence.csv`)
Of 1,811 linked resources 1,433 are proven by identity keys; 378 are inferred from timing. Held-out recovery of known accounts:
1 eligible candidate 127/129 (98.4%), 2-3 candidates 75/123 (61.0%), overall 80.2% (pessimistic setting). 108 hidden accounts are in the
ambiguous group, so some hidden accounts and the statuses that depend on them are probably wrong. The public sample linked
17 inferred accounts, all correct (14 unique, 3 ambiguous).

## 6. Additional evaluation cases created by the team (kept apart from the official samples)
Synthetic datasets are built by `code/tests/synth.py` (own identities, audit log, ledger evidence; real `deletion_rules.csv`).
| # | Scenario | Asserted behaviour | Result |
|---|---|---|---|
| S1 | historical e-mail | request by old and by current e-mail give identical INCOMPLETE plans | pass |
| S2 | deleted record recreated by sync + active flow | findings for flow and account; DISABLE_DATA_FLOW precedes DELETE_RECORD; flow verified | pass |
| S3 | support message with PII | REPORT_EXPOSED_PII + REDACT_RECORD (never DELETE) + VERIFY_RECORD_REDACTED | pass |
| S4 | restorable backup | REPORT_RESTORE_RISK + BLOCK_RESTORE (never DELETE) + VERIFY_RESTORE_BLOCKED | pass |
| S5 | legal hold | COMPLETE with VERIFY_RECORD_PRESENT only | pass |
| S6 | observation captured after audit_at | ignored; only the in-time file is cited | pass |
| S7 | primary says gone, replica still has it | worst copy wins -> INCOMPLETE | pass |
| S8 | already anonymised analytics | COMPLETE, VERIFY_RECORD_ANONYMIZED, no findings | pass |
| S9 | two people with the same name | the other person's account never attached | pass |
| S10 | access request, not deletion | `NOT_APPLICABLE`, exactly `[]` | pass |
| M1-M5 | metamorphic: post-audit observation, reported_status variation, unrelated user's records, evidence file order, repeat run | output unchanged | pass |
| V1-V17 | validator mutations (missing/duplicate/unsorted rows, bad JSON, extra field, step numbers, state pair, order, evidence on remediation, unknown/future citation, COMPLETE with finding, NA with steps, missing verification, wrong treatment) | each rejected | pass |
| G1-G2 | planner guards (missing retained record, removed backup) | pass |
| I1 | 18 multilingual / injection intent phrases | pass |
Also `code/evaluation/custom_cases.py` (4 checks on real data). Test run: 40 tests, all OK (`logs/1_tests.log`).

## 7. Failures found and improvements made
See `audit_findings.md` (F1-F16). Headline: 11 uncited COMPLETE steps fixed with in-time snapshot citations; new independent semantic
validator; intent classifier hardened against negation/injection (no change on the 550 official requests); latent planner holes closed;
heuristic links quantified instead of silently trusted.

## 8. Remaining limitations
Ambiguous key-less account links (61% in the held-out test); sample case_04 style hand-made cases; unrecoverable audit ids;
OCR/catalog-bound artifacts (cases 33-63); legal-hold register PDF not used; NOT_APPLICABLE unmeasured on official gold;
hidden accuracy unknown.
