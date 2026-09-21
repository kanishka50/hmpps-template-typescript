#!/usr/bin/env python3
"""Per-stage hypothesis analysis: R1 (bounded effect), R2 (duplication), R3 (utilisation).

Reads experiment/data/measurements-all.csv and prints summaries only (no raw rows).
Subject-agnostic: the same script is meant to run unchanged on every subject.

Each hypothesis is tested at the stage where its treatment acts (EXPERIMENT-PLAN.md §4):

  caching           A vs B, install stage energy       (replicated as C vs D)
  stage reduction   A vs C, pipeline energy            (replicated as B vs D)
  across machines   B vs E, pipeline energy            (R2)
  across cores      B vs F, test stage energy          (R3)

Statistics, per comparison:
  - % change in the mean, with a 95% bootstrap interval (groups resampled separately)
  - Mann-Whitney U, two-sided, exact; Holm-corrected over the four primary comparisons
  - Cliff's delta (effect size; + means the treatment is larger)

CPU model as a blocking factor. Every CSV row is one stage on one machine, so every row
has exactly one processor, including each of Config E's three machines. A multiplicative
processor factor is estimated from all rows,

    log(energy) = cell(config, job, stage) + processor

and each row is divided by its processor's factor before summing. The comparisons are then
repeated on these normalised energies. A model-free check is also reported: the same
comparison restricted to the most common processor.

Usage: python experiment/stage-analysis.py [path/to/measurements-all.csv]
"""

import sys

import numpy as np
import pandas as pd
from scipy import stats

PATH = sys.argv[1] if len(sys.argv) > 1 else "experiment/data/measurements-all.csv"
STAGES = ["toolchain", "install", "lint", "test", "build", "deploy"]
RNG = np.random.default_rng(20260921)
N_BOOT = 10000

PRIMARY = [
    ("caching", "A", "B", "install"),
    ("stage reduction", "A", "C", "pipeline"),
    ("across machines (R2)", "B", "E", "pipeline"),
    ("across cores (R3)", "B", "F", "test"),
]
SECONDARY = [
    ("caching, given minimal", "C", "D", "install"),
    ("caching", "A", "B", "pipeline"),
    ("stage reduction, given caching", "B", "D", "pipeline"),
    ("across machines", "B", "E", "install"),
    ("across machines", "B", "E", "test"),
    ("across cores", "B", "F", "pipeline"),
    ("across machines, wall-clock", "B", "E", "wall_s"),
    ("across cores, wall-clock", "B", "F", "wall_s"),
    ("across cores, test duration", "B", "F", "test_s"),
]


def load():
    d = pd.read_csv(PATH)
    d["log_e"] = np.log(d["energy_j"])
    return d


def cpu_factors(d):
    """Multiplicative processor factor on energy, relative to the most common processor."""
    ref = d.drop_duplicates(["run_id", "job"])["cpu_model"].value_counts().index[0]
    cell = d["config"] + "|" + d["job"] + "|" + d["label"]
    X_cell = pd.get_dummies(cell, dtype=float)
    X_cpu = pd.get_dummies(d["cpu_model"], dtype=float).drop(columns=[ref])
    X = pd.concat([X_cell, X_cpu], axis=1)
    beta, *_ = np.linalg.lstsq(X.values, d["log_e"].values, rcond=None)
    coef = pd.Series(beta, index=X.columns)
    fac = {ref: 1.0}
    fac.update({c: float(np.exp(coef[c])) for c in X_cpu.columns})
    return ref, fac


