#!/usr/bin/env bash
set -euo pipefail

project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
output_dir=${1:-"$project_dir/dist/release"}
mkdir -p -- "$output_dir"
output_dir=$(cd -- "$output_dir" && pwd)
work_dir=$(mktemp -d)
trap 'rm -rf -- "$work_dir"' EXIT

for arch in amd64 arm64; do
    bundle="$work_dir/vpn-relay-linux-$arch"
    mkdir -p -- "$bundle/release-bin"
    for file in install.sh configure.py ingress.yaml egress.yaml vpn-relay.service egress-tls.conf README.md; do
        install -m 0644 "$project_dir/$file" "$bundle/$file"
    done
    install -m 0644 "$project_dir/amneziawg-fork/LICENSE" "$bundle/LICENSE-AmneziaWG"
    printf '%s\n' "$arch" > "$bundle/release-bin/architecture"
    (
        cd -- "$project_dir/amneziawg-fork"
        export CGO_ENABLED=0 GOOS=linux GOARCH=$arch
        go build -trimpath -buildvcs=false -o "$bundle/release-bin/amneziawg-go-baseline" .
        go build -trimpath -buildvcs=false -tags awg_experimental -o "$bundle/release-bin/amneziawg-go-experimental" .
        go build -trimpath -buildvcs=false -tags awg_smooth -o "$bundle/release-bin/amneziawg-go-smooth" .
        go build -trimpath -buildvcs=false -tags awg_adaptive -o "$bundle/release-bin/amneziawg-go-adaptive" .
    )
    tar -C "$work_dir" -czf "$output_dir/vpn-relay-linux-$arch.tar.gz" "vpn-relay-linux-$arch"
done

(
    cd -- "$output_dir"
    sha256sum vpn-relay-linux-{amd64,arm64}.tar.gz > SHA256SUMS
)
