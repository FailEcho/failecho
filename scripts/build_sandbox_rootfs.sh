#!/bin/sh
# Build the sandbox VM's root image and fetch its kernel. Run as root, once,
# and again whenever failecho_sandbox/guest_init.py changes.
#
# What it produces, under /var/lib/failecho-sandbox:
#   vmlinux       the Firecracker CI kernel (6.1, x86_64), with its .config
#   rootfs.ext4   Ubuntu noble minbase + python3, pip, uv, requests, httpx,
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
SIZE_MB=450

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
    --include=python3,python3-venv,python3-pip,ca-certificates,iproute2 \
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
