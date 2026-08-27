from __future__ import annotations

import logging
import re
import time
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from .config import Config
from .serialization import normalize_row


LOGGER = logging.getLogger("hosxp-polling-agent.database")

OPITEMRECE_COLUMNS = """
hos_guid, vn, hn, an, icode, qty, drugusage, idr, iperday, iperdose,
recetime, unitprice, vstdate, vsttime, doctor, rxdate, rxtime, sp_use,
hcode, print, dep_code, finance_number, discount, use_right, node_id,
order_no, sub_type, pttype, income, remain, item_type, staff, doctor_lock,
paidst, item_no, last_modified, sum_price, cost, stock_department_id,
command_doctor, opi_doctor_finance_type_id
""".strip()


class SourceError(RuntimeError):
    pass


class SlaveUnhealthy(SourceError):
    pass


def _row_to_dict(cursor: Any, row: Any) -> Dict[str, Any]:
    columns = [item[0] for item in cursor.description]
    return dict(zip(columns, row))


def prepare_vn_query(raw_query: str) -> str:
    query = raw_query.strip()
    if query.endswith(";"):
        query = query[:-1].rstrip()
    if not re.match(r"^SELECT\b", query, flags=re.IGNORECASE):
        raise SourceError("sql.txt must contain exactly one read-only SELECT")
    if ";" in query:
        raise SourceError("sql.txt must contain only one SQL statement")
    # A single statement that starts with SELECT cannot contain MariaDB DML/DDL,
    # but SELECT itself still has a few file/locking/side-effect forms. Block
    # those explicitly while allowing scalar functions such as REPLACE().
    forbidden_select_forms = re.compile(
        r"\bINTO\s+(OUTFILE|DUMPFILE)\b|"
        r"\bLOAD_FILE\s*\(|"
        r"\b(GET_LOCK|RELEASE_LOCK|SLEEP|BENCHMARK)\s*\(|"
        r"\bFOR\s+UPDATE\b|"
        r"\bLOCK\s+IN\s+SHARE\s+MODE\b",
        flags=re.IGNORECASE,
    )
    if forbidden_select_forms.search(query):
        raise SourceError("sql.txt contains an unsafe SELECT form")
    if "{{VN}}" in query:
        query = query.replace("{{VN}}", "?", 1)
    placeholder_count = query.count("?")
    if placeholder_count == 0:
        if not re.search(
            r"\bFROM\s+opitemrece\s+(AS\s+)?o\b", query, flags=re.IGNORECASE
        ):
            raise SourceError(
                "Remote SQL has no vn placeholder and opitemrece alias o was not found"
            )
        insertion_points = []
        for pattern in (r"\bGROUP\s+BY\b", r"\bORDER\s+BY\b", r"\bLIMIT\b"):
            matches = list(re.finditer(pattern, query, flags=re.IGNORECASE))
            if matches:
                insertion_points.append(matches[-1].start())
        position = min(insertion_points) if insertion_points else len(query)
        query = query[:position].rstrip() + "\nAND o.vn = ?\n" + query[position:]
        placeholder_count = 1
    if placeholder_count != 1:
        raise SourceError("SQL must contain exactly one vn placeholder (?, or {{VN}})")
    return query


def load_vn_query(path: Path) -> str:
    return prepare_vn_query(path.read_text(encoding="utf-8-sig"))


def prepare_vn_batch_query(raw_query: str, vn_count: int) -> str:
    """Build a PyMySQL query for one safely-bound VN batch.

    The remote query is still validated as one read-only SELECT with exactly
    one VN marker. For more than one VN we only expand a recognised equality
    or IN predicate and leave every value as a driver parameter.
    """
    if vn_count < 1:
        raise SourceError("VN batch must contain at least one value")

    query = prepare_vn_query(raw_query).replace("%", "%%")
    if vn_count == 1:
        return query.replace("?", "%s", 1)

    markers = ", ".join(["%s"] * vn_count)
    equality = re.compile(
        r"(?P<column>(?:`?[A-Za-z_][A-Za-z0-9_]*`?\.)?`?vn`?)\s*=\s*\?",
        flags=re.IGNORECASE,
    )
    query, replacements = equality.subn(
        lambda match: f"{match.group('column')} IN ({markers})",
        query,
        count=1,
    )
    if replacements == 0:
        in_marker = re.compile(r"\bIN\s*\(\s*\?\s*\)", flags=re.IGNORECASE)
        query, replacements = in_marker.subn(
            f"IN ({markers})",
            query,
            count=1,
        )
    if replacements != 1 or "?" in query:
        raise SourceError(
            "SQL VN placeholder must be used as 'vn = ?' or 'vn IN (?)' "
            "to query more than one VN"
        )
    return query


