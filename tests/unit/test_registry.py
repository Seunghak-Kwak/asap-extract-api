import pytest

from app.extract import registry


def test_unknown_dataset() -> None:
    with pytest.raises(registry.ExtractValidationError):
        registry.get("not_a_dataset")


def test_missing_required_filter() -> None:
    ds = registry.EVENTS
    with pytest.raises(registry.ExtractValidationError) as ei:
        registry.validate_filters(ds, {"from": "2026-01-01T00:00:00"})
    assert "to" in str(ei.value)


def test_unknown_filter() -> None:
    with pytest.raises(registry.ExtractValidationError) as ei:
        registry.validate_filters(
            registry.EVENTS,
            {"from": "2026-01-01T00:00:00", "to": "2026-01-02T00:00:00", "evil": 1},
        )
    assert "evil" in str(ei.value)


def test_inverted_range() -> None:
    with pytest.raises(registry.ExtractValidationError):
        registry.validate_filters(
            registry.EVENTS,
            {"from": "2026-01-02T00:00:00", "to": "2026-01-01T00:00:00"},
        )


def test_happy_path_parses_datetimes_and_lists() -> None:
    out = registry.validate_filters(
        registry.EVENTS,
        {
            "from": "2026-01-01T00:00:00Z",
            "to": "2026-01-02T00:00:00Z",
            "category": ["view", "click"],
        },
    )
    assert out["from"].year == 2026
    assert out["category"] == ["view", "click"]


def test_list_filter_must_be_nonempty_list() -> None:
    with pytest.raises(registry.ExtractValidationError):
        registry.validate_filters(
            registry.EVENTS,
            {
                "from": "2026-01-01T00:00:00",
                "to": "2026-01-02T00:00:00",
                "category": [],
            },
        )
