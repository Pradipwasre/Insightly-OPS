#!/usr/bin/env bash
# Deploy (or update) Insightly on EKS.
#   jenkins/scripts/deploy_eks.sh                    # apps only (uses .image-tag or git commit)
#   BOOTSTRAP=true jenkins/scripts/deploy_eks.sh     # first time: also install cluster add-ons via Helm
#   RUN_INGEST=true jenkins/scripts/deploy_eks.sh    # also (re)load RDS from S3
# Needs: AWS CLI v2, kubectl, helm, python3, and `terraform apply` done in infra/.
set -euo pipefail
cd "$(dirname "$0")/../.."

tf() { terraform -chdir=infra output -raw "$1"; }
export AWS_REGION=${AWS_REGION:-$(tf region)}
export APP_ENV=${APP_ENV:-$(tf environment)}
export NAME=${NAME:-$(tf name)}
export S3_BUCKET=${S3_BUCKET:-$(tf s3_bucket)}
export ECR_REGISTRY=${ECR_REGISTRY:-$(tf ecr_registry)}
CLUSTER=${CLUSTER:-$(tf cluster_name)}
export IMAGE_TAG=${IMAGE_TAG:-$(cat .image-tag 2>/dev/null || git rev-parse --short HEAD)}
export LLM_MODEL=${LLM_MODEL:-claude-haiku-5-5}
export UI_ALLOWED_CIDR=${UI_ALLOWED_CIDR:-0.0.0.0/0}
render() { python3 jenkins/scripts/render_k8s.py "$@"; }

echo "==> kubeconfig for $CLUSTER ($AWS_REGION), image tag $IMAGE_TAG"
aws eks update-kubeconfig --region "$AWS_REGION" --name "$CLUSTER" >/dev/null
kubectl get nodes -o wide

if [[ "${BOOTSTRAP:-false}" == "true" ]] || ! kubectl get crd externalsecrets.external-secrets.io >/dev/null 2>&1; then
  echo "==> installing cluster add-ons (Helm)"
  helm repo add external-secrets https://charts.external-secrets.io >/dev/null
  helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null
  helm repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/ >/dev/null
  helm repo update >/dev/null
  # Pin with e.g. ESO_CHART_VERSION=1.1.0 for reproducible installs
  helm upgrade --install external-secrets external-secrets/external-secrets -n external-secrets --create-namespace \
    ${ESO_CHART_VERSION:+--version "$ESO_CHART_VERSION"} --set installCRDs=true --wait
  helm upgrade --install metrics-server metrics-server/metrics-server -n kube-system \
    ${METRICS_CHART_VERSION:+--version "$METRICS_CHART_VERSION"} --wait
  helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack -n monitoring --create-namespace \
    ${KPS_CHART_VERSION:+--version "$KPS_CHART_VERSION"} \
    --set grafana.adminPassword="${GRAFANA_PASSWORD:-$(openssl rand -hex 12)}" \
    --set prometheus.prometheusSpec.retention=15d --wait --timeout 10m
  kubectl -n monitoring create configmap insightly-dashboard \
    --from-file=monitoring/grafana/dashboards/insightly-overview.json --dry-run=client -o yaml \
    | kubectl label --local -f - grafana_dashboard=1 -o yaml | kubectl apply -f -
fi

echo "==> namespace, config, secrets"
render k8s/namespace.yaml k8s/configmap.yaml k8s/external-secret.yaml | kubectl apply -f -
if ! aws secretsmanager get-secret-value --region "$AWS_REGION" --secret-id "$NAME/anthropic" --query SecretString --output text >/dev/null 2>&1; then
  echo "!! The secret $NAME/anthropic has no value yet. Run: make secret-anthropic" >&2
  exit 1
fi
echo "    waiting for External Secrets to create insightly-secrets..."
kubectl -n insightly wait --for=condition=Ready externalsecret/insightly-secrets --timeout=120s

if [[ "${RUN_INGEST:-false}" == "true" ]]; then
  echo "==> ingest job (S3 -> RDS)"
  kubectl -n insightly delete job "insightly-ingest-$IMAGE_TAG" --ignore-not-found >/dev/null
  render k8s/ingest-job.yaml | kubectl apply -f -
  JOB="job/insightly-ingest-$IMAGE_TAG"
  # wait for success OR failure (kubectl wait alone would hang for an hour on a failed job)
  for _ in $(seq 1 360); do
    ok=$(kubectl -n insightly get "$JOB" -o jsonpath='{.status.succeeded}' 2>/dev/null)
    bad=$(kubectl -n insightly get "$JOB" -o jsonpath='{.status.conditions[?(@.type=="Failed")].status}' 2>/dev/null)
    [[ "$ok" == "1" ]] && break
    if [[ "$bad" == "True" ]]; then
      echo "!! ingest job failed:" >&2; kubectl -n insightly logs "$JOB" --tail=40 >&2; exit 1
    fi
    sleep 10
  done
  [[ "$ok" == "1" ]] || { echo "!! ingest job still running after 60 min" >&2; exit 1; }
  kubectl -n insightly logs "$JOB" --tail=15
fi

echo "==> api + ui"
render k8s/api-deployment.yaml k8s/api-service.yaml k8s/ui-deployment.yaml k8s/ui-service.yaml k8s/hpa.yaml | kubectl apply -f -
kubectl -n insightly rollout status deploy/insightly-api --timeout=300s
kubectl -n insightly rollout status deploy/insightly-ui --timeout=300s

if kubectl get crd servicemonitors.monitoring.coreos.com >/dev/null 2>&1; then
  echo "==> monitoring (ServiceMonitor + alert rules)"
  render k8s/servicemonitor.yaml | kubectl apply -f -
  { printf 'apiVersion: monitoring.coreos.com/v1\nkind: PrometheusRule\nmetadata:\n  name: insightly\n  namespace: insightly\n'
    printf '  labels:\n    release: kube-prometheus-stack\nspec:\n'
    sed 's/^/  /' monitoring/alertmanager/alerts.yml | grep -v '^  #'; } | kubectl apply -f -
fi

echo "==> smoke test"
kubectl -n insightly exec deploy/insightly-api -- python -c \
  "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/ready').read().decode())"

HOST=$(kubectl -n insightly get svc insightly-ui -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)
echo "==> done. UI: http://${HOST:-<load balancer still provisioning: kubectl -n insightly get svc insightly-ui -w>}"
