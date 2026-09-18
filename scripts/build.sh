#!/usr/bin/env bash
set -euo pipefail

# Usage: scripts/build.sh [Local|Dev|PubDev|Prod]
# Local  - build only, tag wireguard-network-manager:${VERSION} + :latest, no push.
# Dev    - build + push to ${INTERNAL_REG}, tagged dev-latest / dev-${VERSION}.
# PubDev - build + push to Docker Hub under ${DOCKER_USER}, tagged dev-latest / dev-${VERSION}.
# Prod   - build + push to Docker Hub under ${DOCKER_USER}, tagged latest / ${VERSION}.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKERFILE="${ROOT_DIR}/docker/Dockerfile"
IMAGE_NAME="wireguard-network-manager"
VERSION="$(cat "${ROOT_DIR}/VERSION")"
MODE="${1:-Local}"

cd "${ROOT_DIR}"

docker_login() {
  : "${DOCKER_USER:?DOCKER_USER must be set}"
  : "${DOCKER_PAT:?DOCKER_PAT must be set}"
  echo "${DOCKER_PAT}" | docker login -u "${DOCKER_USER}" --password-stdin
}

case "${MODE}" in
  Local)
    docker build -f "${DOCKERFILE}" -t "${IMAGE_NAME}:${VERSION}" -t "${IMAGE_NAME}:latest" .
    ;;
  Dev)
    : "${INTERNAL_REG:?INTERNAL_REG must be set for a Dev build}"
    docker build \
      -f "${DOCKERFILE}" \
      -t "${INTERNAL_REG}/${IMAGE_NAME}:dev-latest" \
      -t "${INTERNAL_REG}/${IMAGE_NAME}:dev-${VERSION}" \
      .
    docker push "${INTERNAL_REG}/${IMAGE_NAME}:dev-latest"
    docker push "${INTERNAL_REG}/${IMAGE_NAME}:dev-${VERSION}"
    ;;
  PubDev)
    docker_login
    docker build \
      -f "${DOCKERFILE}" \
      -t "${DOCKER_USER}/${IMAGE_NAME}:dev-latest" \
      -t "${DOCKER_USER}/${IMAGE_NAME}:dev-${VERSION}" \
      .
    docker push "${DOCKER_USER}/${IMAGE_NAME}:dev-latest"
    docker push "${DOCKER_USER}/${IMAGE_NAME}:dev-${VERSION}"
    ;;
  Prod)
    docker_login
    docker build \
      -f "${DOCKERFILE}" \
      -t "${DOCKER_USER}/${IMAGE_NAME}:latest" \
      -t "${DOCKER_USER}/${IMAGE_NAME}:${VERSION}" \
      .
    docker push "${DOCKER_USER}/${IMAGE_NAME}:latest"
    docker push "${DOCKER_USER}/${IMAGE_NAME}:${VERSION}"
    ;;
  *)
    echo "Unknown build mode: ${MODE} (expected Local, Dev, PubDev, or Prod)" >&2
    exit 1
    ;;
esac
