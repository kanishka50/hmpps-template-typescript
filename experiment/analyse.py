#!/usr/bin/env python3
"""Summarise the collected measurements: per-configuration variance and per-stage energy.

Reads experiment/data/measurements-all.csv and prints summaries only. No raw rows
are printed, so the output can be read in a session without carrying the dataset.

Two quantities per run:

  pipeline energy   the SUM of every row's energy_j. For Config E the three jobs
                    (lint, test, build) each run on their own VM and each installs,
                    so all of their rows belong to the one run's energy budget.

  wall-clock        the sum of stage durations for a single-job configuration; for
                    Config E, the MAXIMUM over its jobs, because the jobs run
                    concurrently. This is stage time only: it excludes queueing,
                    checkout and runner start-up, so it understates the wall-clock
                    GitHub reports. It is used for comparing configurations, not
                    for absolute claims.

Usage: python experiment/analyse.py [path/to/measurements-all.csv]
"""

import csv
import statistics
import sys
from collections import defaultdict

PATH = sys.argv[1] if len(sys.argv) > 1 else "experiment/data/measurements-all.csv"
CONFIG_ORDER = ["A", "B", "C", "D", "E", "F"]
CONFIG_NAME = {
    "A": "Full (reference)",
    "B": "Cached",
    "C": "Minimal",
    "D": "Cached+Minimal",
    "E": "Cached+Parallel",
    "F": "Cached+Workers",
}


def cv(values):
    """Coefficient of variation, as a percentage. Undefined for fewer than 2 runs."""
    if len(values) < 2:
        return None
    mean = statistics.mean(values)
    if mean == 0:
        return None
    return 100.0 * statistics.stdev(values) / mean


def main():
    rows = []
    with open(PATH, newline="") as fh:
        for r in csv.DictReader(fh):
            r["energy_j"] = float(r["energy_j"])
            r["duration_s"] = float(r["duration_s"])
            r["cpu_avg_pct"] = float(r["cpu_avg_pct"])
            rows.append(r)

    # ---- per run -------------------------------------------------------
    run_energy = defaultdict(float)          # (config, run_id) -> J
    run_job_dur = defaultdict(float)         # (config, run_id, job) -> s
    run_cpus = defaultdict(set)              # (config, run_id) -> {cpu_model}
    stage_energy = defaultdict(list)         # (config, label) -> [J per run]
    stage_per_run = defaultdict(float)       # (config, run_id, label) -> J

    for r in rows:
        key = (r["config"], r["run_id"])
        run_energy[key] += r["energy_j"]
        run_job_dur[(r["config"], r["run_id"], r["job"])] += r["duration_s"]
        run_cpus[key].add(r["cpu_model"])
        stage_per_run[(r["config"], r["run_id"], r["label"])] += r["energy_j"]

    for (cfg, run_id, label), joules in stage_per_run.items():
        stage_energy[(cfg, label)].append(joules)

    run_wall = defaultdict(float)
    for (cfg, run_id, job), secs in run_job_dur.items():
        key = (cfg, run_id)
        run_wall[key] = max(run_wall[key], secs) if cfg == "E" else run_wall[key] + secs

    by_config = defaultdict(list)
    for (cfg, run_id), joules in run_energy.items():
        by_config[cfg].append((run_id, joules, run_wall[(cfg, run_id)]))

    # ---- report --------------------------------------------------------
    print("=" * 78)
    print(" PIPELINE ENERGY PER CONFIGURATION")
    print("=" * 78)
    print(f"{'Cfg':<4}{'name':<18}{'n':>3}{'mean J':>10}{'sd J':>9}{'CV %':>8}"
          f"{'min J':>9}{'max J':>9}{'wall s':>9}")
    means = {}
    for cfg in CONFIG_ORDER:
        runs = by_config.get(cfg, [])
        if not runs:
            continue
        energies = [e for _, e, _ in runs]
        walls = [w for _, _, w in runs]
        means[cfg] = statistics.mean(energies)
        sd = statistics.stdev(energies) if len(energies) > 1 else 0.0
        c = cv(energies)
        print(f"{cfg:<4}{CONFIG_NAME[cfg]:<18}{len(runs):>3}{means[cfg]:>10.1f}"
              f"{sd:>9.1f}{(f'{c:.1f}' if c is not None else '  -'):>8}"
              f"{min(energies):>9.1f}{max(energies):>9.1f}"
              f"{statistics.mean(walls):>9.1f}")

    print()
    print("=" * 78)
    print(" HYPOTHESIS COMPARISONS (mean pipeline energy)")
    print("=" * 78)
    comparisons = [
        ("H2", "A", "B", "caching"),
        ("H3", "A", "C", "stage reduction"),
        ("H3'", "B", "D", "stage reduction, given caching"),
        ("H4", "B", "E", "parallelisation across machines"),
        ("H4'", "B", "F", "parallelisation across cores"),
    ]
    for tag, base, treat, what in comparisons:
        if base in means and treat in means:
            delta = 100.0 * (means[treat] - means[base]) / means[base]
            n_base = len(by_config[base])
            n_treat = len(by_config[treat])
            print(f"{tag:<4} {base} -> {treat}  {what:<32}{delta:>+8.1f}%"
                  f"   (n={n_base},{n_treat})")

    print()
    print("=" * 78)
    print(" PER-STAGE ENERGY (mean J per run, CV across runs)")
    print("=" * 78)
    labels = ["toolchain", "install", "lint", "test", "build", "deploy"]
    print(f"{'Cfg':<4}" + "".join(f"{lab:>16}" for lab in labels))
    for cfg in CONFIG_ORDER:
        if cfg not in by_config:
            continue
        cells = []
        for lab in labels:
            vals = stage_energy.get((cfg, lab), [])
            if not vals:
                cells.append(f"{'-':>16}")
                continue
            c = cv(vals)
            cells.append(f"{statistics.mean(vals):>10.1f}"
                         + (f"{c:>5.0f}%" if c is not None else f"{'':>6}"))
        print(f"{cfg:<4}" + "".join(cells))

    print()
    print("=" * 78)
    print(" PROCESSOR ALLOCATION (the runner lottery)")
    print("=" * 78)
    cpu_runs = defaultdict(set)
    for (cfg, run_id), cpus in run_cpus.items():
        for cpu in cpus:
            cpu_runs[cpu].add((cfg, run_id))
    for cpu, runs in sorted(cpu_runs.items(), key=lambda kv: -len(kv[1])):
        cfgs = sorted({c for c, _ in runs})
        print(f"  {len(runs):>3} runs  {cpu:<42} configs: {','.join(cfgs)}")

    mixed = [(c, r) for (c, r), cpus in run_cpus.items() if len(cpus) > 1]
    if mixed:
        print(f"\n  NOTE: {len(mixed)} run(s) spanned more than one processor model")
        print("  (expected for Config E: its jobs are separate VMs).")

    total_runs = len(run_energy)
    print()
    print(f"{total_runs} runs analysed from {PATH}")
    if any(len(v) < 2 for v in by_config.values()):
        print("WARNING: at least one configuration has n<2; its CV is undefined.")


if __name__ == "__main__":
    main()
