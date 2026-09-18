#!/bin/sh
# Build the sandbox VM's root image and fetch its kernel. Run as root, once,
# and again whenever failecho_sandbox/guest_init.py changes.
#
# What it produces, under /var/lib/failecho-sandbox:
#   vmlinux       the Firecracker CI kernel (6.1, x86_64), with its .config
#   rootfs.ext4   Ubuntu noble minbase + python3, pip, uv, requests, httpx,
#                 curl, git, OpenCode, and Node LTS from nodejs.org with npm and npx
#                 (Debian's npm does not configure inside debootstrap),
#                 failecho-autoreport, an unprivileged `runner` user, and
#                 our guest init at /usr/local/bin/sandbox-init
#
# The image is mounted read-only by the guest kernel, so it is built once and
# never written to by a VM. The Firecracker binary itself is installed
# separately from the project's GitHub release, checksum verified
# (docs/sandbox.md has the three commands).
set -eu

DEST=${FAILECHO_SANDBOX_IMAGES:-/var/lib/failecho-sandbox}
REPO=$(cd "$(dirname "$0")/.." && pwd)
CI=firecracker-ci/v1.15/x86_64
KERNEL=vmlinux-6.1.155
NODE_VERSION=v24.21.0
# OpenCode (opencode.ai), the agent the opencode personas drive; the linux
# binary package from npm, pinned with its sha512 from the registry
OPENCODE_VERSION=1.18.31
OPENCODE_SHA512=sha512-gTJ5nGNq+KJblwmeusEdz8YFwvkV7a3xTOV9EKKEmsv0Lkbajq/oQO9jEC0sMiS66mjHDOrhLgIUxSVqfLBM8g==
SIZE_MB=1000

mkdir -p "$DEST"
cd "$DEST"

if [ ! -s vmlinux ]; then
    curl -sSL -o vmlinux "https://s3.amazonaws.com/spec.ccfc.min/$CI/$KERNEL"
    curl -sSL -o vmlinux.config "https://s3.amazonaws.com/spec.ccfc.min/$CI/$KERNEL.config"
fi
for opt in VIRTIO_VSOCKETS VIRTIO_NET VIRTIO_BLK EXT4_FS DEVTMPFS_MOUNT; do
    grep -q "^CONFIG_$opt=y" vmlinux.config || { echo "kernel lacks CONFIG_$opt"; exit 1; }
done

command -v debootstrap >/dev/null || apt-get install -y -qq debootstrap
rm -rf rootfs-build
debootstrap --variant=minbase --components=main,universe \
    --include=python3,python3-venv,python3-pip,ca-certificates,iproute2,curl,git,xz-utils \
    noble rootfs-build http://archive.ubuntu.com/ubuntu

# the wrapper version the guest gets is the one in this checkout, from PyPI
AR_VERSION=$(sed -n 's/^__version__ = "\([^"]*\)"/\1/p' "$REPO/failecho_autoreport/__init__.py")
chroot rootfs-build /bin/sh -c "
    pip install --quiet --break-system-packages --no-cache-dir uv requests httpx failecho-autoreport==$AR_VERSION
    useradd -m -u 1000 -s /bin/sh runner
    apt-get clean
    rm -rf /var/lib/apt/lists/* /var/cache/apt/* /usr/share/doc/* /usr/share/man/*
    echo sandbox > /etc/hostname
    printf '127.0.0.1 localhost sandbox\n' > /etc/hosts
    printf 'nameserver 127.0.0.1\n' > /etc/resolv.conf
"
# Node LTS, the official build, checksum checked against the release's list.
# Into /usr/local so node, npm and npx are on the guest's PATH.
NODE_TAR="node-$NODE_VERSION-linux-x64.tar.xz"
if [ ! -s "$NODE_TAR" ]; then
    curl -sSL -o "$NODE_TAR" "https://nodejs.org/dist/$NODE_VERSION/$NODE_TAR"
    curl -sSL -o SHASUMS256.txt "https://nodejs.org/dist/$NODE_VERSION/SHASUMS256.txt"
fi
grep " $NODE_TAR\$" SHASUMS256.txt | sha256sum -c - >/dev/null
tar -xJf "$NODE_TAR" -C rootfs-build/usr/local --strip-components=1 --exclude='*/share/doc' --exclude='*/include'
chroot rootfs-build /usr/local/bin/node --version

# OpenCode: one static-ish binary from the npm platform package, checksummed.
OC_TGZ="opencode-linux-x64-$OPENCODE_VERSION.tgz"
if [ ! -s "$OC_TGZ" ]; then
    curl -sSL -o "$OC_TGZ" "https://registry.npmjs.org/opencode-linux-x64/-/$OC_TGZ"
fi
[ "sha512-$(openssl dgst -sha512 -binary "$OC_TGZ" | base64 -w0)" = "$OPENCODE_SHA512" ] || { echo "opencode checksum mismatch"; exit 1; }
tar -xzf "$OC_TGZ" -C rootfs-build/usr/local/bin --strip-components=2 package/bin/opencode
chmod 755 rootfs-build/usr/local/bin/opencode
# (not run in the chroot: the bun-built binary aborts without /proc and /dev;
#  `python -m failecho_sandbox selftest` checks it inside a booted VM)

install -m 755 "$REPO/failecho_sandbox/guest_init.py" rootfs-build/usr/local/bin/sandbox-init
mkdir -p rootfs-build/work

# built beside the live image and swapped in atomically: a VM booting
# during the build still finds a complete image
rm -f rootfs.ext4.new
truncate -s "${SIZE_MB}M" rootfs.ext4.new
mkfs.ext4 -q -F -d rootfs-build -L sandbox-root rootfs.ext4.new
rm -rf rootfs-build
chmod 644 vmlinux rootfs.ext4.new
mv -f rootfs.ext4.new rootfs.ext4
ls -lh vmlinux rootfs.ext4
echo "built; run: python -m failecho_sandbox selftest"
