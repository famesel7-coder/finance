import argparse
import json
import os
import re
import sqlite3
import ssl
import subprocess
import sys
from collections import defaultdict
from contextlib import closing
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
from pydantic import SecretStr

from app.config import Settings
from app.tinvest import TInvestClient

SCHEMA = """
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    captured_at TEXT PRIMARY KEY,
    operations_from TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS portfolio_operations (
    account_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    event_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (account_id, operation_id)
);
CREATE INDEX IF NOT EXISTS portfolio_operations_event_at
ON portfolio_operations (event_at);
"""

CASH_FLOW_FIELDS = {
    "OPERATION_TYPE_DIVIDEND": "dividends",
    "OPERATION_TYPE_DIV_EXT": "dividends",
    "OPERATION_TYPE_DIVIDEND_TAX": "dividend_tax",
    "OPERATION_TYPE_DIVIDEND_TAX_PROGRESSIVE": "dividend_tax",
    "OPERATION_TYPE_BROKER_FEE": "broker_fees",
}


def sync_journal(
    client: TInvestClient, database: Path, since: datetime, until: datetime
) -> dict:
    accounts = []
    instruments = {}
    operations = []
    for account in client.list_accounts():
        if account.get("accessLevel") == "ACCOUNT_ACCESS_LEVEL_NO_ACCESS":
            continue
        account_id = account["id"]
        portfolio = client.get_portfolio(account_id)
        accounts.append({"account": account, "portfolio": portfolio})
        for position in portfolio.get("positions", []):
            if position.get("instrumentType") == "currency":
                continue
            uid = position.get("instrumentUid")
            identifier = uid or position.get("figi")
            if identifier and identifier not in instruments:
                id_type = "INSTRUMENT_ID_TYPE_UID" if uid else "INSTRUMENT_ID_TYPE_FIGI"
                try:
                    instruments[identifier] = client.get_instrument(identifier, id_type)
                except httpx.HTTPStatusError as error:
                    if error.response.status_code != 404:
                        raise
                    instruments[identifier] = {"unavailable": True}
        for event in client.iter_operations(account_id, since, until):
            event_at = datetime.fromisoformat(event["date"]).astimezone(UTC)
            operations.append(
                (
                    account_id, event["id"], event_at.isoformat(timespec="microseconds"),
                    json.dumps(event),
                )
            )

    database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.close(os.open(database, os.O_CREAT | os.O_WRONLY, 0o600))
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript(SCHEMA)
        with connection:
            connection.execute(
                """
                INSERT INTO portfolio_snapshots VALUES (?, ?, ?)
                ON CONFLICT (captured_at) DO UPDATE
                SET operations_from = excluded.operations_from, payload = excluded.payload
                """,
                (
                    until.isoformat(timespec="microseconds"),
                    since.isoformat(timespec="microseconds"),
                    json.dumps({"accounts": accounts, "instruments": instruments}),
                ),
            )
            connection.executemany(
                """
                INSERT INTO portfolio_operations VALUES (?, ?, ?, ?)
                ON CONFLICT (account_id, operation_id) DO UPDATE
                SET event_at = excluded.event_at, payload = excluded.payload
                """,
                operations,
            )
    return {
        "snapshot_at": until.isoformat(timespec="seconds"),
        "accounts": len(accounts),
        "instruments": len(instruments),
        "operations_received": len(operations),
    }


def summarize_journal(database: Path, since: datetime) -> dict:
    if not database.is_file():
        raise ValueError("Journal is missing. Run sync first.")

    totals = defaultdict(lambda: {field: Decimal(0) for field in CASH_FLOW_FIELDS.values()})
    with closing(sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)) as connection:
        latest = connection.execute(
            "SELECT captured_at, operations_from FROM portfolio_snapshots "
            "ORDER BY captured_at DESC LIMIT 1"
        ).fetchone()
        if latest is None:
            raise ValueError("Journal has no snapshots. Run sync first.")
        for (payload,) in connection.execute(
            "SELECT payload FROM portfolio_operations WHERE event_at >= ? AND event_at <= ?",
            (since.isoformat(timespec="microseconds"), latest[0]),
        ):
            event = json.loads(payload)
            field = CASH_FLOW_FIELDS.get(event.get("type"))
            payment = event.get("payment") or {}
            currency = payment.get("currency")
            if field and currency:
                value = Decimal(payment.get("units", 0)) + Decimal(payment.get("nano", 0)).scaleb(-9)
                totals[currency.upper()][field] += value

    for values in totals.values():
        values["dividends_after_recorded_tax"] = values["dividends"] + values["dividend_tax"]
    return {
        "period_from": since.isoformat(timespec="seconds"),
        "snapshot_at": latest[0],
        "last_sync_from": latest[1],
        "cash_flow_by_currency": totals,
    }


def read_token(service: str | None, account: str | None) -> SecretStr:
    if service:
        command = ["/usr/bin/security", "find-generic-password", "-s", service, "-w"]
        if account:
            command.extend(["-a", account])
        try:
            result = subprocess.run(command, capture_output=True, check=True, timeout=55)
        except (OSError, subprocess.SubprocessError):
            raise ValueError("Could not read the specified Keychain item") from None
        token = result.stdout.decode("utf-8").strip()
    else:
        token = os.environ.get("TINVEST_TOKEN", "").strip()
    if not token:
        raise ValueError("Provide TINVEST_TOKEN or a Keychain service")
    if not re.fullmatch(r"[A-Za-z0-9._~+/=\-]+", token):
        raise ValueError("The configured T-Invest token has an invalid format")
    return SecretStr(token)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync T-Invest portfolio data into a local journal.")
    parser.add_argument("command", choices=("sync", "summary"))
    parser.add_argument("--database", type=Path, default=Path("data/portfolio.sqlite3"))
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--keychain-service")
    parser.add_argument("--keychain-account")
    args = parser.parse_args()
    if args.days < 1:
        parser.error("--days must be positive")

    now = datetime.now(UTC)
    since = now - timedelta(days=args.days)
    try:
        if args.command == "summary":
            result = summarize_journal(args.database, since)
        else:
            settings = Settings(
                _env_file=None,
                tinvest_token=read_token(args.keychain_service, args.keychain_account),
                tinvest_sandbox=False,
                allow_live_trading=False,
            )
            verify = True
            if sys.platform == "darwin":
                import truststore

                verify = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            with httpx.Client(timeout=20, verify=verify) as transport:
                result = sync_journal(TInvestClient(settings, transport), args.database, since, now)
    except httpx.HTTPStatusError as error:
        parser.exit(1, f"T-Invest returned HTTP {error.response.status_code}.\n")
    except httpx.RequestError as error:
        parser.exit(1, f"T-Invest connection failed ({type(error).__name__}).\n")
    except (KeyError, TypeError) as error:
        parser.exit(1, f"Unexpected broker response ({type(error).__name__}).\n")
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"{error}\n")

    print(json.dumps(result, default=str, indent=2))


if __name__ == "__main__":
    main()
