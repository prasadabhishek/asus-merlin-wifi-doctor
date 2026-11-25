---
name: asus-merlin-wifi-doctor
description: >-
  Diagnoses macOS Wi-Fi and home network bottlenecks using a strictly sequential
  4-step benchmark (RF congestion scan, idle hop-by-hop latency, uncached DNS,
  and networkQuality bufferbloat), guides or automates ASUSWRT / Asuswrt-Merlin
  router optimization via chrome-devtools, and outputs a Before vs. After
  comparison table. Use when the user asks to diagnose slow Wi-Fi, fix network
  instability or bufferbloat, find the cleanest Wi-Fi channels, or tune an ASUS
  or Asuswrt-Merlin router.
---

# ASUS Merlin Wi-Fi Doctor

## Overview

End-to-end diagnostic and optimization skill for macOS + home routers, with a specialized **ASUSWRT / Asuswrt-Merlin** browser playbook. Measures real bottlenecks empirically (skipping placebo OS tweaks), confirms router changes safely before applying, and verifies improvements with an identical **Before vs. After** benchmark.

## Dependencies

- **`chrome-devtools`** (MCP / skill): Optional. Used when the user is logged into their ASUSWRT / Asuswrt-Merlin admin UI in Chrome to inspect live settings, run the router's wired WAN speedtest, and apply channel/QoS fixes.
- **`uv`**: Used to run the bundled Python 3 stdlib helper script (`uv run`).

## Quick Start

Run the strictly sequential baseline benchmark and save structured JSON:

```bash
uv run ~/.gemini/config/skills/asus-merlin-wifi-doctor/scripts/wifi_doctor.py benchmark --output /tmp/wifi-before.json
```

After router/network changes are applied, re-run and generate the Before vs. After table:

```bash
uv run ~/.gemini/config/skills/asus-merlin-wifi-doctor/scripts/wifi_doctor.py benchmark --output /tmp/wifi-after.json
uv run ~/.gemini/config/skills/asus-merlin-wifi-doctor/scripts/wifi_doctor.py compare \
  --before /tmp/wifi-before.json \
  --after /tmp/wifi-after.json \
  --output /tmp/wifi-comparison.md
```

## Utility Scripts

`scripts/wifi_doctor.py` uses Python 3 standard library only (zero third-party dependencies) and writes full structured output to files.

### Subcommands

1. **`benchmark --output <path.json>`**
   Runs all 4 diagnostic steps **strictly sequentially** (never runs ping in parallel with `networkQuality` so idle latency is never polluted by load):
   - **Step 1 (Wi-Fi Link & RF Scan):** Parses `system_profiler SPAirPortDataType` and `netstat -I <if> -b -d` for PHY mode, band, channel, width, RSSI/noise/SNR, Tx rate, MCS index, interface errors/drops, and tallies nearby competing APs into 5 GHz 80 MHz blocks (UNII-1 `36–48`, UNII-2A DFS `52–64`, UNII-2C DFS `100–112`, `116–128` TDWR, `132–144`, UNII-3 `149–161`) and 2.4 GHz channels (`1`, `6`, `11`, plus rogue overlapping channels). Recommends the cleanest 5 GHz (DFS + non-DFS) and 2.4 GHz channels.
   - **Step 2 (Idle Hop-by-Hop Latency & Stability):** Pings default gateway (30 packets, `-i 0.2`), `1.1.1.1` (30 packets), IPv6 `2606:4700:4700::1111` (if an IPv6 default route is active), runs a 5-hop `traceroute -n -m 5`, and checks active proxies (`scutil --proxy`) and IPv4 VPN tunnels (`utun`/`wg`/`ppp`).
   - **Step 3 (DNS Latency):** Inspects `scutil --dns` and runs 5 uncached random-subdomain `dig` queries + 1 cached `apple.com` query against primary DNS, router IP, `1.1.1.1`, and `8.8.8.8`. Flags port-53 DNS interception when cached RTT to `1.1.1.1` is lower than minimum ICMP ping to `1.1.1.1`.
   - **Step 4 (Throughput & Bufferbloat Under Load):** Runs `networkQuality -v` and parses downlink Mbps, uplink Mbps, idle latency/RPM, loaded responsiveness/RPM, HTTP loaded latency, and ECN/L4S status.

2. **`rf-scan --output <path.json>`**
   Fast standalone Step 1 check (active Wi-Fi link + channel congestion tally). Run right after changing router channels to confirm the client reconnected on the target band, channel, and width (e.g. `80MHz`, not `20MHz`).

3. **`compare --before <before.json> --after <after.json> --output <report.md>`**
   Computes exact deltas and writes a Markdown **Before vs. After** comparison table to `--output`.

## Workflow

### 1. Run Baseline Benchmark (Sequential)
- Run `wifi_doctor.py benchmark --output /tmp/wifi-before.json`.
- Read `/tmp/wifi-before.json` and summarize exact measurements in a concise baseline table.

