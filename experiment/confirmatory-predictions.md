# Confirmatory Round — Predictions, Declared Before Collection

Declared **2026-09-22, before any confirmatory run was dispatched.** The file is committed and
pushed to each subject's fork before the first run, so the repository history timestamps it ahead
of the data.

Everything below comes from the exploratory phase (all four subjects analysed:
[hmpps-results.md](hmpps-results.md), [ghostfolio-results.md](ghostfolio-results.md),
[monize-results.md](monize-results.md), [hmpps-template-results.md](hmpps-template-results.md)).
The confirmatory round tests the rules on **new data that has not been looked at**; the two data
sets are analysed and reported separately and are never pooled.

## 1. Design (frozen)

| Element | Setting |
|---|---|
| Subjects | hmpps-activities-management, ghostfolio, monize (backend), hmpps-template-typescript — the same commits and harnesses as the exploratory phase |
| Configurations | A–F, unchanged (EXPERIMENT-PLAN.md §3) |
| Runs | **10 completed runs per configuration per subject** (240 kept runs) |
| Machines | GitHub-hosted, **AMD EPYC 7763 only**: every job checks its processor first and a run on any other processor cancels itself before anything is measured, and is sent again ([methodology-decisions.md](../notes/methodology-decisions.md) Decision 12). For Config E all three machines must be EPYC 7763 |
| Order | Serial within a subject, shuffled from a recorded seed; the four subjects run at the same time |
| Instrument | ECO-CI v5 with its EPYC 7763 power model, which now matches the hardware on every observation |
| Measured at | The stage where the treatment acts (caching → install; stage reduction → pipeline; across machines → pipeline; across cores → test) |

## 2. Decision rule (fixed in advance)

- **Test:** Mann–Whitney U, two-sided, exact; **Holm correction over the four primary comparisons
  of each subject**; α = 0.05. Effect = % change in the mean with a 95% bootstrap interval.
- **Directional prediction confirmed** if the effect is in the predicted direction and significant
  after Holm.
- **"No detectable difference" confirmed** if the effect is not significant **and** its 95%
  interval lies within **±10%**, the exploratory noise floor. Non-significance alone is not counted
  as confirmation.
- **Otherwise the prediction fails**, and is reported as failed.
- No processor adjustment: every observation is on one processor. The analysis script is
  `stage-analysis.py`, unchanged.

## 3. Predictions — the four primary comparisons

The rule a comparison tests is in brackets. Exploratory value = all runs; the EPYC 7763-only
value is shown for R3, where it is the closer guide.

### Caching, A → B, install stage (R1)

| Subject | Prediction | Exploratory |
|---|---|---|
| hmpps | **decrease** | −16.5% |
| ghostfolio | **decrease** | −34.8% |
| monize | **decrease** | −16.0% |
| hmpps-template | **decrease** | −29.9% |

### Stage reduction, A → C, pipeline (R1)

| Subject | Prediction | Exploratory |
|---|---|---|
| hmpps | **decrease**, about −70% | −70.0% |
| ghostfolio | **decrease**, about −30% (from stage shares) | −28.7% |
| monize | **decrease**, about −68% (from stage shares) | −68.8% |
| hmpps-template | **decrease**, about −17% (from stage shares) | −18.5% |

### Across machines, B → E, pipeline (R2)

| Subject | Prediction | Exploratory (processor-adjusted) |
|---|---|---|
| hmpps | **increase** | +13.0% |
| ghostfolio | **increase** | +13.9% |
| monize | **no detectable difference** — setup is ≈2% of the pipeline, so R2's penalty (+3.4%) is below the noise | +0.3% |
| hmpps-template | **increase**, the largest of the four (fixed costs dominate) | +63.4% |

On every subject, E's wall-clock is predicted to be **shorter** than B's.

### Across cores, B → F, test stage (R3)

| Subject | Prediction | Why | Exploratory (all / 7763 only) |
|---|---|---|---|
| hmpps | **decrease** | speed-up (2.79×) beyond even the steepest linear power curve's break-even (≈2.38×) | −42.4% / −40.9% |
| monize | **decrease** | speed-up (1.80×) above ECO-CI's break-even (1.60×) | −12.0% / −12.6% |
| ghostfolio | **no detectable difference** | speed-up (1.62×) at break-even (1.61×) | −0.5% / +2.4% |
| hmpps-template | **increase** | parallel is *slower* (0.85×) while CPU rises; no power curve can make that cheaper | +25.4% / +11.8% |

## 4. Secondary predictions (reported, not in the Holm family)

- **R1 cap on caching.** The pipeline effect of caching is no larger in size than install's share
  of pipeline energy (hmpps 6.4%, ghostfolio 15.3%, monize 1.9%, hmpps-template 27%).
- **R3 speed-ups.** hmpps above 2.38×; monize and ghostfolio between 1× and their CPU ratio;
  hmpps-template below 1×.
- **R2 mechanism.** E's install energy is about 3× B's on every subject (three machines each
  install once).

## 5. Known risks to these predictions, stated now

- **hmpps-template, R3.** On the EPYC 7763 alone the exploratory increase was smaller (+11.8%, 7
  runs) and the test stage is short (≈2 s), so noise is high. The direction is predicted; reaching
  significance with 10 runs is not certain.
- **ghostfolio, R3** sits at break-even, and its interval was wider than ±10% in the exploratory
  data, so the equivalence criterion may not be met even if there is no real effect.
- **monize, serial unit tests** once ran out of Node's heap (threat T15). Any such failure is logged
  and counted, not hidden.

## 6. What is not settled by this document

Whether the thesis hypotheses stay as H1–H4 or are restated as R1–R3 is a question for the
supervisor. The predictions above are written against R1–R3; each maps onto H1–H4 (caching,
stage reduction, and the two forms of parallelisation) without changing its direction.
