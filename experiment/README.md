# Green DevOps measurement harness

Added to a fork of `ministryofjustice/hmpps-template-typescript` (MIT, Crown
Copyright) for the research project *Towards Green DevOps: Measuring and
Optimising the Resource Consumption and Environmental Impact of CI/CD
Pipelines* (Kanishka, University of Kelaniya). This is subject 4 of four
(small / fast). The harness is ported from the subject-1 fork
(`kanishka50/hmpps-activities-management`).

**The upstream application is not modified.** Everything added lives in
`experiment/`, `.github/workflows/config-*.yml`, `.github/workflows/cache-warmup.yml`,
`.github/workflows/checks.yml`, `Dockerfile.experiment` and
`Dockerfile.experiment.dockerignore`. Every run hashes all tracked files except
those additions and fails unless the hash matches upstream commit
`5d1eb72e1b7db024aebf03441eb4c0e5e9ab54b0`.

## Configurations

| Config | Cache | Lint | Tests | Machines |
|---|---|---|---|---|
| A Full | no | yes | `jest --runInBand` | 1 |
| B Cached | yes | yes | `jest --runInBand` | 1 |
| C Minimal | no | no | `jest --runInBand --shard=1/4` | 1 |
| D Cached+Minimal | yes | no | `jest --runInBand --shard=1/4` | 1 |
| E Cached+Parallel | yes | yes | `jest --runInBand`, lint and test on separate machines | 3 |
| F Cached+Workers | yes | yes | `jest` (Jest's default worker count) | 1 |

Measured stages: `toolchain`, `install`, `lint`, `test`, `build`, `deploy`.
`toolchain` installs npm 12.0.2, which the subject requires (`.npmrc`
engine-strict, `engines.npm ^12`). It uses a throwaway npm cache, so it is the
same work in every configuration and caching acts on `install` alone.

## Files

| File | Purpose |
|---|---|
| `experiment/templates/*.yml` | The single source for the six workflows |
| `experiment/generate-workflows.py` | Generates `config-a.yml` ... `config-f.yml`; edit templates, not outputs |
| `experiment/run-pilot.sh` | Dispatches runs strictly serially in a seeded random order |
| `experiment/collect-results.sh` | Downloads artefacts (log fallback) into `experiment/data/` |
| `experiment/analyse.py`, `experiment/stage-analysis.py` | Summary and per-stage analysis |
| `.github/workflows/cache-warmup.yml` | Unmeasured; creates the npm cache the cached configurations require |
| `.github/workflows/checks.yml` | Unmeasured; the runner check made before the harness was built |
| `Dockerfile.experiment` | The deploy stage: packages the already-built `dist/` and `node_modules` |

## Running

```bash
python experiment/generate-workflows.py            # after editing a template
gh workflow run cache-warmup.yml --repo kanishka50/hmpps-template-typescript   # when no cache exists
bash experiment/run-pilot.sh 10 <SEED>             # serial, seeded
bash experiment/collect-results.sh
python experiment/stage-analysis.py
```

Nothing is published to npm, pushed to a registry, or sent to ECO-CI's servers
(`send-data: false` on every measurement).
