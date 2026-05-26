from datetime import datetime

from app.extract import registry
from app.extract.paginator import _build_keyset_query, _build_offset_query


def test_keyset_first_batch_has_no_cursor() -> None:
    sql, params = _build_keyset_query(
        registry.EVENTS,
        {"from": datetime(2026, 1, 1), "to": datetime(2026, 1, 2)},
        cursor=None,
        batch_size=10,
    )
    assert "occurred_at" in sql
    assert "LIMIT 10" in sql
    assert "ORDER BY `occurred_at`, `id`" in sql
    assert ") > (" not in sql
    assert params == [datetime(2026, 1, 1), datetime(2026, 1, 2)]


def test_keyset_subsequent_batch_adds_predicate() -> None:
    sql, params = _build_keyset_query(
        registry.EVENTS,
        {"from": datetime(2026, 1, 1), "to": datetime(2026, 1, 2)},
        cursor=(datetime(2026, 1, 1, 12), 12345),
        batch_size=10,
    )
    assert "(`occurred_at`, `id`) > (%s, %s)" in sql
    assert params[-2:] == [datetime(2026, 1, 1, 12), 12345]


def test_keyset_optional_list_filter_becomes_in_clause() -> None:
    sql, params = _build_keyset_query(
        registry.EVENTS,
        {
            "from": datetime(2026, 1, 1),
            "to": datetime(2026, 1, 2),
            "category": ["view", "click"],
        },
        cursor=None,
        batch_size=5,
    )
    assert "`category` IN (%s, %s)" in sql
    assert "view" in params and "click" in params


def test_keyset_uses_no_offset() -> None:
    sql, _ = _build_keyset_query(
        registry.EVENTS,
        {"from": datetime(2026, 1, 1), "to": datetime(2026, 1, 2)},
        cursor=(datetime(2026, 1, 1, 12), 12345),
        batch_size=10,
    )
    assert "OFFSET" not in sql.upper()


def test_offset_first_batch() -> None:
    sql, params = _build_offset_query(
        registry.EVENTS,
        {"from": datetime(2026, 1, 1), "to": datetime(2026, 1, 2)},
        offset=0,
        batch_size=10,
    )
    assert "LIMIT 10 OFFSET 0" in sql
    assert "ORDER BY `occurred_at`, `id`" in sql
    # OFFSET path must NOT add a cursor predicate
    assert ") > (" not in sql
    assert params == [datetime(2026, 1, 1), datetime(2026, 1, 2)]


def test_offset_advances() -> None:
    sql, _ = _build_offset_query(
        registry.EVENTS, {}, offset=2500, batch_size=500,
    )
    assert "LIMIT 500 OFFSET 2500" in sql
