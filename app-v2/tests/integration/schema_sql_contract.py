"""Exact real-PostgreSQL catalog contract for all canonical tables."""

from __future__ import annotations

import shutil
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, assert_never
from uuid import uuid4

import pytest
from pydantic import TypeAdapter

from .schema_sql_expectations import SCHEMA, TABLES

if TYPE_CHECKING:
    from collections.abc import Iterator

ConstraintKind = Literal["p", "u", "f", "c"]


@dataclass(frozen=True, slots=True)
class Catalog:
    tables: set[str]
    pk: dict[str, tuple[str, ...]]
    unique: dict[str, set[tuple[str, ...]]]
    fk: dict[str, set[tuple[tuple[str, ...], str, tuple[str, ...]]]]
    checks: dict[str, dict[tuple[str, ...], str]]
    submission_columns: set[tuple[str, str]]
    order_columns: set[str]
    provenance_columns: dict[str, set[tuple[str, str]]]


def _run(docker: str, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed executable and argument vector
        [docker, *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def _provenance_column_catalog(docker: str, name: str) -> dict[str, set[tuple[str, str]]]:
    """Load explicit provenance-column nullability from the disposable catalog."""
    query = (
        "SELECT table_name,column_name,is_nullable "
        "FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name IN "
        "('tb_disclosure','tb_disclosure_signal','tb_news','tb_news_signal',"
        "'tb_review_price_snapshots')"
    )
    rows = _run(
        docker,
        [
            "exec",
            name,
            "psql",
            "-U",
            "postgres",
            "-d",
            "contracts",
            "-At",
            "-F",
            "|",
            "-c",
            query,
        ],
    ).stdout.splitlines()
    columns: defaultdict[str, set[tuple[str, str]]] = defaultdict(set)
    for row in rows:
        table, column, nullable = row.split("|", maxsplit=2)
        columns[table].add((column, nullable))
    return dict(columns)


@pytest.fixture(scope="module")
def postgres_catalog() -> Iterator[Catalog]:
    """Load schema into isolated PostgreSQL with no published host port."""
    docker = shutil.which("docker")
    if docker is None:
        pytest.fail("Docker is required for PostgreSQL schema integration tests")
    name = f"quantinue-schema-{uuid4().hex}"
    _ = _run(
        docker,
        [
            "run",
            "--rm",
            "-d",
            "--name",
            name,
            "-e",
            "POSTGRES_PASSWORD=test-only",
            "-e",
            "POSTGRES_DB=contracts",
            "-v",
            f"{SCHEMA}:/docker-entrypoint-initdb.d/001.sql:ro",
            "postgres:16-alpine",
        ],
    )
    try:
        table_query = (
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' ORDER BY 1"
        )
        tables: set[str] = set()
        for _attempt in range(60):
            try:
                result = _run(
                    docker,
                    [
                        "exec",
                        name,
                        "psql",
                        "-U",
                        "postgres",
                        "-d",
                        "contracts",
                        "-Atc",
                        table_query,
                    ],
                )
            except subprocess.CalledProcessError:
                time.sleep(0.05)
                continue
            tables = set(result.stdout.splitlines())
            if tables == TABLES:
                break
            time.sleep(0.05)
        query = """
            SELECT c.relname, x.contype,
              coalesce((SELECT string_agg(a.attname,',' ORDER BY k.ord)
                FROM unnest(x.conkey) WITH ORDINALITY k(attnum,ord)
                JOIN pg_attribute a ON a.attrelid=x.conrelid AND a.attnum=k.attnum),''),
              coalesce(tc.relname,''),
              coalesce((SELECT string_agg(a.attname,',' ORDER BY k.ord)
                FROM unnest(x.confkey) WITH ORDINALITY k(attnum,ord)
                JOIN pg_attribute a ON a.attrelid=x.confrelid AND a.attnum=k.attnum),''),
              pg_get_constraintdef(x.oid)
            FROM pg_constraint x JOIN pg_class c ON c.oid=x.conrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            LEFT JOIN pg_class tc ON tc.oid=x.confrelid
            WHERE n.nspname='public' ORDER BY 1,2,3
        """
        rows = _run(
            docker,
            [
                "exec",
                name,
                "psql",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "postgres",
                "-d",
                "contracts",
                "-At",
                "-F",
                "|",
                "-c",
                query,
            ],
        ).stdout.splitlines()
        pk: dict[str, tuple[str, ...]] = {}
        unique: defaultdict[str, set[tuple[str, ...]]] = defaultdict(set)
        fk: defaultdict[str, set[tuple[tuple[str, ...], str, tuple[str, ...]]]] = defaultdict(set)
        checks: defaultdict[str, dict[tuple[str, ...], str]] = defaultdict(dict)
        kind_adapter: TypeAdapter[ConstraintKind] = TypeAdapter(ConstraintKind)
        for row in rows:
            table, raw_kind, source, target, target_columns, definition = row.split("|", maxsplit=5)
            kind = kind_adapter.validate_python(raw_kind)
            source_tuple = tuple(source.split(","))
            match kind:
                case "p":
                    pk[table] = source_tuple
                case "u":
                    unique[table].add(source_tuple)
                case "f":
                    fk[table].add((source_tuple, target, tuple(target_columns.split(","))))
                case "c":
                    checks[table][source_tuple] = definition.lower()
                case unreachable:
                    assert_never(unreachable)
        column_query = (
            "SELECT column_name,is_nullable FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='order_submissions' "
            "ORDER BY ordinal_position"
        )
        column_rows = _run(
            docker,
            [
                "exec",
                name,
                "psql",
                "-U",
                "postgres",
                "-d",
                "contracts",
                "-At",
                "-F",
                "|",
                "-c",
                column_query,
            ],
        ).stdout.splitlines()
        submission_columns = {
            (column, nullable)
            for column, nullable in (row.split("|", maxsplit=1) for row in column_rows)
        }
        order_columns = set(
            _run(
                docker,
                [
                    "exec",
                    name,
                    "psql",
                    "-U",
                    "postgres",
                    "-d",
                    "contracts",
                    "-At",
                    "-c",
                    (
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name='tb_order'"
                    ),
                ],
            ).stdout.splitlines()
        )
        provenance_columns = _provenance_column_catalog(docker, name)
        yield Catalog(
            tables,
            pk,
            dict(unique),
            dict(fk),
            dict(checks),
            submission_columns,
            order_columns,
            provenance_columns,
        )
    finally:
        _ = subprocess.run(  # noqa: S603 - cleanup only the UUID task container
            [docker, "rm", "-f", name],
            check=False,
            capture_output=True,
            text=True,
        )
