#!/usr/bin/env python3
"""Sequential macOS Wi-Fi & Home Network diagnostic benchmark and Before/After comparator."""

import argparse
import json
import random
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def run_cmd(cmd: list[str], timeout: int = 90) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"Command timed out after {timeout}s: {' '.join(cmd)}"
    except FileNotFoundError:
        return 127, "", f"Command not found: {cmd[0]}"


def detect_wifi_interface() -> str:
    rc, out, _ = run_cmd(["networksetup", "-listallhardwareports"], timeout=10)
    if rc == 0:
        lines = out.splitlines()
        for i, line in enumerate(lines):
            if "Wi-Fi" in line or "AirPort" in line:
                if i + 1 < len(lines) and "Device:" in lines[i + 1]:
                    return lines[i + 1].split("Device:")[1].strip()
    return "en0"


def parse_netstat_errors(iface: str) -> dict:
    rc, out, _ = run_cmd(["netstat", "-I", iface, "-b", "-d"], timeout=10)
    if rc != 0:
        return {"error": "netstat failed"}
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 12 and parts[0] == iface and "<Link#" in parts[2]:
            return {
                "ipkts": int(parts[4]),
                "ierrs": int(parts[5]),
                "ibytes": int(parts[6]),
                "opkts": int(parts[7]),
                "oerrs": int(parts[8]),
                "obytes": int(parts[9]),
                "coll": int(parts[10]),
                "drop": int(parts[11]),
            }
    return {}


def block_5g(ch: int) -> str:
    if 36 <= ch <= 48:
        return "UNII-1 (36-48)"
    if 52 <= ch <= 64:
        return "UNII-2A DFS (52-64)"
    if 100 <= ch <= 112:
        return "UNII-2C DFS (100-112)"
    if 116 <= ch <= 128:
        return "UNII-2C DFS (116-128, TDWR)"
    if 132 <= ch <= 144:
        return "UNII-2C DFS (132-144)"
    if 149 <= ch <= 161:
        return "UNII-3 (149-161)"
    if ch == 165:
        return "UNII-3 (165, 20MHz)"
    return f"Other ({ch})"


