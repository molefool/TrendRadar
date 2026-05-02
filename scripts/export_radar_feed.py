#!/usr/bin/env python3
import argparse
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo


def parse_args():
    parser = argparse.ArgumentParser(description="Export TrendRadar SQLite data to a static finance-app feed.")
    parser.add_argument("--input-dir", default="output/news", help="Directory containing TrendRadar news sqlite files.")
    parser.add_argument("--output-dir", default="_site/radar", help="Directory for exported JSON feed files.")
    parser.add_argument("--timezone", default="Asia/Shanghai", help="Timezone used to convert HH:MM crawl times.")
    return parser.parse_args()


def latest_db(input_dir: Path) -> Optional[Path]:
    db_files = sorted(input_dir.glob("*.db"))
    if not db_files:
        return None
    return db_files[-1]


def hhmm_to_unix(date_text: str, time_text: str, tz: ZoneInfo) -> int:
    value = (time_text or "").strip()
    for fmt in ("%H:%M", "%H时%M分", "%H-%M"):
        try:
            dt = datetime.strptime(f"{date_text} {value}", f"%Y-%m-%d {fmt}")
            return int(dt.replace(tzinfo=tz).timestamp())
        except ValueError:
            pass
    return int(time.time())


def load_feed(db_path: Path, tz_name: str) -> dict:
    date_text = db_path.stem
    tz = ZoneInfo(tz_name)
    generated_at = int(time.time())

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    latest_record = conn.execute(
        "SELECT id, crawl_time FROM crawl_records ORDER BY id DESC LIMIT 1"
    ).fetchone()
    latest_crawl_time = latest_record["crawl_time"] if latest_record else ""

    where_clause = ""
    params: tuple[str, ...] = ()
    if latest_crawl_time:
        where_clause = "WHERE n.last_crawl_time = ?"
        params = (latest_crawl_time,)

    rows = conn.execute(
        f"""
        SELECT n.title, n.platform_id, COALESCE(p.name, n.platform_id) AS source_name,
               n.rank, n.url, n.mobile_url, n.first_crawl_time, n.last_crawl_time,
               n.crawl_count
        FROM news_items n
        LEFT JOIN platforms p ON n.platform_id = p.id
        {where_clause}
        ORDER BY n.platform_id ASC, n.rank ASC, n.id ASC
        """,
        params,
    ).fetchall()

    source_counts: dict[str, dict] = {}
    items = []
    for row in rows:
        source_id = row["platform_id"]
        source_name = row["source_name"] or source_id
        source_counts.setdefault(source_id, {"id": source_id, "name": source_name, "count": 0})
        source_counts[source_id]["count"] += 1
        items.append(
            {
                "type": "hotlist",
                "sourceId": source_id,
                "sourceName": source_name,
                "title": row["title"],
                "rank": int(row["rank"] or 0),
                "url": row["url"] or "",
                "mobileUrl": row["mobile_url"] or "",
                "firstSeenTime": hhmm_to_unix(date_text, row["first_crawl_time"], tz),
                "lastSeenTime": hhmm_to_unix(date_text, row["last_crawl_time"], tz),
                "seenCount": int(row["crawl_count"] or 1),
            }
        )

    failed_sources = []
    if latest_record:
        failed_rows = conn.execute(
            """
            SELECT css.platform_id, COALESCE(p.name, css.platform_id) AS source_name
            FROM crawl_source_status css
            LEFT JOIN platforms p ON css.platform_id = p.id
            WHERE css.crawl_record_id = ? AND css.status = 'failed'
            ORDER BY css.platform_id ASC
            """,
            (latest_record["id"],),
        ).fetchall()
        failed_sources = [
            {"id": row["platform_id"], "name": row["source_name"] or row["platform_id"]}
            for row in failed_rows
        ]

    conn.close()

    return {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "date": date_text,
        "crawlTime": latest_crawl_time,
        "dataTypes": ["hotlist"],
        "sources": sorted(source_counts.values(), key=lambda item: item["name"]),
        "items": items,
        "failedSources": failed_sources,
    }


def write_feed(feed: dict, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    latest_path = output_dir / "latest.json"
    dated_path = output_dir / f"{feed['date']}.json"
    payload = json.dumps(feed, ensure_ascii=False, indent=2)
    latest_path.write_text(payload + "\n", encoding="utf-8")
    dated_path.write_text(payload + "\n", encoding="utf-8")

    index_path = output_dir.parent / "index.html"
    index_path.write_text(
        """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TrendRadar Feed</title>
</head>
<body>
  <p><a href="./radar/latest.json">radar/latest.json</a></p>
</body>
</html>
""",
        encoding="utf-8",
    )


def main() -> None:
    args = parse_args()
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    db_path = latest_db(input_dir)

    if not db_path:
        today = datetime.now(ZoneInfo(args.timezone)).strftime("%Y-%m-%d")
        feed = {
            "schemaVersion": 1,
            "generatedAt": int(time.time()),
            "date": today,
            "crawlTime": "",
            "dataTypes": ["hotlist"],
            "sources": [],
            "items": [],
            "failedSources": [],
            "warnings": ["TrendRadar did not produce a news database."],
        }
    else:
        feed = load_feed(db_path, args.timezone)

    write_feed(feed, output_dir)
    print(f"Exported {len(feed['items'])} items to {output_dir / 'latest.json'}")


if __name__ == "__main__":
    main()
