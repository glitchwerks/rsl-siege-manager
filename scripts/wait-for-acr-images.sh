#!/usr/bin/env bash

set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "Usage: $0 <registry-name> <image-tag> <repository> [repository ...]" >&2
  exit 2
fi

registry_name=$1
image_tag=$2
shift 2
repositories=("$@")

attempts=${ACR_WAIT_ATTEMPTS:-40}
interval_seconds=${ACR_WAIT_INTERVAL_SECONDS:-15}

if ! [[ $attempts =~ ^[1-9][0-9]*$ ]]; then
  echo "ACR_WAIT_ATTEMPTS must be a positive integer" >&2
  exit 2
fi

if ! [[ $interval_seconds =~ ^[0-9]+$ ]]; then
  echo "ACR_WAIT_INTERVAL_SECONDS must be a non-negative integer" >&2
  exit 2
fi

for ((attempt = 1; attempt <= attempts; attempt++)); do
  missing=()

  for repository in "${repositories[@]}"; do
    if ! az acr repository show \
      --name "$registry_name" \
      --image "${repository}:${image_tag}" \
      --query digest \
      --output tsv \
      --only-show-errors >/dev/null 2>&1; then
      missing+=("$repository")
    fi
  done

  if [[ ${#missing[@]} -eq 0 ]]; then
    echo "All release images are available for tag ${image_tag}: ${repositories[*]}"
    exit 0
  fi

  if [[ $attempt -eq $attempts ]]; then
    echo "Timed out waiting for release images tagged ${image_tag}: ${missing[*]}" >&2
    exit 1
  fi

  echo "Attempt ${attempt}/${attempts}: waiting for ${missing[*]} tagged ${image_tag}"
  sleep "$interval_seconds"
done