def parse_airport_data(iface: str) -> dict:
    rc, out, err = run_cmd(["system_profiler", "SPAirPortDataType"], timeout=25)
    if rc != 0:
        return {"error": err or "system_profiler failed"}

    # Isolate the target interface section
    lines = out.splitlines()
    in_iface = False
    section = None  # "current" | "other"
    current: dict = {}
    others: list[dict] = []
    cur_other: dict | None = None

    for line in lines:
        stripped = line.strip()
        if re.match(rf"^{re.escape(iface)}:$", stripped):
            in_iface = True
            continue
        if in_iface and re.match(r"^[a-z]+[0-9]+:$", stripped) and stripped != f"{iface}:":
            break
        if not in_iface:
            continue

        if stripped == "Current Network Information:":
            section = "current"
            continue
        if stripped == "Other Local Wi-Fi Networks:":
            section = "other"
            continue

        if section == "current":
            if ":" in stripped and not stripped.endswith(":"):
                k, v = [x.strip() for x in stripped.split(":", 1)]
                current[k] = v
        elif section == "other":
            if stripped.endswith(":"):
                if cur_other:
                    others.append(cur_other)
                cur_other = {"ssid": stripped[:-1]}
            elif cur_other is not None and ":" in stripped:
                k, v = [x.strip() for x in stripped.split(":", 1)]
                cur_other[k] = v

    if cur_other:
        others.append(cur_other)

    def parse_ch(ch_str: str) -> dict:
        m = re.match(r"(\d+)\s*\(([^,]+),\s*(\d+)MHz\)", ch_str or "")
        if not m:
            return {"raw": ch_str}
        return {"channel": int(m.group(1)), "band": m.group(2).strip(), "width_mhz": int(m.group(3))}

    def parse_sig(sig_str: str) -> dict:
        m = re.match(r"(-?\d+)\s*dBm\s*/\s*(-?\d+)\s*dBm", sig_str or "")
        if not m:
            return {"raw": sig_str}
        rssi, noise = int(m.group(1)), int(m.group(2))
        return {"rssi_dbm": rssi, "noise_dbm": noise, "snr_db": rssi - noise}

    cur_ch = parse_ch(current.get("Channel", ""))
    cur_sig = parse_sig(current.get("Signal / Noise", ""))
    active = {
        "phy_mode": current.get("PHY Mode"),
        "channel_raw": current.get("Channel"),
        **cur_ch,
        **cur_sig,
        "transmit_rate_mbps": int(current["Transmit Rate"]) if current.get("Transmit Rate", "").isdigit() else current.get("Transmit Rate"),
        "mcs_index": int(current["MCS Index"]) if current.get("MCS Index", "").isdigit() else current.get("MCS Index"),
        "security": current.get("Security"),
    }

    # Tally competing APs
    blocks_5g: dict[str, list[dict]] = {
        "UNII-1 (36-48)": [],
        "UNII-2A DFS (52-64)": [],
        "UNII-2C DFS (100-112)": [],
        "UNII-2C DFS (116-128, TDWR)": [],
        "UNII-2C DFS (132-144)": [],
        "UNII-3 (149-161)": [],
    }
    ch_2g: dict[int, list[dict]] = {1: [], 6: [], 11: []}
    rogue_2g: list[dict] = []

    for ap in others:
        ch_info = parse_ch(ap.get("Channel", ""))
        sig_info = parse_sig(ap.get("Signal / Noise", ""))
        ch_num = ch_info.get("channel")
        band = ch_info.get("band", "")
        entry = {
            "channel": ch_num,
            "width_mhz": ch_info.get("width_mhz"),
            "rssi_dbm": sig_info.get("rssi_dbm"),
            "noise_dbm": sig_info.get("noise_dbm"),
            "security": ap.get("Security"),
        }
        if "5GHz" in band and ch_num is not None:
            blk = block_5g(ch_num)
            blocks_5g.setdefault(blk, []).append(entry)
        elif "2GHz" in band and ch_num is not None:
            if ch_num in ch_2g:
                ch_2g[ch_num].append(entry)
            else:
                rogue_2g.append(entry)

    def score_aps(aps: list[dict]) -> tuple[int, int]:
        # Lower is cleaner: (count of loud APs >= -75 dBm, strongest rssi or -100)
        loud = sum(1 for a in aps if (a.get("rssi_dbm") or -100) >= -75)
        strongest = max((a.get("rssi_dbm") or -100 for a in aps), default=-100)
        return (loud, strongest, len(aps))

    # Pick cleanest 5 GHz (excluding TDWR 116-128 from primary DFS rec)
    non_tdwr_5g = {k: v for k, v in blocks_5g.items() if "TDWR" not in k}
    cleanest_5g_block = min(non_tdwr_5g.items(), key=lambda kv: score_aps(kv[1]))[0]
    non_dfs_5g = {k: v for k, v in blocks_5g.items() if "DFS" not in k and "165" not in k}
    cleanest_5g_non_dfs = min(non_dfs_5g.items(), key=lambda kv: score_aps(kv[1]))[0]

    # Pick cleanest 2.4 GHz (penalize channels within 4 of a rogue channel)
    def score_2g(ch_num: int, aps: list[dict]) -> tuple:
        rogue_overlap = sum(1 for r in rogue_2g if r.get("channel") and abs(r["channel"] - ch_num) <= 4)
        loud = sum(1 for a in aps if (a.get("rssi_dbm") or -100) >= -70)
        strongest = max((a.get("rssi_dbm") or -100 for a in aps), default=-100)
        return (rogue_overlap, loud, len(aps), strongest)

    cleanest_2g_ch = min(ch_2g.items(), key=lambda kv: score_2g(kv[0], kv[1]))[0]

    summary_5g = {
        blk: {
            "count": len(aps),
            "strongest_dbm": max((a["rssi_dbm"] for a in aps if a["rssi_dbm"] is not None), default=None),
            "aps": aps,
        }
        for blk, aps in blocks_5g.items()
    }
    summary_2g = {
        str(ch): {
            "count": len(aps),
            "strongest_dbm": max((a["rssi_dbm"] for a in aps if a["rssi_dbm"] is not None), default=None),
            "aps": aps,
        }
        for ch, aps in ch_2g.items()
    }

    return {
        "interface": iface,
        "active_link": active,
        "netstat": parse_netstat_errors(iface),
        "rf_scan": {
            "total_competing_aps": len(others),
            "band_5ghz_blocks": summary_5g,
            "band_2ghz_channels": summary_2g,
            "band_2ghz_rogue_overlapping": rogue_2g,
            "recommendations": {
                "cleanest_5ghz_block": cleanest_5g_block,
                "cleanest_5ghz_non_dfs_block": cleanest_5g_non_dfs,
                "cleanest_2ghz_channel": cleanest_2g_ch,
            },
        },
    }


