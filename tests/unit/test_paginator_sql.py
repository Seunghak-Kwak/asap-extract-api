from datetime import datetime

from app.extract import registry
from app.extract.paginator import _build_query


def test_first_batch_has_no_cursor() -> None:
    sql, params = _build_query(
        registry.EVENTS,
        {"from": datetime(2026, 1, 1), "to": datetime(2026, 1, 2)},
        cursor=None,
        batch_size=10,
    )
    assert "occurred_at" in sql
    assert "LIMIT 10" in sql
    assert "ORDER BY `occurred_at`, `id`" in sql
    # no cursor predicate
    assert ") > (" not in sql
    assert params == [datetime(2026, 1, 1), datetime(2026, 1, 2)]


def test_subsequent_batch_adds_keyset_predicate() -> None:
    sql, params = _build_query(
        registry.EVENTS,
        {"from": datetime(2026, 1, 1), "to": datetime(2026, 1, 2)},
        cursor=(datetime(2026, 1, 1, 12), 12345),
        batch_size=10,
    )
    assert "(`occurred_at`, `id`) > (%s, %s)" in sql
    assert params[-2:] == [datetime(2026, 1, 1, 12), 12345]


def test_optional_list_filter_becomes_in_clause() -> None:
    sql, params = _build_query(
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


def test_no_offset_anywhere() -> None:
    sql, _ = _build_query(
        registry.EVENTS,
        {"from": datetime(2026, 1, 1), "to": datetime(2026, 1, 2)},
        cursor=(datetime(2026, 1, 1, 12), 12345),
        batch_size=10,
    )
    assert "OFFSET" not in sql.upper()
