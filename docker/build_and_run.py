#!/usr/bin/env python3
"""Build the Annotation Toolkits image and (re)start its container.

    python3 docker/build_and_run.py --data ~/annotation-workspace
    python3 docker/build_and_run.py --data ./workspace --mount /home/me/dataset
    python3 docker/build_and_run.py --data ./workspace --no-build   # restart only

--data is the workspace, mounted at /data (ANNOTATION_WORKSPACE): the web UI
creates projects.yaml and one projects/<id>/ directory per project there, and
an empty or missing directory is fine -- it is created and starts with no
projects. Uploaded and zip-imported images live inside it.

--mount DIR mounts a host directory at the same path inside the container and
adds it to ANNOTATION_IMPORT_ROOTS, so a project can link an existing dataset
under it ("link a server directory" in the UI) and absolute paths keep
meaning the same thing on both sides. Without --mount only directories inside
the workspace can be linked.

The UI and API are served together on http://<host>:3000.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
IMAGE_NAME = "annotation-toolkits"
CONTAINER_NAME = "annotation-toolkits"
PORT = 3000


def run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True, **kw)


def wait_for_http(url: str, attempts: int = 30) -> str | None:
    """Return the error of the last attempt, or None once the server answers."""
    error = "no attempt made"
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                json.load(response)
                return None
        except urllib.error.HTTPError as exc:
            # The server is up; a 500 here means /data/projects.yaml is invalid,
            # which the body explains better than a timeout would.
            return f"HTTP {exc.code}: {exc.read().decode(errors='replace')}"
        except (urllib.error.URLError, OSError, ValueError) as exc:
            error = str(exc)
        time.sleep(1)
    return error


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", required=True, type=Path,
                        help="workspace directory (created if missing), mounted at /data")
    parser.add_argument("--mount", action="append", default=[], type=Path,
                        metavar="DIR",
                        help="extra host directory mounted at the same path "
                             "(repeatable); projects may link datasets under it")
    parser.add_argument("--bind", default="0.0.0.0",
                        help="host address to publish port 3000 on (default: "
                             "all interfaces; 127.0.0.1 keeps it local-only)")
    parser.add_argument("--no-build", action="store_true",
                        help="reuse the existing image")
    parser.add_argument("--no-gpu", action="store_true",
                        help="start without --gpus all (CPU-only host)")
    args = parser.parse_args()

    data = args.data.expanduser().resolve()
    if data.exists() and not data.is_dir():
        sys.exit(f"--data {data}: not a directory")
    # Created here, by the invoking user, so the bind mount is owned by the
    # same uid the container runs as rather than by root via the daemon.
    data.mkdir(parents=True, exist_ok=True)
    mounts = [path.expanduser().resolve() for path in args.mount]
    for path in mounts:
        if not path.is_dir():
            sys.exit(f"--mount {path}: not a directory")

    if not args.no_build:
        # Bake the invoking user's uid into the image: results are written into
        # the bind mounts, and must stay owned (and writable) by that user.
        build = ["docker", "build", "-t", IMAGE_NAME]
        if os.getuid() != 0:  # root keeps the image default (1001)
            build += ["--build-arg", f"APP_UID={os.getuid()}",
                      "--build-arg", f"APP_GID={os.getgid()}"]
        run(build + [str(REPO)])

    subprocess.run(["docker", "rm", "-f", CONTAINER_NAME],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    cmd = [
        "docker", "run", "-d",
        "--name", CONTAINER_NAME,
        "--restart", "unless-stopped",
        "-p", f"{args.bind}:{PORT}:{PORT}",
        "-v", f"{data}:/data",
        # Linkable dataset directories: the workspace plus every --mount.
        "-e", "ANNOTATION_IMPORT_ROOTS=" + os.pathsep.join(
            ["/data", *(str(path) for path in mounts)]),
    ]
    if not args.no_gpu:
        cmd += [
            "--gpus", "all",
            # Torch DataLoader workers share tensors through /dev/shm, and the
            # 64MB Docker default ends in a "bus error" partway into a run.
            "--shm-size", "8g",
        ]
    for path in mounts:
        cmd += ["-v", f"{path}:{path}"]
    cmd.append(IMAGE_NAME)
    run(cmd)

    print(f"\ncontainer '{CONTAINER_NAME}' started, waiting for the server...")
    error = wait_for_http(f"http://127.0.0.1:{PORT}/api/projects")
    if error:
        print(f"  server not healthy: {error}")
        print(f"  logs: docker logs {CONTAINER_NAME}")
    else:
        print(f"  web UI: http://{'localhost' if args.bind == '0.0.0.0' else args.bind}:{PORT}")
        if args.bind == "0.0.0.0":
            print("  note: published on all interfaces and the platform has no "
                  "login -- anyone who can reach this host can annotate. "
                  "Use --bind 127.0.0.1 to keep it local.")

    if not args.no_gpu:
        print("\nchecking GPU passthrough...")
        probe = subprocess.run(
            ["docker", "exec", CONTAINER_NAME, "python", "-c",
             "import torch, onnxruntime as ort;"
             "print('torch cuda:', torch.cuda.is_available(),"
             " torch.cuda.get_device_name(0) if torch.cuda.is_available() else '-');"
             "print('onnxruntime:', ort.get_available_providers())"],
            capture_output=True, text=True, timeout=180,
        )
        output = probe.stdout.strip() or probe.stderr.strip().splitlines()[-1]
        print("  " + output.replace("\n", "\n  "))

    print(f"\nteardown: docker rm -f {CONTAINER_NAME}")


if __name__ == "__main__":
    main()
