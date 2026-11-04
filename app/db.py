"""Read-only DuckDB access with per-query timeouts and row limits."""
import datetime as dt
import threading
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb


class QueryError(Exception):
    pass


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool


class Database:
    def __init__(self, path: Path):
        self._con = duckdb.connect(
            str(path), read_only=True, config={"enable_external_access": False}
        )

    def query(self, sql: str, params: list | None = None, timeout_s: float = 5.0,
              max_rows: int = 1000) -> QueryResult:
        cur = self._con.cursor()
        timer = threading.Timer(timeout_s, cur.interrupt)
        timer.start()
        try:
            cur.execute(sql, params or [])
            columns = [d[0] for d in cur.description]
            rows = cur.fetchmany(max_rows + 1)
        except duckdb.InterruptException as e:
            raise QueryError(f"query timed out after {timeout_s}s") from e
        except duckdb.Error as e:
            raise QueryError(str(e)) from e
        finally:
            timer.cancel()
            cur.close()
        return QueryResult(columns, rows[:max_rows], len(rows) > max_rows)

    def explain(self, sql: str) -> None:
        self.query("EXPLAIN " + sql)


def to_jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, dt.time):
        return value.strftime("%H:%M:%S")
    return value
