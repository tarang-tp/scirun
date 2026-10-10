# SciRun

**A CLI for running and managing scientific workloads on Kubernetes.**

> **Status: pre-alpha, V1 works on a local kind cluster.** SciRun is a learning/systems project. It targets one local cluster and CPU workloads only. See [docs/DESIGN.md](docs/DESIGN.md) for the architecture and decisions.

## Why SciRun

Running a containerized scientific computation on Kubernetes normally means hand-writing Job manifests and juggling `kubectl` for status, logs, results and cleanup. SciRun hides that: describe an experiment in a small YAML file, submit it, and get results back.

## Usage

```bash
scirun submit examples/mandelbrot.yaml   # create a Kubernetes Job, prints the job ID
scirun submit <spec> --follow            # ...and watch it finish (runs the gather step too)
scirun status <job-id>                   # phase, start time, duration, failure detail
scirun logs <job-id> [-f]                # print or stream container logs
scirun results <job-id> [--out DIR]      # copy output files to ./results/<job-id>
scirun report <job-id> [<job-id> ...]    # compare requested resources, timings, usage
scirun list                              # jobs on the cluster, newest first
scirun delete <job-id>... [--results]    # remove jobs; --results also deletes output files
scirun gather <job-id>                   # (re)run the combine step of a parallel job
scirun status <job-id> --tiles           # one row per tile of a parallel job
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

## Parallel jobs

Setting `parallelism` splits a job across that many pods. Kubernetes runs it as an Indexed Job: each pod gets its own index and works on its own slice (for the Mandelbrot workload, one horizontal strip of the image). When every tile has finished, a second small job (`gather`) combines them.

```bash
scirun submit examples/mandelbrot-parallel.yaml --follow
```

```text
● mandelbrot-91659f  ███████████████░░░░░  6/8 tiles · 12s
● mandelbrot-91659f-gather  Succeeded gather · 3s
```

What it handles:

- **Crashes:** each tile retries on its own (`retries`, default 2). Finished tiles are never redone. A tile that runs out of retries is marked failed, the others still finish, and the gather step refuses to run on incomplete data.
- **Safe retries:** workloads write files atomically (temp file, then rename), so a crashed or repeated pod can't leave a half-written tile.
- **Stragglers:** `scirun status <job-id>` flags a running tile that is much slower than the finished ones.
- **Walking away:** if you close the terminal, the tiles keep running. `scirun gather <job-id>` finishes the job later, and is safe to repeat.

Example, 3000x2000 render, 8 tiles, on one kind node: 50s for a single pod and 16s for 8 pods (about 3.1x). The compute itself dropped from 46s to 8s, but starting 8 pods and the uneven strips (the middle of the image is the slow part) eat much of the gain. A 1-CPU pod can't use more than one core, so the speedup comes only from more pods.

`scirun report` shows `1×8` for 1 CPU per pod across 8 pods.

The Mandelbrot workload has failure-injection flags for experiments: `--fail-tiles 2,5 --fail-attempts 1` makes those tiles crash on their first attempt, and `--slow-tile 4 --slow-seconds 40` makes one tile slow.

## Experiment spec

```yaml
name: mandelbrot
image: scirun/mandelbrot:0.3
args: ["--width", 2000, "--height", 1333, "--max-iter", 300, "--out", "/results/mandelbrot.png"]
resources:        # applied as both requests and limits
  cpu: "1"
  memory: 1Gi
results:
  path: /results  # files written here are what `scirun results` returns
parallelism: 8    # optional: split across 8 pods (default 1)
retries: 2        # optional: extra attempts per tile (default 2)
gather:           # optional, needs parallelism > 1: args for the combine step
  args: ["--gather", "--width", 2000, "--height", 1333, "--out", "/results/mandelbrot.png"]
```

An optional `command` overrides the image entrypoint. Unknown fields are rejected. In a parallel job the workload receives `JOB_COMPLETION_INDEX` (its tile) and `SCIRUN_TILE_COUNT`, and `NODE_NAME` is set for every job.

## Setup (macOS, Apple Silicon)

Requires Docker Desktop (running), plus:

```bash
brew install git python@3.12 uv kubectl kind
```

```bash
uv sync
./scripts/create-cluster.sh                 # kind cluster "scirun": 1 control plane + 3 workers, results folder mounted
docker build -t scirun/mandelbrot:0.3 workloads/mandelbrot
kind load docker-image scirun/mandelbrot:0.3 --name scirun
uv run scirun submit examples/mandelbrot.yaml
```

Job results land in `~/.scirun/results/<job-id>/` (override with `SCIRUN_RESULTS_DIR` before creating the cluster). The cluster must be created with `scripts/create-cluster.sh`, because the folder mount can only be set when kind creates the cluster. `SCIRUN_WORKERS=0` gives a single node (lighter on Docker's memory); the default of 3 workers lets parallel jobs spread across nodes.

## Writing your own workload

Package it as a container image, load it into kind, and write output files to the spec's `results.path`. To appear in `scirun report`, also write a `metrics.json` there with `wall_seconds`, `cpu_seconds` and `peak_memory_bytes` (see `workloads/mandelbrot/main.py`). To run in parallel, read `JOB_COMPLETION_INDEX` and `SCIRUN_TILE_COUNT`, write each tile atomically, and give a `gather` mode that checks all tiles exist before combining them.

## Development

```bash
uv run pytest
uv run ruff check .
```

## Limitations

- Local kind cluster only; results use a host-mounted folder, so it is not portable to a real cluster
- CPU workloads only; parallel splitting is a convention the workload must implement (only Mandelbrot does), not something SciRun can do for an arbitrary program
- Parallel jobs are one batch of independent tiles plus a combine step; no tile-to-tile communication (no MPI-style jobs)
- No GPUs, Slurm, autoscaling, web dashboard, or scheduling beyond what Kubernetes provides
- Old results folders are only removed by `scirun delete --results`
- Usage metrics come from the workload itself, not from cluster monitoring

## Roadmap

Done: Mandelbrot workload, Kubernetes Job, CLI (`submit`, `status`, `logs`), results retrieval, resource report.
Also done: `list`, `delete`, parallel jobs with per-tile retries and a gather step. Possible next steps: cluster-level metrics for long jobs, object-storage results for real clusters.

## License

[MIT](LICENSE)
