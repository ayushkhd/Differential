"""ClickHouse access for the harness and backend. Only this lane talks to ClickHouse (contract 2)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import clickhouse_connect
from dotenv import load_dotenv

load_dotenv()

COLUMNS = ["run_id", "attack_id", "family", "variant", "version", "ts", "type", "actor", "payload"]

DDL = """
CREATE TABLE IF NOT EXISTS events (
  run_id String, attack_id String, family LowCardinality(String),
  variant UInt16, version LowCardinality(String), ts DateTime64(3),
  type LowCardinality(String), actor String, payload String
) ENGINE = MergeTree ORDER BY (run_id, attack_id, version, ts)
"""


def client():
    return clickhouse_connect.get_client(
        host=os.environ["CLICKHOUSE_HOST"],
        user=os.environ.get("CLICKHOUSE_USER", "default"),
        password=os.environ["CLICKHOUSE_PASSWORD"],
        secure=os.environ.get("CLICKHOUSE_SECURE", "1") == "1",
    )


def init() -> None:
    client().command(DDL)


def row(run_id: str, attack_id: str, family: str, variant: int, version: str,
        type_: str, actor: str, payload: dict) -> list:
    return [run_id, attack_id, family, variant, version, datetime.now(timezone.utc),
            type_, actor, json.dumps(payload)]


def insert(rows: list[list]) -> None:
    if rows:
        client().insert("events", rows, column_names=COLUMNS)


if __name__ == "__main__":
    init()
    print("SELECT 1 ->", client().query("SELECT 1").result_set[0][0])
    print("events rows:", client().query("SELECT count() FROM events").result_set[0][0])
