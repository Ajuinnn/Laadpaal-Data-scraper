#!/usr/bin/env python3
"""Scrape and analyze Reddit complaints about public EV charging failures.

The tool intentionally uses only the Python standard library. It fetches public
Reddit JSON endpoints, classifies posts/comments with transparent keyword rules,
and writes CSV, JSON, Markdown and HTML outputs with inline SVG charts.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import math
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent
DEFAULT_QUERY_FILE = ROOT / "default_queries.json"
SAMPLE_FILE = ROOT / "sample_reddit_items.jsonl"

USER_AGENT = (
    "ev-charger-reddit-complaint-research/0.1 "
    "(local academic-style analysis; contact: local-user)"
)


CATEGORY_RULES: dict[str, dict[str, Any]] = {
    "connectivity_backend": {
        "label": "Connectiviteit/backend",
        "description": "Netwerk, mobiele verbinding, backend, station online/offline-status.",
        "keywords": [
            "network",
            "connectivity",
            "offline",
            "online",
            "backend",
            "server",
            "cellular",
            "sim",
            "connection",
            "internet",
            "ghost station",
            "zombie station",
            "ocpp",
            "netwerk",
            "verbinding",
            "geen bereik",
            "simkaart",
            "onbekend",
            "status onbekend",
        ],
    },
    "software_firmware": {
        "label": "Software/firmware",
        "description": "Vastgelopen schermen, foutcodes, reboot/reset, handshake/startproblemen.",
        "keywords": [
            "software",
            "firmware",
            "bug",
            "crash",
            "reboot",
            "reset",
            "frozen",
            "error",
            "fault",
            "code",
            "handshake",
            "initiation",
            "would not start",
            "won't start",
            "not start",
            "vehicle not requesting",
            "vastgelopen",
            "storing",
            "foutmelding",
            "foutcode",
            "herstart",
            "start niet",
            "begint niet",
            "update",
        ],
    },
    "payment_authentication": {
        "label": "Betaling/authenticatie",
        "description": "Laadpas, app, kaartlezer, RFID, bankkaart, roaming en autorisatie.",
        "keywords": [
            "payment",
            "pay",
            "card reader",
            "credit card",
            "debit card",
            "tap to pay",
            "rfid",
            "authorize",
            "authorization",
            "auth",
            "declined",
            "preauth",
            "plug and charge",
            "autocharge",
            "app to pay",
            "pay by app",
            "payment app",
            "laadpas",
            "bankkaart",
            "betaal",
            "betalen",
            "betaalsysteem",
            "kaartlezer",
            "pas",
            "druppel",
            "roaming",
            "factuur",
            "autoriseren",
            "geweigerd",
        ],
    },
    "cable_connector": {
        "label": "Kabel/connector",
        "description": "Kabel, stekker, connector, latch/lock, te korte kabel, temperatuursensor.",
        "keywords": [
            "cable",
            "connector",
            "plug",
            "handle",
            "latch",
            "locked",
            "won't release",
            "would not release",
            "too short",
            "cooling",
            "temperature sensor",
            "ccs",
            "j1772",
            "nacs",
            "kabel",
            "stekker",
            "connector",
            "vergrendeling",
            "niet los",
            "te kort",
            "laadkabel",
            "plug",
        ],
    },
    "screen_reader": {
        "label": "Scherm/reader",
        "description": "Display, touchscreen, kaartlezer of zichtbare terminal werkt niet.",
        "keywords": [
            "screen",
            "display",
            "touchscreen",
            "blank",
            "unresponsive",
            "reader",
            "terminal",
            "scherm",
            "display",
            "touchscreen",
            "zwart scherm",
            "kaartlezer",
        ],
    },
    "vandalism_theft_damage": {
        "label": "Vandalisme/diefstal/schade",
        "description": "Kabeldiefstal, koper, vandalisme, aanrijding of fysiek misbruik.",
        "keywords": [
            "vandalism",
            "vandalized",
            "theft",
            "stolen",
            "cut",
            "copper",
            "damaged",
            "smashed",
            "driven over",
            "abuse",
            "cable theft",
            "vandalisme",
            "diefstal",
            "gestolen",
            "doorgeknipt",
            "koper",
            "vernield",
            "kapot gereden",
            "aanrijding",
            "sabotage",
            "zwart gespoten",
        ],
    },
    "maintenance_operator": {
        "label": "Onderhoud/operator",
        "description": "Trage reparatie, tickets, service, eigenaar/operator, onderdelen en SLA.",
        "keywords": [
            "maintenance",
            "repair",
            "technician",
            "fixed",
            "service",
            "support",
            "owner",
            "operator",
            "reported",
            "ticket",
            "sla",
            "out for weeks",
            "out for months",
            "onderhoud",
            "reparatie",
            "monteur",
            "technieker",
            "exploitant",
            "beheerder",
            "melding",
            "gemeld",
            "maanden",
            "weken",
            "ticket",
            "communicatie",
            "buiten werking",
        ],
    },
    "availability_queue": {
        "label": "Bezet/wachtrij",
        "description": "Te weinig beschikbare laders, wachtrijen, laadpaalklevers, overbezetting.",
        "keywords": [
            "queue",
            "busy",
            "wait",
            "waiting",
            "in use",
            "occupied",
            "full",
            "charging all day",
            "oversubscribed",
            "no availability",
            "bezet",
            "wachtrij",
            "wachten",
            "laadpaalklever",
            "vol",
            "taxi",
            "hele dag",
        ],
    },
    "parking_access": {
        "label": "Parkeren/toegang",
        "description": "ICE-blokkades, foutparkeerders, slechte toegang, borden/plaatsen.",
        "keywords": [
            "iced",
            "blocked",
            "parked",
            "parking",
            "accessibility",
            "bad parking",
            "gasoline car",
            "ice car",
            "foutparkeerder",
            "benzine",
            "diesel",
            "geblokkeerd",
            "parkeerplaats",
            "bord",
            "stoep",
        ],
    },
    "speed_power_grid": {
        "label": "Snelheid/net/vermogen",
        "description": "Traag laden, derating, netcongestie, transformator, load balancing.",
        "keywords": [
            "slow",
            "throttled",
            "low power",
            "derated",
            "reduced",
            "grid",
            "transformer",
            "power outage",
            "load balancing",
            "netcongestie",
            "traag",
            "afgeknepen",
            "uitgeschakeld",
            "transformator",
            "vermogen",
            "loadbalance",
            "load balancing",
        ],
    },
    "home_installation_wallbox": {
        "label": "Thuis/Wallbox/installatie",
        "description": "Thuislader, Wallbox, stopcontact, GFCI/aardlek, breaker, torque/installatie.",
        "keywords": [
            "wallbox",
            "home charger",
            "outlet",
            "receptacle",
            "nema",
            "gfci",
            "breaker",
            "hardwire",
            "hardwired",
            "leviton",
            "torque",
            "garage",
            "thuis",
            "thuislader",
            "stopcontact",
            "meterkast",
            "aardlek",
            "zekering",
            "differentieel",
            "installatie",
            "capaciteitstarief",
            "oprit",
        ],
    },
}


CHARGER_TERMS = [
    "charger",
    "charging station",
    "charge station",
    "evse",
    "dcfc",
    "fast charger",
    "supercharger",
    "chargepoint",
    "evgo",
    "electrify america",
    "ionity",
    "allego",
    "shell recharge",
    "bp pulse",
    "gridserve",
    "laadpaal",
    "laadpunt",
    "laadstation",
    "snellader",
    "laadplein",
    "wallbox",
]

COMPLAINT_TERMS = [
    "broken",
    "not working",
    "doesn't work",
    "dont work",
    "don't work",
    "fail",
    "failed",
    "failure",
    "won't",
    "would not",
    "offline",
    "out of service",
    "error",
    "issue",
    "problem",
    "fault",
    "kapot",
    "storing",
    "werkt niet",
    "defect",
    "buiten werking",
    "fout",
    "probleem",
    "faalt",
    "geweigerd",
    "geblokkeerd",
]

PUBLIC_SCOPE_TERMS = [
    "public",
    "public charger",
    "charging network",
    "dcfc",
    "fast charger",
    "service station",
    "parking garage",
    "supermarket",
    "electrify america",
    "evgo",
    "chargepoint",
    "ionity",
    "allego",
    "vattenfall",
    "incharge",
    "shell recharge",
    "bp pulse",
    "gridserve",
    "publieke",
    "openbare",
    "snellader",
    "laadplein",
    "parkeergarage",
    "supermarkt",
]

HOME_SCOPE_TERMS = [
    "home charger",
    "wallbox",
    "garage",
    "nema",
    "gfci",
    "outlet",
    "receptacle",
    "breaker",
    "hardwire",
    "hardwired",
    "thuis",
    "thuislader",
    "stopcontact",
    "meterkast",
    "oprit",
    "aardlek",
    "zekering",
]

DUTCH_HINTS = [
    "laadpaal",
    "laadpas",
    "publieke",
    "openbare",
    "kapot",
    "storing",
    "werkt",
    "niet",
    "buiten werking",
    "beheerder",
    "monteur",
    "snellader",
]


@dataclass
class FetchStats:
    searches_attempted: int = 0
    posts_found: int = 0
    posts_fetched: int = 0
    comments_seen: int = 0
    records_written: int = 0
    failures: int = 0


def normalize_text(value: str) -> str:
    value = value or ""
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\u2019", "'").replace("\u2018", "'")
    value = value.replace("\u201c", '"').replace("\u201d", '"')
    return re.sub(r"\s+", " ", value).strip()


def fold(value: str) -> str:
    return normalize_text(value).casefold()


def contains_phrase(text: str, phrase: str) -> bool:
    phrase = fold(phrase)
    if " " in phrase or "-" in phrase:
        return phrase in text
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text) is not None


def count_keyword_hits(text: str, keywords: Iterable[str]) -> tuple[int, list[str]]:
    score = 0
    hits: list[str] = []
    for keyword in keywords:
        if contains_phrase(text, keyword):
            hits.append(keyword)
            score += 2 if " " in keyword else 1
    return score, hits


def detect_language(text: str) -> str:
    folded = fold(text)
    dutch_hits = sum(1 for term in DUTCH_HINTS if contains_phrase(folded, term))
    return "nl" if dutch_hits >= 2 else "en"


def classify_scope(text: str, subreddit: str = "") -> str:
    folded = fold(text)
    public_score, _ = count_keyword_hits(folded, PUBLIC_SCOPE_TERMS)
    home_score, _ = count_keyword_hits(folded, HOME_SCOPE_TERMS)
    if subreddit.casefold() in {"evmobiliteit", "belgium", "belgium2", "thenetherlands"}:
        public_score += 1 if contains_phrase(folded, "laadpaal") else 0
    if home_score > public_score and home_score >= 2:
        return "home"
    if public_score >= 1:
        return "public"
    return "unknown"


def classify_record(record: dict[str, Any]) -> dict[str, Any]:
    text = normalize_text(
        " ".join(
            [
                record.get("post_title", ""),
                record.get("text", ""),
            ]
        )
    )
    folded = fold(text)
    category_scores: dict[str, int] = {}
    category_hits: dict[str, list[str]] = {}
    for key, rule in CATEGORY_RULES.items():
        score, hits = count_keyword_hits(folded, rule["keywords"])
        if score:
            category_scores[key] = score
            category_hits[key] = hits

    charger_score, charger_hits = count_keyword_hits(folded, CHARGER_TERMS)
    complaint_score, complaint_hits = count_keyword_hits(folded, COMPLAINT_TERMS)

    scope = record.get("scope") or classify_scope(text, record.get("subreddit", ""))
    language = record.get("language") or detect_language(text)

    categories = [
        key
        for key, _ in sorted(
            category_scores.items(),
            key=lambda item: (-item[1], CATEGORY_RULES[item[0]]["label"]),
        )
    ]
    primary_issue = categories[0] if categories else "unknown_charger_issue"
    relevant = bool(categories) and (charger_score > 0 or scope in {"public", "home"})
    complaint_like = complaint_score > 0 or any(
        key in category_scores
        for key in [
            "maintenance_operator",
            "vandalism_theft_damage",
            "parking_access",
            "availability_queue",
        ]
    )

    enriched = dict(record)
    enriched.update(
        {
            "text": normalize_text(record.get("text", "")),
            "scope": scope,
            "language": language,
            "categories": categories,
            "primary_issue": primary_issue,
            "category_scores": category_scores,
            "category_hits": category_hits,
            "charger_hits": charger_hits,
            "complaint_hits": complaint_hits,
            "is_relevant_complaint": relevant and complaint_like,
            "excerpt": make_excerpt(record.get("text", "")),
        }
    )
    return enriched


def make_excerpt(text: str, max_len: int = 260) -> str:
    text = normalize_text(text)
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "..."


def parse_reddit_post_id(url: str) -> str | None:
    match = re.search(r"/comments/([a-z0-9]+)/", url, flags=re.IGNORECASE)
    return match.group(1) if match else None


def request_json(url: str, timeout: int = 30, retries: int = 2, delay: float = 1.0) -> Any:
    last_error: Exception | None = None
    candidates = [url]
    if "www.reddit.com" in url:
        candidates.append(url.replace("www.reddit.com", "api.reddit.com"))
        candidates.append(url.replace("www.reddit.com", "old.reddit.com"))

    for candidate in candidates:
        for attempt in range(retries + 1):
            req = urllib.request.Request(
                candidate,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json,text/json;q=0.9,*/*;q=0.1",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    raw = response.read().decode("utf-8", errors="replace")
                    return json.loads(raw)
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code in {403, 404}:
                    break
                if exc.code == 429:
                    time.sleep(delay * (attempt + 2))
                else:
                    time.sleep(delay * (attempt + 1))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                time.sleep(delay * (attempt + 1))
    raise RuntimeError(f"Could not fetch JSON from {url}: {last_error}")


def search_reddit(query: str, subreddit: str | None, limit: int, delay: float) -> list[dict[str, Any]]:
    params = {
        "q": query,
        "sort": "relevance",
        "t": "all",
        "limit": str(limit),
        "raw_json": "1",
    }
    if subreddit:
        params["restrict_sr"] = "1"
        base = f"https://www.reddit.com/r/{urllib.parse.quote(subreddit)}/search.json"
    else:
        params["type"] = "link"
        base = "https://www.reddit.com/search.json"
    url = base + "?" + urllib.parse.urlencode(params)
    data = request_json(url, delay=delay)
    children = data.get("data", {}).get("children", []) if isinstance(data, dict) else []
    posts: list[dict[str, Any]] = []
    for child in children:
        if child.get("kind") != "t3":
            continue
        post = child.get("data", {})
        posts.append(post)
    return posts


def fetch_post_with_comments(post_id: str, limit: int, delay: float) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    params = {
        "limit": str(limit),
        "raw_json": "1",
        "sort": "confidence",
    }
    url = f"https://www.reddit.com/comments/{post_id}.json?" + urllib.parse.urlencode(params)
    data = request_json(url, delay=delay)
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"Unexpected comments payload for {post_id}")
    post_listing = data[0].get("data", {}).get("children", [])
    post = post_listing[0].get("data", {}) if post_listing else {}
    comments_listing = data[1].get("data", {}).get("children", []) if len(data) > 1 else []
    comments = list(walk_comments(comments_listing))
    return post, comments


def walk_comments(children: list[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for child in children:
        if child.get("kind") != "t1":
            continue
        data = child.get("data", {})
        yield data
        replies = data.get("replies")
        if isinstance(replies, dict):
            nested = replies.get("data", {}).get("children", [])
            yield from walk_comments(nested)


def post_to_record(post: dict[str, Any]) -> dict[str, Any]:
    permalink = post.get("permalink") or ""
    return {
        "source_type": "post",
        "subreddit": post.get("subreddit", ""),
        "post_id": post.get("id", ""),
        "post_title": normalize_text(post.get("title", "")),
        "item_id": post.get("id", ""),
        "url": "https://www.reddit.com" + permalink if permalink.startswith("/") else permalink,
        "created_utc": post.get("created_utc"),
        "score": post.get("score"),
        "num_comments": post.get("num_comments"),
        "text": normalize_text(" ".join([post.get("title", ""), post.get("selftext", "")])),
    }


def comment_to_record(comment: dict[str, Any], post: dict[str, Any]) -> dict[str, Any]:
    post_permalink = post.get("permalink") or ""
    comment_id = comment.get("id", "")
    url = "https://www.reddit.com" + post_permalink
    if comment_id:
        url = url.rstrip("/") + f"/{comment_id}/"
    return {
        "source_type": "comment",
        "subreddit": post.get("subreddit", ""),
        "post_id": post.get("id", ""),
        "post_title": normalize_text(post.get("title", "")),
        "item_id": comment_id,
        "url": url,
        "created_utc": comment.get("created_utc"),
        "score": comment.get("score"),
        "text": normalize_text(comment.get("body", "")),
    }


def load_query_config(path: Path | None) -> dict[str, Any]:
    path = path or DEFAULT_QUERY_FILE
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def iter_seed_posts(config: dict[str, Any], extra_seed_urls: list[str]) -> Iterable[str]:
    for url in config.get("seed_urls", []):
        post_id = parse_reddit_post_id(url)
        if post_id:
            yield post_id
    for url in extra_seed_urls:
        post_id = parse_reddit_post_id(url)
        if post_id:
            yield post_id


def scrape(args: argparse.Namespace) -> Path:
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / "raw_items.jsonl"
    config = load_query_config(Path(args.query_file) if args.query_file else None)
    stats = FetchStats()
    seen_posts: set[str] = set()
    seen_records: set[str] = set()

    with raw_path.open("w", encoding="utf-8") as out:
        if args.sample:
            for record in read_jsonl(SAMPLE_FILE):
                enriched = classify_record(record)
                out.write(json.dumps(enriched, ensure_ascii=False) + "\n")
                stats.records_written += 1
            write_json(output_dir / "scrape_stats.json", stats.__dict__)
            print(f"Sample data written: {raw_path}")
            return raw_path

        candidate_post_ids: list[str] = []
        for post_id in iter_seed_posts(config, args.seed_url or []):
            if post_id not in seen_posts:
                candidate_post_ids.append(post_id)
                seen_posts.add(post_id)

        for search in config.get("subreddit_searches", []):
            subreddit = search.get("subreddit")
            for query in search.get("queries", []):
                if args.max_posts and len(candidate_post_ids) >= args.max_posts:
                    break
                stats.searches_attempted += 1
                try:
                    posts = search_reddit(query, subreddit, args.limit_per_query, args.delay)
                    stats.posts_found += len(posts)
                    for post in posts:
                        post_id = post.get("id")
                        if post_id and post_id not in seen_posts:
                            candidate_post_ids.append(post_id)
                            seen_posts.add(post_id)
                except Exception as exc:  # noqa: BLE001 - CLI should keep going across searches
                    stats.failures += 1
                    print(f"Search failed [{subreddit or 'global'} :: {query}]: {exc}", file=sys.stderr)
                time.sleep(args.delay)

        for query in config.get("global_searches", []):
            if args.max_posts and len(candidate_post_ids) >= args.max_posts:
                break
            stats.searches_attempted += 1
            try:
                posts = search_reddit(query, None, args.limit_per_query, args.delay)
                stats.posts_found += len(posts)
                for post in posts:
                    post_id = post.get("id")
                    if post_id and post_id not in seen_posts:
                        candidate_post_ids.append(post_id)
                        seen_posts.add(post_id)
            except Exception as exc:  # noqa: BLE001
                stats.failures += 1
                print(f"Search failed [global :: {query}]: {exc}", file=sys.stderr)
            time.sleep(args.delay)

        if args.max_posts:
            candidate_post_ids = candidate_post_ids[: args.max_posts]

        for index, post_id in enumerate(candidate_post_ids, start=1):
            try:
                post, comments = fetch_post_with_comments(post_id, args.comments_per_post, args.delay)
                stats.posts_fetched += 1
                records = [post_to_record(post)]
                records.extend(comment_to_record(comment, post) for comment in comments)
                stats.comments_seen += len(comments)
                for record in records:
                    key = f"{record.get('source_type')}:{record.get('item_id')}"
                    if key in seen_records:
                        continue
                    seen_records.add(key)
                    enriched = classify_record(record)
                    if not enriched["is_relevant_complaint"] and not args.keep_irrelevant:
                        continue
                    out.write(json.dumps(enriched, ensure_ascii=False) + "\n")
                    stats.records_written += 1
                print(
                    f"[{index}/{len(candidate_post_ids)}] fetched {post_id}: "
                    f"{len(comments)} comments, {stats.records_written} records kept"
                )
            except Exception as exc:  # noqa: BLE001
                stats.failures += 1
                print(f"Post fetch failed [{post_id}]: {exc}", file=sys.stderr)
            time.sleep(args.delay)

    write_json(output_dir / "scrape_stats.json", stats.__dict__)
    print(f"Raw data written: {raw_path}")
    return raw_path


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_chart_svg(path: Path, svg_markup: str) -> None:
    if not svg_markup.lstrip().startswith("<svg"):
        return
    path.write_text(svg_markup, encoding="utf-8")


def utc_month(value: Any) -> str:
    if not value:
        return "unknown"
    try:
        return dt.datetime.fromtimestamp(float(value), tz=dt.UTC).strftime("%Y-%m")
    except (ValueError, OSError, TypeError):
        return "unknown"


def utc_date(value: Any) -> str:
    if not value:
        return ""
    try:
        return dt.datetime.fromtimestamp(float(value), tz=dt.UTC).strftime("%Y-%m-%d")
    except (ValueError, OSError, TypeError):
        return ""


def filter_records(records: list[dict[str, Any]], scope: str) -> list[dict[str, Any]]:
    if scope == "all":
        return records
    return [record for record in records if record.get("scope") == scope]


def analyze(args: argparse.Namespace) -> Path:
    input_path = Path(args.input).resolve()
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    records = [classify_record(record) for record in read_jsonl(input_path)]
    scoped = filter_records(records, args.scope)

    table_rows = []
    for record in scoped:
        table_rows.append(
            {
                "date": utc_date(record.get("created_utc")),
                "source_type": record.get("source_type", ""),
                "scope": record.get("scope", ""),
                "language": record.get("language", ""),
                "subreddit": record.get("subreddit", ""),
                "score": record.get("score", ""),
                "post_title": record.get("post_title", ""),
                "primary_issue": record.get("primary_issue", ""),
                "primary_issue_label": issue_label(record.get("primary_issue", "")),
                "categories": ";".join(record.get("categories", [])),
                "excerpt": record.get("excerpt", ""),
                "url": record.get("url", ""),
            }
        )
    write_csv(
        output_dir / "items.csv",
        table_rows,
        [
            "date",
            "source_type",
            "scope",
            "language",
            "subreddit",
            "score",
            "post_title",
            "primary_issue",
            "primary_issue_label",
            "categories",
            "excerpt",
            "url",
        ],
    )

    analysis = build_analysis(scoped, all_records=records, scope=args.scope)
    write_json(output_dir / "analysis.json", analysis)
    write_csv(output_dir / "issue_counts.csv", analysis["issue_counts"], ["issue", "label", "count", "percent"])
    write_csv(
        output_dir / "primary_issue_counts.csv",
        analysis["primary_issue_counts"],
        ["issue", "label", "count", "percent"],
    )
    write_csv(output_dir / "scope_counts.csv", analysis["scope_counts"], ["scope", "count", "percent"])
    write_csv(output_dir / "subreddit_counts.csv", analysis["subreddit_counts"], ["subreddit", "count", "percent"])
    write_csv(output_dir / "monthly_counts.csv", analysis["monthly_counts"], ["month", "count"])
    write_csv(
        output_dir / "examples.csv",
        analysis["examples"],
        ["issue", "label", "date", "subreddit", "score", "excerpt", "url"],
    )
    write_chart_svg(
        output_dir / "primary_issue_chart.svg",
        render_bar_svg(
            [(row["label"], row["count"]) for row in analysis["primary_issue_counts"][:10]],
            "Primaire oorzaak",
        ),
    )
    write_chart_svg(
        output_dir / "issue_counts_chart.svg",
        render_bar_svg(
            [(row["label"], row["count"]) for row in analysis["issue_counts"][:10]],
            "Multi-label thema's",
        ),
    )
    write_chart_svg(
        output_dir / "monthly_counts_chart.svg",
        render_line_svg(
            [(row["month"], row["count"]) for row in analysis["monthly_counts"] if row["month"] != "unknown"],
            "Klachten per maand",
        ),
    )
    handoff_md = render_ai_handoff(analysis, scoped)
    (output_dir / "ai_handoff.md").write_text(handoff_md, encoding="utf-8")
    write_json(
        output_dir / "ai_handoff_manifest.json",
        build_handoff_manifest(output_dir, input_path, analysis),
    )

    report_md = render_markdown_report(analysis)
    (output_dir / "report.md").write_text(report_md, encoding="utf-8")
    report_html = render_html_report(analysis)
    report_path = output_dir / "report.html"
    report_path.write_text(report_html, encoding="utf-8")
    bundle_path = create_handoff_zip(output_dir, input_path)
    print(f"Report written: {report_path}")
    print(f"AI handoff bundle written: {bundle_path}")
    return report_path


def issue_label(issue: str) -> str:
    if issue in CATEGORY_RULES:
        return CATEGORY_RULES[issue]["label"]
    if issue == "unknown_charger_issue":
        return "Onbekend laadprobleem"
    return issue or "Onbekend"


def build_analysis(records: list[dict[str, Any]], all_records: list[dict[str, Any]], scope: str) -> dict[str, Any]:
    total = len(records)
    issue_counter: Counter[str] = Counter()
    primary_counter: Counter[str] = Counter()
    subreddit_counter: Counter[str] = Counter()
    scope_counter: Counter[str] = Counter(record.get("scope", "unknown") for record in all_records)
    language_counter: Counter[str] = Counter(record.get("language", "unknown") for record in records)
    month_counter: Counter[str] = Counter()

    for record in records:
        subreddit_counter[record.get("subreddit", "unknown")] += 1
        month_counter[utc_month(record.get("created_utc"))] += 1
        primary_counter[record.get("primary_issue", "unknown_charger_issue")] += 1
        for issue in record.get("categories", []):
            issue_counter[issue] += 1

    issue_counts = counter_to_rows(issue_counter, total, "issue", with_labels=True)
    primary_counts = counter_to_rows(primary_counter, total, "issue", with_labels=True)
    subreddit_counts = counter_to_rows(subreddit_counter, total, "subreddit")
    scope_counts = counter_to_rows(scope_counter, len(all_records), "scope")
    monthly_counts = [{"month": key, "count": value} for key, value in sorted(month_counter.items())]

    examples = build_examples(records)
    conclusion = build_conclusion(total, primary_counts, issue_counts, scope)

    return {
        "generated_at": dt.datetime.now(tz=dt.UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "scope": scope,
        "total_records": total,
        "total_all_scopes": len(all_records),
        "post_count": sum(1 for record in records if record.get("source_type") == "post"),
        "comment_count": sum(1 for record in records if record.get("source_type") == "comment"),
        "unique_posts": len({record.get("post_id") for record in records if record.get("post_id")}),
        "unique_subreddits": len({record.get("subreddit") for record in records if record.get("subreddit")}),
        "date_range": date_range(records),
        "issue_counts": issue_counts,
        "primary_issue_counts": primary_counts,
        "subreddit_counts": subreddit_counts,
        "scope_counts": scope_counts,
        "language_counts": counter_to_rows(language_counter, total, "language"),
        "monthly_counts": monthly_counts,
        "examples": examples,
        "conclusion": conclusion,
        "category_descriptions": {
            key: {"label": value["label"], "description": value["description"]}
            for key, value in CATEGORY_RULES.items()
        },
        "method_note": (
            "Deze tool meet Reddit-klachtvolume, niet de echte faalkans van laadpalen. "
            "Classificatie is transparant keyword-based en kan worden aangepast in de code."
        ),
    }


def counter_to_rows(counter: Counter[str], total: int, key_name: str, with_labels: bool = False) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, count in counter.most_common():
        row = {
            key_name: key,
            "count": count,
            "percent": round((count / total * 100), 1) if total else 0.0,
        }
        if with_labels:
            row["label"] = issue_label(key)
        rows.append(row)
    return rows


def date_range(records: list[dict[str, Any]]) -> str:
    dates = [utc_date(record.get("created_utc")) for record in records if utc_date(record.get("created_utc"))]
    if not dates:
        return ""
    return f"{min(dates)} tot {max(dates)}"


def build_examples(records: list[dict[str, Any]], per_issue: int = 3) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        for issue in record.get("categories", []) or [record.get("primary_issue", "unknown_charger_issue")]:
            grouped[issue].append(record)

    rows: list[dict[str, Any]] = []
    for issue, items in grouped.items():
        sorted_items = sorted(
            items,
            key=lambda item: (
                int(item.get("score") or 0),
                len(item.get("categories", [])),
            ),
            reverse=True,
        )
        for item in sorted_items[:per_issue]:
            rows.append(
                {
                    "issue": issue,
                    "label": issue_label(issue),
                    "date": utc_date(item.get("created_utc")),
                    "subreddit": item.get("subreddit", ""),
                    "score": item.get("score", ""),
                    "excerpt": item.get("excerpt", ""),
                    "url": item.get("url", ""),
                }
            )
    return rows


def build_handoff_manifest(output_dir: Path, input_path: Path, analysis: dict[str, Any]) -> dict[str, Any]:
    included = [
        "ai_handoff.md",
        "analysis.json",
        "items.csv",
        "issue_counts.csv",
        "primary_issue_counts.csv",
        "subreddit_counts.csv",
        "scope_counts.csv",
        "monthly_counts.csv",
        "examples.csv",
        "report.md",
        "report.html",
        "primary_issue_chart.svg",
        "issue_counts_chart.svg",
        "monthly_counts_chart.svg",
    ]
    if input_path.exists():
        included.insert(1, "raw_items.jsonl")
    return {
        "purpose": "Upload deze bundel naar Codex/ChatGPT voor een diepere conclusie uit Reddit-klachten over EV-laadpalen.",
        "generated_at": analysis["generated_at"],
        "scope": analysis["scope"],
        "total_records": analysis["total_records"],
        "source_raw_items": str(input_path),
        "output_dir": str(output_dir),
        "recommended_file_to_upload": "codex_handoff.zip",
        "included_files": included,
    }


def create_handoff_zip(output_dir: Path, input_path: Path) -> Path:
    bundle_path = output_dir / "codex_handoff.zip"
    files = [
        output_dir / "ai_handoff.md",
        input_path,
        output_dir / "analysis.json",
        output_dir / "items.csv",
        output_dir / "issue_counts.csv",
        output_dir / "primary_issue_counts.csv",
        output_dir / "subreddit_counts.csv",
        output_dir / "scope_counts.csv",
        output_dir / "monthly_counts.csv",
        output_dir / "examples.csv",
        output_dir / "report.md",
        output_dir / "report.html",
        output_dir / "primary_issue_chart.svg",
        output_dir / "issue_counts_chart.svg",
        output_dir / "monthly_counts_chart.svg",
        output_dir / "ai_handoff_manifest.json",
    ]
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        seen_names: set[str] = set()
        for path in files:
            if not path.exists() or path.is_dir():
                continue
            arcname = "raw_items.jsonl" if path.resolve() == input_path.resolve() else path.name
            if arcname in seen_names:
                continue
            seen_names.add(arcname)
            archive.write(path, arcname=arcname)
    return bundle_path


def build_conclusion(
    total: int,
    primary_counts: list[dict[str, Any]],
    issue_counts: list[dict[str, Any]],
    scope: str,
) -> str:
    if not total:
        return (
            "Er zijn geen relevante records gevonden voor deze scope. "
            "Verbreed de zoekqueries, verhoog --limit-per-query of gebruik --scope all."
        )
    top_primary = primary_counts[:3]
    top_multi = issue_counts[:4]
    scope_text = {
        "public": "publieke laadpalen",
        "home": "thuisladers/wallboxen",
        "all": "alle laadcontexten",
        "unknown": "onbekende laadcontexten",
    }.get(scope, scope)
    primary_sentence = ", ".join(
        f"{row['label']} ({row['count']} records, {row['percent']}%)" for row in top_primary
    )
    multi_sentence = ", ".join(f"{row['label']} ({row['count']})" for row in top_multi)
    return (
        f"Voor {scope_text} komt in deze Reddit-set vooral dit naar voren: {primary_sentence}. "
        f"Omdat records meerdere labels kunnen krijgen, zijn de breedst genoemde thema's: {multi_sentence}. "
        "Lees dit als signaalanalyse van klachten: het laat zien waar mensen over klagen, niet hoeveel fysieke laders objectief defect zijn."
    )


def render_markdown_report(analysis: dict[str, Any]) -> str:
    lines = [
        "# Reddit EV-laadpaal klachtenanalyse",
        "",
        f"Gegenereerd: {analysis['generated_at']}",
        f"Scope: {analysis['scope']}",
        "",
        "## Conclusie",
        "",
        analysis["conclusion"],
        "",
        "## Kerncijfers",
        "",
        f"- Records in analyse: {analysis['total_records']}",
        f"- Unieke posts: {analysis['unique_posts']}",
        f"- Subreddits: {analysis['unique_subreddits']}",
        f"- Periode: {analysis['date_range'] or 'onbekend'}",
        "",
        "## Primaire oorzaak per record",
        "",
        "| Oorzaak | Aantal | % |",
        "|---|---:|---:|",
    ]
    for row in analysis["primary_issue_counts"]:
        lines.append(f"| {row['label']} | {row['count']} | {row['percent']} |")
    lines.extend(["", "## Multi-label thema's", "", "| Thema | Aantal | % |", "|---|---:|---:|"])
    for row in analysis["issue_counts"]:
        lines.append(f"| {row['label']} | {row['count']} | {row['percent']} |")
    lines.extend(["", "## Voorbeelden", ""])
    for row in analysis["examples"][:12]:
        lines.append(f"- **{row['label']}** / r/{row['subreddit']}: {row['excerpt']} ({row['url']})")
    lines.extend(["", f"> {analysis['method_note']}"])
    return "\n".join(lines) + "\n"


def render_ai_handoff(analysis: dict[str, Any], records: list[dict[str, Any]], max_records: int = 120) -> str:
    top_records = sorted(
        records,
        key=lambda item: (
            int(item.get("score") or 0),
            len(item.get("categories", [])),
        ),
        reverse=True,
    )[:max_records]
    lines = [
        "# AI-handoff: Reddit EV-laadpaal klachten",
        "",
        "Gebruik deze bundel om een sterkere conclusie te maken uit de Reddit-data.",
        "",
        "## Vraag aan Codex/ChatGPT",
        "",
        (
            "Analyseer de meegeleverde Reddit-data over klachten rond EV-laadpalen. "
            "Maak een duidelijke conclusie over wat meestal kapot gaat, welke patronen terugkomen, "
            "welke categorieen dominant zijn, en welke nuance nodig is omdat Reddit-data klachtvolume meet "
            "en geen objectieve faalkans. Gebruik de tabellen, ruwe records en voorbeelden. "
            "Geef de conclusie in het Nederlands met een korte executive summary, statistieken, "
            "een interpretatie per foutcategorie en concrete aanbevelingen voor verder onderzoek."
        ),
        "",
        "## Bestanden in de bundel",
        "",
        "- `raw_items.jsonl`: ruwe Reddit records met classificatievelden",
        "- `analysis.json`: gestructureerde samenvatting",
        "- `items.csv`: tabel met alle geclassificeerde records",
        "- `issue_counts.csv`: multi-label tellingen",
        "- `primary_issue_counts.csv`: primaire oorzaak per record",
        "- `examples.csv`: voorbeelden per categorie",
        "- `report.html` en `report.md`: automatisch rapport",
        "- `*.svg`: losse grafieken",
        "",
        "## Automatische conclusie van de tool",
        "",
        analysis["conclusion"],
        "",
        "## Kerncijfers",
        "",
        f"- Scope: `{analysis['scope']}`",
        f"- Records in analyse: `{analysis['total_records']}`",
        f"- Unieke posts: `{analysis['unique_posts']}`",
        f"- Subreddits: `{analysis['unique_subreddits']}`",
        f"- Periode: `{analysis['date_range'] or 'onbekend'}`",
        "",
        "## Primaire oorzaak per record",
        "",
        "| Oorzaak | Aantal | % |",
        "|---|---:|---:|",
    ]
    for row in analysis["primary_issue_counts"]:
        lines.append(f"| {row['label']} | {row['count']} | {row['percent']} |")
    lines.extend(["", "## Multi-label thema's", "", "| Thema | Aantal | % |", "|---|---:|---:|"])
    for row in analysis["issue_counts"]:
        lines.append(f"| {row['label']} | {row['count']} | {row['percent']} |")
    lines.extend(["", "## Belangrijkste records om te bekijken", ""])
    for record in top_records:
        labels = ", ".join(issue_label(issue) for issue in record.get("categories", [])) or "Onbekend"
        lines.append(
            f"- **{labels}** - r/{record.get('subreddit', '')} - score {record.get('score', '')} - "
            f"{record.get('excerpt', '')} - {record.get('url', '')}"
        )
    lines.extend(["", "## Methodische waarschuwing", "", analysis["method_note"]])
    return "\n".join(lines) + "\n"


def render_html_report(analysis: dict[str, Any]) -> str:
    primary_chart = render_bar_svg(
        [(row["label"], row["count"]) for row in analysis["primary_issue_counts"][:10]],
        "Primaire oorzaak",
    )
    multi_chart = render_bar_svg(
        [(row["label"], row["count"]) for row in analysis["issue_counts"][:10]],
        "Multi-label thema's",
    )
    month_chart = render_line_svg(
        [(row["month"], row["count"]) for row in analysis["monthly_counts"] if row["month"] != "unknown"],
        "Klachten per maand",
    )
    issue_rows = "".join(
        f"<tr><td>{esc(row['label'])}</td><td>{row['count']}</td><td>{row['percent']}%</td></tr>"
        for row in analysis["issue_counts"]
    )
    primary_rows = "".join(
        f"<tr><td>{esc(row['label'])}</td><td>{row['count']}</td><td>{row['percent']}%</td></tr>"
        for row in analysis["primary_issue_counts"]
    )
    subreddit_rows = "".join(
        f"<tr><td>r/{esc(row['subreddit'])}</td><td>{row['count']}</td><td>{row['percent']}%</td></tr>"
        for row in analysis["subreddit_counts"][:20]
    )
    scope_rows = "".join(
        f"<tr><td>{esc(row['scope'])}</td><td>{row['count']}</td><td>{row['percent']}%</td></tr>"
        for row in analysis["scope_counts"]
    )
    examples = "".join(
        "<article class='example'>"
        f"<div><strong>{esc(row['label'])}</strong> - r/{esc(row['subreddit'])} - "
        f"{esc(str(row['date']))} - score {esc(str(row['score']))}</div>"
        f"<p>{esc(row['excerpt'])}</p>"
        f"<a href='{esc(row['url'])}'>Bron op Reddit</a>"
        "</article>"
        for row in analysis["examples"][:24]
    )
    category_cards = "".join(
        "<div class='category-card'>"
        f"<strong>{esc(value['label'])}</strong>"
        f"<span>{esc(value['description'])}</span>"
        "</div>"
        for value in analysis["category_descriptions"].values()
    )
    return f"""<!doctype html>