def get_default_gateway() -> str | None:
    rc, out, _ = run_cmd(["route", "-n", "get", "default"], timeout=10)
    if rc == 0:
        m = re.search(r"gateway:\s*(\S+)", out)
        if m:
            return m.group(1)
    return None


def run_ping(target: str, count: int = 30, ipv6: bool = False) -> dict:
    cmd = ["ping6" if ipv6 else "ping", "-c", str(count), "-i", "0.2", target]
    rc, out, err = run_cmd(cmd, timeout=30)
    loss_m = re.search(r"([\d.]+)%\s+packet loss", out)
    rtt_m = re.search(r"min/avg/max/stddev\s*=\s*([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)\s*ms", out)
    if not rtt_m:
        return {"target": target, "ok": False, "error": (err or out).strip()[:200]}
    return {
        "target": target,
        "ok": True,
        "packets": count,
        "loss_pct": float(loss_m.group(1)) if loss_m else None,
        "min_ms": float(rtt_m.group(1)),
        "avg_ms": float(rtt_m.group(2)),
        "max_ms": float(rtt_m.group(3)),
        "stddev_ms": float(rtt_m.group(4)),
    }


def step2_idle_latency() -> dict:
    gw = get_default_gateway()
    gw_ping = run_ping(gw, 30) if gw else {"ok": False, "error": "No default gateway"}
    cf_ping = run_ping("1.1.1.1", 30)

    rc6, _, _ = run_cmd(["route", "-n", "get", "-inet6", "default"], timeout=5)
    if rc6 == 0:
        ipv6_ping = run_ping("2606:4700:4700::1111", 30, ipv6=True)
    else:
        ipv6_ping = {"active": False}

    _, tr_out, _ = run_cmd(["traceroute", "-n", "-m", "5", "-w", "2", "1.1.1.1"], timeout=25)
    _, proxy_out, _ = run_cmd(["scutil", "--proxy"], timeout=5)
    _, if_out, _ = run_cmd(["ifconfig"], timeout=5)
    vpn_ifaces = re.findall(r"^((?:utun|tun|wg|ppp|ipsec)\d+):.*?(?=\n[a-z0-9]+:|\Z)", if_out, re.S | re.M)
    active_vpns = []
    for m in re.finditer(r"^((?:utun|tun|wg|ppp|ipsec)\d+):(.*?)(?=\n[a-z0-9]+:|\Z)", if_out, re.S | re.M):
        if "inet " in m.group(2):
            ip_m = re.search(r"inet\s+(\S+)", m.group(2))
            active_vpns.append({"interface": m.group(1), "ipv4": ip_m.group(1) if ip_m else None})

    return {
        "gateway": gw,
        "gateway_ping": gw_ping,
        "cloudflare_1_1_1_1_ping": cf_ping,
        "ipv6_ping": ipv6_ping,
        "traceroute_5hop": tr_out.strip().splitlines(),
        "active_ipv4_vpn_tunnels": active_vpns,
        "has_http_proxy": "HTTPEnable : 1" in proxy_out or "HTTPSEnable : 1" in proxy_out,
    }


