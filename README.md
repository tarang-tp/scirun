# SciRun

**Run scientific computations on Kubernetes without writing Kubernetes.**

Describe an experiment in a small YAML file, and SciRun runs it as a Kubernetes Job, watches it, retrieves the results, and records exactly what produced them. It can also split a job across many pods, retry the pieces that crash, and sweep a computation over different resource settings to show how it scales.

```text
$ scirun submit examples/mandelbrot-parallel.yaml --follow
● Submitted mandelbrot-91659f
  ⎿  mandelbrot · scirun/mandelbrot:0.3 · 8 tiles · 1 CPU · 1Gi
● mandelbrot-91659f  ███████████████░░░░░  6/8 tiles · 12s
● mandelbrot-91659f-gather  Succeeded gather · 3s
  ⎿  fetch output with: scirun results mandelbrot-91659f
```

> **Status: pre-alpha.** SciRun works end to end on a **local [kind](https://kind.sigs.k8s.io/) cluster**, and that is the only setup it targets. It has been developed and tested on macOS (Apple Silicon) with Docker Desktop. Linux is untested, and it is not meant for shared or production clusters (see [Limitations](#limitations)). The interface may change.

## What it does

- **Submit and track:** `submit`, `status`, `logs`, `results`, `list`, `delete`, with colored terminal output that stays plain when piped.
- **Parallel jobs:** split a job into tiles, one pod each. Crashed tiles retry on their own, slow tiles are flagged, and a gather step combines the pieces.
- **Reproducible runs:** every job records its spec, git commit, image ID and output checksums. `rerun` resubmits the saved spec and `verify` checks that two runs produced byte-identical outputs.
- **Parameter sweeps:** run one spec over a grid of CPU, memory or parallelism settings and get a comparison table with speedups.
- **Resource reports:** requested versus used CPU and memory, per job.

## Quick start

You need Docker Desktop (running) and a few command-line tools:

```bash
brew install git python@3.12 uv kubectl kind
```

```bash
git clone https://github.com/tarang-tp/scirun.git && cd scirun
uv sync
./scripts/create-cluster.sh                     # local kind cluster: 1 control plane + 3 workers
docker build -t scirun/mandelbrot:0.3 workloads/mandelbrot
kind load docker-image scirun/mandelbrot:0.3 --name scirun
uv run scirun submit examples/mandelbrot.yaml
```

`submit` prints a job ID. Then:

```bash
uv run scirun status <job-id>
uv run scirun logs <job-id>
uv run scirun results <job-id>      # copies the PNG to ./results/<job-id>/
```

To type `scirun` instead of `uv run scirun`, run `source .venv/bin/activate` first. The examples below assume you have.

Things worth knowing:

- SciRun uses your **current kubectl context** and the `default` namespace. Run `scirun` with no arguments to see which cluster it will use.
- Create the cluster with `scripts/create-cluster.sh`. It mounts a folder on your Mac (`~/.scirun/results`, or `SCIRUN_RESULTS_DIR`) into every node so results outlive pods, and kind can only set that up when the cluster is created.
- Four nodes use a fair amount of Docker's memory. `SCIRUN_WORKERS=0 ./scripts/create-cluster.sh` makes a single node instead.

## Commands

| Command | What it does |
|---|---|
| `scirun submit <spec> [-f]` | Run a spec as a Job. `-f` watches it finish, and runs the gather step for parallel jobs |
| `scirun status <job-id> [--tiles]` | Phase, timing, failure reason; `--tiles` shows one row per tile |
| `scirun logs <job-id> [-f]` | Print or stream container logs |
| `scirun results <job-id> [--out DIR]` | Copy output files to local disk |
| `scirun list` | Jobs on the cluster, newest first |
| `scirun report <job-id>...` | Requested resources, timings and measured usage, side by side |
| `scirun delete <job-id>... [--results]` | Remove jobs; `--results` also deletes the output files |
| `scirun gather <job-id>` | (Re)run the combine step of a parallel job. Safe to repeat |
| `scirun show <job-id>` | Provenance and a SHA-256 for every output file |
| `scirun rerun <job-id> [-f]` | Resubmit the exact spec a job ran with |
| `scirun verify <job-a> <job-b>` | Do two runs have byte-identical outputs? |
| `scirun sweep <spec> --vary k=a,b` | Run a grid of settings one at a time and compare them |

Output is colored in a terminal; set `NO_COLOR=1` to turn that off. `submit` prints only the job ID when piped and `logs` is always raw, so both are safe in scripts.

## Experiment spec

```yaml
name: mandelbrot
image: scirun/mandelbrot:0.3
args: ["--width", 3000, "--height", 2000, "--max-iter", 400, "--out", "/results/mandelbrot.png"]
resources:          # applied as both requests and limits, per pod
  cpu: "1"
  memory: 1Gi
results:
  path: /results    # files written here are what `scirun results` returns
parallelism: 8      # optional: split across 8 pods (default 1)
retries: 2          # optional: extra attempts per tile (default 2)
gather:             # optional, needs parallelism > 1: args for the combine step
  args: ["--gather", "--width", 3000, "--height", 2000, "--out", "/results/mandelbrot.png"]
```

`command` (optional) overrides the image entrypoint, and `verify_ignore` (optional) lists output patterns that `verify` skips. Unknown fields and invalid CPU or memory values are rejected up front. More in [examples/](examples/).

## Parallel jobs

With `parallelism: N`, SciRun runs an Indexed Job: N pods, each told its tile number (`JOB_COMPLETION_INDEX`) and the tile count (`SCIRUN_TILE_COUNT`). When all tiles succeed, a second small job runs the spec's `gather` args to combine them.

- **Crashes are contained.** Each tile retries independently, and finished tiles are never redone. A tile that runs out of retries is marked failed, the others still finish, and the gather step refuses to combine incomplete data.
- **Retries are safe.** The Mandelbrot workload writes every file atomically (temp file, then rename), so a crashed or duplicated pod cannot leave a half-written tile.
- **Slow tiles are flagged** in `scirun status` when one runs much longer than the finished ones.
- **You can walk away.** If you close the terminal, the tiles keep running, and `scirun gather <job-id>` finishes the job later.

```text
$ scirun status mandelbrot-db740b --tiles
● mandelbrot-db740b  Succeeded
  ⎿  8/8 tiles done
  ⎿  started 12:45:36 · ran 11s
  ⎿  gather: Succeeded

TILE  STATE  TRIES  NODE            TIME
0     done   1      scirun-worker   5s
1     done   1      scirun-worker2  5s
2     done   1      scirun-worker3  7s
3     done   1      scirun-worker   8s
4     done   1      scirun-worker3  8s
5     done   1      scirun-worker   7s
6     done   1      scirun-worker2  5s
7     done   1      scirun-worker2  5s
```

The Mandelbrot workload has flags for trying out failures: `--fail-tiles 2,5 --fail-attempts 1` crashes those tiles on their first attempt, and `--slow-tile 4 --slow-seconds 40` slows one down.

## Reproducible runs

Every `submit` saves a record next to the results (`~/.scirun/results/.jobs/<job-id>.json`): the full spec, the git commit and dirty flag of the repo containing the spec, the spec file's SHA-256, the local Docker image ID, and the SciRun version.

```bash
scirun show <job-id>              # provenance + a checksum for each output file
scirun rerun <job-id> --follow    # resubmit the saved spec, even if the YAML has changed since
scirun verify <job-a> <job-b>     # compare outputs byte for byte; exits 1 if they differ
```

```text
● Identical  mandelbrot-8c6b0c = mandelbrot-e5f65e
  ⎿  1 files, all byte-for-byte equal
```

That example compares a single-pod run with an 8-tile run: the stitched image matches the single render exactly. `verify` skips files that legitimately differ between identical runs (timings, retry counters, intermediate tiles). It compares bytes, so a workload with hardware-dependent floating point can differ between runs; there is no numeric tolerance yet.

## Parameter sweeps

```bash
scirun sweep examples/mandelbrot-parallel.yaml --vary parallelism=1,2,4,8
scirun sweep spec.yaml --vary cpu=0.5,1 --vary memory=512Mi,1Gi    # several --vary make a grid
scirun sweep spec.yaml --vary parallelism=1,2,4 --dry-run           # show the plan only
```

```text
SETTINGS         JOB                TIME   SPEEDUP  COMPUTE  CPU   MEM
● parallelism=1  mandelbrot-3bf626  50.0s  1.0x     46.3s    100%  419Mi
● parallelism=2  mandelbrot-8b4607  30.0s  1.7x     23.2s    101%  248Mi
● parallelism=4  mandelbrot-b806e5  20.0s  2.5x     13.8s    87%   141Mi
● parallelism=8  mandelbrot-f57705  14.0s  3.6x     7.6s     82%   87Mi
```

*Measured on one Apple Silicon laptop with a 4-node kind cluster; your numbers will differ.* TIME is submit to finish, including pod startup and the gather step. COMPUTE is the workload's own time. 8 pods cut compute about 6x but total time only 3.6x, because starting pods costs time and the strips are uneven (the middle of the image is the expensive part).

You can vary `cpu`, `memory`, `parallelism` and `retries`, up to 16 runs. Runs go one at a time on purpose, so they don't compete for CPUs and distort each other's timings. A failed run is shown in the table and the sweep carries on.

## Bring your own workload

SciRun runs any container image. For the basics, write output files to the spec's `results.path`. To go further:

- **Show up in `scirun report`:** also write a `metrics.json` there with `wall_seconds`, `cpu_seconds` and `peak_memory_bytes`.
- **Run in parallel:** read `JOB_COMPLETION_INDEX` and `SCIRUN_TILE_COUNT`, write each tile atomically, and provide a gather mode that checks every tile exists before combining them. SciRun cannot split an arbitrary program for you; the workload has to be written so its work divides.
- **Get your image into the cluster:** build it locally and `kind load docker-image <image> --name scirun`. There is no registry.

[workloads/mandelbrot/main.py](workloads/mandelbrot/main.py) is a complete example of all three.

## How it works

```text
scirun CLI (Python, Typer)  ->  Kubernetes API  ->  Job (or Indexed Job + gather Job)  ->  your container
```

SciRun keeps no server or database. Job state lives in Kubernetes (labels on the Job), and a small JSON record per job lives beside the results on disk. The CLI drives the tile-then-gather sequence itself, and can pick it up again from the saved record. The design, the choices behind it and the measurements are in [docs/DESIGN.md](docs/DESIGN.md).

## Limitations

- **Local kind clusters only.** Results use a host-mounted folder (`hostPath`), which is fine for a laptop and wrong for a shared or production cluster. Do not point SciRun at a cluster you care about.
- **Whatever your kubectl context says.** SciRun does not ask which cluster to use. `list` and `delete` only touch jobs SciRun created, but `submit` will run on the current context.
- **CPU workloads, one container per job.** No GPUs, Slurm integration, autoscaling or web dashboard.
- **Parallelism is independent tiles plus a combine step.** No communication between pods, so no MPI-style jobs.
- **Usage numbers come from the workload itself**, not cluster monitoring, so a workload that does not write `metrics.json` shows none.
- **Only the Mandelbrot workload ships.** It exists to exercise SciRun; the interesting part is the harness around it.
- Tested on macOS (Apple Silicon) only.

## Development

```bash
uv sync
uv run pytest        # unit tests, including running the workload as a subprocess
uv run ruff check .
```

The tests need no cluster. The end-to-end behavior described above was checked by hand against a kind cluster.

## License

[MIT](LICENSE)
