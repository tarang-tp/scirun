# SciRun Design (v0 draft)

> Status: Stages 0-4 are implemented and the V1 definition in section 10 was verified end to end on a local kind cluster. Sections describe what was built and why. Update this document as decisions change.

## 1. Goal

Let a user submit a containerized scientific computation to Kubernetes without writing Job manifests or running repeated `kubectl` commands.

**First milestone (before any CLI code):** run a scientific computation as a Kubernetes Job on a local Mac cluster, inspect its status, retrieve its logs, and verify it completed.

**Principle:** build the simplest working system first; add infrastructure only once the problem it solves is understood.

## 2. Non-goals for V1

Web dashboard, GPUs, Slurm, autoscaling, multi-cluster, complex scheduling, authentication/multi-tenancy.

## 3. Architecture

```text
User
  |
SciRun CLI (Python, Typer)
  |
Kubernetes API (official Python client, local kubeconfig)
  |
Kubernetes Job -> Pod -> Container (scientific workload)
```

SciRun is stateless in V1: job identity and state live in Kubernetes (labels/annotations on the Job), not in a local database.

## 4. Technology choices

| Concern | Choice | Reason |
|---|---|---|
| Language | Python 3.12+ | Matches scientific ecosystem |
| CLI | Typer | Less boilerplate than argparse |
| K8s access | `kubernetes` Python client | Official, avoids shelling out to kubectl |
| Packaging | uv + pyproject.toml | Fast, reproducible envs |
| Local cluster | kind | Lightweight, runs in Docker |
| Container runtime | Docker Desktop | Simplest on Apple Silicon (Colima later, maybe) |

Note: Apple Silicon means arm64 images; workload images should be built for the local architecture.

## 5. Experiment spec (draft)

`examples/mandelbrot.yaml`:

```yaml
name: mandelbrot
image: scirun/mandelbrot:0.1
args: ["--width", 2000, "--out", "/results/mandelbrot.png"]   # optional `command` overrides the image entrypoint
resources:        # applied as both requests and limits
  cpu: "1"
  memory: 1Gi
results:
  path: /results  # mounted as an emptyDir until Stage 3
```

Implemented in Stage 2 (`src/scirun/config.py`). Unknown fields are rejected so typos fail fast. Job IDs are `<name>-<6 hex>`, used as the Kubernetes Job name; Jobs carry the labels `app.kubernetes.io/managed-by=scirun` and `scirun.io/experiment=<name>`.

## 6. CLI commands (planned)

| Command | Behavior |
|---|---|
| `scirun submit <spec.yaml>` | Validate spec, create Job, print job ID |
| `scirun status <job-id>` | Show phase, start/end time, duration |
| `scirun logs <job-id> [--follow]` | Print or stream pod logs |
| `scirun results <job-id> [--out DIR]` | Copy result files to local disk |
| `scirun report <job-id>...` | Compare requests, timings and measured usage |
| `scirun submit <spec> --follow` | Submit and watch progress; for parallel jobs, also runs the gather step |
| `scirun gather <job-id>` | (Re)run the gather step of a parallel job |
| `scirun show <job-id>` | Provenance and output checksums |
| `scirun rerun <job-id>` | Resubmit the saved spec |
| `scirun verify <job-a> <job-b>` | Compare outputs byte for byte |
| `scirun sweep <spec> --vary k=a,b` | Run a grid of settings sequentially and compare |
| `scirun status <job-id> --tiles` | Per-tile state, attempts, node and time |

Added after V1: `scirun list` (SciRun-labeled Jobs, newest first) and `scirun delete <job-id>... [--results]`. `delete` only touches Jobs labeled `app.kubernetes.io/managed-by=scirun`, keeps result files unless `--results` is given (which asks for confirmation unless `-y`), and rejects job IDs that are not plain `[a-z0-9-]` names.

## 7. Results handling (decided in Stage 3)

Candidates considered:

1. `kubectl cp`-style copy from a completed pod (pod must still exist; fragile).
2. Shared hostPath/extraMounts volume between the kind node and the Mac (simple locally, not portable).
3. Object storage (MinIO) in-cluster (robust, more moving parts).

**Chosen for V1: option 2.** `scripts/create-cluster.sh` creates the kind cluster with `~/.scirun/results` (override with `SCIRUN_RESULTS_DIR`) mounted at `/scirun-results` in the node. Each Job mounts `/scirun-results/<job-id>` as a `hostPath` at the spec's `results.path`. `scirun results <job-id>` copies that folder to `./results/<job-id>` (or `--out`). Results therefore outlive the pod and the Job, and `results` works after the Job's TTL cleanup.

