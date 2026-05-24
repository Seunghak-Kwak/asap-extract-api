USE warehouse;

CREATE TABLE IF NOT EXISTS events (
  id           BIGINT       NOT NULL,
  occurred_at  DATETIME(3)  NOT NULL,
  category     VARCHAR(64)  NOT NULL,
  user_id      BIGINT       NOT NULL,
  payload      JSON         NOT NULL,
  PRIMARY KEY (id),
  KEY ix_events_keyset (occurred_at, id),
  KEY ix_events_category (category),
  KEY ix_events_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- read-only user already created via MYSQL_USER; just ensure SELECT-only on warehouse.
GRANT SELECT ON warehouse.* TO 'extract_ro'@'%';
FLUSH PRIVILEGES;
