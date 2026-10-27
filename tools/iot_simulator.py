#!/usr/bin/env python3
"""Send simulated soil readings to the API — the external IoT pipeline.

Shares the exact generator and contract with the in-process simulator
(iot.py): run this tool *or* start the in-process simulation, not both
against the same device (the readings would interleave).

Usage:
    python3 tools/iot_simulator.py --device SOIL-001 --token <token>
    python3 tools/iot_simulator.py --device SOIL-001 --token <token> \
        --scenario dry --interval 2 --count 3
    python3 tools/iot_simulator.py --device SOIL-001 --token <token> \
        --count 0        # run forever (Ctrl-C to stop)

Values are simulated — the API stores them with source `iot_simulator`.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import iot  # noqa: E402


def send(url, payload, token):
    """POST one contract payload; returns (status_code, parsed_body_or_None)."""
    request = urllib.request.Request(
        f"{url.rstrip('/')}/api/iot/readings",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Device-Token": token,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, body


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Simulated IoT readings → POST /api/iot/readings")
    parser.add_argument("--url", default="http://localhost:8000",
                        help="API base URL (default: %(default)s)")
    parser.add_argument("--device", required=True,
                        help="registered device id, e.g. SOIL-001")
    parser.add_argument("--token", required=True,
                        help="device token (X-Device-Token)")
    parser.add_argument("--scenario", default="normal",
                        choices=sorted(iot.SCENARIOS),
                        help="baseline scenario (default: %(default)s)")
    parser.add_argument("--interval", type=float, default=5.0,
                        help="seconds between readings (default: %(default)s)")
    parser.add_argument("--count", type=int, default=10,
                        help="readings to send; 0 = forever (default: %(default)s)")
    args = parser.parse_args(argv)

    state = iot.new_state(args.scenario)
    sent = 0
    print(f"Sending simulated readings to {args.url} as {args.device} "
          f"(scenario={args.scenario}, interval={args.interval}s, "
          f"count={'∞' if args.count == 0 else args.count})")

    while args.count == 0 or sent < args.count:
        state = iot.step_state(state, args.scenario)
        payload = iot.build_payload(state, args.device)
        status, body = send(args.url, payload, args.token)
        sent += 1
        m = payload["measurements"]
        print(f"[{sent}] HTTP {status} "
              f"ph={m['ph']:.2f} N={m['nitrogen']:.1f} P={m['phosphorus']:.1f} "
              f"K={m['potassium']:.1f} moist={m['moisture']:.1f}% "
              f"temp={m['temperature']:.1f}C → {body}")

        if status in (401, 404):
            print("Aborting: auth/device problem (see above).", file=sys.stderr)
            return 1
        if status == 422:
            print("Aborting: the server rejected this reading (contract "
                  "mismatch).", file=sys.stderr)
            return 1
        if status >= 500:
            print("Aborting: server error.", file=sys.stderr)
            return 1

        if args.count == 0 or sent < args.count:
            time.sleep(args.interval)

    print(f"Done: {sent} simulated reading(s) accepted.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(130)
    except urllib.error.URLError as exc:
        print(f"Cannot reach the API at the given URL: {exc.reason}",
              file=sys.stderr)
        sys.exit(1)
