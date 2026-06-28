#!/usr/bin/env python3
import argparse
import csv
import datetime as dt
import json
import re
import subprocess
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from statistics import mean

TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9\-]{2,}")
DEFAULT_FEEDS = [
    "https://feeds.feedburner.com/TechCrunch/",
    "https://www.reutersagency.com/feed/?best-topics=technology",
    "https://www.reutersagency.com/feed/?best-topics=business-finance",
]
STOPWORDS = {
    "about", "after", "again", "agent", "agents", "align", "also", "among", "and", "are", "because",
    "been", "being", "between", "both", "build", "can", "companies", "company", "data", "from", "future",
    "have", "into", "layer", "layers", "long", "more", "most", "next", "over", "platform", "prompt",
    "same", "should", "some", "systems", "that", "their", "them", "there", "these", "they", "this",
    "toward", "under", "using", "view", "vision", "what", "when", "with", "would",
}


def tokenize(text: str):
    return [t.lower() for t in TOKEN_RE.findall(text or "")]


def read_pdf_text(path: Path) -> str:
    try:
        result = subprocess.run(
            ["pdftotext", str(path), "-"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return result.stdout
    except Exception:
        return ""


def load_vision_text(root: Path, prompt_file: Path | None, pdf_glob: str):
    parts = []
    if prompt_file and prompt_file.exists():
        parts.append(prompt_file.read_text(encoding="utf-8"))
    pdf_paths = sorted(root.glob(pdf_glob))
    for pdf in pdf_paths:
        text = read_pdf_text(pdf)
        if text:
            parts.append(text)
    return "\n".join(parts), [str(p.name) for p in pdf_paths]


def decompose_vision(vision_text: str):
    freq = {}
    for token in tokenize(vision_text):
        if token in STOPWORDS:
            continue
        freq[token] = freq.get(token, 0) + 1
    keywords = sorted(freq, key=lambda k: (-freq[k], k))[:50]

    technologies = [k for k in keywords if k in {"ai", "agentic", "robotics", "automation", "autonomous", "sensors", "llm", "vision"}]
    industries = [k for k in keywords if k in {"manufacturing", "logistics", "energy", "healthcare", "finance", "mobility"}]
    signals = [k for k in keywords if k in {"interoperability", "coordination", "policy", "adaptive", "autonomy", "optimization"}]
    themes = keywords[:10]
    return {
        "themes": themes,
        "keywords": keywords,
        "technologies": technologies,
        "industries": industries,
        "signals": signals,
        "weights": freq,
    }


def fetch_text(url: str):
    with urllib.request.urlopen(url, timeout=20) as resp:
        return resp.read()


def parse_rss(feed_url: str):
    try:
        raw = fetch_text(feed_url)
        root = ET.fromstring(raw)
    except Exception:
        return []

    items = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        desc = (item.findtext("description") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        if title:
            items.append({"title": title, "description": desc, "link": link, "published": pub, "source": feed_url})
    return items


def infer_entity_name(title: str):
    title = re.sub(r"\s+", " ", title).strip()
    parts = re.split(r"[:\-\|]", title)
    return (parts[0] if parts else title).strip()[:120]


def score_alignment(text: str, weights: dict):
    tokens = tokenize(text)
    if not tokens or not weights:
        return 0.0, []
    matched = []
    raw = 0
    for tok in tokens:
        if tok in weights:
            raw += weights[tok]
            matched.append(tok)
    normalized = min(10.0, round((raw / max(1, len(tokens))) * 2.5, 2))
    return normalized, sorted(set(matched))[:12]


def trend_direction(values: list[float]):
    if len(values) < 2:
        return "stable", 0.0
    slope = values[-1] - values[0]
    pct = round((slope / values[0]) * 100, 2) if values[0] else 0.0
    if pct > 3:
        return "up", pct
    if pct < -3:
        return "down", pct
    return "stable", pct


def fetch_stock_history(ticker: str, days: int = 30):
    query = urllib.parse.urlencode({"s": f"{ticker.lower()}.us", "i": "d"})
    url = f"https://stooq.com/q/d/l/?{query}"
    try:
        body = fetch_text(url).decode("utf-8", errors="ignore")
    except Exception:
        return []

    rows = list(csv.DictReader(body.splitlines()))
    closes = []
    for row in rows[-days:]:
        try:
            closes.append(float(row.get("Close", "")))
        except Exception:
            continue
    return closes


def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data):
    ensure_dir(path.parent)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def process_daily(root: Path, vision_prompt_file: str, pdf_glob: str, date_str: str | None):
    run_date = dt.date.fromisoformat(date_str) if date_str else dt.date.today()
    prompt_path = root / vision_prompt_file if vision_prompt_file else None
    vision_text, pdf_files = load_vision_text(root, prompt_path, pdf_glob)
    vision = decompose_vision(vision_text)

    discovered = []
    for feed in DEFAULT_FEEDS:
        discovered.extend(parse_rss(feed))

    tracked_path = root / "data" / "tracked_entities.json"
    tracked = load_json(tracked_path, {})

    entity_rows = []
    for item in discovered[:120]:
        combined = f"{item['title']} {item['description']}"
        score, matched = score_alignment(combined, vision["weights"])
        if score < 0.5:
            continue

        name = infer_entity_name(item["title"])
        entity = tracked.get(name, {"name": name, "first_seen": str(run_date), "updates": 0, "ticker": ""})
        entity["updates"] = entity.get("updates", 0) + 1
        entity["last_seen"] = str(run_date)
        entity["latest_score"] = score
        entity["latest_match"] = matched
        entity["latest_link"] = item.get("link", "")

        if not entity.get("ticker"):
            m = re.search(r"\(([A-Z]{1,5})\)", item["title"])
            if m:
                entity["ticker"] = m.group(1)

        stock = {"trend_direction": "stable", "trend_pct_30d": 0.0, "rating": "watch"}
        if entity.get("ticker"):
            prices = fetch_stock_history(entity["ticker"], 30)
            direction, pct = trend_direction(prices)
            rating = "high" if score >= 7 and direction == "up" else "medium" if score >= 4 else "watch"
            stock = {
                "ticker": entity["ticker"],
                "trend_direction": direction,
                "trend_pct_30d": pct,
                "price_samples": len(prices),
                "rating": rating,
            }

        tracked[name] = entity
        entity_rows.append(
            {
                "name": name,
                "type": "company|article",
                "alignment_score": score,
                "trend_direction": stock["trend_direction"],
                "rationale": f"Matched themes: {', '.join(matched[:6])}" if matched else "Low direct match",
                "strategic_importance": "High" if score >= 7 else "Medium" if score >= 4 else "Low",
                "stock_market_analysis": stock,
                "source": item.get("source", ""),
                "link": item.get("link", ""),
            }
        )

    save_json(tracked_path, tracked)

    daily_report = {
        "date": str(run_date),
        "vision_pdfs": pdf_files,
        "themes": vision["themes"],
        "technologies": vision["technologies"],
        "industries": vision["industries"],
        "signals": vision["signals"],
        "entities": sorted(entity_rows, key=lambda e: e["alignment_score"], reverse=True)[:50],
    }

    daily_path = root / "data" / "daily" / f"{run_date.isoformat()}.json"
    save_json(daily_path, daily_report)
    return daily_path, tracked_path


def build_monthly_summary(root: Path, month: str | None):
    if month is None:
        now = dt.date.today()
        month = f"{now.year:04d}-{now.month:02d}"

    daily_dir = root / "data" / "daily"
    files = sorted(daily_dir.glob(f"{month}-*.json"))
    rows = [load_json(p, {}) for p in files]

    entities = {}
    for row in rows:
        for ent in row.get("entities", []):
            name = ent.get("name", "")
            if not name:
                continue
            bucket = entities.setdefault(name, {"scores": [], "up": 0, "down": 0, "stable": 0})
            bucket["scores"].append(ent.get("alignment_score", 0.0))
            td = ent.get("trend_direction", "stable")
            bucket[td] = bucket.get(td, 0) + 1

    monthly_entities = []
    for name, bucket in entities.items():
        avg_score = round(mean(bucket["scores"]), 2) if bucket["scores"] else 0.0
        direction = max(["up", "down", "stable"], key=lambda d: bucket.get(d, 0))
        monthly_entities.append({
            "name": name,
            "average_alignment_score": avg_score,
            "dominant_trend_direction": direction,
            "observations": len(bucket["scores"]),
        })

    monthly_report = {
        "month": month,
        "days_processed": len(rows),
        "entity_count": len(monthly_entities),
        "entities": sorted(monthly_entities, key=lambda e: e["average_alignment_score"], reverse=True)[:100],
    }
    out = root / "data" / "monthly" / f"{month}.json"
    save_json(out, monthly_report)
    return out


def main():
    parser = argparse.ArgumentParser(description="Investment agent: daily discovery, tracking, stock trends, monthly rollups.")
    sub = parser.add_subparsers(dest="command", required=True)

    daily = sub.add_parser("daily")
    daily.add_argument("--date", default=None, help="Run date (YYYY-MM-DD). Defaults to today.")
    daily.add_argument("--prompt-file", default="investment_agent_prompt.txt", help="Prompt text file in repo root.")
    daily.add_argument("--pdf-glob", default="investment-vision-*.pdf", help="Glob for vision PDFs in repo root.")

    monthly = sub.add_parser("monthly")
    monthly.add_argument("--month", default=None, help="Month to summarize (YYYY-MM). Defaults to current month.")

    args = parser.parse_args()
    root = Path(__file__).resolve().parent

    if args.command == "daily":
        daily_path, tracked_path = process_daily(root, args.prompt_file, args.pdf_glob, args.date)
        print(json.dumps({"daily_report": str(daily_path), "tracked_entities": str(tracked_path)}, indent=2))
    elif args.command == "monthly":
        monthly_path = build_monthly_summary(root, args.month)
        print(json.dumps({"monthly_report": str(monthly_path)}, indent=2))


if __name__ == "__main__":
    main()
