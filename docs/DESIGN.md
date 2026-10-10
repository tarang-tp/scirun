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

Possible later: `list`, `delete`.

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
