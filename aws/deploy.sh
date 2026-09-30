#!/usr/bin/env bash
# Build the Lambda image, push it to ECR and deploy the CloudFormation stack.
#
#   aws/deploy.sh                 # deploy (or update) the stack
#   aws/deploy.sh --destroy       # delete the stack and the ECR repository
#
# Environment (all optional):
#   AWS_PROFILE      the AWS CLI profile to use
#   AWS_REGION       default: the profile's region
#   STACK_NAME       default: kerykeion-api (also the ECR repository and Lambda name)
#   EPHEMERIS_TIER   medium (1550-2650, default) | base (1850-2150, smaller image)
set -euo pipefail

cd "$(dirname "$0")/.."

STACK_NAME=${STACK_NAME:-kerykeion-api}
EPHEMERIS_TIER=${EPHEMERIS_TIER:-medium}
REGION=${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || true)}}

die() { echo "deploy: $*" >&2; exit 1; }
step() { printf '\n==> %s\n' "$*"; }

[ -n "$REGION" ] || die "no AWS region; set AWS_REGION or run 'aws configure'"
command -v aws >/dev/null || die "the AWS CLI is not installed"
# Rancher Desktop installs docker outside the default PATH.
if ! command -v docker >/dev/null && [ -x "$HOME/.rd/bin/docker" ]; then
  PATH="$HOME/.rd/bin:$PATH"
fi
export AWS_REGION=$REGION

ACCOUNT=$(aws sts get-caller-identity --query Account --output text) \
  || die "AWS credentials are not usable; run 'aws sso login' or 'aws configure'"
REGISTRY="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"

if [ "${1:-}" = "--destroy" ]; then
  step "Deleting stack $STACK_NAME in $REGION"
  aws cloudformation delete-stack --stack-name "$STACK_NAME"
  aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME"
  step "Deleting ECR repository $STACK_NAME"
  aws ecr delete-repository --repository-name "$STACK_NAME" --force >/dev/null 2>&1 || true
  echo "Done."
  exit 0
fi

command -v docker >/dev/null || die "docker is not installed (or Rancher Desktop / Docker Desktop is not running)"
docker info >/dev/null 2>&1 || die "the docker daemon is not running"

step "ECR repository $REGISTRY/$STACK_NAME"
if ! aws ecr describe-repositories --repository-names "$STACK_NAME" >/dev/null 2>&1; then
  aws ecr create-repository --repository-name "$STACK_NAME" \
    --image-scanning-configuration scanOnPush=true >/dev/null
  # Keep the last 10 images; older ones only cost storage.
  aws ecr put-lifecycle-policy --repository-name "$STACK_NAME" --lifecycle-policy-text \
    '{"rules":[{"rulePriority":1,"description":"keep last 10","selection":{"tagStatus":"any","countType":"imageCountMoreThan","countNumber":10},"action":{"type":"expire"}}]}' >/dev/null
fi

TAG=$(git rev-parse --short HEAD)
if ! git diff --quiet HEAD -- kerykeion cli aws pyproject.toml; then
  TAG="$TAG-dirty-$(date +%Y%m%d%H%M%S)"  # uncommitted changes: never reuse a clean tag
fi
IMAGE="$REGISTRY/$STACK_NAME:$TAG"

step "Building and pushing $IMAGE (ephemeris tier: $EPHEMERIS_TIER)"
aws ecr get-login-password | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null
# Lambda rejects multi-manifest images, so no provenance/SBOM attestations.
docker buildx build --platform linux/arm64 --provenance=false --sbom=false \
  -f aws/Dockerfile --build-arg EPHEMERIS_TIER="$EPHEMERIS_TIER" \
  -t "$IMAGE" --push .

step "Deploying stack $STACK_NAME"
aws cloudformation deploy \
  --stack-name "$STACK_NAME" \
  --template-file aws/template.yaml \
  --capabilities CAPABILITY_IAM \
  --no-fail-on-empty-changeset \
  --parameter-overrides ImageUri="$IMAGE"

output() {
  aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
API_URL=$(output ApiUrl)
API_KEY=$(aws apigateway get-api-key --api-key "$(output ApiKeyId)" --include-value --query value --output text)

step "Deployed"
cat <<EOF
  export KERYKEION_API_URL=$API_URL
  export KERYKEION_API_KEY=$API_KEY

  curl -s "\$KERYKEION_API_URL/" -H "x-api-key: \$KERYKEION_API_KEY" | jq '.commands | keys'
EOF