Limitations: kind-only (the mount is fixed at cluster creation, so an existing cluster must be recreated), local-only, and no cleanup of old results folders. Moving to a real cluster would mean option 3.

## 7b. Observability (Stage 4)

`scirun report <job-id>...` prints one row per job: requested CPU/memory, Job time (start to finish, includes startup), run time, CPU time and peak memory with utilization against the request.

Sources, joined by job ID:

- **Requests:** `scirun submit` saves `~/.scirun/results/.jobs/<job-id>.json`, so they survive Job cleanup.
- **Job time and phase:** the live Kubernetes Job (shown as `gone` after TTL cleanup).
- **Usage:** the workload writes `metrics.json` (`wall_seconds`, `cpu_seconds`, `peak_memory_bytes`) into its results folder. This is a convention workloads opt into; the Mandelbrot workload uses `getrusage`.

Why not metrics-server: it samples about every 15s, which misses short jobs, and it needs extra cluster setup. Revisit for long-running workloads.

First finding (Mandelbrot 2000x1333, single-threaded numpy): run time was 70s, 31s, 16s and 16s at 0.25, 0.5, 1 and 2 CPUs. Time scales with the CPU limit up to 1 CPU and then stops; at 2 CPUs utilization is 51% because the workload cannot use a second core. Peak memory was 223Mi regardless. A 64Mi limit gives `OOMKilled` (exit 137), shown by `scirun status`.

## 7c. Parallel jobs

A spec with `parallelism: N` becomes a Kubernetes Indexed Job (`completionMode: Indexed`, `completions: N`, `parallelism: N`). Kubernetes gives each pod `JOB_COMPLETION_INDEX`; SciRun adds `SCIRUN_TILE_COUNT` and `NODE_NAME`. Tiles are independent: no communication between pods.

Decisions:

- **Per-tile retries:** `backoffLimitPerIndex: <retries>` with `maxFailedIndexes: N`. A tile that crashes is rerun alone; one that exhausts its retries does not cancel the others, so a failed job still leaves N-1 usable tiles. The Job ends `Failed` and `status` names the failed tiles.
- **Idempotent tiles:** each tile output is written to a temp file and renamed, so a retry or a duplicate pod cannot expose a partial file. Rerunning a finished tile overwrites it with identical bytes.
- **Gather as a second Job (`<job-id>-gather`):** SciRun creates it after the tile Job succeeds, in the same image and on the same results folder, running the spec's `gather.args`. The gather step verifies that every tile exists and that the row count matches before writing the final file, and exits non-zero otherwise. Chosen over stitching inside the CLI to keep workload-specific logic out of `scirun` and to exercise real Job-to-Job sequencing.
- **No controller:** the CLI drives the sequence. `submit --follow` watches the tile Job, then submits gather. If the CLI exits, the tiles still finish, and `scirun gather <job-id>` resumes from the saved spec in `.jobs/<id>.json`. It is safe to repeat: an existing successful gather Job is reused, a failed one is replaced.
- **Phase of an Indexed Job:** `status.succeeded` counts finished pods, so a job is `Succeeded` only once Kubernetes sets the `Complete` condition.
- **Stragglers:** a running tile is flagged slow when it has run longer than twice the median of finished tiles (at least 3 finished tiles and 5s). This is a heuristic, shown by `status`, not acted on automatically.
- **Naming:** spec names are limited to 40 characters so `<name>-<6 hex>-gather` stays within the 63-character label limit.
- **Tiling:** the Mandelbrot workload splits the image into horizontal strips of near-equal row count. The strips are not equal work (the middle of the set takes the full iteration count), which makes them a natural source of stragglers.

Measured on one kind node (3000x2000, 400 iterations): 50.0s single pod vs 16.0s for 8 tiles (3.1x job time), with compute time 46.4s vs 7.9s (5.9x). The gap is pod startup and scheduling overhead plus load imbalance.

## 7d. Reproducibility

Goal: be able to say what produced a result, rerun it, and check that the rerun matches.

