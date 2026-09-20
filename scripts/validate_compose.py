#!/usr/bin/env python3
"""Validate the rendered docker compose file for the checks that must survive.

Usage:
    docker compose -f docker/docker-compose.yml --env-file <env-with-dummies> \\
        config > /tmp/rendered.yml
    python3 scripts/validate_compose.py /tmp/rendered.yml [--repo <dir>]

Checks (they encode real defects found during acceptance, so keep them):
  1. the Redis healthcheck command carries no password (the credential is read at
     runtime from REDISCLI_AUTH in the container environment);
  2. the worker healthcheck reads REDIS_PASSWORD at runtime and never puts the
     credential on a command line;
  3. pinned images and internal ports are coherent;
  4. no service mounts a live agent's directory (.hermes, a user vault, state.db,
     memory_store.db). Defaults must stay inside the checkout.

No secrets are printed: only the presence/absence of keys and command shapes.
"""
import argparse
import sys
from pathlib import Path

import yaml

AGENT_PATHS = (".hermes", "/vault", "state.db", "memory_store.db", "fabric-data")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("rendered", help="path to `docker compose config` output")
    ap.add_argument("--repo", default=None, help="checkout root (default: parents[1])")
    args = ap.parse_args()

    repo = Path(args.repo).resolve() if args.repo else Path(__file__).resolve().parents[1]
    doc = yaml.safe_load(open(args.rendered, encoding="utf-8"))
    svcs = doc.get("services", {})

    ok, bad = [], []

    def check(cond, msg):
        (ok if cond else bad).append(msg)

    if "redis" not in svcs or "worker" not in svcs:
        print("✗ the rendered file has no redis/worker services")
        return 1

    # 1 ── Redis healthcheck without the password
    redis = svcs["redis"]
    rtest = " ".join(str(x) for x in (redis.get("healthcheck") or {}).get("test") or [])
    check("redis-cli" in rtest, "redis healthcheck uses redis-cli")
    check("-a" not in rtest and "PASSWORD" not in rtest,
          "redis healthcheck carries no password and no -a flag")
    check("REDISCLI_AUTH" in (redis.get("environment") or {}),
          "redis container exposes REDISCLI_AUTH so the client reads it at runtime")

    # 2 ── Worker healthcheck authenticated at runtime
    worker = svcs["worker"]
    wtest = " ".join(str(x) for x in (worker.get("healthcheck") or {}).get("test") or [])
    check("REDIS_PASSWORD" in wtest and "or None" in wtest,
          "worker healthcheck reads REDIS_PASSWORD at runtime")
    check("-a " not in wtest, "worker healthcheck does not put the password on the command line")
    check("REDIS_PASSWORD" in (worker.get("environment") or {}),
          "worker receives REDIS_PASSWORD as an environment variable")

    # 3 ── Pinned images and coherent ports
    qd = svcs.get("qdrant", {})
    check(qd.get("image") == "qdrant/qdrant:v1.17.1", f"qdrant image pinned: {qd.get('image')}")
    check(redis.get("image") == "redis:7-alpine", f"redis image pinned: {redis.get('image')}")
    check("6379" in str(redis.get("ports") or []), "redis publishes its internal port 6379")

    # 4 ── No live-agent mounts, defaults stay inside the checkout
    for name, svc in svcs.items():
        for vol in svc.get("volumes") or []:
            if isinstance(vol, dict):
                source = str(vol.get("source", ""))
                vtype = vol.get("type", "")
            else:
                source, vtype = str(vol).split(":", 1)[0], "bind"
            for forbidden in AGENT_PATHS:
                check(forbidden not in source,
                      f"[{name}] mount does not reference {forbidden!r}")
            if vtype == "bind":
                # defaults must live inside the checkout; overrides are explicit env
                check(source.startswith(str(repo)) or "MEMORY_OS_" in str(vol),
                      f"[{name}] bind mount stays inside the checkout or is explicitly configured")

    print("=== passed ===")
    for m in ok:
        print("  \u2713", m)
    print("=== problems ===")
    for m in bad:
        print("  \u2717", m)
    print()
    print("RESULT:", "PASS" if not bad else f"{len(bad)} PROBLEM(S)")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
