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
scirun show <job-id>                     # provenance: spec, git commit, image, output checksums
scirun rerun <job-id> [-f]               # resubmit the exact same spec
scirun verify <job-a> <job-b>            # did two runs produce byte-identical outputs?
scirun sweep <spec> --vary k=a,b         # run a grid of settings and compare them
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

## Reproducible runs

Every `submit` saves what produced the result next to it, in `~/.scirun/results/.jobs/<job-id>.json`: the full spec, the git commit and dirty state of the repo the spec lives in, the spec file's SHA-256, the local Docker image ID, and the SciRun version.

```bash
scirun show <job-id>              # provenance + a SHA-256 for every output file
scirun rerun <job-id> --follow    # resubmit the saved spec, even if the YAML file has since changed
scirun verify <job-a> <job-b>     # compare outputs byte for byte
```

```text
● Identical  mandelbrot-8c6b0c = mandelbrot-e5f65e
  ⎿  1 files, all byte-for-byte equal
```

That example compares a 1-pod run with an 8-tile run: the stitched image is the same file. `verify` skips files that legitimately differ between identical runs (`metrics.json`, retry counters, and the intermediate `tiles/` folder). Override with `verify_ignore: [patterns]` in the spec. `rerun` warns if the local image has changed since the original run.

Limits: `verify` compares bytes, so a workload with non-deterministic or hardware-dependent floating point can differ between identical-looking runs, and there is no numeric tolerance yet. The image ID comes from the local Docker daemon, so it is recorded only when `docker` is available; git info only when the spec is inside a git repository.

## Parameter sweeps

`scirun sweep` runs one spec under several settings, one run at a time, and ends with a comparison table.

```bash
scirun sweep examples/mandelbrot-parallel.yaml --vary parallelism=1,2,4,8
scirun sweep spec.yaml --vary cpu=0.5,1 --vary memory=512Mi,1Gi   # several --vary make a grid
scirun sweep spec.yaml --vary parallelism=1,2,4 --dry-run          # show the plan, submit nothing
```

```text
SETTINGS         JOB                TIME   SPEEDUP  COMPUTE  CPU   MEM
● parallelism=1  mandelbrot-3bf626  50.0s  1.0x     46.3s    100%  419Mi
● parallelism=2  mandelbrot-8b4607  30.0s  1.7x     23.2s    101%  248Mi
● parallelism=4  mandelbrot-b806e5  20.0s  2.5x     13.8s    87%   141Mi
● parallelism=8  mandelbrot-f57705  14.0s  3.6x     7.6s     82%   87Mi
```

Reading it: TIME runs from submit to finish and includes pod startup and the gather step; COMPUTE is the workload's own time. Doubling the pods does not halve TIME: 8 pods cut compute from 46s to 7.6s (6x) but TIME only 3.6x, because of startup overhead and uneven strips. CPU is how much of the requested CPUs the workload actually used.

Details: variables are `cpu`, `memory`, `parallelism` and `retries`; at most 16 runs; a failed run is shown and the sweep carries on. Runs are sequential on purpose, since concurrent jobs would share the cluster's CPUs and distort each other's timings. Every run is a normal job with its own record, so `scirun show`, `rerun` and `verify` work on them, and `scirun report <ids>` reprints the comparison. Setting `parallelism=1` drops the gather step automatically.

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
verify_ignore: ["metrics.json", "*.attempts", "tiles/*"]   # optional: outputs `scirun verify` skips (this is the default)
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
Also done: `list`, `delete`, parallel jobs with per-tile retries and a gather step, reproducible runs (`show`, `rerun`, `verify`). parameter sweeps (`sweep`). Possible next steps: cluster-level metrics for long jobs, object-storage results for real clusters.

## License

[MIT](LICENSE)
