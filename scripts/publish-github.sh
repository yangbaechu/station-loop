#!/usr/bin/env bash
# Publish only application sources, never local accounts or game transcripts.
set -euo pipefail
task_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
prepare_only=false
case "${1:-}" in
    --prepare-only) prepare_only=true ;;
    '') ;;
    *) echo 'Usage: bash scripts/publish-github.sh [--prepare-only]' >&2; exit 2 ;;
esac

mkdir -p "$task_root/.station-loop"
publish_dir=$(mktemp -d "$task_root/.station-loop/publish-XXXXXX")
tar -C "$task_root" --exclude='__pycache__' --exclude='*.pyc' -cf - \
    .gitignore .env.example LICENSE README.md pyproject.toml \
    docs rules scenarios scripts station_loop tests | tar -C "$publish_dir" -xf -
git -C "$publish_dir" init --initial-branch=main --quiet
git -C "$publish_dir" add -- .
git -C "$publish_dir" -c user.name=yangbaechu \
    -c user.email=45089264+yangbaechu@users.noreply.github.com \
    commit --quiet -m 'Build Station Loop neuro-symbolic agent playground'
printf 'Prepared source commit: %s\n' "$publish_dir"
if "$prepare_only"; then exit 0; fi

if ! gh api user --jq .login >/dev/null; then
    echo 'GitHub connection failed. Check your network and run gh auth login if needed.' >&2
    printf 'Prepared files remain in %s\n' "$publish_dir" >&2
    exit 1
fi
gh repo create yangbaechu/station-loop --public --source "$publish_dir" \
    --remote origin --push \
    --description 'Wirelog 기반 우주정거장 협력 에이전트 실험실 — neuro-symbolic agent playground'
