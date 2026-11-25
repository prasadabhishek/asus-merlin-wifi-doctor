# ASUS Merlin Wi-Fi Doctor

A reusable agent skill and Python CLI for diagnosing macOS Wi-Fi and home network bottlenecks, optimizing **ASUSWRT / Asuswrt-Merlin** routers, and verifying real gains with a strictly sequential **Before vs. After** benchmark.

## What It Does

1. **Strictly Sequential 4-Step Benchmark** (`scripts/wifi_doctor.py benchmark`):
   - **Step 1 — Wi-Fi Link & RF Congestion Scan:** Inspects PHY mode, band, channel, width, RSSI/noise/SNR, Tx rate, MCS index, interface packet drops, and tallies competing APs across 5 GHz 80 MHz blocks (UNII-1, UNII-2A/2C DFS, UNII-3) and 2.4 GHz channels (`1/6/11` + rogue overlapping channels) to recommend the cleanest channels.
   - **Step 2 — Idle Hop-by-Hop Latency & Stability:** 30-packet gateway ping, 30-packet `1.1.1.1` ping (+ IPv6 if active), 5-hop traceroute, and active VPN/proxy detection.
   - **Step 3 — Uncached DNS Latency:** Benchmarks primary DNS, router, `1.1.1.1`, and `8.8.8.8` with randomized uncached queries and detects port-53 DNS interception.
   - **Step 4 — Throughput & Bufferbloat Under Load:** Runs macOS `networkQuality -v` for downlink/uplink capacity, idle vs. loaded responsiveness (RPM), HTTP loaded latency, and ECN/L4S status.
2. **ASUSWRT / Asuswrt-Merlin Optimization Playbook** (`SKILL.md`):
   - Isolates wired WAN speed from Wi-Fi speed via `AdaptiveQoS_InternetSpeed.asp`.
   - Checks hardware NAT acceleration (`Archer` / `Flow Cache`) on `Tools_Sysinfo.asp` before recommending Cake SQM vs. hardware-accelerated Adaptive QoS.
   - Safely locks 5 GHz (`100/80` or cleanest DFS/non-DFS 80 MHz block, avoiding TDWR `120–128`) and 2.4 GHz (`20 MHz` on cleanest of `1/6/11`).
3. **Before vs. After Comparison** (`scripts/wifi_doctor.py compare`):
   - Generates a Markdown comparison table with exact percentage deltas.

## Install as an Agent Skill

```bash
npx skills add prasadabhishek/asus-merlin-wifi-doctor
```

## Standalone CLI Usage (macOS)

Requires Python 3 (standard library only — zero external dependencies).

```bash
# Quick RF scan & cleanest channel recommendation
uv run scripts/wifi_doctor.py rf-scan --output /tmp/rf.json

# Full 4-step sequential baseline benchmark
uv run scripts/wifi_doctor.py benchmark --output /tmp/before.json

# After applying router changes, re-test and compare
uv run scripts/wifi_doctor.py benchmark --output /tmp/after.json
uv run scripts/wifi_doctor.py compare \
  --before /tmp/before.json \
  --after /tmp/after.json \
  --output /tmp/comparison.md
```

## License

MIT
