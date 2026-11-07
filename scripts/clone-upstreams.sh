#!/usr/bin/env bash
# Fetch inspected upstream sources; keep application code outside vendor/.
set -euo pipefail

task_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
inspect_main=false
dry_run=false

for arg in "$@"; do
    case "$arg" in
        --inspect-main) inspect_main=true ;;
        --dry-run) dry_run=true ;;
        --help|-h)
            echo 'Usage: bash scripts/clone-upstreams.sh [--inspect-main] [--dry-run]'
            echo 'Default: clone pinned Wirelog and PyreWire into vendor/.'
            echo '--inspect-main: also clone the separately inspected Wirelog main commit.'
            exit 0
            ;;
        *) echo "Unknown argument: $arg" >&2; exit 2 ;;
    esac
done

clone_pinned() {
    local name=$1 repo=$2 revision=$3
    local destination="$task_root/vendor/$name"
    if "$dry_run"; then
        printf '%s -> %s @ %s\n' "$repo" "$destination" "$revision"
        return
    fi
    if [[ -L "$destination" ]]; then
        echo "Refusing symlink destination: $destination" >&2
        return 1
    fi
    if [[ -e "$destination" ]]; then
        if [[ ! -d "$destination/.git" ]]; then
            echo "Destination exists but is not an independent clone: $destination" >&2
            return 1
        fi
        if [[ "$(git -C "$destination" remote get-url origin)" != "$repo" ]]; then
            echo "Unexpected origin in $destination; leaving it unchanged." >&2
            return 1
        fi
        if [[ -n "$(git -C "$destination" status --porcelain)" ]]; then
            echo "Local changes in $destination; leaving them unchanged." >&2
            return 1
        fi
    else
        mkdir -p -- "$task_root/vendor"
        git clone --depth 1 -- "$repo" "$destination"
    fi
    git -C "$destination" fetch --depth 1 origin "$revision"
    git -C "$destination" checkout --detach "$revision"
    [[ "$(git -C "$destination" rev-parse HEAD)" == "$revision" ]]
    printf 'Ready: %s @ %s\n' "$destination" "$revision"
}

clone_pinned wirelog https://github.com/semantic-reasoning/wirelog.git \
    c353350232c35356b34085abf5780a2fcb81e80f
clone_pinned PyreWire https://github.com/semantic-reasoning/PyreWire.git \
    3ab87f7560c9312f585f02da1b0c6c5ce1a4da06
if "$inspect_main"; then
    clone_pinned wirelog-main https://github.com/semantic-reasoning/wirelog.git \
        cbdd4da940d55e9806c4e06f78d7b48e4c29a674
fi
