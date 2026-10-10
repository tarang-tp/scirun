#!/usr/bin/env bash
# Create the local kind cluster used by SciRun, with a Mac folder mounted into every node
# so job results survive pod deletion. Refuses if the cluster already exists.
#
#   SCIRUN_WORKERS=3   worker nodes besides the control plane (default 3; 0 = single node)
#   SCIRUN_CLUSTER     cluster name (default scirun)
#   SCIRUN_RESULTS_DIR results folder on the Mac (default ~/.scirun/results)
set -euo pipefail

NAME="${SCIRUN_CLUSTER:-scirun}"
WORKERS="${SCIRUN_WORKERS:-3}"
RESULTS_DIR="${SCIRUN_RESULTS_DIR:-$HOME/.scirun/results}"
NODE_PATH="/scirun-results"   # keep in sync with NODE_RESULTS_ROOT in src/scirun/config.py

if kind get clusters 2>/dev/null | grep -qx "$NAME"; then
  echo "kind cluster '$NAME' already exists. To rebuild it: kind delete cluster --name $NAME" >&2
  exit 1
fi

mkdir -p "$RESULTS_DIR"

node() {  # one node block; every node mounts the same Mac folder
  cat <<YAML
  - role: $1
    extraMounts:
      - hostPath: $RESULTS_DIR
        containerPath: $NODE_PATH
YAML
}

{
  printf 'kind: Cluster\napiVersion: kind.x-k8s.io/v1alpha4\nnodes:\n'
  node control-plane
  for _ in $(seq 1 "$WORKERS"); do node worker; done
} | kind create cluster --name "$NAME" --config -
