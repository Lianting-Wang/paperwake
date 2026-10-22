#!/bin/sh
set -eu

if [ "$(uname -m)" != aarch64 ]; then
    echo "Use the linux/arm64 build platform for this Pi deployment." >&2
    exit 1
fi

# Compile a temporary copy and write artifacts to the output mount.
build_dir=$(mktemp -d)
trap 'rm -rf "$build_dir"' EXIT HUP INT TERM
cp -R /workspace/src/display/IT8951 "$build_dir/IT8951"
cd "$build_dir/IT8951"
make clean
make -j2 LIB=BCM
mkdir -p /out
cp epd /out/epd
file /out/epd
(cd /out && sha256sum epd > epd.sha256)
{
    date -u '+Built at: %Y-%m-%dT%H:%M:%SZ'
    gcc --version
    dpkg-query -W -f='${Package} ${Version}\n' gcc libc6 make
    sha256sum /workspace/vendor/bcm2835-1.60.tar.gz
} > /out/build-info.txt