def per_run(d, energy_col):
    """One row per run: stage energies, pipeline energy, wall-clock, processor(s)."""
    g = d.groupby(["config", "run_id"])
    out = g[energy_col].sum().rename("pipeline").to_frame()
    st = d.pivot_table(index=["config", "run_id"], columns="label", values=energy_col,
                       aggfunc="sum")
    out = out.join(st)
    job_s = d.groupby(["config", "run_id", "job"])["duration_s"].sum()
    out["wall_s"] = job_s.groupby(["config", "run_id"]).max()
    out["test_s"] = d[d.label == "test"].groupby(["config", "run_id"])["duration_s"].sum()
    cpus = g["cpu_model"].agg(lambda s: s.iloc[0] if s.nunique() == 1 else "mixed")
    out["cpu"] = cpus
    # processor of the machine that ran the stage (for stage-level stratified checks)
    for lab in STAGES:
        sub = d[d.label == lab].groupby(["config", "run_id"])["cpu_model"]
        out[f"cpu_{lab}"] = sub.agg(lambda s: s.iloc[0] if s.nunique() == 1 else "mixed")
    return out.reset_index()


def boot_pct(x, y):
    bx = RNG.choice(x, (N_BOOT, len(x))).mean(1)
    by = RNG.choice(y, (N_BOOT, len(y))).mean(1)
    r = 100 * (by / bx - 1)
    return np.percentile(r, [2.5, 97.5])


def cliffs(x, y):
    diff = np.subtract.outer(y, x)
    return (np.sum(diff > 0) - np.sum(diff < 0)) / diff.size


def compare(runs, base, treat, metric):
    x = runs.loc[runs.config == base, metric].dropna().values
    y = runs.loc[runs.config == treat, metric].dropna().values
    pct = 100 * (y.mean() / x.mean() - 1)
    lo, hi = boot_pct(x, y)
    p = stats.mannwhitneyu(y, x, alternative="two-sided", method="exact").pvalue
    return dict(n=(len(x), len(y)), mean_b=x.mean(), mean_t=y.mean(), pct=pct, lo=lo, hi=hi,
                p=p, delta=cliffs(x, y), cv_b=100 * x.std(ddof=1) / x.mean(),
                cv_t=100 * y.std(ddof=1) / y.mean())


def holm(ps):
    order = np.argsort(ps)
    adj = np.empty(len(ps))
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, (len(ps) - rank) * ps[i])
        adj[i] = min(1.0, running)
    return adj


def fmt(r, p_adj=None):
    s = (f"n={r['n'][0]},{r['n'][1]}  {r['mean_b']:8.1f} -> {r['mean_t']:8.1f}  "
         f"{r['pct']:+6.1f}% [{r['lo']:+6.1f}, {r['hi']:+6.1f}]  "
         f"p={r['p']:.2g}")
    if p_adj is not None:
        s += f" (Holm {p_adj:.2g})"
    return s + f"  delta={r['delta']:+.2f}"


def section(title):
    print("\n" + "=" * 96 + f"\n {title}\n" + "=" * 96)


