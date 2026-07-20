# OpenShift Deployment

Deploy cap-evolve on OpenShift with GPU-accelerated model serving via [vLLM](https://github.com/vllm-project/vllm). Both the agent and optimizer models run on-cluster — no external API keys required.

## Architecture

```
┌───────────────────────────────────────────────────┐
│  OpenShift  (namespace: cap-evolve)                │
│                                                    │
│  vLLM Agent (7B, 1 GPU) ◄── cap-evolve Runner ──► vLLM Optimizer (14B, 2 GPU)
│                                                    │
└───────────────────────────────────────────────────┘
```

| Component | Role | Resources |
|-----------|------|-----------|
| **vLLM Agent** | Executes benchmark tasks (the model being optimized) | 1 GPU |
| **vLLM Optimizer** | Analyzes failures, proposes skill improvements | 2 GPUs |
| **Runner** | Runs the `cap-evolve` optimization loop | CPU only |

## Prerequisites

- `oc` CLI logged into your cluster
- GPU nodes with the [NVIDIA GPU Operator](https://docs.nvidia.com/datacenter/cloud-native/openshift/latest/index.html) (minimum 3 GPUs)
- Container registry ([Quay.io](https://quay.io), GHCR, or internal)
- [HuggingFace token](https://huggingface.co/settings/tokens) (for gated models)

## Deploy

All commands run from the repo root. Replace `<your-org>` with your registry organization.

```bash
# 1. Namespace and RBAC
oc apply -f openshift/manifests/namespace.yaml
oc apply -f openshift/manifests/service-account.yaml

# 2. Secrets
oc create secret generic hf-token \
  --from-literal=HF_TOKEN=<YOUR_HF_TOKEN> \
  -n cap-evolve

# 3. Model servers
oc apply -f openshift/manifests/vllm-serving.yaml      # agent  (7B, 1 GPU)
oc apply -f openshift/manifests/vllm-optimizer.yaml     # optimizer (14B, 2 GPUs)

# Wait for models to load (5-15 min on first deploy)
oc get pods -n cap-evolve -l component=vllm -w

# 4. Build and push the runner image
podman login quay.io
podman build -t quay.io/<your-org>/cap-evolve-runner:latest \
  -f openshift/images/cap-evolve-runner/Dockerfile .
podman push quay.io/<your-org>/cap-evolve-runner:latest

# 5. Copy your project config onto the workspace PVC
#    (.capevolve/project/ is generated locally by the intake/scaffold phase —
#    it contains your adapter, capevolve.yaml, and seed capability.
#    See docs/OPTIMIZE_YOUR_OWN.md for how to create one.)
oc apply -f openshift/manifests/cap-evolve-runner-deployment.yaml
POD=$(oc get pod -n cap-evolve -l app=cap-evolve-runner \
  -o jsonpath='{.items[0].metadata.name}' --field-selector=status.phase=Running)
oc cp .capevolve/project cap-evolve/$POD:/workspace/.capevolve/project

# 6. Run the optimizer (update image: in the manifest first)
oc apply -f openshift/manifests/cap-evolve-runner-job.yaml
oc logs -n cap-evolve job/cap-evolve-run -f

# 7. Get results
oc cp cap-evolve/$POD:/workspace/.capevolve ./results
cat results/run_*/report.md
```

## Configuration

### Environment variables

Set in the runner Job or Deployment manifest (`env:` section).

| Variable | Example | Description |
|----------|---------|-------------|
| `TAU2_MODEL` | `openai/qwen2.5-7b-instruct` | Agent model ([litellm](https://docs.litellm.ai/) format) |
| `TAU2_API_BASE` | `http://vllm-agent:80/v1` | Agent vLLM endpoint |
| `TAU2_API_KEY` | `dummy` | vLLM accepts any value |
| `TAU2_MAX_CONCURRENCY` | `30` | Concurrent eval requests |
| `OPTIMIZER_MODEL` | `openai/qwen2.5-14b-instruct` | Optimizer model |
| `OPTIMIZER_API_BASE` | `http://vllm-optimizer:80/v1` | Optimizer vLLM endpoint |
| `OPTIMIZER_API_KEY` | `dummy` | vLLM accepts any value |

### Swapping models

1. Edit the vLLM manifest — change the model name, GPU count, and `--tensor-parallel-size`
2. Update the matching env var in the runner manifest

| Size | GPUs | `--tensor-parallel-size` | Memory request |
|------|------|--------------------------|----------------|
| 7B | 1 | 1 (default) | 8 Gi |
| 14B | 2 | 2 | 32 Gi |
| 32B+ | 4 | 4 | 64 Gi |
