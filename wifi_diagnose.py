#!/usr/bin/env python3
"""Read-only Wi-Fi diagnosis for a home network (built for JioFiber, works on any router).

Run it on a laptop/PC connected to the slow Wi-Fi:  python3 wifi_diagnose.py
It changes nothing: it only reads link info, scans nearby networks and pings.
It never reads or prints Wi-Fi passwords.

The idea: compare latency/loss to the router (Wi-Fi hop) with latency/loss to the
internet (ISP hop). If the router hop is bad, the problem is Wi-Fi (signal, band,
channel congestion). If only the internet hop is bad, it's the line/ISP.
"""
import platform
import re
import subprocess
import sys

SYSTEM = platform.system()


def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def default_gateway():
    if SYSTEM == "Windows":
        m = re.search(r"0\.0\.0\.0\s+0\.0\.0\.0\s+(\d+\.\d+\.\d+\.\d+)", run(["route", "print", "0.0.0.0"]))
    elif SYSTEM == "Darwin":
        m = re.search(r"gateway:\s*(\S+)", run(["route", "-n", "get", "default"]))
    else:
        m = re.search(r"default via (\S+)", run(["ip", "route"]))
    return m.group(1) if m else "192.168.29.1"  # JioFiber default


def ping(host, count=20):
    flag = "-n" if SYSTEM == "Windows" else "-c"
    out = run(["ping", flag, str(count), host])
    loss = re.search(r"(\d+(?:\.\d+)?)% (?:packet )?loss", out)
    if SYSTEM == "Windows":
        avg = re.search(r"Average = (\d+)ms", out)
        jitter = None
    else:
        stats = re.search(r"= [\d.]+/([\d.]+)/[\d.]+/([\d.]+)", out)
        avg, jitter = (stats, stats) if stats else (None, None)
    return (
        float(loss.group(1)) if loss else None,
        float(avg.group(1)) if avg else None,
        float(jitter.group(2)) if jitter else None,
    )


def wifi_link():
    """Return raw text describing the current Wi-Fi link (signal, channel, rate)."""
    if SYSTEM == "Windows":
        out = run(["netsh", "wlan", "show", "interfaces"])
        keep = ("SSID", "Radio type", "Band", "Channel", "Receive rate", "Transmit rate", "Signal")
    elif SYSTEM == "Darwin":
        out = run(["system_profiler", "SPAirPortDataType"])
        out = out.split("Other Local Wi-Fi Networks")[0]
        keep = ("PHY Mode", "Channel", "Signal / Noise", "Transmit Rate")
    else:
        out = run(["nmcli", "-f", "IN-USE,SSID,CHAN,FREQ,RATE,SIGNAL,SECURITY", "dev", "wifi"])
        return "\n".join(l for l in out.splitlines() if l.startswith(("IN-USE", "*")))
    return "\n".join(l.strip() for l in out.splitlines() if l.strip().startswith(keep))


def nearby_networks():
    """Return [(ssid, channel)] for visible networks, to judge channel congestion."""
    nets = []
    if SYSTEM == "Windows":
        ssid = None
        for line in run(["netsh", "wlan", "show", "networks", "mode=bssid"]).splitlines():
            if m := re.match(r"\s*SSID \d+ : (.*)", line):
                ssid = m.group(1)
            elif m := re.match(r"\s*Channel\s*: (\d+)", line):
                nets.append((ssid, int(m.group(1))))
    elif SYSTEM == "Darwin":
        out = run(["system_profiler", "SPAirPortDataType"]).split("Other Local Wi-Fi Networks")[-1]
        for m in re.finditer(r"\n\s+(\S[^\n]*):\n\s+PHY Mode.*?\n\s+Channel: (\d+)", out):
            nets.append((m.group(1), int(m.group(2))))
    else:
        for line in run(["nmcli", "-t", "-f", "SSID,CHAN", "dev", "wifi"]).splitlines():
            ssid, _, chan = line.rpartition(":")
            if chan.isdigit():
                nets.append((ssid, int(chan)))
    return nets


def main():
    print(f"== Wi-Fi diagnosis ({SYSTEM}) — read-only ==\n")

    print("-- Current link --")
    link = wifi_link()
    print(link or "(could not read link info; are you on Wi-Fi?)")

    nets = nearby_networks()
    by_chan = {}
    for _, ch in nets:
        by_chan[ch] = by_chan.get(ch, 0) + 1
    print("\n-- Nearby networks per channel --")
    for ch in sorted(by_chan):
        band = "2.4 GHz" if ch <= 14 else "5 GHz"
        print(f"  ch {ch:>3} ({band}): {by_chan[ch]}")

    gw = default_gateway()
    print(f"\n-- Ping router ({gw}) --")
    gw_loss, gw_avg, gw_jit = ping(gw)
    print(f"  loss {gw_loss}%  avg {gw_avg} ms  jitter {gw_jit} ms")
    print("-- Ping internet (1.1.1.1) --")
    net_loss, net_avg, net_jit = ping("1.1.1.1")
    print(f"  loss {net_loss}%  avg {net_avg} ms  jitter {net_jit} ms")

    print("\n== Findings ==")
    findings = []
    if gw_avg is not None and (gw_avg > 10 or (gw_loss or 0) > 1):
        findings.append("Router hop is slow/lossy -> Wi-Fi problem (signal, band or congestion), not the ISP.")
    elif net_avg is not None and (net_avg > 60 or (net_loss or 0) > 1):
        findings.append("Router hop is fine but internet hop is slow -> line/ISP side; reboot the ONT, then call Jio (199).")
    sig = re.search(r"Signal\s*:\s*(\d+)%", link) or re.search(r"^\*.*\s(\d+)\s+\S+\s*$", link, re.M)
    if sig and int(sig.group(1)) < 50:
        findings.append(f"Weak signal ({sig.group(1)}%) -> move closer / remove obstructions / consider a mesh node.")
    rssi = re.search(r"Signal / Noise: (-\d+) dBm", link)
    if rssi and int(rssi.group(1)) < -67:
        findings.append(f"Weak signal ({rssi.group(1)} dBm) -> move closer / remove obstructions / consider a mesh node.")
    chan = re.search(r"Channel\s*:?\s*(\d+)", link) or re.search(r"^\*\s+.*?\s(\d{1,3})\s+\d+ MHz", link, re.M)
    if chan:
        ch = int(chan.group(1))
        if ch <= 14:
            findings.append(f"You are on 2.4 GHz (ch {ch}) -> join the 5 GHz SSID (usually '<name>_5G') for much higher speed.")
            if by_chan.get(ch, 0) > 3:
                quiet = min((1, 6, 11), key=lambda c: by_chan.get(c, 0))
                findings.append(f"Channel {ch} is crowded ({by_chan[ch]} networks) -> set the router's 2.4 GHz channel to {quiet}.")
        elif by_chan.get(ch, 0) > 3:
            findings.append(f"5 GHz channel {ch} is crowded ({by_chan[ch]} networks) -> pick a quieter 5 GHz channel in the router.")
    if gw_avg is None and net_avg is None and not link:
        print("- Could not measure anything (no Wi-Fi link or ping unavailable). Run this on a device on your Wi-Fi.")
        return 1
    print("\n".join(f"- {f}" for f in findings) or "- No obvious Wi-Fi problem; run a speed test next to the router and compare to your plan.")


if __name__ == "__main__":
    sys.exit(main())