def step3_dns_latency(gw: str | None, cf_min_ping_ms: float | None) -> dict:
    _, dns_out, _ = run_cmd(["scutil", "--dns"], timeout=5)
    m = re.search(r"nameserver\[0\]\s*:\s*(\S+)", dns_out)
    primary = m.group(1) if m else None

    servers = []
    for s in [primary, gw, "1.1.1.1", "8.8.8.8"]:
        if s and s not in servers:
            servers.append(s)

    results = {}
    ts = int(time.time())
    for srv in servers:
        uncached = []
        for i in range(1, 6):
            q = f"wd-{ts}-{random.randint(1000,99999)}-{i}.example.com"
            _, out, _ = run_cmd(["dig", "+tries=1", "+time=3", f"@{srv}", q, "A"], timeout=10)
            qm = re.search(r"Query time:\s*(\d+)\s*msec", out)
            if qm:
                uncached.append(int(qm.group(1)))
        _, c_out, _ = run_cmd(["dig", "+tries=1", "+time=3", f"@{srv}", "apple.com", "A"], timeout=10)
        cm = re.search(r"Query time:\s*(\d+)\s*msec", c_out)
        cached_ms = int(cm.group(1)) if cm else None
        results[srv] = {
            "uncached_ms": uncached,
            "uncached_avg_ms": round(sum(uncached) / len(uncached), 1) if uncached else None,
            "cached_apple_ms": cached_ms,
        }

    port53_intercepted = False
    cf_cached = results.get("1.1.1.1", {}).get("cached_apple_ms")
    if cf_cached is not None and cf_min_ping_ms is not None and cf_cached < cf_min_ping_ms - 2.0:
        port53_intercepted = True

    return {
        "primary_system_dns": primary,
        "servers": results,
        "port53_interception_suspected": port53_intercepted,
    }


def step4_network_quality() -> dict:
    rc, out, err = run_cmd(["networkQuality", "-v"], timeout=90)
    if rc != 0:
        return {"ok": False, "error": err or out}

    def grab(pattern: str, cast=float):
        m = re.search(pattern, out)
        return cast(m.group(1)) if m else None

    return {
        "ok": True,
        "downlink_mbps": grab(r"Downlink capacity:\s*([\d.]+)\s*Mbps"),
        "uplink_mbps": grab(r"Uplink capacity:\s*([\d.]+)\s*Mbps"),
        "idle_rpm": grab(r"Idle Latency:\s*\n\s*(\d+)\s*RPM", int),
        "idle_ms": grab(r"Idle Latency:\s*\n\s*\d+\s*RPM\s*\(([\d.]+)\s*milliseconds\)"),
        "responsiveness_rating": grab(r"Responsiveness:\s*(\w+)", str),
        "loaded_rpm": grab(r"Responsiveness:\s*\w+\s*\n\s*(\d+)\s*RPM", int),
        "loaded_ms": grab(r"Responsiveness:\s*\w+\s*\n\s*\d+\s*RPM\s*\(([\d.]+)\s*milliseconds\)"),
        "http_loaded_ms": grab(r"HTTP loaded:\s*\d+\s*RPM\s*\(([\d.]+)\s*milliseconds\)"),
        "ecn_l4s": grab(r"(ECN [^\n]+)", str),
    }


