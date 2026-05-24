from prometheus_client import Counter, Histogram

extracts_started = Counter(
    "extracts_started_total", "Extract jobs started", ["dataset"]
)
extracts_finished = Counter(
    "extracts_finished_total", "Extract jobs finished", ["dataset", "status"]
)
extract_rows = Counter(
    "extract_rows_total", "Total rows written across extracts", ["dataset"]
)
extract_duration = Histogram(
    "extract_duration_seconds",
    "Extract wall-clock duration",
    ["dataset"],
    buckets=(1, 5, 15, 60, 300, 900, 1800, 3600),
)
