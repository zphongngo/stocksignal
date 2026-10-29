import csv
from contextlib import contextmanager
import io
import math
import sqlite3
import threading
from datetime import datetime, time
from pathlib import Path


CSV_COLUMNS = {
    "ticker": "ticker",
    "dtyyyymmdd": "trade_date",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "volume": "volume",
}
IMPORT_BATCH_SIZE = 5000
DATABASE_TIMEOUT_SECONDS = 60
DAILY_CLOSE_TIME = time(15, 30)
_SCHEMA_LOCK = threading.Lock()
_INITIALIZED_DATABASES = set()


@contextmanager
def _database_connection(database_path):
    connection = sqlite3.connect(
        database_path,
        timeout=DATABASE_TIMEOUT_SECONDS,
    )
    try:
        connection.execute(
            f"PRAGMA busy_timeout = {DATABASE_TIMEOUT_SECONDS * 1000}"
        )
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_database(database_path):
    database_path = Path(database_path).resolve()
    database_key = str(database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with _SCHEMA_LOCK:
        if database_key in _INITIALIZED_DATABASES:
            return
        with _database_connection(database_path) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_prices (
                    ticker TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL NOT NULL,
                    volume INTEGER,
                    source TEXT NOT NULL,
                    PRIMARY KEY (ticker, trade_date)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_daily_prices_date "
                "ON daily_prices (trade_date DESC, ticker)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_signals (
                    ticker TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    signal TEXT NOT NULL CHECK (signal IN ('BUY', 'SELL', 'HOLD')),
                    ma20 REAL NOT NULL,
                    ma50 REAL NOT NULL,
                    rsi14 REAL NOT NULL,
                    ma_short_period INTEGER NOT NULL DEFAULT 20,
                    ma_long_period INTEGER NOT NULL DEFAULT 50,
                    PRIMARY KEY (ticker, trade_date),
                    FOREIGN KEY (ticker, trade_date)
                        REFERENCES daily_prices (ticker, trade_date)
                )
                """
            )
            signal_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(daily_signals)")
            }
            if "ma_short_period" not in signal_columns:
                connection.execute(
                    "ALTER TABLE daily_signals ADD COLUMN "
                    "ma_short_period INTEGER NOT NULL DEFAULT 20"
                )
            if "ma_long_period" not in signal_columns:
                connection.execute(
                    "ALTER TABLE daily_signals ADD COLUMN "
                    "ma_long_period INTEGER NOT NULL DEFAULT 50"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_daily_signals_date "
                "ON daily_signals (trade_date DESC, signal, ticker)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_captures (
                    trade_date TEXT PRIMARY KEY,
                    captured_at TEXT NOT NULL,
                    quote_count INTEGER NOT NULL
                )
                """
            )
        _INITIALIZED_DATABASES.add(database_key)


def _normalized_headers(fieldnames):
    return {
        str(name or "").strip().strip("<>").lower(): name
        for name in fieldnames or []
    }


def _parse_csv_row(row, headers, source_name, row_number):
    values = {key: row.get(headers[key]) for key in CSV_COLUMNS}
    ticker = str(values["ticker"] or "").strip().upper()
    if not ticker:
        return None
    try:
        trade_date = datetime.strptime(
            str(values["dtyyyymmdd"]).strip(), "%Y%m%d"
        ).date().isoformat()
        prices = [
            float(str(values[column]).strip())
            for column in ("open", "high", "low", "close")
        ]
        volume = int(float(str(values["volume"]).strip()))
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(
            f"{source_name}: invalid price/date data at row {row_number}."
        ) from error
    if not all(math.isfinite(value) for value in prices) or volume < 0:
        raise ValueError(f"{source_name}: invalid values at row {row_number}.")
    return ticker, trade_date, *prices, volume, source_name


