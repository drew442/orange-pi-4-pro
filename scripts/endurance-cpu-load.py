#!/usr/bin/env python3
"""Explicitly invoked, unprivileged RAM-only load; never starts a VM."""
import argparse
import hashlib
import os
import signal
import time

DURATIONS = {'smoke': 60, 'release': 86400}


def worker():
    # fork avoids multiprocessing semaphores and their /dev/shm backing files.
    running = True
    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    os.nice(19)
    block = bytes(1024 * 1024)
    while running:
        hashlib.sha256(block).digest()


def run(mode):
    if os.getuid() == 0 or os.geteuid() == 0:
        raise ValueError('CPU load must run as an unprivileged user')
    duration = DURATIONS[mode]
    interrupted = False
    def stop(*_):
        nonlocal interrupted
        interrupted = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    children = set()
    failed = False
    deadline = time.monotonic() + duration
    try:
        for _ in range(4):
            pid = os.fork()
            if pid == 0:
                try:
                    worker()
                except BaseException:
                    os._exit(1)
                os._exit(0)
            children.add(pid)
        while not interrupted and time.monotonic() < deadline:
            for pid in tuple(children):
                ended, _ = os.waitpid(pid, os.WNOHANG)
                if ended:
                    children.remove(pid)
                    failed = True
            if failed:
                break
            time.sleep(min(0.2, max(0, deadline - time.monotonic())))
    finally:
        for pid in children:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        limit = time.monotonic() + 3
        while children and time.monotonic() < limit:
            for pid in tuple(children):
                ended, status = os.waitpid(pid, os.WNOHANG)
                if ended:
                    children.remove(pid)
                    failed |= status != 0
            if children:
                time.sleep(0.05)
        for pid in children:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            failed = True
    return 1 if failed else 2 if interrupted else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=DURATIONS, required=True)
    args = parser.parse_args()
    return run(args.mode)


if __name__ == '__main__':
    raise SystemExit(main())
