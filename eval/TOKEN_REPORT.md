# Token report: triage eval, first 10 labelled tickets

MLflow run `358a3bb6cf4347ac95bb8aeef7fae729` in `mlflow.db`, 2026-09-27.

## Agent tokens (Gemini, from MLflow traces)

| Ticket | Input | Output | Total |
|---|---:|---:|---:|
| T-1042 | 7560 | 684 | 8686 |
| T-1043 | 7530 | 568 | 8454 |
| T-1044 | 11414 | 813 | 12726 |
| T-1045 | 7500 | 546 | 8368 |
| T-1046 | 7796 | 552 | 8674 |
| T-1047 | 7540 | 600 | 8500 |
| T-1048 | 11664 | 956 | 13222 |
| T-1049 | — | — | — |
| T-1050 | — | — | — |
| T-1051 | — | — | — |
| **All 10** | **61,004** | **4,719** | **68,630** |

- T-1049 to T-1051 hit Gemini's free-tier rate limit (429) and used no tokens.
- Totals include model tokens beyond input and output, such as thinking tokens.

## Coding tokens (Claude Code, this build session)

Summed from this session's transcript usage: 604 assistant responses on Claude Opus 5.5.

| Kind | Tokens |
|---|---:|
| Input (uncached) | 1,322 |
| Output | 805,834 |
| Cache read | 222,949,999 |
| Cache write | 1,277,529 |

This covers the whole session: building the TPM Status Agent (Epics 1 and 2) and running this eval. It isn't split per task.
