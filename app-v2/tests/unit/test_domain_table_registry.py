from quantinue.db.domain import _TABLES


def test_benchmark_ledger_is_reflected_before_repository_reads_it() -> None:
    # Given / When
    tables = frozenset(_TABLES)

    # Then
    assert "tb_benchmark_price" in tables
