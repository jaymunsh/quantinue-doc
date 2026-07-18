"""PostgreSQL schema contract assertions."""

from .schema_sql_contract import Catalog
from .schema_sql_expectations import (
    CHECKS,
    FK,
    ORDER_LEG_COLUMNS,
    PK,
    PROVENANCE_COLUMNS,
    TABLES,
    UNIQUE,
)


def test_catalog_matches_complete_contract(postgres_catalog: Catalog) -> None:
    # Given/When: real catalog identities and relationships are loaded
    actual = postgres_catalog
    # Then: exact sets catch both omissions and extras
    assert actual.tables == TABLES
    assert actual.pk == PK
    assert {table: actual.unique.get(table, set()) for table in TABLES} == {
        table: UNIQUE.get(table, set()) for table in TABLES
    }
    assert {table: actual.fk.get(table, set()) for table in TABLES} == {
        table: FK.get(table, set()) for table in TABLES
    }
    assert {table: set(actual.checks.get(table, {})) for table in TABLES} == {
        table: set(CHECKS.get(table, {})) for table in TABLES
    }


def test_catalog_check_definitions_match_all_semantics(postgres_catalog: Catalog) -> None:
    # Given/When/Then: every expected CHECK has its complete declared semantics
    for table, expected_checks in CHECKS.items():
        for columns, fragments in expected_checks.items():
            definition = postgres_catalog.checks[table][columns]
            assert all(fragment in definition for fragment in fragments), (
                table,
                columns,
                definition,
            )


def test_order_submission_columns_support_pre_submit_token_claim(
    postgres_catalog: Catalog,
) -> None:
    # Given/When: reservation column/nullability is read from PostgreSQL
    # Then: ownership exists before nullable completion linkage
    assert postgres_catalog.submission_columns == {
        ("submission_id", "NO"),
        ("client_order_id", "NO"),
        ("state", "NO"),
        ("owner_token", "NO"),
        ("claimed_at", "NO"),
        ("stale_after", "NO"),
        ("run_id", "YES"),
        ("order_id", "YES"),
        ("broker_order_id", "YES"),
        ("result_payload", "YES"),
        ("last_error", "YES"),
        ("created_at", "NO"),
        ("updated_at", "NO"),
    }


def test_order_catalog_represents_parent_and_child_bracket_ids(
    postgres_catalog: Catalog,
) -> None:
    assert postgres_catalog.order_columns >= ORDER_LEG_COLUMNS


def test_catalog_requires_auditable_provenance_columns(postgres_catalog: Catalog) -> None:
    """Every persisted evidence boundary exposes the required lineage fields."""
    # Given / When
    actual = postgres_catalog.provenance_columns

    # Then
    for table, expected_columns in PROVENANCE_COLUMNS.items():
        assert expected_columns <= actual.get(table, set())
