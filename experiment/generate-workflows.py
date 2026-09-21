#!/usr/bin/env python3
"""Generate the six pipeline-configuration workflows from the templates.

The point of generating them is that the configurations then CANNOT differ in
anything except the factors that define them. Editing a generated workflow by
hand defeats that guarantee - edit the template instead and re-run this.

    python experiment/generate-workflows.py

Configurations (the independent variable, at six levels):

    A Full             no cache | lint | all tests
    B Cached           CACHE    | lint | all tests
    C Minimal          no cache | ---- | 1/4 of tests
    D Cached+Minimal   CACHE    | ---- | 1/4 of tests
    E Cached+Parallel  CACHE    | lint and test as parallel jobs (3 VMs)
    F Cached+Workers   CACHE    | lint | all tests, ACROSS THE RUNNER'S CORES

"Minimal" is mechanical: lint removed, tests run with the runner's own
`--shard=1/4`. Nothing is selected by hand, so no subset can have been chosen
for its effect.

Configurations E and F are two different meanings of "run the tests in
parallel", and separating them is the point of F:

    E spreads the work over THREE MACHINES. Each is a fresh VM that installs
      its own dependencies, so the install stage is paid three times.
    F spreads the work over the CORES OF ONE MACHINE. Nothing is duplicated;
      the runner already has four cores and the subject uses one of them.

B is the common control for both: B, E and F run identical work (cache, lint,
the whole suite) and differ only in how the test stage is distributed.

Subject: hmpps-template-typescript (subject 4). Its .npmrc sets engine-strict
with engines.npm ^12, so every job first installs npm 12 as its own measured
stage, `toolchain`, kept apart from `install` (see TOOLCHAIN_BLOCK).
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
TPL = ROOT / "experiment" / "templates"
OUT = ROOT / ".github" / "workflows"

NO_CACHE = "          # NO `cache:` key. This is an UNCACHED configuration."
CACHE = "          # Dependency caching ON - a defining factor of this configuration.\n          cache: 'npm'"

# The test setting is set EXPLICITLY in every configuration and is never
# inherited from the subject application. This matters as soon as there is more
# than one subject: hmpps pins `jest --runInBand` in its own package.json, but
# another project may already run in parallel by default. Defining B and F by
# what was CHANGED would make them mean different things on different subjects,
# and they could not be compared. They are defined by what they DO:
#
#   one process  - the runner's own force-serial flag, stated explicitly.
#   default      - the flag simply absent, so the test runner picks its own
#                  worker count (Jest: cores - 1). Never a number chosen by
#                  hand, for the same reason --shard=1/4 is not: a hand-picked
#                  number could be suspected of being chosen for its effect.
#                  Every run logs `nproc`, so the count is recoverable.
#
# This subject's own CI command is `npm run test:ci` = `jest --runInBand`, so
# A-E state the subject's default explicitly and F removes the flag.
TEST_ALL = "npx jest --runInBand"
TEST_SHARD_SERIAL = "npx jest --runInBand --shard=1/4"
TEST_WORKERS = "npx jest"
# --shard=1/4 is Jest's own deterministic quarter of the suite: the same files
# every run, chosen by the runner rather than by us.
TEST_SHARD = TEST_SHARD_SERIAL

# The measured workload is pinned by CONTENT: one hash over every tracked file
# of upstream 5d1eb72 except the experiment's own additions (.github/,
# experiment/, Dockerfile.experiment*). The whole tree rather than a list of
# folders, because `eslint .` lints the whole repository: a hand-made list
# could miss a file a stage reads. The experiment's commits sit on top of the
# upstream commit, so HEAD's sha differs while this hash must not.
PIN_ENV = """  SUBJECT_UPSTREAM_SHA: 5d1eb72e1b7db024aebf03441eb4c0e5e9ab54b0
  SUBJECT_WORKLOAD_HASH: 755e02a307a2fb9ddd29a42992508b84730eb670
  # Exact npm version, so a new 12.x release cannot change the toolchain or
  # install stages part-way through collection (upstream's Dockerfile pins it too).
  NPM_VERSION: '12.0.2'
"""

PIN_STEP = r"""      - name: Verify the measured workload is the pinned one
        run: |
          HASH="$(git ls-tree -r --full-tree HEAD \
                  | awk -F'\t' '$2 !~ /^(\.github\/|experiment\/|Dockerfile\.experiment)/' \
                  | sha1sum | cut -d' ' -f1)"
          echo "HEAD commit   : $(git rev-parse HEAD)"
          echo "server tree   : $(git rev-parse HEAD:server)"
          echo "workload hash : $HASH"
          if [ "$HASH" != "${{ env.SUBJECT_WORKLOAD_HASH }}" ]; then
            echo "::error::The workload differs from upstream ${{ env.SUBJECT_UPSTREAM_SHA }}; do not use this run."
            exit 1
          fi
          echo "Workload matches the pinned upstream source."
"""

# STAGE: TOOLCHAIN. The subject refuses to install under the npm that ships
# with Node 24 (.npmrc: engine-strict; engines.npm ^12), so npm 12 is installed
# first, in EVERY configuration and in every Config E job. It is measured as its
# own stage so that it cannot mask or inflate the caching effect on `install`.
#
# It runs from $RUNNER_TEMP (outside the repository, so the subject's .npmrc
# does not apply to installing npm itself) and with a THROWAWAY npm cache. The
# second point matters: in ~/.npm the npm tarball would be saved by the cache
# warm-up and restored in the cached configurations, and caching would then act
# on two stages instead of one. With its own cache the toolchain stage does the
# same work in all six configurations.
TOOLCHAIN_BLOCK = """      # ================= STAGE: TOOLCHAIN =================
      - name: Install npm ${{ env.NPM_VERSION }} (required by the subject)
        working-directory: ${{ runner.temp }}
        run: |
          npm install -g "npm@${{ env.NPM_VERSION }}" --cache "$RUNNER_TEMP/npm-toolchain-cache" --no-fund --no-audit
          v="$(npm -v)"
          echo "npm $v"
          [ "$v" = "${{ env.NPM_VERSION }}" ] || { echo "::error::npm is $v, expected ${{ env.NPM_VERSION }}."; exit 1; }

      - name: ECO-CI - measure toolchain
        uses: green-coding-solutions/eco-ci-energy-estimation@v5
        with:
          task: get-measurement
          label: toolchain
          send-data: false