### 2. Rank Real Bottlenecks (Skip Placebo Tweaks)
Evaluate empirically:
- **Co-channel interference:** High SNR (`> 28 dB`) paired with depressed MCS (`<= 6`) and loud (`>= -65 dBm`) competing APs on the same 5 GHz 80 MHz block.
- **Bufferbloat:** Loaded latency (`networkQuality` responsiveness) spiking `> 2.5×` idle latency or `< 500 RPM`.
- **2.4 GHz width/overlap:** 2.4 GHz running `40 MHz` in a noisy environment (`noise > -80 dBm`) or overlapping a rogue non-1/6/11 channel.
- **Skip placebo fixes:** Do not recommend macOS `sysctl`/TCP tweaks, and do not recommend DNS changes if uncached DNS is already `< 30 ms`.

### 3. Identify Router & Modem (Read-Only)
- Identify router vendor via `arp -n <gateway>` and unauthenticated HTTP headers on `http://<gateway>` (never bypass auth).
- Check if a cable modem/gateway is reachable at `http://192.168.100.1` or `http://10.0.0.1` and verify from traceroute Hop 2 whether the modem is in Bridge Mode (no double-NAT).
- Ask for the user's subscribed ISP plan speed and router model (or inspect via `chrome-devtools` if the user is logged into the router UI in Chrome).

### 4. ASUSWRT / Asuswrt-Merlin Playbook (Confirm Before Applying!)
**ALWAYS ask the user for confirmation before applying router changes**, because wireless restarts drop Wi-Fi for 15–60 seconds (up to 60s for DFS CAC).

1. **Check Hardware Acceleration (`Tools_Sysinfo.asp`):** Verify `Archer` / `Flow Cache` (or `Runner`) HW NAT acceleration status and client counts.
2. **Isolate WAN vs. Wi-Fi Speed (`AdaptiveQoS_InternetSpeed.asp`):** Run the router's built-in wired Ookla WAN speedtest (`Go`) to measure true ISP/modem throughput directly at the router.
3. **5 GHz Channel & Width (`Advanced_Wireless_Content.asp`, `wl_unit = 1`):**
   - Lock to the cleanest 80 MHz block from the RF scan (prefer DFS `100` `[100–112]` or `132` `[132–144]` to escape crowded UNII-1/UNII-3; avoid 160 MHz across US TDWR weather-radar channels `120–128`).
   - **Critical ASUSWRT quirk:** Set `wl_bw = 3` (`80 MHz`) **first** so `wl_channel` populates `/80` values (e.g. `100/80`), then select `100/80` (**not** bare `100`, which saves as `20 MHz`!) and call `applyRule()`. Wait ~60s for DFS CAC.
   - Keep `802.11ax`, `WMM`, `OFDMA`, and `Beamforming` enabled (`Advanced_WAdvanced_Content.asp`).
4. **2.4 GHz Channel & Width (`Advanced_Wireless_Content.asp`, `wl_unit = 0`):**
   - Set `wl_bw = 1` (`20 MHz`) and `wl_channel` to the cleanest of `1`, `6`, or `11` (avoiding any rogue overlapping channel), then call `applyRule()`.
5. **Guest SSIDs / ISP Hotspots (`Guest_network.asp`):** Disable unused guest SSIDs and ISP public hotspots to reclaim airtime.
6. **QoS / Cake SQM (`QoS_EZQoS.asp`) — Match to Router CPU & ISP Plan:**
   - **ISP plan `<= 250–300 Mbps`:** Enable **Cake SQM** (`qos_type = 9` on Merlin) with **DOCSIS overhead** (`18` bytes, `mpu 64` for cable) and manual limits at ~90% of measured speed.
   - **ISP plan `>= 500 Mbps–1 Gbps` on mid-range ARM routers (e.g. RT-AX58U):** **Do NOT enable Cake**—it disables Broadcom Archer/Flow Cache HW acceleration and caps throughput around ~250–300 Mbps. Keep HW acceleration enabled; use hardware-accelerated **Adaptive QoS (`fq_codel`)** only if loaded responsiveness remains poor after fixing Wi-Fi.
7. **Post-Apply Verification:** Run `rf-scan` to confirm macOS reconnected to the 5 GHz SSID (not failed over to 2.4 GHz during DFS CAC) and shows the target channel and **`80MHz`** width.

### 5. Re-Test & Output Before vs. After Table
- Once confirmed on the target 5 GHz channel/width, run `wifi_doctor.py benchmark --output /tmp/wifi-after.json` and `wifi_doctor.py compare --before /tmp/wifi-before.json --after /tmp/wifi-after.json --output /tmp/wifi-comparison.md`.
- Present the **Before vs. After** comparison table and concise takeaways.

## Common Mistakes

- **Running ping and `networkQuality` in parallel:** Pollutes idle latency with load; always use the sequential `benchmark` subcommand.
- **Saving bare `100` instead of `100/80` on ASUSWRT:** Causes 5 GHz to come up at `20 MHz` instead of `80 MHz`. Always verify width with `rf-scan` after applying.
- **Enabling Cake SQM on a 1 Gbps line with a mid-range ARM router (e.g. RT-AX58U):** Disables hardware NAT acceleration and throttles a 1 Gbps line to ~250–300 Mbps. Always check wired WAN speed and HW acceleration first.
- **Forgetting macOS 2.4 GHz failover during DFS CAC:** Wait ~60 seconds for DFS Channel 100 CAC, then reconnect macOS to the 5 GHz SSID before running the After benchmark.
