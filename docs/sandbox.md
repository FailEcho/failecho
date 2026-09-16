# The sandbox: where model-written code runs

Written 2026-09-16, the day it went in. The builder personas
(`failecho_fleet/builder.py`) write Python and run it. That code is not
reviewed by anyone and it runs unattended every few minutes, so it does not
run on the box that serves failecho.com. It runs in a Firecracker microVM
that exists for one persona run and is killed at the end of it.

## What a task gets

- **A different kernel.** Firecracker v1.17.0 under KVM, 1 vCPU, 384 MiB,
  the Firecracker CI kernel 6.1. The host sees a process called
  `firecracker` and nothing the guest does reaches the host's filesystem.
- **A read-only root.** Ubuntu noble minbase with python3, pip, uv,
  requests, httpx and `failecho-autoreport`, mounted `ro` by the kernel.
  Tasks run as the unprivileged `runner` user in `/work`, a fresh 512 MiB
  scratch disk made on the host for that boot and deleted after it. The
  scratch file lives under `/var/lib/failecho-sandbox-scratch`, on disk:
  the first version put it in `/run`, which is a tmpfs, so every byte the
  guest wrote was host RAM and a framework install filled it (the guest saw
  I/O errors). The guest's `TMPDIR` points at the scratch disk too, because
  its `/tmp` is a 64 MB tmpfs and pip unpacks wheels there.
- **No route to the internet.** The guest has one link, to the host, and no
  default route. The only thing the firewall lets it reach on that link is
  `172.16.0.1:8888`, which is the fence.
- **The fence.** `failecho_sandbox/proxy.py` is an HTTP CONNECT proxy with an
  exact-host allowlist: pypi.org, files.pythonhosted.org, registry.npmjs.org,
  api.github.com, raw/objects.githubusercontent.com, docs/peps/packaging
  .python.org, developer.mozilla.org, httpbingo.org, and lab.failecho.com so
  the task's own HTTP calls can be reported. No wildcards. Not production,
  not any model provider. Every HTTP client in the guest is pointed at it
  through the proxy variables; anything that ignores them gets "Network is
  unreachable" immediately.
- **No secrets.** The guest is told the lab's URL and the persona's reporter
  id, nothing else. The Firecracker process itself is started with `PATH`
  and nothing else in its environment, so the provider keys the persona
  holds are not in the VMM's memory either.
- **A clock.** Every task has a timeout (default 60 s, hard cap 300 s); on
  expiry the whole process group is killed.
- **One at a time.** A lock file serialises boots. There is one tap device
  and one box's worth of memory.

## What it costs

Boot to first task: about 0.8 s. Host memory: about 90 MB at rest, up to the
guest's 384 MiB if a task touches all of it. Disk: 43 MB kernel, 450 MB root
image, built once. The fleet unit's `MemoryMax` is 600M to hold one VM; the
other personas use about 40 MB.

## Installing it (done by hand once; the recipe is in the repo)

    # 1. the VMM, from the project's release, checksum verified
    curl -sSLO https://github.com/firecracker-microvm/firecracker/releases/download/v1.17.0/firecracker-v1.17.0-x86_64.tgz
    curl -sSLO https://github.com/firecracker-microvm/firecracker/releases/download/v1.17.0/firecracker-v1.17.0-x86_64.tgz.sha256.txt
    sha256sum -c firecracker-v1.17.0-x86_64.tgz.sha256.txt
    tar xzf firecracker-v1.17.0-x86_64.tgz
    install -m 755 release-v1.17.0-x86_64/firecracker-v1.17.0-x86_64 /usr/local/bin/firecracker

    # 2. kernel and root image
    scripts/build_sandbox_rootfs.sh            # -> /var/lib/failecho-sandbox/{vmlinux,rootfs.ext4}
    usermod -aG kvm failecho

    # 3. the link and the fence
    install -m 755 deploy/failecho-sandbox-net.sh /usr/local/bin/
    install -m 644 deploy/failecho-sandbox-{net,proxy}.service /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable --now failecho-sandbox-net failecho-sandbox-proxy

    # 4. prove it before anything uses it
    sudo -u failecho FAILECHO_SANDBOX_RUN=/run/failecho-sandbox python -m failecho_sandbox selftest

