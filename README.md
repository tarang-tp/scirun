# SciRun

**A CLI for running and managing scientific workloads on Kubernetes.**

> **Status: pre-alpha, V1 works on a local kind cluster.** SciRun is a learning/systems project. It targets one local cluster and CPU workloads only. See [docs/DESIGN.md](docs/DESIGN.md) for the architecture and decisions.

## Why SciRun

Running a containerized scientific computation on Kubernetes normally means hand-writing Job manifests and juggling `kubectl` for status, logs, results and cleanup. SciRun hides that: describe an experiment in a small YAML file, submit it, and get results back.

## Usage

```bash
scirun submit examples/mandelbrot.yaml   # create a Kubernetes Job, prints the job ID
scirun status <job-id>                   # phase, start time, duration, failure detail
scirun logs <job-id> [-f]                # print or stream container logs
scirun results <job-id> [--out DIR]      # copy output files to ./results/<job-id>
scirun report <job-id> [<job-id> ...]    # compare requested resources, timings, usage
scirun list                              # jobs on the cluster, newest first
scirun delete <job-id>... [--results]    # remove jobs; --results also deletes output files
```

Example report, running the same workload at different CPU limits:

```text
JOB                PHASE      CPU   MEM  TIME   RUN    CPU USED      MEM USED
mandelbrot-87eda2  Succeeded  0.25  1Gi  74.0s  70.2s  17.7s (101%)  223Mi (22%)
mandelbrot-155a53  Succeeded  0.5   1Gi  35.0s  31.4s  15.8s (101%)  223Mi (22%)
mandelbrot-97a503  Succeeded  1     1Gi  19.0s  15.7s  15.9s (101%)  223Mi (22%)
mandelbrot-1814f2  Succeeded  2     1Gi  19.0s  15.6s  16.1s (51%)   223Mi (22%)
```

Output is colored (purple/green/grey) in a terminal. Set `NO_COLOR=1` to turn that off. `scirun submit` prints only the job ID when piped, and `scirun logs` is always raw, so both are safe in scripts. Running `scirun` with no arguments shows a welcome banner.

## Experiment spec

```yaml
name: mandelbrot
image: scirun/mandelbrot:0.2
args: ["--width", 2000, "--height", 1333, "--max-iter", 300, "--out", "/results/mandelbrot.png"]
resources:        # applied as both requests and limits
  cpu: "1"
  memory: 1Gi
results:
  path: /results  # files written here are what `scirun results` returns
```

An optional `command` overrides the image entrypoint. Unknown fields are rejected.

## Setup (macOS, Apple Silicon)

Requires Docker Desktop (running), plus:

```bash
brew install git python@3.12 uv kubectl kind
```

```bash
uv sync
./scripts/create-cluster.sh                 # kind cluster "scirun" with a results folder mounted
docker build -t scirun/mandelbrot:0.2 workloads/mandelbrot
kind load docker-image scirun/mandelbrot:0.2 --name scirun
uv run scirun submit examples/mandelbrot.yaml
```

Job results land in `~/.scirun/results/<job-id>/` (override with `SCIRUN_RESULTS_DIR` before creating the cluster). The cluster must be created with `scripts/create-cluster.sh`, because the folder mount can only be set when kind creates the cluster.

## Writing your own workload

Package it as a container image, load it into kind, and write output files to the spec's `results.path`. To appear in `scirun report`, also write a `metrics.json` there with `wall_seconds`, `cpu_seconds` and `peak_memory_bytes` (see `workloads/mandelbrot/main.py`).

## Development

```bash
uv run pytest
uv run ruff check .
```

## Limitations

- Local kind cluster only; results use a host-mounted folder, so it is not portable to a real cluster
- CPU workloads only; one container per job
- No GPUs, Slurm, autoscaling, web dashboard, or scheduling beyond what Kubernetes provides
- Old results folders are only removed by `scirun delete --results`
- Usage metrics come from the workload itself, not from cluster monitoring

## Roadmap

Done: Mandelbrot workload, Kubernetes Job, CLI (`submit`, `status`, `logs`), results retrieval, resource report.
Also done: `list` and `delete`. Possible next steps: cluster-level metrics for long jobs, object-storage results for real clusters.

## License

TBD.