def cmd_rf_scan(output_path: str) -> None:
    iface = detect_wifi_interface()
    data = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "step1_wifi_rf": parse_airport_data(iface),
    }
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(data, indent=2))
    link = data["step1_wifi_rf"]["active_link"]
    recs = data["step1_wifi_rf"]["rf_scan"]["recommendations"]
    print(f"Success! RF scan written to: {output_path}")
    print(
        f"Active: {link.get('phy_mode')} | {link.get('channel_raw')} | "
        f"RSSI/Noise: {link.get('rssi_dbm')}/{link.get('noise_dbm')} dBm (SNR {link.get('snr_db')} dB) | "
        f"Tx: {link.get('transmit_rate_mbps')} Mbps (MCS {link.get('mcs_index')})"
    )
    print(
        f"Cleanest 5GHz: {recs['cleanest_5ghz_block']} (non-DFS: {recs['cleanest_5ghz_non_dfs_block']}) | "
        f"Cleanest 2.4GHz: Ch {recs['cleanest_2ghz_channel']}"
    )


def cmd_benchmark(output_path: str) -> None:
    iface = detect_wifi_interface()
    print("[1/4] Scanning Wi-Fi link & RF congestion...", file=sys.stderr)
    s1 = parse_airport_data(iface)

    print("[2/4] Measuring idle hop-by-hop latency (30 pkts gateway + 30 pkts 1.1.1.1 + traceroute)...", file=sys.stderr)
    s2 = step2_idle_latency()

    print("[3/4] Benchmarking uncached DNS latency...", file=sys.stderr)
    cf_min = s2.get("cloudflare_1_1_1_1_ping", {}).get("min_ms")
    s3 = step3_dns_latency(s2.get("gateway"), cf_min)

    print("[4/4] Running networkQuality -v (throughput & bufferbloat under load)...", file=sys.stderr)
    s4 = step4_network_quality()

    data = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "step1_wifi_rf": s1,
        "step2_idle_latency": s2,
        "step3_dns": s3,
        "step4_network_quality": s4,
    }
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(data, indent=2))
    print(f"Success! Full 4-step sequential benchmark written to: {output_path}")


def pct_delta(before: float | None, after: float | None, higher_is_better: bool = True) -> str:
    if before is None or after is None or before == 0:
        return "—"
    pct = ((after - before) / before) * 100.0
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.1f}%"


