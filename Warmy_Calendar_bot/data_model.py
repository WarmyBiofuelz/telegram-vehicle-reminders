from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Dict, List, Tuple


EVENT_MAP: Dict[str, str] = {
    "LV Kelių mokestis": "lv_road_toll",
    "LT Kelių mokestis": "lt_road_toll",
    "TA galiojimas": "inspection",
    "CA draudimas iki": "insurance",
    "Registracijos liudijimas": "registration_certificate",
}

EVENT_LABEL_LT: Dict[str, str] = {
    "lv_road_toll": "LV kelių mokestis",
    "lt_road_toll": "LT kelių mokestis",
    "inspection": "TA galiojimas",
    "insurance": "CA draudimas",
    "registration_certificate": "Registracijos liudijimas",
}


@dataclass
class DeadlineRecord:
    plate: str
    event_type: str
    expiry_date: dt.date | None


def normalize_event(event_raw: str) -> str | None:
    key = EVENT_MAP.get(event_raw.strip())
    return key


def latest_by_plate_event(records: List[Tuple[str, str, dt.date | None, dt.datetime | None]]) -> List[DeadlineRecord]:
    buckets: Dict[Tuple[str, str], Tuple[dt.date | None, dt.datetime | None]] = {}
    for plate, event_type, expiry, ts in records:
        k = (plate, event_type)
        prev = buckets.get(k)
        if prev is None:
            buckets[k] = (expiry, ts)
        else:
            prev_expiry, prev_ts = prev
            # Choose greater expiry; tie-breaker by newer timestamp
            if (expiry or dt.date.min) > (prev_expiry or dt.date.min):
                buckets[k] = (expiry, ts)
            elif (expiry == prev_expiry) and (ts and ((prev_ts is None) or ts > prev_ts)):
                buckets[k] = (expiry, ts)

    result: List[DeadlineRecord] = []
    for (plate, event_type), (expiry, _) in buckets.items():
        result.append(DeadlineRecord(plate=plate, event_type=event_type, expiry_date=expiry))
    return result


def compute_windows(today: dt.date, records: List[DeadlineRecord]) -> Tuple[List[DeadlineRecord], List[DeadlineRecord]]:
    upcoming: List[DeadlineRecord] = []
    expired: List[DeadlineRecord] = []
    for r in records:
        if r.event_type == "registration_certificate":
            continue
        if not r.expiry_date:
            continue
        delta = (r.expiry_date - today).days
        if delta in (5, 1):
            upcoming.append(r)
        elif delta < 0:
            expired.append(r)
    return upcoming, expired


def format_summary_lt(upcoming: List[DeadlineRecord], expired: List[DeadlineRecord]) -> str:
    lines: List[str] = []
    if upcoming:
        lines.append("Artėjantys (5 d., 1 d.):")
        for r in sorted(upcoming, key=lambda x: (x.expiry_date or dt.date.min, x.plate, x.event_type)):
            label = EVENT_LABEL_LT.get(r.event_type, r.event_type)
            date_str = (r.expiry_date or dt.date.min).isoformat()
            lines.append(f"{r.plate} — {label} — {date_str}")
        lines.append("")
    if expired:
        lines.append("Nebegalioja:")
        for r in sorted(expired, key=lambda x: (x.expiry_date or dt.date.min, x.plate, x.event_type)):
            label = EVENT_LABEL_LT.get(r.event_type, r.event_type)
            date_str = (r.expiry_date or dt.date.min).isoformat()
            lines.append(f"{r.plate} — {label} — nebegalioja nuo {date_str}")
    if not lines:
        return "Šiandien priminimų nėra."
    return "\n".join(lines)