def import_csv_files(database_path, files, progress_callback=None):
    initialize_database(database_path)
    files = list(files)
    if not files:
        raise ValueError("Select at least one CSV file.")

    total_rows = 0
    for file_index, uploaded_file in enumerate(files, start=1):
        uploaded_file.seek(0)
        text_file = io.TextIOWrapper(
            uploaded_file, encoding="utf-8-sig", newline=""
        )
        try:
            record_count = sum(1 for _ in csv.reader(text_file))
        finally:
            text_file.detach()
        total_rows += max(0, record_count - 1)
        if progress_callback:
            progress_callback(
                0.1 * file_index / len(files),
                f"Scanning files: {file_index}/{len(files)}",
            )
    if total_rows == 0:
        raise ValueError("Selected CSV files contain no data rows.")

    imported_rows = 0
    skipped_rows = 0
    processed_rows = 0
    with _database_connection(database_path) as connection:
        for uploaded_file in files:
            source_name = getattr(uploaded_file, "name", "uploaded.csv")
            uploaded_file.seek(0)
            text_file = io.TextIOWrapper(
                uploaded_file, encoding="utf-8-sig", newline=""
            )
            try:
                reader = csv.DictReader(text_file)
                headers = _normalized_headers(reader.fieldnames)
                missing = set(CSV_COLUMNS) - headers.keys()
                if missing:
                    raise ValueError(
                        f"{source_name}: missing columns: {', '.join(sorted(missing))}."
                    )

                batch = []
                file_rows = 0
                for row_number, row in enumerate(reader, start=2):
                    processed_rows += 1
                    parsed_row = _parse_csv_row(row, headers, source_name, row_number)
                    if parsed_row is None:
                        skipped_rows += 1
                        continue
                    batch.append(parsed_row)
                    if len(batch) >= IMPORT_BATCH_SIZE:
                        connection.executemany(
                            """
                            INSERT INTO daily_prices (
                                ticker, trade_date, open, high, low, close, volume, source
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT (ticker, trade_date) DO UPDATE SET
                                open = excluded.open,
                                high = excluded.high,
                                low = excluded.low,
                                close = excluded.close,
                                volume = excluded.volume,
                                source = excluded.source
                            """,
                            batch,
                        )
                        file_rows += len(batch)
                        batch.clear()
                        connection.commit()
                        if progress_callback:
                            progress_callback(
                                0.1 + 0.9 * processed_rows / total_rows,
                                f"Importing {source_name}: "
                                f"{processed_rows:,}/{total_rows:,} rows",
                            )
                if batch:
                    connection.executemany(
                        """
                        INSERT INTO daily_prices (
                            ticker, trade_date, open, high, low, close, volume, source
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (ticker, trade_date) DO UPDATE SET
                            open = excluded.open,
                            high = excluded.high,
                            low = excluded.low,
                            close = excluded.close,
                            volume = excluded.volume,
                            source = excluded.source
                        """,
                        batch,
                    )
                    file_rows += len(batch)
                    connection.commit()
                if not file_rows:
                    raise ValueError(f"{source_name}: file contains no price rows.")
                imported_rows += file_rows
                if progress_callback:
                    progress_callback(
                        0.1 + 0.9 * processed_rows / total_rows,
                        f"Imported {source_name}: "
                        f"{processed_rows:,}/{total_rows:,} rows",
                    )
            finally:
                text_file.detach()
    if progress_callback:
        progress_callback(1.0, "Import complete")
    return imported_rows, skipped_rows


