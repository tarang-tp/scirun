# SciRun

**A CLI for running and managing scientific workloads on Kubernetes.**

> **Status: planning / pre-alpha. Nothing described below works yet.** Every command and feature in this README is *planned*. See [docs/DESIGN.md](docs/DESIGN.md) for the engineering blueprint and milestones.

## Why SciRun

Running a containerized scientific computation on Kubernetes today means hand-writing Job manifests and juggling `kubectl` commands for status, logs, and cleanup. SciRun aims to hide that boilerplate: describe an experiment in a small YAML file, submit it, and get results back.

## Planned workflow

```bash
scirun submit experiments/mandelbrot.yaml   # create a Kubernetes Job
scirun status <job-id>                      # pending / running / succeeded / failed
scirun logs <job-id>                        # stream container logs
scirun results <job-id>                     # fetch output files locally
```

```text
User -> SciRun CLI (Python) -> Kubernetes API -> Job -> Containerized workload
```

## V1 scope (planned)

- One local Kubernetes cluster (kind)
- CPU-only workloads
- Job submission, status tracking, logs
- Simple local retrieval of results

## Explicitly out of scope for V1

Web dashboard, GPUs, Slurm integration, autoscaling, distributed scheduling.

## Planned stack

Python 3.12+, [Typer](https://typer.tiangolo.com/), the official Kubernetes Python client, uv, Docker, kind.

## Development setup (for contributors, once code exists)

```bash
brew install git python@3.12 uv kubectl kind
brew install --cask docker
kind create cluster --name scirun
kubectl get nodes
```

## Roadmap

0. Mandelbrot workload, run locally and in Docker
1. Run it as a hand-written Kubernetes Job
2. CLI: `submit`, `status`, `logs`
3. Results retrieval
4. Observability: duration, resource requests/usage

## License

TBD.
