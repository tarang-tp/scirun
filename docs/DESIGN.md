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