def _calculate_indicators(
    closes,
    ma_short_period=20,
    ma_long_period=50,
    rsi_level1=50.0,
    rsi_level2=70.0,
):
    if not 2 <= ma_short_period < ma_long_period <= 500:
        raise ValueError("MA periods must satisfy 2 <= short < long <= 500.")
    if not 0 <= rsi_level1 < rsi_level2 <= 100:
        raise ValueError("RSI levels must satisfy 0 <= level 1 < level 2 <= 100.")
    if len(closes) < ma_long_period + 1:
        return None

    previous_closes = closes[-ma_long_period - 1:-1]
    ma_short = math.fsum(closes[-ma_short_period:]) / ma_short_period
    ma_long = math.fsum(closes[-ma_long_period:]) / ma_long_period
    previous_ma_short = math.fsum(previous_closes[-ma_short_period:]) / ma_short_period
    previous_ma_long = math.fsum(previous_closes) / ma_long_period

    changes = [
        current - previous
        for previous, current in zip(closes[-15:-1], closes[-14:])
    ]
    average_gain = math.fsum(max(change, 0) for change in changes) / 14
    average_loss = math.fsum(max(-change, 0) for change in changes) / 14
    if average_loss == 0:
        rsi14 = 100.0 if average_gain else 50.0
    else:
        rsi14 = 100 - 100 / (1 + average_gain / average_loss)

    crossed_up = previous_ma_short <= previous_ma_long and ma_short > ma_long
    crossed_down = previous_ma_short >= previous_ma_long and ma_short < ma_long
    if crossed_up and rsi_level1 <= rsi14 < rsi_level2:
        signal = "BUY"
    elif crossed_down and 100 - rsi_level2 <= rsi14 <= rsi_level1:
        signal = "SELL"
    else:
        signal = "HOLD"
    return signal, ma_short, ma_long, rsi14


def calculate_intraday_signals(
    database_path,
    quotes,
    tickers,
    trade_date,
    ma_short_period=20,
    ma_long_period=50,
    rsi_level1=50.0,
    rsi_level2=70.0,
):
    initialize_database(database_path)
    trade_date = trade_date.isoformat() if hasattr(trade_date, "isoformat") else str(trade_date)
    signals = []
    with _database_connection(database_path) as connection:
        for ticker in sorted({str(item).strip().upper() for item in tickers if str(item).strip()}):
            quote = quotes.get(ticker)
            if not quote:
                continue
            try:
                live_close = float(quote["price"])
            except (KeyError, TypeError, ValueError):
                continue
            if not math.isfinite(live_close) or live_close <= 0:
                continue

            history_rows_needed = max(ma_long_period, 14)
            rows = connection.execute(
                """
                SELECT close FROM daily_prices
                WHERE ticker = ? AND trade_date < ?
                ORDER BY trade_date DESC LIMIT ?
                """,
                (ticker, trade_date, history_rows_needed),
            ).fetchall()
            if len(rows) < history_rows_needed:
                continue
            closes = [row[0] for row in reversed(rows)] + [live_close]
            indicators = _calculate_indicators(
                closes,
                ma_short_period,
                ma_long_period,
                rsi_level1,
                rsi_level2,
            )
            if indicators is None:
                continue
            signal, ma20, ma50, rsi14 = indicators
            if signal not in ("BUY", "SELL"):
                continue
            signals.append({
                "ticker": ticker,
                "trade_date": trade_date,
                "signal": signal,
                "price": live_close,
                "ma20": ma20,
                "ma50": ma50,
                "ma_short_period": ma_short_period,
                "ma_long_period": ma_long_period,
                "rsi14": rsi14,
            })
    return signals


