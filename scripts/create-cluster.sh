#!/usr/bin/env bash
# Create the local kind cluster used by SciRun, with a Mac folder mounted into the node
# so job results survive pod deletion. Safe to re-run: refuses if the cluster exists.
set -euo pipefail

NAME="${SCIRUN_CLUSTER:-scirun}"
RESULTS_DIR="${SCIRUN_RESULTS_DIR:-$HOME/.scirun/results}"
NODE_PATH="/scirun-results"   # keep in sync with NODE_RESULTS_ROOT in src/scirun/config.py

if kind get clusters 2>/dev/null | grep -qx "$NAME"; then
  echo "kind cluster '$NAME' already exists. To rebuild it: kind delete cluster --name $NAME" >&2
  exit 1
fi

mkdir -p "$RESULTS_DIR"
kind create cluster --name "$NAME" --config - <<YAML
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    extraMounts:
      - hostPath: $RESULTS_DIR
        containerPath: $NODE_PATH
YAML
