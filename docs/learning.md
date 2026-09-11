# Learning, resume and stabilisation

## Learning

During the learn window every supervisory write seen under a confident, non-transitioning phase
is counted per target per phase (`GrammarLearner`). At the end of the window the counts become
fractions and, per target, the set of learned coherent phases (fraction at or above 0.1). Learning
is frozen during evaluate: an anomaly seen while evaluating is never absorbed into normal.

## Persistence and resume

Each silo's calibrated phase config is written to `silos/<endpoint>/profile.json` at the end of
observe and its grammar to `silos/<endpoint>/grammar.json` at the end of learn.
`--resume-from <earlier run>` loads both after the observe window (the state signal is still
discovered from live traffic) and skips learning for every silo that has a saved grammar. A silo
without one learns afresh and says so. The single-silo `--grammar` and `--profile` flags remain.

## Cycles and stabilisation

`CycleTracker` counts completed process cycles from the de-duplicated phase sequence. It assumes the
process is periodic in its phase sequence and confirms a period only after two full repetitions
(RISING, STABLE, RISING must not be mistaken for a period of 2), reporting the boundaries passed
until then together with the grammar snapshot taken at each. A phase that breaks the pattern stops
counting rather than guessing.

`Stabilisation` compares the grammar at each completed learn cycle with the previous one: the
largest per-target L1 change in phase fractions (a new target counts as 1.0) and whether any
learned coherent phase set changed. Stable after K consecutive cycles with no set change and a
change below epsilon. K (`--stable-cycles`, default 3) and epsilon (`--stable-epsilon`, default
0.05) are research parameters: they are stored in `run.json` and printed beside `cycles_to_stable`
in every report and `learning_progress` event.

The model-building cost record per silo in `run.json`: `cycles_observed`, `cycles_to_stable`,
`wall_seconds_to_stable`, `writes_observed`, the per-cycle `history`, and the run-level
`human_hours` field which the operator fills in by hand (the engine cannot measure it and does not
guess). `--learn-until-stable` ends the learn window as soon as every silo is stable, with
`--learn` as the upper bound.