def record_daily_close(
    database_path,
    quotes,
    captured_at,
    session,
    ma_short_period=20,
    ma_long_period=50,
    rsi_level1=50.0,
    rsi_level2=70.0,
):
    if (
        captured_at.weekday() >= 5
        or captured_at.time() < DAILY_CLOSE_TIME
        or session != "ENDED"
        or not quotes
    ):
        return False, []

    initialize_database(database_path)
    trade_date = captured_at.date().isoformat()
    new_signals = []
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        previous_capture = connection.execute(
            "SELECT captured_at FROM daily_captures WHERE trade_date = ?",
            (trade_date,),
        ).fetchone()
        if previous_capture:
            previous_captured_at = datetime.fromisoformat(previous_capture[0])
            if previous_captured_at.time() >= DAILY_CLOSE_TIME:
                connection.rollback()
                return False, []

        valid_quotes = []
        for ticker, quote in quotes.items():
            close = float(quote["price"])
            volume = int(quote.get("volume", 0) or 0)
            if not math.isfinite(close) or close <= 0 or volume < 0:
                continue
            valid_quotes.append((ticker, close, volume))

        if not valid_quotes:
            connection.rollback()
            return False, []

        connection.executemany(
            """
            INSERT INTO daily_prices (
                ticker, trade_date, open, high, low, close, volume, source
            ) VALUES (?, ?, NULL, NULL, NULL, ?, ?, 'vietcap_close')
            ON CONFLICT (ticker, trade_date) DO UPDATE SET
                close = excluded.close,
                volume = excluded.volume,
                source = excluded.source
            """,
            [
                (ticker, trade_date, close, volume)
                for ticker, close, volume in valid_quotes
            ],
        )

        for ticker, _, _ in valid_quotes:
            rows = connection.execute(
                """
                SELECT trade_date, close FROM daily_prices
                WHERE ticker = ? AND trade_date <= ?
                ORDER BY trade_date DESC LIMIT ?
                """,
                (ticker, trade_date, max(ma_long_period + 1, 15)),
            ).fetchall()
            rows.reverse()
            indicators = _calculate_indicators(
                [row[1] for row in rows],
                ma_short_period,
                ma_long_period,
                rsi_level1,
                rsi_level2,
            )
            if indicators is None:
                continue
            signal, ma20, ma50, rsi14 = indicators
            connection.execute(
                """
                INSERT INTO daily_signals (
                    ticker, trade_date, signal, ma20, ma50, rsi14,
                    ma_short_period, ma_long_period
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (ticker, trade_date) DO UPDATE SET
                    signal = excluded.signal,
                    ma20 = excluded.ma20,
                    ma50 = excluded.ma50,
                    rsi14 = excluded.rsi14,
                    ma_short_period = excluded.ma_short_period,
                    ma_long_period = excluded.ma_long_period
                """,
                (
                    ticker,
                    trade_date,
                    signal,
                    ma20,
                    ma50,
                    rsi14,
                    ma_short_period,
                    ma_long_period,
                ),
            )
            if signal != "HOLD":
                new_signals.append({
                    "ticker": ticker,
                    "trade_date": trade_date,
                    "signal": signal,
                    "ma20": ma20,
                    "ma50": ma50,
                    "ma_short_period": ma_short_period,
                    "ma_long_period": ma_long_period,
                    "rsi14": rsi14,
                })

        connection.execute(
            """
            INSERT INTO daily_captures (trade_date, captured_at, quote_count)
            VALUES (?, ?, ?)
            ON CONFLICT (trade_date) DO UPDATE SET
                captured_at = excluded.captured_at,
                quote_count = excluded.quote_count
            """,
            (trade_date, captured_at.isoformat(timespec="seconds"), len(valid_quotes)),
        )
        connection.commit()
        return True, new_signals
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_history_summary(database_path):
    initialize_database(database_path)
    with _database_connection(database_path) as connection:
        row_count, latest_date = connection.execute(
            "SELECT COUNT(*), MAX(trade_date) FROM daily_prices"
        ).fetchone()
        return row_count, latest_date


def has_final_daily_close(database_path, trade_date):
    initialize_database(database_path)
    trade_date = (
        trade_date.isoformat() if hasattr(trade_date, "isoformat") else str(trade_date)
    )
    with _database_connection(database_path) as connection:
        row = connection.execute(
            "SELECT captured_at FROM daily_captures WHERE trade_date = ?",
            (trade_date,),
        ).fetchone()
    if not row:
        return False
    return datetime.fromisoformat(row[0]).time() >= DAILY_CLOSE_TIME