def main():
    d = load()
    ref, fac = cpu_factors(d)
    d["energy_norm"] = d["energy_j"] / d["cpu_model"].map(fac)
    raw = per_run(d, "energy_j")
    norm = per_run(d, "energy_norm")

    section("PROCESSOR FACTORS (energy, relative to the most common processor; all stage rows)")
    counts = d.drop_duplicates(["run_id", "job"])["cpu_model"].value_counts()
    for cpu, f in sorted(fac.items(), key=lambda kv: kv[1]):
        print(f"  {f:6.3f}  {counts[cpu]:>3} machines  {cpu}")
    by_cfg = pd.crosstab(raw["cpu"], raw["config"])
    print("\n  processor per run (E: 'mixed' when its three machines differ)")
    print(by_cfg.to_string().replace("\n", "\n  "))

    section("NOISE FLOOR: CV of pipeline energy per configuration, raw vs processor-normalised")
    for cfg in sorted(raw.config.unique()):
        a = raw.loc[raw.config == cfg, "pipeline"]
        b = norm.loc[norm.config == cfg, "pipeline"]
        print(f"  {cfg}  raw {100 * a.std() / a.mean():5.1f}%   normalised "
              f"{100 * b.std() / b.mean():5.1f}%")

    section("PRIMARY COMPARISONS, AT THE STAGE WHERE THE TREATMENT ACTS")
    for label, runs in (("raw", raw), ("processor-normalised", norm)):
        print(f"\n  -- {label} --")
        res = [compare(runs, b, t, m) for _, b, t, m in PRIMARY]
        adj = holm([r["p"] for r in res])
        for (name, b, t, m), r, pa in zip(PRIMARY, res, adj):
            print(f"  {name:<22}{b}->{t} {m:<9}{fmt(r, pa)}")

    section("SECONDARY COMPARISONS (not corrected; supporting detail)")
    for label, runs in (("raw", raw), ("processor-normalised", norm)):
        print(f"\n  -- {label} --")
        for name, b, t, m in SECONDARY:
            if m in ("wall_s", "test_s") and label != "raw":
                continue
            print(f"  {name:<31}{b}->{t} {m:<9}{fmt(compare(runs, b, t, m))}")

    section(f"MODEL-FREE BLOCKING CHECK: only machines with the most common processor ({ref})")
    for name, b, t, m in PRIMARY + SECONDARY[:6]:
        col = "cpu" if m == "pipeline" else f"cpu_{m}"
        sub = raw[raw[col] == ref]
        x = sub.loc[sub.config == b, m].dropna().values
        y = sub.loc[sub.config == t, m].dropna().values
        if len(x) < 2 or len(y) < 2:
            print(f"  {name:<31}{b}->{t} {m:<9}n={len(x)},{len(y)}  too few")
            continue
        p = stats.mannwhitneyu(y, x, alternative="two-sided", method="exact").pvalue
        print(f"  {name:<31}{b}->{t} {m:<9}n={len(x)},{len(y)}  "
              f"{100 * (y.mean() / x.mean() - 1):+6.1f}%  p={p:.2g}  delta={cliffs(x, y):+.2f}")

    # ---------------------------------------------------------------- R1
    section("R1 BOUNDED EFFECT: predicted pipeline change = stage share x stage change")
    for name, b, t, stages in (("caching", "A", "B", ["install"]),
                               ("caching, given minimal", "C", "D", ["install"]),
                               ("stage reduction", "A", "C", ["lint", "test"]),
                               ("stage reduction, given caching", "B", "D", ["lint", "test"])):
        mb = raw[raw.config == b].mean(numeric_only=True)
        mt = raw[raw.config == t].mean(numeric_only=True).fillna(0)
        share = sum(mb[s] for s in stages) / mb["pipeline"]
        pred = 100 * sum(mt[s] - mb[s] for s in stages) / mb["pipeline"]
        obs = compare(raw, b, t, "pipeline")
        print(f"  {name:<31}{b}->{t}  share of {'+'.join(stages):<10}{100 * share:5.1f}%  "
              f"cap -{100 * share:.1f}%  predicted {pred:+6.1f}%  observed {obs['pct']:+6.1f}% "
              f"[{obs['lo']:+.1f}, {obs['hi']:+.1f}]")

    print("\n  Runs per group to detect the effect at 80% power, alpha 0.05 (normal approx.):")
    for name, b, t, m in (("caching", "A", "B", "install"), ("caching", "A", "B", "pipeline"),
                          ("across cores", "B", "F", "test"), ("across cores", "B", "F", "pipeline"),
                          ("across machines", "B", "E", "pipeline")):
        r = compare(raw, b, t, m)
        cv = (r["cv_b"] + r["cv_t"]) / 2
        n = 2 * (1.96 + 0.8416) ** 2 * (cv / r["pct"]) ** 2 if r["pct"] else float("inf")
        print(f"    {name:<16}{b}->{t} {m:<9} effect {r['pct']:+6.1f}%  CV {cv:4.1f}%  "
              f"n ~ {int(np.ceil(n))}")

    # ---------------------------------------------------------------- R2
    section("R2 DUPLICATION: E re-pays setup on each extra machine")
    mb = raw[raw.config == "B"].mean(numeric_only=True)
    me = raw[raw.config == "E"].mean(numeric_only=True)
    print(f"  B install (one machine)          {mb['install']:7.1f} J")
    print(f"  E install (three machines)       {me['install']:7.1f} J   "
          f"= {me['install'] / mb['install']:.2f} x B")
    print(f"  predicted extra: 2 x B install   {2 * mb['install']:7.1f} J   "
          f"({100 * 2 * mb['install'] / mb['pipeline']:+.1f}% of B's pipeline)")
    print(f"  observed extra install           {me['install'] - mb['install']:7.1f} J")
    print(f"  observed pipeline change         {me['pipeline'] - mb['pipeline']:+7.1f} J   "
          f"({100 * (me['pipeline'] / mb['pipeline'] - 1):+.1f}%)")
    for s in ("toolchain", "lint", "test", "build", "deploy"):
        print(f"    {s:<7} B {mb[s]:7.1f}  E {me[s]:7.1f}  ({100 * (me[s] / mb[s] - 1):+.1f}%)")
    print("  Not measured by either: checkout and runner start-up on the two extra machines.")

    # ---------------------------------------------------------------- R3
    section("R3 UTILISATION: power vs CPU, break-even speed-up, predicted vs observed")
    fit = stats.linregress(d["cpu_avg_pct"], d["power_avg_w"])
    print(f"  all {len(d)} stage rows: power = {fit.intercept:.2f} + {fit.slope:.4f} x CPU%   "
          f"r = {fit.rvalue:+.3f}")
    print("  per processor (>= 20 rows):")
    for cpu, g in d.groupby("cpu_model"):
        if len(g) >= 20:
            f2 = stats.linregress(g["cpu_avg_pct"], g["power_avg_w"])
            print(f"    {len(g):>4} rows  power = {f2.intercept:.2f} + {f2.slope:.4f} x CPU%  "
                  f"r = {f2.rvalue:+.3f}  {cpu}")

    t = d[d.label == "test"]
    tb, tf = t[t.config == "B"], t[t.config == "F"]
    cpu_b, cpu_f = tb.cpu_avg_pct.mean(), tf.cpu_avg_pct.mean()
    pw_b, pw_f = (tb.energy_j.sum() / tb.duration_s.sum(), tf.energy_j.sum() / tf.duration_s.sum())
    model_ratio = (fit.intercept + fit.slope * cpu_f) / (fit.intercept + fit.slope * cpu_b)
    speed = tb.duration_s.mean() / tf.duration_s.mean()
    print(f"\n  test stage  B: {cpu_b:5.1f}% CPU, {pw_b:.2f} W, {tb.duration_s.mean():6.1f} s")
    print(f"              F: {cpu_f:5.1f}% CPU, {pw_f:.2f} W, {tf.duration_s.mean():6.1f} s")
    print(f"  break-even speed-up (model power ratio)     {model_ratio:.2f} x")
    print(f"  break-even speed-up (measured power ratio)  {pw_f / pw_b:.2f} x")
    print(f"  observed speed-up                           {speed:.2f} x")
    print(f"  predicted test energy change (model)        "
          f"{100 * (model_ratio / speed - 1):+.1f}%")
    print(f"  observed test energy change                 "
          f"{100 * (tf.energy_j.mean() / tb.energy_j.mean() - 1):+.1f}%")
    bs, bb = [], []
    for _ in range(N_BOOT):
        xb = tb.sample(len(tb), replace=True, random_state=RNG.integers(1 << 31))
        xf = tf.sample(len(tf), replace=True, random_state=RNG.integers(1 << 31))
        bs.append(xb.duration_s.mean() / xf.duration_s.mean())
        bb.append((xf.energy_j.sum() / xf.duration_s.sum()) / (xb.energy_j.sum() / xb.duration_s.sum()))
    print(f"  95% bootstrap: speed-up [{np.percentile(bs, 2.5):.2f}, {np.percentile(bs, 97.5):.2f}]"
          f"   break-even [{np.percentile(bb, 2.5):.2f}, {np.percentile(bb, 97.5):.2f}]")

    print(f"\n{raw.run_id.nunique()} runs, {len(d)} stage rows analysed from {PATH}")


if __name__ == "__main__":
    main()