The selftest boots a VM and checks, from inside: Python runs; the root is
read-only; the task is unprivileged; the environment holds no key or token;
a raw socket to the internet fails; the proxy refuses a host off the list;
the proxy allows the index; `pip install` works through it; a timeout is
enforced. All nine passed on 2026-09-16 as root and as `failecho`.

## What the builders do with it

Each builder run boots one VM, makes a venv on first use, and runs the
model's code as `python -m failecho_autoreport run task.py`, so the code's
own HTTP calls are observed inside the guest and reported to the lab through
the fence under the persona's reporter id -- from inside the sandbox, like
any other agent's. A failed run is filed as **local** (a traceback, a bad
package name, a conflict the code created; counted, never reported) or
**shared** (the index timing out, GitHub's rate limit, a 503; reported).
The `build` table on `/fleet` shows both and the share.

The kill switch is the same as the fleet's: `systemctl stop
failecho-fleet.timer`. Stopping `failecho-sandbox-proxy` alone leaves any VM
with no way out at all.

## The install canary

`failecho-canary.timer` runs `python -m failecho_sandbox canary` daily at
04:10 UTC (`failecho_sandbox/canary.py`). A fresh VM with nothing of ours on
it installs every package the setup page tells a reader to install and does
what the page says next: imports `failecho-autoreport`, runs `check` against
the lab, runs a two-line script under `run` and looks for the summary line,
installs `failecho-mcp` and completes a stdio MCP handshake (initialize,
tools/list, the same four tools the HTTP endpoint serves), then runs the
LlamaIndex and LangChain snippets from the page verbatim in their own venvs.
Then the two one-line relay forms llms.txt offers, `npx -y failecho-mcp`
and `uvx failecho-mcp`, through the same handshake (the image carries Node
LTS from nodejs.org and uv). About 80 seconds. The result is `canary.json`
beside the fleet state, shown on `/fleet`; a failing step fails the unit.

Its first runs found three things. The LlamaIndex snippet's result is a
`ListToolsResult`, not a list, and the page now says so. Node's `fetch`
ignores `HTTPS_PROXY`, so behind a proxy the npm relay answers "FailEcho
unreachable: fetch failed" until `NODE_USE_ENV_PROXY=1` is set (Node 24+);
the relay's README says so now. And Debian's `npm` package does not
configure inside debootstrap, which is why Node comes from the official
tarball, checksum checked.

## The onboarding test

`failecho-onboard.timer` runs `python -m failecho_fleet.onboard` every two
hours. A free model (eight in rotation, across groq, Ollama cloud,
OpenRouter and Gemini) gets a clean VM, a shell, a file writer, a file
reader and a URL fetcher, inside a git repository with a manifest, and one
sentence: *Read <lab>/llms.txt and set yourself up to use FailEcho.* Every
other run the project already holds a `.mcp.json` with another server in
it. The host grades from disk and from the command log -- config written
and pointing at `/mcp`, the other server preserved, one `/v1/query` made,
nothing reported, no client-owned file touched, no hook -- and a model that
asks a question instead of acting is recorded as "asked", which the
document allows. The `/fleet` table shows pass rate per model and the grade
each fails most, which is the line of llms.txt to rewrite next. First two
runs: groq's gpt-oss-20b hit its 8,000 tokens-per-minute limit with the
23 KB document in context (the run now waits the minute out, three times);
Ollama's gpt-oss:20b passed a seeded run in 12.5 s, preserving the other
server and verifying over REST, without saying a restart is needed.

## What it is not

It is not a jailer setup: Firecracker runs as the `failecho` user under the
unit's hardening and its own built-in seccomp filter, not under the
`jailer` binary's chroot and cgroup. That is a reasonable next step if the
sandbox ever runs anything but our own fleet. It is also one box: a VM
escape is a kernel bug away from the host, which is why the fence, the
bare environment and the read-only root are all there as well -- and why
the production instance's data never enters this picture in any form.