class MariaDBSource:
    def __init__(self, config: Config):
        self.config = config

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        try:
            import pymysql
        except ImportError as exc:  # pragma: no cover - depends on deployment
            raise SourceError(
                "PyMySQL is not installed; run pip install -r requirements.txt"
            ) from exc

        connection = pymysql.connect(
            host=self.config.db_host,
            port=self.config.db_port,
            user=self.config.db_user,
            password=self.config.db_password,
            database=self.config.db_name,
            connect_timeout=5,
            read_timeout=getattr(self.config, "db_read_timeout_seconds", 120),
            write_timeout=10,
            charset="tis620",
            # Decode result bytes ourselves with tolerant CP874/TIS-620 logic.
            # PyMySQL's strict Python tis-620 codec rejects dirty legacy bytes
            # such as 0xA0 and would otherwise abort the entire query batch.
            use_unicode=False,
            autocommit=False,
        )
        try:
            yield connection
        finally:
            try:
                connection.close()
            except Exception:
                # A broken MySQL protocol stream may also fail during close.
                # The original query error is the useful one for retry/reporting.
                pass

    @staticmethod
    def _fetch_all(cursor: Any) -> List[Dict[str, Any]]:
        rows = cursor.fetchall()
        return [normalize_row(_row_to_dict(cursor, row)) for row in rows]

    def _slave_health(self, cursor: Any) -> Optional[Dict[str, Any]]:
        try:
            cursor.execute("SHOW SLAVE STATUS")
            row = cursor.fetchone()
        except Exception as exc:
            if self.config.require_slave_health:
                raise SlaveUnhealthy(f"Cannot read slave status: {exc}") from exc
            return None

        if row is None:
            if self.config.require_slave_health:
                raise SlaveUnhealthy("SHOW SLAVE STATUS returned no row")
            return None

        status = normalize_row(_row_to_dict(cursor, row))
        io_running = str(status.get("Slave_IO_Running", "")).lower() == "yes"
        sql_running = str(status.get("Slave_SQL_Running", "")).lower() == "yes"
        io_error = str(status.get("Last_IO_Error") or "").strip()
        sql_error = str(status.get("Last_SQL_Error") or "").strip()
        lag_raw = status.get("Seconds_Behind_Master")
        lag = int(lag_raw) if lag_raw not in (None, "") else None

        if not io_running or not sql_running or io_error or sql_error:
            raise SlaveUnhealthy(
                "Slave replication is unhealthy: "
                f"io={io_running}, sql={sql_running}, "
                f"io_error={io_error!r}, sql_error={sql_error!r}"
            )
        if lag is None and self.config.require_slave_health:
            raise SlaveUnhealthy("Seconds_Behind_Master is NULL")
        if lag is not None and lag > self.config.max_replication_lag_seconds:
            raise SlaveUnhealthy(
                f"Replication lag {lag}s exceeds "
                f"{self.config.max_replication_lag_seconds}s"
            )

        return {
            "slave_io_running": io_running,
            "slave_sql_running": sql_running,
            "seconds_behind_master": lag,
        }

    def fetch_snapshot(self) -> Tuple[date, Dict[str, Dict[str, Any]], Optional[Dict[str, Any]]]:
        with self._connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute("START TRANSACTION READ ONLY")
                health = self._slave_health(cursor)
                cursor.execute("SELECT CURDATE()")
                server_date = cursor.fetchone()[0]
                start_date = server_date - timedelta(days=self.config.lookback_days)
                end_date = server_date + timedelta(days=1)

                by_visit_date = f"""
                    SELECT {OPITEMRECE_COLUMNS}
                    FROM opitemrece
                    WHERE vstdate >= %s AND vstdate < %s
                """
                cursor.execute(by_visit_date, (start_date, end_date))
                rows = self._fetch_all(cursor)

                by_rx_date = f"""
                    SELECT {OPITEMRECE_COLUMNS}
                    FROM opitemrece
                    WHERE rxdate >= %s AND rxdate < %s
                """
                cursor.execute(by_rx_date, (start_date, end_date))
                rows.extend(self._fetch_all(cursor))
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()

        snapshot = {str(row["hos_guid"]): row for row in rows}
        return server_date, snapshot, health

    def fetch_master_table(self, table: str) -> Dict[str, Dict[str, Any]]:
        """Read one approved HOSxP catalogue table, keyed by its icode."""
        if table not in {"drugitems", "s_drugitems"}:
            raise SourceError(f"Master table is not allowed: {table}")

        with self._connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute("START TRANSACTION READ ONLY")
                cursor.execute(f"SELECT * FROM `{table}` ORDER BY `icode`")
                rows = self._fetch_all(cursor)
                connection.commit()
            except Exception as exc:
                raise SourceError(
                    f"Unable to read HOSxP master table {table}: {exc}"
                ) from exc
            finally:
                try:
                    cursor.close()
                except Exception:
                    pass

        result: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            key = str(row.get("icode") or "").strip()
            if not key:
                raise SourceError(f"HOSxP master table {table} contains an empty icode")
            result[key] = row
        return result

    def run_query_for_vns(
        self, vns: Iterable[str], query: Optional[str] = None
    ) -> Dict[str, List[Dict[str, Any]]]:
        query = prepare_vn_query(query) if query is not None else load_vn_query(
            self.config.sql_file
        )
        unique_vns = sorted({str(vn) for vn in vns if vn})
        if not unique_vns:
            return {}

        results: Dict[str, List[Dict[str, Any]]] = {vn: [] for vn in unique_vns}
        batch_size = max(
            1,
            int(getattr(self.config, "db_query_vn_batch_size", 25)),
        )

        for offset in range(0, len(unique_vns), batch_size):
            batch = unique_vns[offset : offset + batch_size]
            rows = self._query_vn_batch_with_retry(batch, query)
            self._group_vn_rows(batch, rows, results)
        LOGGER.info(
            "Detail query completed: vns=%d batches=%d rows=%d batch_size=%d",
            len(unique_vns),
            (len(unique_vns) + batch_size - 1) // batch_size,
            sum(len(rows) for rows in results.values()),
            batch_size,
        )
        return results

    def _query_vn_batch_with_retry(
        self,
        unique_vns: List[str],
        query: str,
    ) -> List[Dict[str, Any]]:
        retries = max(0, int(getattr(self.config, "db_query_retries", 2)))
        for attempt in range(retries + 1):
            try:
                return self._query_vn_batch(unique_vns, query)
            except Exception as exc:
                if attempt >= retries:
                    raise SourceError(
                        "Detail query batch failed after "
                        f"{attempt + 1} attempt(s), vns={len(unique_vns)}: {exc}"
                    ) from exc
                LOGGER.warning(
                    "Detail query batch connection failed; retrying: "
                    "attempt=%d/%d vns=%d error=%s",
                    attempt + 1,
                    retries + 1,
                    len(unique_vns),
                    exc,
                )
                time.sleep(min(1.0, 0.2 * (attempt + 1)))
        raise AssertionError("unreachable")

    def _query_vn_batch(
        self,
        unique_vns: List[str],
        query: str,
    ) -> List[Dict[str, Any]]:
        driver_query = prepare_vn_batch_query(query, len(unique_vns))

        with self._connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute("START TRANSACTION READ ONLY")
                cursor.execute(driver_query, tuple(unique_vns))
                rows = self._fetch_all(cursor)
                connection.commit()
            except Exception:
                # This transaction is read-only. Avoid sending ROLLBACK over a
                # protocol stream that may already be out of sequence.
                raise
            finally:
                try:
                    cursor.close()
                except Exception:
                    pass
        return rows

    @staticmethod
    def _group_vn_rows(
        unique_vns: List[str],
        rows: List[Dict[str, Any]],
        results: Dict[str, List[Dict[str, Any]]],
    ) -> None:
        for row in rows:
            vn_key = next((key for key in row if key.casefold() == "vn"), None)
            if vn_key is None or row.get(vn_key) in (None, ""):
                if len(unique_vns) == 1:
                    results[unique_vns[0]].append(row)
                    continue
                raise SourceError(
                    "Detail SQL must return a vn column when querying multiple VNs"
                )
            row_vn = str(row[vn_key])
            if row_vn in results:
                results[row_vn].append(row)