def cmd_compare(before_path: str, after_path: str, output_path: str) -> None:
    b = json.loads(Path(before_path).read_text())
    a = json.loads(Path(after_path).read_text())

    b1, a1 = b["step1_wifi_rf"]["active_link"], a["step1_wifi_rf"]["active_link"]
    b2, a2 = b["step2_idle_latency"], a["step2_idle_latency"]
    b3, a3 = b["step3_dns"], a["step3_dns"]
    b4, a4 = b["step4_network_quality"], a["step4_network_quality"]

    b_dns_srv = b3.get("primary_system_dns")
    a_dns_srv = a3.get("primary_system_dns")
    b_dns = b3.get("servers", {}).get(b_dns_srv, {}).get("uncached_avg_ms")
    a_dns = a3.get("servers", {}).get(a_dns_srv, {}).get("uncached_avg_ms")

    rows = [
        ("Wi-Fi Channel / Width", b1.get("channel_raw"), a1.get("channel_raw"), "—"),
        ("Signal / Noise (SNR)", f"{b1.get('rssi_dbm')}/{b1.get('noise_dbm')} dBm ({b1.get('snr_db')} dB)", f"{a1.get('rssi_dbm')}/{a1.get('noise_dbm')} dBm ({a1.get('snr_db')} dB)", "—"),
        ("Tx Rate / MCS", f"{b1.get('transmit_rate_mbps')} Mbps (MCS {b1.get('mcs_index')})", f"{a1.get('transmit_rate_mbps')} Mbps (MCS {a1.get('mcs_index')})", pct_delta(b1.get("transmit_rate_mbps"), a1.get("transmit_rate_mbps"))),
        ("Gateway Ping Avg / σ", f"{b2['gateway_ping'].get('avg_ms')} / {b2['gateway_ping'].get('stddev_ms')} ms", f"{a2['gateway_ping'].get('avg_ms')} / {a2['gateway_ping'].get('stddev_ms')} ms", pct_delta(b2["gateway_ping"].get("avg_ms"), a2["gateway_ping"].get("avg_ms"), False)),
        ("1.1.1.1 Ping Avg / σ", f"{b2['cloudflare_1_1_1_1_ping'].get('avg_ms')} / {b2['cloudflare_1_1_1_1_ping'].get('stddev_ms')} ms", f"{a2['cloudflare_1_1_1_1_ping'].get('avg_ms')} / {a2['cloudflare_1_1_1_1_ping'].get('stddev_ms')} ms", pct_delta(b2["cloudflare_1_1_1_1_ping"].get("avg_ms"), a2["cloudflare_1_1_1_1_ping"].get("avg_ms"), False)),
        ("DNS Uncached Avg", f"{b_dns} ms ({b_dns_srv})", f"{a_dns} ms ({a_dns_srv})", pct_delta(b_dns, a_dns, False)),
        ("Downlink (networkQuality)", f"{b4.get('downlink_mbps')} Mbps", f"{a4.get('downlink_mbps')} Mbps", pct_delta(b4.get("downlink_mbps"), a4.get("downlink_mbps"))),
        ("Uplink (networkQuality)", f"{b4.get('uplink_mbps')} Mbps", f"{a4.get('uplink_mbps')} Mbps", pct_delta(b4.get("uplink_mbps"), a4.get("uplink_mbps"))),
        ("Idle Latency", f"{b4.get('idle_ms')} ms ({b4.get('idle_rpm')} RPM)", f"{a4.get('idle_ms')} ms ({a4.get('idle_rpm')} RPM)", pct_delta(b4.get("idle_ms"), a4.get("idle_ms"), False)),
        ("Loaded Responsiveness", f"{b4.get('loaded_ms')} ms ({b4.get('loaded_rpm')} RPM)", f"{a4.get('loaded_ms')} ms ({a4.get('loaded_rpm')} RPM)", pct_delta(b4.get("loaded_rpm"), a4.get("loaded_rpm"))),
        ("HTTP Loaded Latency", f"{b4.get('http_loaded_ms')} ms", f"{a4.get('http_loaded_ms')} ms", pct_delta(b4.get("http_loaded_ms"), a4.get("http_loaded_ms"), False)),
    ]

    md_lines = [
        "### Before vs. After Benchmark Comparison",
        "",
        "| Metric | Before | After | Delta |",
        "|---|---|---|---|",
    ]
    for metric, bv, av, delta in rows:
        md_lines.append(f"| **{metric}** | `{bv}` | **`{av}`** | {delta} |")

    report = "\n".join(md_lines) + "\n"
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(report)
    print(f"Success! Comparison table written to: {output_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="macOS Wi-Fi & Home Network Doctor CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_rf = sub.add_parser("rf-scan", help="Run Step 1 Wi-Fi link & RF congestion scan")
    p_rf.add_argument("--output", required=True, help="Path to output JSON file")

    p_b = sub.add_parser("benchmark", help="Run full 4-step sequential benchmark")
    p_b.add_argument("--output", required=True, help="Path to output JSON file")

    p_c = sub.add_parser("compare", help="Generate Before vs. After Markdown table")
    p_c.add_argument("--before", required=True, help="Before benchmark JSON")
    p_c.add_argument("--after", required=True, help="After benchmark JSON")
    p_c.add_argument("--output", required=True, help="Output Markdown path")

    args = parser.parse_args()
    if args.command == "rf-scan":
        cmd_rf_scan(args.output)
    elif args.command == "benchmark":
        cmd_benchmark(args.output)
    elif args.command == "compare":
        cmd_compare(args.before, args.after, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
