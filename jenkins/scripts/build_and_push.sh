#!/usr/bin/env bash
# Build the 3 images for linux/amd64 (the EKS nodes) and push them to ECR.
#   jenkins/scripts/build_and_push.sh            # tag = current git commit
#   IMAGE_TAG=v1.2.0 jenkins/scripts/build_and_push.sh
# Needs: docker (with buildx), AWS CLI v2 logged in (aws sts get-caller-identity works).
set -euo pipefail
cd "$(dirname "$0")/../.."

tf() { terraform -chdir=infra output -raw "$1" 2>/dev/null || true; }
AWS_REGION=${AWS_REGION:-$(tf region)}
AWS_REGION=${AWS_REGION:-ap-south-1}
ACCOUNT_ID=${ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text)}
ECR_REGISTRY=${ECR_REGISTRY:-$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com}
IMAGE_TAG=${IMAGE_TAG:-$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)}
PLATFORM=${PLATFORM:-linux/amd64}   # Apple Silicon Macs build arm64 by default; EKS nodes are x86_64

if [[ -n "$(git status --porcelain 2>/dev/null)" && "${ALLOW_DIRTY:-false}" != "true" && -z "${CI:-}" ]]; then
  echo "Uncommitted changes: the image tag $IMAGE_TAG would not match the code. Commit first, or ALLOW_DIRTY=true." >&2
  exit 1
fi

echo "==> ECR login ($ECR_REGISTRY)"
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$ECR_REGISTRY"
docker buildx inspect insightly >/dev/null 2>&1 || docker buildx create --name insightly --use >/dev/null
docker buildx use insightly

for image in api ui ingest; do
  repo="insightly-$image"
  if aws ecr describe-images --region "$AWS_REGION" --repository-name "$repo" --image-ids imageTag="$IMAGE_TAG" >/dev/null 2>&1; then
    echo "==> $repo:$IMAGE_TAG already in ECR (tags are immutable), skipping"
    continue
  fi
  echo "==> building $repo:$IMAGE_TAG for $PLATFORM"
  docker buildx build --platform "$PLATFORM" -f "docker/Dockerfile.$image" \
    -t "$ECR_REGISTRY/$repo:$IMAGE_TAG" --provenance=false --push .
done

echo "==> scan results (HIGH/CRITICAL findings)"
for image in api ui ingest; do
  aws ecr wait image-scan-complete --region "$AWS_REGION" --repository-name "insightly-$image" --image-id imageTag="$IMAGE_TAG" 2>/dev/null || true
  aws ecr describe-image-scan-findings --region "$AWS_REGION" --repository-name "insightly-$image" --image-id imageTag="$IMAGE_TAG" \
    --query 'imageScanFindings.findingSeverityCounts' --output json 2>/dev/null || true
done
echo "$IMAGE_TAG" > .image-tag
echo "==> pushed tag $IMAGE_TAG"
