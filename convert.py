#!/usr/bin/env python3
import datetime as dt
import html
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

RUV1_BASE = "https://muninn.ruv.is/files/xml/ruv/"
RUV2_BASE = "https://muninn.ruv.is/files/xml/ruv2/"

# Output IDs that match typical IPTV epg_channel_id conventions
CHAN_MAP = {
    "ruv1": {"base": RUV1_BASE, "id": "ruv.is", "name": "RÚV"},
    "ruv2": {"base": RUV2_BASE, "id": "ruv2.is", "name": "RÚV 2"},
}

def fetch_text(url: str, timeout=30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "ruv-xmltv/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")

def pick_latest_xml(base_url: str) -> str:
    listing = fetch_text(base_url)

    # Find ANY tokens that look like paths ending in .xml
    # Works for HTML autoindex, S3 XML listings (<Key>...</Key>), etc.
    raw = re.findall(r'[\w./-]+\.xml', listing, flags=re.IGNORECASE)

    # Normalize and dedupe
    candidates = sorted(set(x.split("?")[0] for x in raw))

    # Filter out obvious non-targets (rare but harmless)
    candidates = [c for c in candidates if not c.lower().endswith("xmltv.dtd")]

    if not candidates:
        snippet = listing[:1200].replace("\n", "\\n")
        raise RuntimeError(
            f"No .xml filenames found at {base_url}. Page starts with: {snippet}"
        )

    latest = candidates[-1]

    # If it's already a full URL, return it.
    if latest.startswith("http://") or latest.startswith("https://"):
        return latest

    # If it's a path like "2026/01/14.xml" or "ruv/2026-01-14.xml",
    # join it to the base safely.
    return base_url.rstrip("/") + "/" + latest.lstrip("/")

def parse_kringla_schedule(xml_text: str):
    """
    Parses the RÚV 'schedule/service/event' format (Kringla-like) into a list:
      services = [{name, service_id, events:[{start, stop, title, desc}]}]
    """
    root = ET.fromstring(xml_text)
    if root.tag.lower() != "schedule":
        raise ValueError(f"Expected <schedule>, got <{root.tag}>")

    services_out = []
    for svc in root.findall(".//service"):
        service_id = svc.get("service-id") or svc.get("service_id") or ""
        service_name = svc.get("service-name") or svc.get("service_name") or ""

        events = []
        for ev in svc.findall("./event"):
            start_time = ev.get("start-time") or ev.get("start_time")
            duration = ev.get("duration")
            if not start_time or not duration:
                continue

            start_dt = dt.datetime.strptime(start_time.replace("T", " "), "%Y-%m-%d %H:%M:%S")
            h, m, s = duration.strip().split(":")
            stop_dt = start_dt + dt.timedelta(hours=int(h), minutes=int(m), seconds=int(s))

            title_el = ev.find("./title")
            desc_el = ev.find("./description")
            title = (title_el.text or "").strip() if title_el is not None else ""
            desc = (desc_el.text or "").strip() if desc_el is not None else ""

            events.append({"start": start_dt, "stop": stop_dt, "title": title, "desc": desc})

        services_out.append({"service_id": service_id, "service_name": service_name, "events": events})

    return services_out

def clamp_overlaps_shorten_previous(events):
    """
    Keep all start times. If an event overlaps the next, shorten its stop.
    Assumes events are sorted by start.
    """
    for i in range(len(events) - 1):
        if events[i]["stop"] > events[i + 1]["start"]:
            events[i]["stop"] = events[i + 1]["start"]
    # Drop any that became invalid
    return [e for e in events if e["stop"] > e["start"]]

def xmltv_time(d: dt.datetime) -> str:
    # Iceland is UTC year-round; and Muninn times are Iceland local => UTC.
    return d.strftime("%Y%m%d%H%M%S") + " +0000"

def emit_channel(out_lines, chan_id, display_name):
    out_lines.append(f'  <channel id="{html.escape(chan_id)}">')
    out_lines.append(f'    <display-name>{html.escape(display_name)}</display-name>')
    out_lines.append("  </channel>")

def emit_programme(out_lines, chan_id, e):
    out_lines.append(
        f'  <programme start="{html.escape(xmltv_time(e["start"]))}" stop="{html.escape(xmltv_time(e["stop"]))}" channel="{html.escape(chan_id)}">'
    )
    if e["title"]:
        out_lines.append(f'    <title>{html.escape(e["title"])}</title>')
    if e["desc"]:
        out_lines.append(f'    <desc>{html.escape(e["desc"])}</desc>')
    out_lines.append("  </programme>")

def main():
    out = []
    out.append('<?xml version="1.0" encoding="utf-8"?>')
    out.append('<!DOCTYPE tv SYSTEM "xmltv.dtd">')
    out.append('<tv generator-info-name="ruv-xmltv (muninn)">')


    # Convert each service feed into one XMLTV channel
    for key, meta in CHAN_MAP.items():
        latest_url = pick_latest_xml(meta["base"])
        xml_text = fetch_text(latest_url)

        services = parse_kringla_schedule(xml_text)

        # The file usually contains exactly one <service>, but we’ll handle multiples safely.
        # We output them all under the same XMLTV channel id for that station,
        # combining and sorting events.
        all_events = []
        display_name = meta["name"]

        for svc in services:
            if svc["service_name"]:
                # Keep the nicest service name if present
                display_name = svc["service_name"]
            all_events.extend(svc["events"])

        all_events.sort(key=lambda e: e["start"])
        all_events = clamp_overlaps_shorten_previous(all_events)

        emit_channel(out, meta["id"], display_name)
        for e in all_events:
            emit_programme(out, meta["id"], e)

    out.append("</tv>")
    sys.stdout.write("\n".join(out) + "\n")

if __name__ == "__main__":
    main()
