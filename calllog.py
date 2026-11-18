#!/usr/bin/env python3
"""Operation Hang Up v0.2 — a local log of unwanted calls.

Commands: add, quick, list, repeats, case, registry, backup, restore.

Defense only. This tool records what happened. It does not dial, text,
look up, file a complaint, or contact anyone. Phone data stays in
data/ and is not for any public copy. An encrypted backup may be kept
at backup/calllog.enc; the key lives outside this folder.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tarfile
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from zoneinfo import ZoneInfo

VERSION = "v0.2"
TZ = ZoneInfo("America/Chicago")
KINDS = ("live", "recording")
CASE_MIN = 3
STORE_VERSION = 2
REGISTRY_HONOR_DAYS = 31
WINDOW_DAYS = 365
DEFAULT_KEY_DIR = Path.home() / ".config" / "hang-up"
KEY_NAME = "key"
BACKUP_HEADER = "HANGUP-BACKUP-v1"
ALGORITHM = "fernet"

# Published rules this kit checks against. Not legal advice.
DNC_SOURCES = [
    (
        "47 C.F.R. § 64.1200(c)(2)",
        "https://www.ecfr.gov/current/title-47/chapter-I/subchapter-B/part-64/subpart-L/section-64.1200",
        "Sellers must honor National Do Not Call registrations. The safe harbor "
        "requires using a registry version obtained no more than 31 days before a call.",
    ),
    (
        "FCC enforcement order (2009)",
        "https://www.fcc.gov/eb/Orders/2009/DOC-307630A1.html",
        "Entities making telephone solicitations must honor do-not-call "
        "registrations starting no later than 31 days after a number is placed "
        "on the National Do Not Call Registry.",
    ),
    (
        "FTC National Do Not Call Registry (consumer article, archived)",
        "https://web.archive.org/web/20210510152337/https://www.consumer.ftc.gov/articles/0108-national-do-not-call-registry",
        "A number should appear on the Registry the next day, but it can take "
        "up to 31 days for sales calls to stop.",
    ),
    (
        "FTC Do Not Call Databook 2025",
        "https://search.ftc.gov/system/files/ftc_gov/pdf/DNC-Data-Book-2025.pdf",
        "Telemarketers and sellers must remove numbers added to the Registry "
        "from their call lists at least every 31 days.",
    ),
    (
        "47 U.S.C. § 227(c)(5)",
        "https://www.law.cornell.edu/uscode/text/47/227",
        "A private right of action is available to a person who has received "
        "more than one telephone call within any 12-month period by or on behalf "
        "of the same entity in violation of the regulations prescribed under "
        "that subsection ($500 per violation, up to $1,500 if willful or knowing).",
    ),
    (
        "National Do Not Call Registry",
        "https://www.donotcall.gov",
        "Free consumer registration; registrations do not expire.",
    ),
]

KIND_WORDS = {
    "live": "live",
    "person": "live",
    "human": "live",
    "recording": "recording",
    "recorded": "recording",
    "prerecorded": "recording",
    "pre-recorded": "recording",
    "voicemail": "recording",
    "vm": "recording",
    "robocall": "recording",
    "robo": "recording",
}

FILLER = {
    "a",
    "an",
    "the",
    "got",
    "get",
    "from",
    "about",
    "call",
    "called",
    "calls",
    "phone",
    "spam",
    "junk",
    "scam",
    "unwanted",
    "telemarketing",
    "telemarketer",
    "sales",
    "robo",
    "robocall",
    "voicemail",
    "vm",
}


class CallLogError(Exception):
    """User-facing error for a bad number, date, or command."""


def home() -> Path:
    raw = os.environ.get("HANGUP_HOME", "").strip()
    return Path(raw).resolve() if raw else Path(__file__).resolve().parent


def data_dir() -> Path:
    path = home() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def evidence_dir() -> Path:
    path = data_dir() / "evidence"
    path.mkdir(parents=True, exist_ok=True)
    return path


def store_path() -> Path:
    raw = os.environ.get("HANGUP_STORE", "").strip()
    return Path(raw).resolve() if raw else data_dir() / "calls.json"


def cases_dir() -> Path:
    path = data_dir() / "cases"
    path.mkdir(parents=True, exist_ok=True)
    return path


def backup_path() -> Path:
    raw = os.environ.get("HANGUP_BACKUP", "").strip()
    if raw:
        return Path(raw).resolve()
    path = home() / "backup"
    path.mkdir(parents=True, exist_ok=True)
    return path / "calllog.enc"


def key_path() -> Path:
    raw = os.environ.get("HANGUP_KEY_PATH", "").strip()
    if raw:
        return Path(raw).resolve()
    raw_dir = os.environ.get("HANGUP_KEY_DIR", "").strip()
    folder = Path(raw_dir).resolve() if raw_dir else DEFAULT_KEY_DIR
    return folder / KEY_NAME


def normalize_number(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        raise CallLogError("Need a phone number with digits.")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) < 7 or len(digits) > 15:
        raise CallLogError("That number does not look usable (need 7 to 15 digits).")
    return digits


def display_number(digits: str) -> str:
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return digits


def parse_when(raw: str | None) -> datetime:
    text = (raw or "now").strip()
    if text.lower() == "now":
        return datetime.now(TZ)
    for fmt in (
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %I:%M%p",
        "%Y-%m-%d %I:%M %p",
        "%Y-%m-%d %I%p",
        "%Y-%m-%d %I %p",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(text.replace(".", "").upper() if "p" in text.lower() or "a" in text.lower() else text, fmt).replace(tzinfo=TZ)
        except ValueError:
            try:
                return datetime.strptime(text, fmt).replace(tzinfo=TZ)
            except ValueError:
                continue
    # Allow "2026-11-18 2:30pm"
    relaxed = re.sub(r"\s+", " ", text.strip())
    match = re.fullmatch(
        r"(\d{4}-\d{2}-\d{2})(?:\s+(\d{1,2})(?::(\d{2}))?\s*([ap]m)?)?",
        relaxed,
        flags=re.I,
    )
    if match:
        day = datetime.strptime(match.group(1), "%Y-%m-%d")
        if match.group(2):
            hour = int(match.group(2))
            minute = int(match.group(3) or 0)
            ampm = (match.group(4) or "").lower()
            if ampm:
                if hour == 12:
                    hour = 0 if ampm == "am" else 12
                elif ampm == "pm":
                    hour += 12
            return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)
        return datetime(day.year, day.month, day.day, 0, 0, tzinfo=TZ)
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError as exc:
        raise CallLogError(
            "Date needs to be 'YYYY-MM-DD HH:MM', an ISO stamp, or 'now'."
        ) from exc
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=TZ)
    return stamp.astimezone(TZ)


def parse_registry_date(raw: str | None) -> date:
    text = (raw or "today").strip()
    if text.lower() in {"today", "now"}:
        return datetime.now(TZ).date()
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise CallLogError("Registry date needs to be 'YYYY-MM-DD' or 'today'.") from exc


def load_store(path: Path | None = None) -> dict[str, Any]:
    path = path or store_path()
    if not path.is_file():
        return {"version": STORE_VERSION, "registry": None, "calls": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CallLogError(f"Call log is not valid JSON: {path}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("calls"), list):
        raise CallLogError(f"Call log is missing a 'calls' list: {path}")
    data.setdefault("version", STORE_VERSION)
    data.setdefault("registry", None)
    return data


def save_store(data: dict[str, Any], path: Path | None = None) -> None:
    path = path or store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data["version"] = STORE_VERSION
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _next_id(calls: list[dict[str, Any]]) -> int:
    used = [int(c["id"]) for c in calls if isinstance(c.get("id"), int) or str(c.get("id", "")).isdigit()]
    return (max(used) + 1) if used else 1


def company_key(raw: str | None) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", (raw or "").lower()).strip()
    return re.sub(r"\s+", " ", text)


def company_of(row: dict[str, Any]) -> str:
    explicit = (row.get("company") or "").strip()
    if explicit:
        return explicit
    name = (row.get("caller_id") or "").strip()
    if name and company_key(name) not in {"", "unknown"}:
        return name
    return ""


def _copy_evidence(paths: list[str] | None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not paths:
        return records
    dest_dir = evidence_dir()
    for raw in paths:
        src = Path(raw).expanduser()
        if not src.is_file():
            raise CallLogError(f"Evidence file not found: {raw}")
        payload = src.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        suffix = src.suffix.lower()
        stored = f"{digest}{suffix}"
        dest = dest_dir / stored
        if not dest.exists():
            dest.write_bytes(payload)
        records.append(
            {
                "original_name": src.name,
                "stored_as": stored,
                "sha256": digest,
                "bytes": len(payload),
            }
        )
    return records


def add_call(
    number: str,
    when: str | None,
    caller_id: str,
    pitch: str,
    kind: str,
    company: str | None = None,
    evidence: list[str] | None = None,
    raw_text: str | None = None,
    auto_backup: bool = True,
) -> dict[str, Any]:
    kind = (kind or "").strip().lower()
    if kind not in KINDS:
        raise CallLogError("Kind must be 'live' or 'recording'.")
    name = (caller_id or "").strip()
    pitch_text = (pitch or "").strip()
    if not name:
        raise CallLogError("Need a caller-ID name (use 'unknown' if the phone showed nothing).")
    if not pitch_text:
        raise CallLogError("Need a short note about what they pitched.")
    stamp = parse_when(when)
    digits = normalize_number(number)
    data = load_store()
    entry: dict[str, Any] = {
        "id": _next_id(data["calls"]),
        "number": digits,
        "number_display": display_number(digits),
        "when": stamp.isoformat(timespec="seconds"),
        "caller_id": name,
        "pitch": pitch_text,
        "kind": kind,
        "logged_at": datetime.now(TZ).isoformat(timespec="seconds"),
    }
    company_name = (company or "").strip()
    if company_name:
        entry["company"] = company_name
    if raw_text:
        entry["raw_text"] = raw_text
    records = _copy_evidence(evidence)
    if records:
        entry["evidence"] = records
    data["calls"].append(entry)
    save_store(data)
    if auto_backup:
        write_backup()
    return entry


_PHONE_RE = re.compile(
    r"""(?x)
    (?<!\d)
    (?:\+?1[\s\-.]*)?
    (?:
        \(\s*\d{3}\s*\)[\s\-.]*\d{3}[\s\-.]?\d{4}
        | \d{3}[\s\-.]\d{3}[\s\-.]\d{4}
        | \d{10}
    )
    (?!\d)
    """
)
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_TIME_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)\b", flags=re.I)
_TIME_24_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)(?!\s*[ap]m)\b", flags=re.I)


def parse_quick(text: str, company: str | None = None) -> dict[str, Any]:
    """Best-guess parse of a free-text call note. Asks nothing."""
    raw = (text or "").strip()
    if not raw:
        raise CallLogError("Need a short description of the call.")
    work = raw
    phone_match = _PHONE_RE.search(work)
    if not phone_match:
        raise CallLogError("Could not find a phone number in that note.")
    number = normalize_number(phone_match.group(0))
    work = work[: phone_match.start()] + " " + work[phone_match.end() :]

    date_match = _DATE_RE.search(work)
    day: date | None = None
    if date_match:
        day = datetime.strptime(date_match.group(1), "%Y-%m-%d").date()
        work = work[: date_match.start()] + " " + work[date_match.end() :]

    hour = minute = None
    ampm = ""
    time_match = _TIME_RE.search(work)
    time_24 = _TIME_24_RE.search(work)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2) or 0)
        ampm = re.sub(r"[.\s]", "", time_match.group(3)).lower()
        work = work[: time_match.start()] + " " + work[time_match.end() :]
    elif time_24:
        hour = int(time_24.group(1))
        minute = int(time_24.group(2))
        work = work[: time_24.start()] + " " + work[time_24.end() :]

    if day is None and hour is None:
        stamp = datetime.now(TZ)
    else:
        if day is None:
            day = datetime.now(TZ).date()
        if hour is None:
            hour, minute = 0, 0
        elif ampm:
            if hour == 12:
                hour = 0 if ampm == "am" else 12
            elif ampm == "pm":
                hour += 12
        stamp = datetime(day.year, day.month, day.day, hour, minute or 0, tzinfo=TZ)

    kind = "live"
    kind_found = False
    tokens = re.findall(r"[A-Za-z0-9&'+-]+", work)
    kept: list[str] = []
    for tok in tokens:
        mapped = KIND_WORDS.get(tok.lower())
        if mapped and not kind_found:
            kind = mapped
            kind_found = True
            continue
        kept.append(tok)

    meaningful = [t for t in kept if t.lower() not in FILLER]
    name = "unknown"
    pitch = "unspecified"
    parsed_company = ""

    caps = []
    rest: list[str] = []
    grabbing_caps = True
    for tok in meaningful:
        if grabbing_caps and tok.isupper() and any(ch.isalpha() for ch in tok) and len(tok) > 1:
            caps.append(tok)
            continue
        grabbing_caps = False
        rest.append(tok)

    if caps:
        parsed_company = " ".join(caps)
        name = parsed_company
        pitch = " ".join(rest) if rest else parsed_company
    elif meaningful:
        # A leading proper-name run, then the pitch; otherwise the whole remainder is the pitch.
        if len(meaningful) >= 2 and meaningful[0][:1].isupper():
            name_parts = [meaningful[0]]
            idx = 1
            while idx < len(meaningful) - 1 and meaningful[idx][:1].isupper():
                name_parts.append(meaningful[idx])
                idx += 1
            if idx < len(meaningful):
                name = " ".join(name_parts)
                parsed_company = name
                pitch = " ".join(meaningful[idx:])
            else:
                pitch = " ".join(meaningful)
        else:
            pitch = " ".join(meaningful)

    company_name = (company or "").strip() or parsed_company
    if company_name and name == "unknown":
        name = company_name

    return {
        "number": number,
        "when": stamp.strftime("%Y-%m-%d %H:%M"),
        "caller_id": name,
        "pitch": pitch,
        "kind": kind,
        "company": company_name,
        "raw_text": raw,
    }


def quick_add(
    text: str,
    company: str | None = None,
    evidence: list[str] | None = None,
) -> dict[str, Any]:
    parsed = parse_quick(text, company=company)
    return add_call(
        parsed["number"],
        parsed["when"],
        parsed["caller_id"],
        parsed["pitch"],
        parsed["kind"],
        company=parsed["company"] or None,
        evidence=evidence,
        raw_text=parsed["raw_text"],
    )


def list_calls(number: str | None = None) -> list[dict[str, Any]]:
    calls = list(load_store()["calls"])
    if number:
        digits = normalize_number(number)
        calls = [c for c in calls if c.get("number") == digits]
    def _key(row: dict[str, Any]) -> str:
        return str(row.get("when") or "")
    return sorted(calls, key=_key)


def _summarize(groups: dict[str, list[dict[str, Any]]], kind: str) -> list[dict[str, Any]]:
    summary = []
    for key, group in groups.items():
        ordered = sorted(group, key=lambda r: str(r.get("when") or ""))
        names = Counter(r.get("caller_id") or "unknown" for r in ordered)
        pitches = Counter(r.get("pitch") or "" for r in ordered)
        kinds = Counter(r.get("kind") or "unknown" for r in ordered)
        companies = Counter(company_of(r) or "unknown" for r in ordered)
        numbers = Counter(r.get("number") for r in ordered if r.get("number"))
        top_name, _ = names.most_common(1)[0]
        top_pitch, _ = pitches.most_common(1)[0]
        top_company, _ = companies.most_common(1)[0]
        row = {
            "key": key,
            "group": kind,
            "count": len(ordered),
            "first": ordered[0]["when"],
            "last": ordered[-1]["when"],
            "top_name": top_name,
            "top_pitch": top_pitch,
            "top_company": top_company if top_company != "unknown" else "",
            "live": kinds.get("live", 0),
            "recording": kinds.get("recording", 0),
            "calls": ordered,
            "numbers": sorted(numbers),
            "number_count": len(numbers),
        }
        if kind == "number":
            row["number"] = key
            row["number_display"] = display_number(key)
        else:
            row["company"] = top_company if company_key(top_company) == key else (group[0].get("company") or company_of(group[0]))
        summary.append(row)
    summary.sort(key=lambda r: (-r["count"], r["last"]), reverse=False)
    return summary


def grouped_counts(calls: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    rows = calls if calls is not None else list_calls()
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        buckets.setdefault(row["number"], []).append(row)
    return _summarize(buckets, "number")


def grouped_by_company(calls: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    rows = calls if calls is not None else list_calls()
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        name = company_of(row)
        key = company_key(name)
        if not key or key == "unknown":
            continue
        buckets.setdefault(key, []).append(row)
    return _summarize(buckets, "company")


def repeats(minimum: int = 2) -> list[dict[str, Any]]:
    return [row for row in grouped_counts() if row["count"] >= minimum]


def repeats_by_company(minimum: int = 2) -> list[dict[str, Any]]:
    return [row for row in grouped_by_company() if row["count"] >= minimum]


def _fmt_when(raw: str) -> str:
    try:
        stamp = datetime.fromisoformat(raw)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=TZ)
        return stamp.astimezone(TZ).strftime("%Y-%m-%d %H:%M %Z")
    except ValueError:
        return raw


def _as_when(raw: str) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=TZ)
    return stamp.astimezone(TZ)


def registry_date() -> date | None:
    info = load_store().get("registry") or {}
    raw = (info.get("registered_on") or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def set_registry(raw: str | None, auto_backup: bool = True) -> date:
    day = parse_registry_date(raw)
    data = load_store()
    data["registry"] = {
        "registered_on": day.isoformat(),
        "logged_at": datetime.now(TZ).isoformat(timespec="seconds"),
    }
    save_store(data)
    if auto_backup:
        write_backup()
    return day


def honor_date(registered: date) -> date:
    return registered + timedelta(days=REGISTRY_HONOR_DAYS)


def days_since_registration(stamp: datetime, registered: date) -> int:
    return (stamp.date() - registered).days


def is_after_honor_period(stamp: datetime, registered: date) -> bool:
    return days_since_registration(stamp, registered) >= REGISTRY_HONOR_DAYS


def qualifying_calls(
    calls: list[dict[str, Any]], registered: date | None
) -> list[dict[str, Any]]:
    if registered is None:
        return []
    kept = []
    for row in calls:
        stamp = _as_when(str(row.get("when") or ""))
        if stamp and is_after_honor_period(stamp, registered):
            kept.append(row)
    return kept


def twelve_month_test(calls: list[dict[str, Any]], registered: date | None) -> dict[str, Any]:
    """Whether 2+ honor-period calls fall in any 365-day window."""
    if registered is None:
        return {
            "met": False,
            "reason": "no registry date on file",
            "qualifying": 0,
            "window": [],
        }
    qualified = qualifying_calls(calls, registered)
    times: list[tuple[datetime, dict[str, Any]]] = []
    for row in qualified:
        stamp = _as_when(str(row.get("when") or ""))
        if stamp:
            times.append((stamp, row))
    times.sort(key=lambda item: item[0])
    best: list[dict[str, Any]] = []
    for i, (start, _) in enumerate(times):
        end = start + timedelta(days=WINDOW_DAYS)
        window = [row for stamp, row in times if start <= stamp <= end]
        if len(window) > len(best):
            best = window
    met = len(best) >= 2
    return {
        "met": met,
        "reason": (
            f"{len(best)} qualifying call(s) in a 12-month window"
            if best
            else "no calls 31+ days after registration"
        ),
        "qualifying": len(qualified),
        "window": best,
    }


def dnc_analysis(calls: list[dict[str, Any]]) -> dict[str, Any]:
    registered = registry_date()
    per_company = []
    for group in grouped_by_company(calls):
        test = twelve_month_test(group["calls"], registered)
        per_company.append(
            {
                "company": group.get("company") or group["top_company"],
                "count": group["count"],
                "numbers": group["numbers"],
                "test": test,
            }
        )
    return {
        "registered_on": registered.isoformat() if registered else None,
        "honor_on": honor_date(registered).isoformat() if registered else None,
        "overall": twelve_month_test(calls, registered),
        "companies": per_company,
    }


def _evidence_rows(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for call in calls:
        for item in call.get("evidence") or []:
            rows.append({"call_id": call.get("id"), "when": call.get("when"), **item})
    return rows


def render_case(group: dict[str, Any]) -> str:
    registered = registry_date()
    title = group.get("number_display") or group.get("company") or "unknown"
    lines = [
        f"# Case file — {title}",
        "",
    ]
    if group.get("group") == "company" or group.get("company") and not group.get("number"):
        lines.append(f"- **Company:** {group.get('company') or group.get('top_company')}")
        numbers = ", ".join(display_number(n) for n in group.get("numbers") or [])
        lines.append(f"- **Numbers seen:** {numbers or 'none'}")
    else:
        lines.append(f"- **Number:** {group['number_display']} (`{group['number']}`)")
        if group.get("top_company"):
            lines.append(f"- **Most common company:** {group['top_company']}")
    lines.extend(
        [
            f"- **Calls logged:** {group['count']}",
            f"- **First call:** {_fmt_when(group['first'])}",
            f"- **Last call:** {_fmt_when(group['last'])}",
            f"- **Most common caller ID:** {group['top_name']}",
            f"- **Most common pitch:** {group['top_pitch']}",
            f"- **Live / recording:** {group['live']} live, {group['recording']} recording",
            "",
            "## Call log",
            "",
        ]
    )
    for row in group["calls"]:
        company = company_of(row)
        company_bit = f" / {company}" if company and company != row.get("caller_id") else ""
        ev = row.get("evidence") or []
        ev_bit = f"  [evidence: {len(ev)} file(s)]" if ev else ""
        lines.append(
            f"- {_fmt_when(row['when'])} — {row.get('number_display') or display_number(row.get('number', ''))} "
            f"— {row.get('caller_id')}{company_bit} "
            f"({row.get('kind')}): {row.get('pitch')}{ev_bit}"
        )
        if row.get("raw_text"):
            lines.append(f"  - raw note: {row['raw_text']}")

    lines.extend(["", "## Evidence", ""])
    ev_rows = _evidence_rows(group["calls"])
    if ev_rows:
        for item in ev_rows:
            lines.append(
                f"- #{item.get('call_id')}  {item.get('original_name')}  "
                f"stored as `{item.get('stored_as')}`  SHA-256 `{item.get('sha256')}`"
            )
    else:
        lines.append("- None attached.")

    lines.extend(
        [
            "",
            "## Do Not Call dates",
            "",
            "**Not legal advice.** This section is a local checklist against published",
            "federal rules. It is not a determination that a violation occurred, that a",
            "lawsuit would succeed, or that any exception (existing business relationship,",
            "written permission, charity, political call, and others) does or does not apply.",
            "",
        ]
    )
    if registered is None:
        lines.append("- **Registry date:** not recorded yet. Use `calllog.py registry --date YYYY-MM-DD`.")
        lines.append("- The 31-day honor period and the 12-month, 2+ calls test cannot be checked until a date is on file.")
    else:
        honor = honor_date(registered)
        lines.append(f"- **Registry date:** {registered.isoformat()}")
        lines.append(
            f"- **31-day honor mark:** {honor.isoformat()} "
            f"(calls on or after this date are 31+ days after registration)"
        )
        lines.append("")
        lines.append("### Calls and the 31-day mark")
        lines.append("")
        for row in group["calls"]:
            stamp = _as_when(str(row.get("when") or ""))
            if not stamp:
                lines.append(f"- {_fmt_when(row['when'])} — date not parseable")
                continue
            days = days_since_registration(stamp, registered)
            flag = "31+ days after registration" if days >= REGISTRY_HONOR_DAYS else f"before the honor mark (day {days})"
            lines.append(f"- {_fmt_when(row['when'])} — {flag}")
        lines.append("")
        lines.append("### 12-month, 2+ calls test (by or on behalf of the same entity)")
        lines.append("")
        company_groups = grouped_by_company(group["calls"]) or (
            [
                {
                    "company": group.get("company") or group.get("top_company") or "unknown",
                    "calls": group["calls"],
                }
            ]
            if group.get("group") == "company"
            else grouped_by_company(group["calls"])
        )
        if not company_groups and group.get("group") != "company":
            # Number-only file with no company labels: still run the test on this set.
            company_groups = [
                {
                    "company": group.get("top_company") or group.get("top_name") or "this number",
                    "calls": group["calls"],
                }
            ]
        if group.get("group") == "company":
            company_groups = [group]
        seen = set()
        for cgroup in company_groups:
            label = cgroup.get("company") or cgroup.get("top_company") or "unknown"
            key = company_key(label)
            if key in seen:
                continue
            seen.add(key)
            test = twelve_month_test(cgroup["calls"], registered)
            status = "MET" if test["met"] else "not met"
            lines.append(f"- **{label}:** {status} — {test['reason']}")
            for row in test["window"]:
                lines.append(
                    f"  - {_fmt_when(row['when'])}  {row.get('number_display') or ''}  {row.get('pitch')}"
                )
        lines.append("")
        lines.append("Sources for these checks:")
        for title, url, note in DNC_SOURCES:
            lines.append(f"- {title}: {url}")
            lines.append(f"  {note}")

    lines.extend(
        [
            "",
            "## For the human",
            "",
            "This is a local record, not a filing and not legal advice.",
            "Register at https://www.donotcall.gov and turn on carrier spam blocking.",
            "Report unwanted calls (no money lost) at https://www.donotcall.gov/file-a-complaint.",
            "Report a scam or a loss at https://reportfraud.ftc.gov/.",
            "A Telephone Consumer Protection Act suit ($500 per illegal call, up to $1,500",
            "if willful or knowing) is yours to file, usually in small claims, and only",
            "against a real, findable company. Offshore or spoofed callers are often a dead end.",
            "Do not call this number back. Do not harass anyone. Defense only.",
            "",
        ]
    )
    return "\n".join(lines)


def case_filename(digits: str) -> Path:
    return cases_dir() / f"case-{digits}.md"


def company_case_filename(name: str) -> Path:
    slug = company_key(name).replace(" ", "-") or "unknown"
    return cases_dir() / f"case-company-{slug}.md"


def export_case(
    number: str | None = None,
    everyone: bool = False,
    company: str | None = None,
) -> list[Path]:
    if everyone:
        groups = [g for g in grouped_counts() if g["count"] >= CASE_MIN]
        company_groups = [g for g in grouped_by_company() if g["count"] >= CASE_MIN]
        # Prefer a company file when the same calls already qualify as a company case
        # and also always write number files (existing behavior).
        if not groups and not company_groups:
            raise CallLogError(f"No numbers or companies have {CASE_MIN} or more calls yet.")
        groups = groups + company_groups
    elif company:
        key = company_key(company)
        if not key:
            raise CallLogError("Need a company name.")
        groups = [g for g in grouped_by_company() if company_key(g.get("company") or g.get("top_company")) == key]
        if not groups:
            raise CallLogError(f"No calls logged for company '{company}'.")
        if groups[0]["count"] < CASE_MIN:
            raise CallLogError(
                f"{groups[0].get('company') or company} has {groups[0]['count']} call(s); "
                f"a case file needs {CASE_MIN} or more."
            )
    elif number:
        digits = normalize_number(number)
        groups = [g for g in grouped_counts() if g["number"] == digits]
        if not groups:
            raise CallLogError(f"No calls logged for {display_number(digits)}.")
        if groups[0]["count"] < CASE_MIN:
            raise CallLogError(
                f"{display_number(digits)} has {groups[0]['count']} call(s); "
                f"a case file needs {CASE_MIN} or more."
            )
    else:
        raise CallLogError("Pass a number, --company NAME, or --all.")
    written: list[Path] = []
    for group in groups:
        if group.get("group") == "company":
            path = company_case_filename(group.get("company") or group.get("top_company") or "unknown")
        else:
            path = case_filename(group["number"])
        path.write_text(render_case(group), encoding="utf-8")
        written.append(path)
    return written


def _ensure_key_dir(path: Path) -> None:
    folder = path.parent
    folder.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)


def _write_secret(path: Path, data: bytes) -> None:
    _ensure_key_dir(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.chmod(path, 0o600)


def load_or_create_key() -> bytes:
    path = key_path()
    if path.is_file():
        return path.read_bytes().strip()
    key = Fernet.generate_key()
    _write_secret(path, key + b"\n")
    return key


def load_key() -> bytes:
    path = key_path()
    if not path.is_file():
        raise CallLogError(
            f"Hang Up key not found at {path}. Run backup once to create it, "
            "or restore the off-box copy to that path."
        )
    return path.read_bytes().strip()


def _pack_data() -> bytes:
    source = data_dir()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for child in sorted(source.rglob("*")):
            if not child.is_file():
                continue
            if child.name.endswith(".tmp"):
                continue
            arcname = child.relative_to(source).as_posix()
            tar.add(child, arcname=arcname)
    return buffer.getvalue()


def write_backup(path: Path | None = None) -> Path:
    dest = path or backup_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = _pack_data()
    digest = hashlib.sha256(payload).hexdigest()
    token = Fernet(load_or_create_key()).encrypt(payload)
    body = {
        "format": BACKUP_HEADER,
        "algorithm": ALGORITHM,
        "created_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "sha256": digest,
        "token": token.decode("ascii"),
    }
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(dest)
    os.chmod(dest, 0o600)
    return dest


def restore_backup(dest: Path | None = None, source: Path | None = None) -> Path:
    enc = source or backup_path()
    if not enc.is_file():
        raise CallLogError(f"No encrypted backup at {enc}.")
    try:
        body = json.loads(enc.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CallLogError(f"Encrypted backup is not valid JSON: {enc}") from exc
    if body.get("format") != BACKUP_HEADER or body.get("algorithm") != ALGORITHM:
        raise CallLogError("Encrypted backup header is not a Hang Up v1 Fernet archive.")
    try:
        payload = Fernet(load_key()).decrypt(body["token"].encode("ascii"))
    except (InvalidToken, KeyError, ValueError) as exc:
        raise CallLogError("Could not decrypt the backup. The key file may be wrong or missing.") from exc
    expected = body.get("sha256")
    actual = hashlib.sha256(payload).hexdigest()
    if expected and expected != actual:
        raise CallLogError("Decrypted backup did not match its SHA-256 checksum.")
    target = dest or data_dir()
    target.mkdir(parents=True, exist_ok=True)
    # Replace existing call data with the restored tree, leaving the directory in place.
    for child in list(target.iterdir()):
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    buffer = io.BytesIO(payload)
    with tarfile.open(fileobj=buffer, mode="r:gz") as tar:
        tar.extractall(target, filter="data")
    return target


def _print_entry(row: dict[str, Any]) -> None:
    company = company_of(row)
    company_bit = f"  company={company}" if company else ""
    ev = row.get("evidence") or []
    ev_bit = f"  evidence={len(ev)}" if ev else ""
    print(
        f"#{row.get('id')}  {_fmt_when(str(row.get('when')))}  "
        f"{row.get('number_display') or display_number(row.get('number', ''))}  "
        f"{row.get('caller_id')}  ({row.get('kind')}): {row.get('pitch')}"
        f"{company_bit}{ev_bit}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Log unwanted calls. Does not dial, report, or contact anyone."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add", help="Log one call.")
    add.add_argument("--number", required=True)
    add.add_argument("--when", default="now", help="YYYY-MM-DD HH:MM, ISO, or 'now'.")
    add.add_argument("--name", dest="caller_id", required=True, help="Caller-ID name.")
    add.add_argument("--pitch", required=True, help="What they were selling or saying.")
    add.add_argument("--kind", required=True, choices=KINDS)
    add.add_argument("--company", default=None, help="Company or outfit, if known.")
    add.add_argument(
        "--evidence",
        action="append",
        default=[],
        help="Screenshot or voicemail file to attach. Repeatable.",
    )
    quick = sub.add_parser("quick", help="Log a call from one free-text line.")
    quick.add_argument("text", nargs="+", help="Free-text description of the call.")
    quick.add_argument("--company", default=None, help="Company or outfit, if known.")
    quick.add_argument("--evidence", action="append", default=[], help="Attach a file. Repeatable.")
    listing = sub.add_parser("list", help="List logged calls, newest last.")
    listing.add_argument("--number", default=None)
    listing.add_argument("--company", default=None)
    reps = sub.add_parser("repeats", help="Summarize numbers or companies that have called more than once.")
    reps.add_argument("--min", dest="minimum", type=int, default=2)
    case = sub.add_parser("case", help="Export a case-file markdown for a number or company with 3+ calls.")
    case.add_argument("number", nargs="?", default=None)
    case.add_argument("--company", default=None, help="Export a case grouped by company.")
    case.add_argument("--all", dest="everyone", action="store_true")
    registry = sub.add_parser("registry", help="Record or show the National Do Not Call Registry date.")
    registry.add_argument("--date", default=None, help="YYYY-MM-DD or 'today'. Omit to show the date on file.")
    sub.add_parser("backup", help="Write an encrypted copy of data/ to backup/calllog.enc.")
    restore = sub.add_parser("restore", help="Restore data/ from the encrypted backup.")
    restore.add_argument("--to", dest="dest", default=None, help="Directory to restore into (default: data/).")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "add":
            entry = add_call(
                args.number,
                args.when,
                args.caller_id,
                args.pitch,
                args.kind,
                company=args.company,
                evidence=args.evidence or None,
            )
            print(
                f"Logged #{entry['id']}  {entry['number_display']}  "
                f"{entry['caller_id']}  ({entry['kind']})"
            )
            return 0
        if args.cmd == "quick":
            entry = quick_add(" ".join(args.text), company=args.company, evidence=args.evidence or None)
            print(
                f"Logged #{entry['id']}  {entry['number_display']}  "
                f"{entry['caller_id']}  ({entry['kind']})  {entry['pitch']}"
            )
            return 0
        if args.cmd == "list":
            rows = list_calls(args.number)
            if args.company:
                key = company_key(args.company)
                rows = [r for r in rows if company_key(company_of(r)) == key]
            if not rows:
                print("No calls logged." if not (args.number or args.company) else "No calls logged for that filter.")
                return 0
            for row in rows:
                _print_entry(row)
            print(f"{len(rows)} call(s).")
            return 0
        if args.cmd == "repeats":
            numbers = repeats(args.minimum)
            companies = repeats_by_company(args.minimum)
            if not numbers and not companies:
                print(f"No numbers or companies with {args.minimum}+ calls yet.")
                return 0
            if numbers:
                print("By number:")
                for row in numbers:
                    print(
                        f"{row['number_display']}  {row['count']} calls  "
                        f"last {_fmt_when(row['last'])}  "
                        f"{row['top_name']} / {row['top_pitch']}"
                    )
            if companies:
                print("By company:")
                for row in companies:
                    print(
                        f"{row.get('company') or row['top_company']}  {row['count']} calls  "
                        f"({row['number_count']} number(s))  "
                        f"last {_fmt_when(row['last'])}  "
                        f"{row['top_pitch']}"
                    )
            return 0
        if args.cmd == "case":
            paths = export_case(args.number, everyone=args.everyone, company=args.company)
            for path in paths:
                print(f"Wrote {path}")
            return 0
        if args.cmd == "registry":
            if args.date is None:
                day = registry_date()
                if day is None:
                    print("No registry date on file yet.")
                else:
                    print(f"Registered on {day.isoformat()}  (honor mark {honor_date(day).isoformat()})")
                return 0
            day = set_registry(args.date)
            print(f"Recorded registry date {day.isoformat()}  (honor mark {honor_date(day).isoformat()})")
            return 0
        if args.cmd == "backup":
            path = write_backup()
            print(f"Wrote encrypted backup {path}")
            return 0
        if args.cmd == "restore":
            target = Path(args.dest).resolve() if args.dest else None
            path = restore_backup(dest=target)
            print(f"Restored call data to {path}")
            return 0
        raise CallLogError(f"Unknown command: {args.cmd}")
    except CallLogError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
