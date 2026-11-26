#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: bash install.sh ingress|egress|vpn [baseline|experimental|smooth|adaptive]

  ingress          Install the GOST ingress relay and its configuration template.
  egress           Install the GOST egress relay and its configuration template.
  vpn              Install the experimental userspace AmneziaWG binary.
  vpn baseline     Install the baseline userspace AmneziaWG binary.
  vpn smooth       Install AmneziaWG with the experimental padding without size buckets.
  vpn adaptive     Install AmneziaWG with per-peer adaptive padding limits.

After installation, run sudo vpn-relay-configure --help.
EOF
}

fail() {
    printf '%s\n' "$*" >&2
    exit 1
}

case ${1:-} in
    -h|--help) usage; exit 0 ;;
    ingress|egress) [[ $# -eq 1 ]] || fail 'Expected one relay role.' ;;
    vpn) [[ $# -le 2 && ${2:-experimental} =~ ^(baseline|experimental|smooth|adaptive)$ ]] || fail 'Invalid VPN build variant.' ;;
    *) usage >&2; exit 2 ;;
esac

[[ $(uname -s) == Linux ]] || fail 'Run this installer on Linux.'
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
as_root=()
if (( EUID != 0 )); then
    command -v sudo >/dev/null || fail 'Install sudo or run as root.'
    as_root=(sudo)
fi

work_dir=$(mktemp -d)
trap 'rm -rf -- "$work_dir"' EXIT

case $(uname -m) in
    x86_64) arch=amd64 ;;
    aarch64|arm64) arch=arm64 ;;
    *) fail 'The installer supports Linux amd64 and arm64.' ;;
esac

if [[ $1 == vpn ]]; then
    variant=${2:-experimental}
    binary=amneziawg-go-$variant
    if [[ -f $project_dir/release-bin/architecture ]]; then
        read -r release_arch < "$project_dir/release-bin/architecture"
        [[ $release_arch == "$arch" ]] || fail "This release is for $release_arch. Download the $arch archive."
        binary_path=$project_dir/release-bin/$binary
        [[ -f $binary_path ]] || fail "Release archive is missing $binary."
    else
        command -v go >/dev/null || fail 'Go 1.25 or newer is required for a source checkout.'
        build_flags=(-trimpath)
        case $variant in
            experimental) build_flags+=(-tags awg_experimental) ;;
            smooth) build_flags+=(-tags awg_smooth) ;;
            adaptive) build_flags+=(-tags awg_adaptive) ;;
        esac
        binary_path=$work_dir/$binary
        (
            cd -- "$project_dir/amneziawg-fork"
            CGO_ENABLED=0 GOOS=linux GOARCH=$arch go build "${build_flags[@]}" -o "$binary_path" .
        )
    fi
    "${as_root[@]}" install -m 0755 "$binary_path" "/usr/local/bin/$binary"
    "${as_root[@]}" install -m 0755 "$project_dir/configure.py" /usr/local/sbin/vpn-relay-configure
    printf 'Installed /usr/local/bin/%s\n' "$binary"
    exit 0
fi

role=$1
setup_dir=/etc/vpn-relay
systemd_dir=/etc/systemd/system
case $arch in
    amd64) checksum=f4c1255dc892a7014c0ad6e5f1c3573108f7bea4403f04f21873406ab58aa666 ;;
    arm64) checksum=4c704c92df6eeea8a3b08d0de4fd3c44343095a20136b3b6cf334f98d2f174ab ;;
esac

if "${as_root[@]}" test -e "$setup_dir/role"; then
    installed_role=$("${as_root[@]}" cat "$setup_dir/role")
    [[ $installed_role == "$role" ]] || fail "This host already has the $installed_role role."
elif [[ $role == ingress ]] && "${as_root[@]}" test -e "$systemd_dir/vpn-relay.service.d/tls.conf"; then
    fail 'This host already has the egress TLS configuration.'
fi

version=3.3.3
archive=gost_${version}_linux_${arch}.tar.gz
curl --fail --silent --show-error --location --retry 3 --proto '=https' \
    --output "$work_dir/$archive" "https://github.com/go-gost/gost/releases/download/v$version/$archive"
(
    cd -- "$work_dir"
    printf '%s  %s\n' "$checksum" "$archive" | sha256sum --check --status
    tar -xzf "$archive" gost
)

"${as_root[@]}" install -m 0755 "$work_dir/gost" /usr/local/bin/gost
"${as_root[@]}" install -d -m 0700 "$setup_dir"
"${as_root[@]}" install -m 0600 "$project_dir/$role.yaml" "$setup_dir/config.example.yaml"
if [[ $role == egress ]]; then
    "${as_root[@]}" install -d -m 0700 "$setup_dir/tls"
    "${as_root[@]}" install -D -m 0644 "$project_dir/egress-tls.conf" "$systemd_dir/vpn-relay.service.d/tls.conf"
fi
"${as_root[@]}" install -m 0644 "$project_dir/vpn-relay.service" "$systemd_dir/vpn-relay.service"
"${as_root[@]}" install -m 0755 "$project_dir/configure.py" /usr/local/sbin/vpn-relay-configure
printf '%s\n' "$role" > "$work_dir/role"
"${as_root[@]}" install -m 0600 "$work_dir/role" "$setup_dir/role"
"${as_root[@]}" systemctl daemon-reload
printf 'Installed %s relay. Configuration template: %s/config.example.yaml\n' "$role" "$setup_dir"
printf 'Configure it with: sudo vpn-relay-configure %s --help\n' "$role"