def get_latest_daily_quotes(database_path):
    """Return the latest stored close per ticker in the live-board quote shape."""
    initialize_database(database_path)
    with _database_connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT latest.ticker, latest.close, latest.volume, previous.close
            FROM daily_prices AS latest
            JOIN (
                SELECT ticker, MAX(trade_date) AS trade_date
                FROM daily_prices
                GROUP BY ticker
            ) AS latest_date
              ON latest.ticker = latest_date.ticker
             AND latest.trade_date = latest_date.trade_date
            LEFT JOIN daily_prices AS previous
              ON previous.ticker = latest.ticker
             AND previous.trade_date = (
                 SELECT MAX(prior.trade_date)
                 FROM daily_prices AS prior
                 WHERE prior.ticker = latest.ticker
                   AND prior.trade_date < latest.trade_date
             )
            """
        ).fetchall()

    quotes = {}
    for ticker, close, volume, previous_close in rows:
        close = float(close)
        if not math.isfinite(close) or close <= 0:
            continue
        pct = (
            round((close - previous_close) / previous_close * 100, 2)
            if previous_close is not None and previous_close > 0
            else 0.0
        )
        quotes[ticker] = {
            "price": close,
            "pct": pct,
            "vol": (volume or 0) / 1000,
            "volume": volume or 0,
            "cls": "up" if pct > 0 else "down" if pct < 0 else "ref",
        }
    return quotes


def get_average_volumes(database_path, tickers, as_of_date, lookback_sessions):
    initialize_database(database_path)
    ticker_list = sorted({str(ticker).strip().upper() for ticker in tickers if str(ticker).strip()})
    if not ticker_list:
        return {}
    trade_date = as_of_date.isoformat() if hasattr(as_of_date, "isoformat") else str(as_of_date)
    placeholders = ", ".join("?" for _ in ticker_list)
    with _database_connection(database_path) as connection:
        rows = connection.execute(
            f"""
            SELECT ticker, AVG(volume)
            FROM (
                SELECT ticker, volume,
                       ROW_NUMBER() OVER (
                           PARTITION BY ticker ORDER BY trade_date DESC
                       ) AS session_number
                FROM daily_prices
                WHERE ticker IN ({placeholders})
                  AND trade_date < ?
                  AND volume IS NOT NULL
            )
            WHERE session_number <= ?
            GROUP BY ticker
            """,
            (*ticker_list, trade_date, lookback_sessions),
        ).fetchall()
    return {ticker: average for ticker, average in rows}


def get_recent_signals(database_path, limit=100):
    initialize_database(database_path)
    with _database_connection(database_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
                 SELECT ticker, trade_date, signal, ma20, ma50, rsi14,
                     ma_short_period, ma_long_period
            FROM daily_signals
            WHERE signal IN ('BUY', 'SELL')
            ORDER BY trade_date DESC, ticker
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_latest_signal_snapshot(database_path, limit=20, tickers=None):
    initialize_database(database_path)
    ticker_filter = None
    if tickers is not None:
        ticker_filter = sorted({str(ticker).strip().upper() for ticker in tickers if str(ticker).strip()})
    with _database_connection(database_path) as connection:
        connection.row_factory = sqlite3.Row
        latest_date = connection.execute(
            "SELECT MAX(trade_date) FROM daily_signals"
        ).fetchone()[0]
        if latest_date is None:
            return None, []
        query = (
            "SELECT ticker, trade_date, signal, ma20, ma50, rsi14, "
            "ma_short_period, ma_long_period "
            "FROM daily_signals "
            "WHERE trade_date = ? AND signal IN ('BUY', 'SELL')"
        )
        parameters = [latest_date]
        if ticker_filter is not None:
            if not ticker_filter:
                return latest_date, []
            placeholders = ", ".join("?" for _ in ticker_filter)
            query += f" AND ticker IN ({placeholders})"
            parameters.extend(ticker_filter)
        query += " ORDER BY ticker LIMIT ?"
        parameters.append(limit)
        rows = connection.execute(query, parameters).fetchall()
    return latest_date, [dict(row) for row in rows]