- **Record at submit:** `.jobs/<job-id>.json` holds the full spec plus a `provenance` block: UTC time, SciRun version, git commit and dirty flag of the repo containing the spec file, SHA-256 of the spec file, and the Docker image ID. Git and Docker lookups are best-effort subprocess calls (no shell, 5s timeout); a missing tool leaves the field empty rather than failing the submit. `git status` runs with `core.fsmonitor=false` so reading a repo's state cannot execute a configured program.
- **Image identity:** images are loaded into kind from the local Docker daemon, not a registry, so `docker image inspect` is the available source of an ID. A registry digest would replace it for a real cluster.
- **`rerun`:** rebuilds the spec from the record, not from the YAML file, so editing the file afterwards cannot change what is rerun. It inherits the original's spec-file and git info (they describe where the spec came from) and records `rerun_of`. It warns when the image ID changed.
- **`verify`:** hashes each output file (SHA-256, streamed, symlinks skipped) and compares by relative path. Ignored by default: `metrics.json` and `*.attempts` (differ run to run) and `tiles/*` (intermediate pieces that exist only in parallel runs). The ignore set for a comparison is the union of both jobs' lists, so one job's custom list cannot hide a mismatch in the other.
- **Found while testing:** the first version compared everything and reported a 1-pod run and an 8-tile run as different even though the final image was byte-identical, because only the parallel run kept tile files. That is why `tiles/*` is ignored by default.
- **Not covered:** numeric tolerance for floating-point output, hashing of input data, and capturing the Python/library environment inside the image beyond its ID.

## 7e. Parameter sweeps

`scirun sweep <spec> --vary name=v1,v2 [--vary ...]` expands one spec into a grid of runs and compares them.

- **Expansion on the raw YAML:** each combination edits the loaded spec mapping and then goes through the normal `parse_spec`, so a sweep gets exactly the same validation as `submit`, and every value is checked before anything is submitted. This is also why `resources.cpu` and `resources.memory` are now validated as positive Kubernetes quantities in the spec parser: a typo used to surface only as an API error, possibly halfway through a sweep.
- **What can vary:** `cpu`, `memory`, `parallelism`, `retries`. Not the workload's own arguments: sweeping those would need per-workload knowledge, and the point here is how the platform settings affect a fixed computation. At most 16 runs.
- **Sequential by default:** runs share one cluster, so running them together would measure contention, not the setting. The cost is wall-clock time; `--dry-run` shows the plan first.
- **A failure does not stop the sweep:** the failed run is listed (red dot) and the exit code is 1 at the end. Ctrl-C stops after the current run and still prints the table for what finished.
- **`parallelism=1`** removes the `gather` block, since a single pod has nothing to combine.
- **Speedup baseline:** the first successful run. TIME includes the gather job's duration for parallel runs, so the comparison does not flatter them; COMPUTE shows the workload's own time to expose overhead.
- **No sweep state file:** each run is an ordinary job with its own record, and the final hint prints a `scirun report` line to reproduce the table later. Simpler than a second record type.
- **Measured (3000x2000, 4-node kind):** parallelism 1, 2, 4, 8 gave TIME 50, 30, 20, 14s (1.0x to 3.6x) and COMPUTE 46.3, 23.2, 13.8, 7.6s. CPU utilization fell from 100% to 82% as strips became uneven.

## 8. Repository layout

```text
scirun/
├── README.md
├── pyproject.toml
├── src/scirun/{__init__,cli,config}.py
├── src/scirun/kubernetes/{jobs,logs}.py
├── workloads/mandelbrot/{main.py,Dockerfile,requirements.txt}
├── examples/mandelbrot.yaml
├── tests/
└── docs/DESIGN.md
```

## 9. Milestones

| Stage | Deliverable | Done when |
|---|---|---|
| 0 | Mandelbrot workload + Dockerfile | Runs locally and in Docker, produces image |
| 1 | Manual K8s Job manifest | Job completes on kind; status and logs inspected with kubectl; failure case observed |
| 2 | CLI: submit/status/logs | Works against kind with the example spec |
| 3 | `scirun results` | Output file lands on local disk reliably |
| 4 | Observability | Duration and resource requests reported; compare configs |

## 10. Definition of a working V1

From a clean checkout, with kind running, a user can:

1. `scirun submit examples/mandelbrot.yaml`
2. See the job reach `Succeeded` with `scirun status`
3. Read output with `scirun logs`
4. Get the generated image with `scirun results`

and failed jobs report `Failed` with a useful message. Basic unit tests cover spec parsing and Job manifest generation.

## 11. Risks / open questions

- Result retrieval design (section 7)
- Job ID scheme (Job name vs generated UUID label)
- Docker Desktop memory footprint on the dev Mac
- Image distribution to kind (`kind load docker-image` vs local registry)