<html lang="nl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Reddit EV-laadpaal klachtenanalyse</title>
  <style>
    :root {{
      --bg: #f7f8f5;
      --ink: #17201d;
      --muted: #64716b;
      --line: #dce4dd;
      --accent: #156f68;
      --accent-2: #c35b37;
      --panel: #ffffff;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Arial, Helvetica, sans-serif;
      background: var(--bg);
      color: var(--ink);
      line-height: 1.5;
    }}
    header {{
      padding: 32px clamp(18px, 4vw, 56px) 20px;
      background: #e9f1ed;
      border-bottom: 1px solid var(--line);
    }}
    main {{ padding: 24px clamp(18px, 4vw, 56px) 48px; max-width: 1180px; margin: 0 auto; }}
    h1 {{ margin: 0 0 6px; font-size: clamp(28px, 4vw, 44px); letter-spacing: 0; }}
    h2 {{ margin: 28px 0 12px; font-size: 22px; letter-spacing: 0; }}
    p {{ max-width: 920px; }}
    .meta {{ color: var(--muted); }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 18px 0; }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
    }}
    .card strong {{ display: block; font-size: 28px; color: var(--accent); }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; align-items: start; }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      overflow-x: auto;
    }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ text-align: left; padding: 9px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 700; }}
    svg {{ width: 100%; height: auto; display: block; }}
    .example {{ border-top: 1px solid var(--line); padding: 12px 0; }}
    .example:first-child {{ border-top: 0; }}
    .example p {{ margin: 6px 0; }}
    a {{ color: var(--accent); }}
    .note {{ color: var(--muted); font-size: 14px; }}
    .category-list {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 10px; }}
    .category-card {{ background: #fbfcfa; border: 1px solid var(--line); border-radius: 8px; padding: 10px; }}
    .category-card strong, .category-card span {{ display: block; }}
    .category-card span {{ color: var(--muted); font-size: 13px; margin-top: 4px; }}
  </style>
</head>
<body>
  <header>
    <h1>Reddit EV-laadpaal klachtenanalyse</h1>
    <div class="meta">Gegenereerd: {esc(analysis['generated_at'])} - Scope: {esc(analysis['scope'])}</div>
  </header>
  <main>
    <section class="panel">
      <h2>Conclusie</h2>
      <p>{esc(analysis['conclusion'])}</p>
      <p class="note">{esc(analysis['method_note'])}</p>
    </section>
    <section class="cards">
      <div class="card"><strong>{analysis['total_records']}</strong><span>records in analyse</span></div>
      <div class="card"><strong>{analysis['unique_posts']}</strong><span>unieke Reddit-posts</span></div>
      <div class="card"><strong>{analysis['unique_subreddits']}</strong><span>subreddits</span></div>
      <div class="card"><strong>{esc(analysis['date_range'] or 'onbekend')}</strong><span>periode</span></div>
    </section>
    <section class="grid">
      <div class="panel">
        <h2>Primaire oorzaak</h2>
        {primary_chart}
      </div>
      <div class="panel">
        <h2>Alle genoemde thema's</h2>
        {multi_chart}
      </div>
    </section>
    <section class="panel">
      <h2>Tijdlijn</h2>
      {month_chart}
    </section>
    <section class="grid">
      <div class="panel">
        <h2>Primaire oorzaak tabel</h2>
        <table><thead><tr><th>Oorzaak</th><th>Aantal</th><th>%</th></tr></thead><tbody>{primary_rows}</tbody></table>
      </div>
      <div class="panel">
        <h2>Multi-label tabel</h2>
        <table><thead><tr><th>Thema</th><th>Aantal</th><th>%</th></tr></thead><tbody>{issue_rows}</tbody></table>
      </div>
    </section>
    <section class="grid">
      <div class="panel">
        <h2>Subreddits</h2>
        <table><thead><tr><th>Bron</th><th>Aantal</th><th>%</th></tr></thead><tbody>{subreddit_rows}</tbody></table>
      </div>
      <div class="panel">
        <h2>Scopeverdeling</h2>
        <table><thead><tr><th>Scope</th><th>Aantal</th><th>%</th></tr></thead><tbody>{scope_rows}</tbody></table>
      </div>
    </section>
    <section class="panel">
      <h2>Voorbeelden per categorie</h2>
      {examples}
    </section>
    <section class="panel">
      <h2>Categorie-definities</h2>
      <div class="category-list">{category_cards}</div>
    </section>
  </main>
</body>
</html>"""


def render_bar_svg(items: list[tuple[str, int]], title: str) -> str:
    if not items:
        return "<p class='note'>Geen data beschikbaar.</p>"
    width = 760
    row_h = 34
    label_w = 230
    chart_w = width - label_w - 80
    height = 44 + row_h * len(items)
    max_value = max(value for _, value in items) or 1
    rows = []
    for idx, (label, value) in enumerate(items):
        y = 34 + idx * row_h
        bar_w = int((value / max_value) * chart_w)
        fill = "#156f68" if idx % 2 == 0 else "#c35b37"
        rows.append(
            f"<text x='0' y='{y + 18}' font-size='13' fill='#17201d'>{esc_svg(shorten(label, 30))}</text>"
            f"<rect x='{label_w}' y='{y}' width='{bar_w}' height='22' rx='4' fill='{fill}' />"
            f"<text x='{label_w + bar_w + 8}' y='{y + 16}' font-size='13' fill='#17201d'>{value}</text>"
        )
    return (
        f"<svg viewBox='0 0 {width} {height}' role='img' aria-label='{esc_svg(title)}'>"
        f"<text x='0' y='18' font-size='14' fill='#64716b'>{esc_svg(title)}</text>"
        + "".join(rows)
        + "</svg>"
    )


def render_line_svg(items: list[tuple[str, int]], title: str) -> str:
    if not items:
        return "<p class='note'>Geen tijdsdata beschikbaar.</p>"
    width = 900
    height = 280
    pad_l = 44
    pad_r = 24
    pad_t = 28
    pad_b = 52
    chart_w = width - pad_l - pad_r
    chart_h = height - pad_t - pad_b
    max_value = max(value for _, value in items) or 1
    step = chart_w / max(1, len(items) - 1)
    points = []
    circles = []
    labels = []
    for idx, (label, value) in enumerate(items):
        x = pad_l + idx * step
        y = pad_t + chart_h - ((value / max_value) * chart_h)
        points.append(f"{x:.1f},{y:.1f}")
        circles.append(f"<circle cx='{x:.1f}' cy='{y:.1f}' r='4' fill='#156f68'><title>{esc_svg(label)}: {value}</title></circle>")
        if len(items) <= 14 or idx % math.ceil(len(items) / 10) == 0:
            labels.append(
                f"<text x='{x:.1f}' y='{height - 22}' font-size='11' fill='#64716b' text-anchor='middle'>{esc_svg(label)}</text>"
            )
    grid = "".join(
        f"<line x1='{pad_l}' y1='{pad_t + chart_h * tick / 4:.1f}' x2='{width - pad_r}' "
        f"y2='{pad_t + chart_h * tick / 4:.1f}' stroke='#dce4dd'/>"
        for tick in range(5)
    )
    return (
        f"<svg viewBox='0 0 {width} {height}' role='img' aria-label='{esc_svg(title)}'>"
        f"<text x='0' y='18' font-size='14' fill='#64716b'>{esc_svg(title)}</text>"
        f"{grid}<polyline points='{' '.join(points)}' fill='none' stroke='#156f68' stroke-width='3'/>"
        + "".join(circles)
        + "".join(labels)
        + f"<text x='8' y='{pad_t + 4}' font-size='11' fill='#64716b'>{max_value}</text>"
        + f"<text x='16' y='{pad_t + chart_h}' font-size='11' fill='#64716b'>0</text>"
        + "</svg>"
    )


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def esc_svg(value: Any) -> str:
    return html.escape(str(value), quote=True)


def shorten(value: str, max_len: int) -> str:
    return value if len(value) <= max_len else value[: max_len - 3] + "..."


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scrape Reddit EV-laadpaal klachten en genereer statistieken/grafieken.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scrape_parser = sub.add_parser("scrape", help="Scrape Reddit naar raw_items.jsonl.")
    add_scrape_args(scrape_parser)

    analyze_parser = sub.add_parser("analyze", help="Analyseer een raw_items.jsonl bestand.")
    analyze_parser.add_argument("--input", required=True, help="Pad naar raw_items.jsonl.")
    analyze_parser.add_argument("--output", required=True, help="Outputmap voor rapporten.")
    analyze_parser.add_argument(
        "--scope",
        default="public",
        choices=["public", "home", "unknown", "all"],
        help="Welke context analyseren. Standaard: public.",
    )

    run_parser = sub.add_parser("run", help="Scrape en analyseer in een stap.")
    add_scrape_args(run_parser)
    run_parser.add_argument(
        "--scope",
        default="public",
        choices=["public", "home", "unknown", "all"],
        help="Welke context analyseren. Standaard: public.",
    )

    return parser


def add_scrape_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", required=True, help="Outputmap.")
    parser.add_argument("--query-file", help="JSON-config met queries en seed_urls.")
    parser.add_argument("--seed-url", action="append", help="Extra Reddit post URL. Mag meerdere keren.")
    parser.add_argument("--limit-per-query", type=int, default=8, help="Max posts per zoekquery. Standaard: 8.")
    parser.add_argument("--comments-per-post", type=int, default=120, help="Max comments per post fetch. Standaard: 120.")
    parser.add_argument("--max-posts", type=int, default=60, help="Globale limiet op posts. Standaard: 60.")
    parser.add_argument("--delay", type=float, default=1.2, help="Seconden wachten tussen Reddit requests.")
    parser.add_argument("--keep-irrelevant", action="store_true", help="Bewaar ook records zonder klacht-classificatie.")
    parser.add_argument("--sample", action="store_true", help="Gebruik lokale sample-data in plaats van Reddit te scrapen.")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "scrape":
        scrape(args)
        return 0
    if args.command == "analyze":
        analyze(args)
        return 0
    if args.command == "run":
        raw_path = scrape(args)
        analyze_args = argparse.Namespace(input=str(raw_path), output=args.output, scope=args.scope)
        analyze(analyze_args)
        return 0
    parser.error("Unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
