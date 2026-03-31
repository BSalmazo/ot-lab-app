# Prompt Template for ChatGPT (Scientific Paper Drafting)

Use the attached files as the only source of truth.

## Task

Write a scientific paper draft (English) about OT Lab App and its checkpoint validation campaign.

## Hard constraints

1. Ground every empirical claim in provided evidence files.
2. Do not invent results, metrics, or experiment settings.
3. Explicitly separate facts from interpretation.
4. Include a limitations/threats-to-validity section.
5. Keep terminology consistent with the artifacts (CT1..CT7, Modbus/TCP, en0/lo0, session model).

## Required structure

1. Title
2. Abstract
3. Introduction
4. System Architecture
5. Experimental Methodology
6. Results
7. Discussion and Threats to Validity
8. Related Work placeholder section (if sources unavailable)
9. Conclusion
10. Replication package statement

## Mandatory inclusions

- Final checkpoint decision: `CHECKPOINT APROVADO`
- Full CT summary (CT1..CT7 all PASS in approved campaign)
- Multi-interface evidence (`en0`, `lo0`)
- Quantitative table from `PAPER_RESULTS_TABLES.md`

## Writing style

- Scientific and concise.
- Avoid marketing language.
- Prefer precise, reproducible language.
- Add clearly labeled assumptions where needed.

## Output format

Provide:
1. Full paper draft.
2. A short "Evidence Traceability" appendix mapping each major claim to file/section evidence.
