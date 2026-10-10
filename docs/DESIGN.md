# SciRun Design (v0 draft)

> Everything here is **planned**. Update this document as decisions change.

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
command: ["python", "main.py", "--width", "2000", "--out", "/results/mandelbrot.png"]
resources:
  cpu: "1"
  memory: 1Gi
results:
  path: /results
```

Open question: field names are provisional and will be finalized in Stage 2.

## 6. CLI commands (planned)

| Command | Behavior |
|---|---|
| `scirun submit <spec.yaml>` | Validate spec, create Job, print job ID |
| `scirun status <job-id>` | Show phase, start/end time, duration |
| `scirun logs <job-id> [--follow]` | Print or stream pod logs |
| `scirun results <job-id> [--out DIR]` | Copy result files to local disk |

Possible later: `list`, `delete`.

## 7. Results handling (open question)

V1 needs a simple, reliable mechanism. Candidates:

1. `kubectl cp`-style copy from a completed pod (pod must still exist; fragile).
2. Shared hostPath/extraMounts volume between the kind node and the Mac (simple locally, not portable).
3. Object storage (MinIO) in-cluster (robust, more moving parts).

Leaning toward option 2 for V1, decided in Stage 3.

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