"""

LINT_BLOCK = """
      # ================= STAGE: LINT =================
      - name: Lint
        run: npm run lint

      - name: ECO-CI - measure lint
        uses: green-coding-solutions/eco-ci-energy-estimation@v5
        with:
          task: get-measurement
          label: lint
          send-data: false
"""

# Config C and D remove the lint stage entirely; the comment keeps the removal
# visible in the generated file rather than leaving a silent gap.
NO_LINT_BLOCK = """
      # ================= STAGE: LINT - REMOVED =================
      # This is a MINIMAL configuration: the lint stage is not run. Lint emits
      # no files (eslint reports; tsc runs with --noEmit), so no later stage
      # loses an input. What is given up is detection, not build correctness.
"""

SINGLE = [
    # id, name, cache line, lint block, test command, expected measurement rows
    # rows: toolchain, install, [lint], test, build, deploy
    ("A", "Full", NO_CACHE, LINT_BLOCK, TEST_ALL, 6),
    ("B", "Cached", CACHE, LINT_BLOCK, TEST_ALL, 6),
    ("C", "Minimal", NO_CACHE, NO_LINT_BLOCK, TEST_SHARD, 5),
    ("D", "Cached+Minimal", CACHE, NO_LINT_BLOCK, TEST_SHARD, 5),
    ("F", "Cached+Workers", CACHE, LINT_BLOCK, TEST_WORKERS, 6),
]

# Config E: three jobs. lint and test run concurrently; build+deploy waits for
# both. Each job installs for itself because each is a fresh VM.
PARALLEL_JOBS = [
    (
        "lint",
        "Lint (parallel with test)",
        "",
        """      - name: Lint
        run: npm run lint

      - name: ECO-CI - measure lint
        uses: green-coding-solutions/eco-ci-energy-estimation@v5
        with:
          task: get-measurement
          label: lint
          send-data: false
""",
        3,
    ),
    (
        "test",
        "Test (parallel with lint)",
        "",
        """      - name: Test
        run: npx jest --runInBand

      - name: ECO-CI - measure test
        uses: green-coding-solutions/eco-ci-energy-estimation@v5
        with:
          task: get-measurement
          label: test
          send-data: false
""",
        3,
    ),
    (
        "build",
        "Build and deploy",
        "    needs: [lint, test]",
        """      - name: Build
        run: npm run build

      - name: ECO-CI - measure build
        uses: green-coding-solutions/eco-ci-energy-estimation@v5
        with:
          task: get-measurement
          label: build
          send-data: false

      - name: Deploy (package built app into image)
        run: docker build -f Dockerfile.experiment -t hmpps-template:packaged .

      - name: ECO-CI - measure deploy
        uses: green-coding-solutions/eco-ci-energy-estimation@v5
        with:
          task: get-measurement
          label: deploy
          send-data: false
""",
        4,
    ),
]

HEADER = "# GENERATED FILE - do not edit. Regenerate with:\n#   python experiment/generate-workflows.py\n"


def subst_common(text: str) -> str:
    out = (text.replace("@@PIN_ENV@@", PIN_ENV)
               .replace("@@PIN_STEP@@", PIN_STEP)
               .replace("@@TOOLCHAIN_BLOCK@@", TOOLCHAIN_BLOCK))
    assert "@@" not in out, "unreplaced placeholder"
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    single_tpl = (TPL / "single-job.yml").read_text(encoding="utf-8")

    for cid, name, cache, lint, test_cmd, rows in SINGLE:
        text = (
            single_tpl.replace("@@CONFIG@@", cid)
            .replace("@@CONFIG_NAME@@", name)
            .replace("@@CACHE_LINE@@", cache)
            .replace("@@CACHE_EXPECTED@@", "none" if cache is NO_CACHE else "hit")
            .replace("@@LINT_BLOCK@@", lint)
            .replace("@@TEST_CMD@@", test_cmd)
            .replace("@@EXPECTED_ROWS@@", str(rows))
        )
        text = subst_common(text)
        path = OUT / f"config-{cid.lower()}.yml"
        path.write_text(HEADER + text, encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(ROOT)}")

    head = subst_common((TPL / "parallel-header.yml").read_text(encoding="utf-8"))
    job_tpl = (TPL / "parallel-job.yml").read_text(encoding="utf-8")
    parts = [head]
    for job_id, job_name, needs, work, rows in PARALLEL_JOBS:
        parts.append(
            job_tpl.replace("@@JOB_ID@@", job_id)
            .replace("@@JOB_NAME@@", job_name)
            .replace("@@NEEDS@@", needs)
            .replace("@@CACHE_EXPECTED@@", "hit")   # every Config E job is cached
            .replace("@@WORK@@", work)
            .replace("@@EXPECTED_ROWS@@", str(rows))
        )
    parts = [parts[0]] + [subst_common(p) for p in parts[1:]]
    path = OUT / "config-e.yml"
    path.write_text(HEADER + "\n".join(parts), encoding="utf-8", newline="\n")
    print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
