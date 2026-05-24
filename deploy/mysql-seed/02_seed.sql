USE warehouse;

-- Generate 100k synthetic rows spread across 2026-01-01..2026-01-31.
-- Enough to exercise keyset pagination (>=10 batches at batch=10000).
DROP PROCEDURE IF EXISTS seed_events;
DELIMITER $$
CREATE PROCEDURE seed_events()
BEGIN
  DECLARE i INT DEFAULT 0;
  WHILE i < 100000 DO
    INSERT INTO events (id, occurred_at, category, user_id, payload) VALUES (
      i + 1,
      DATE_ADD('2026-01-01 00:00:00', INTERVAL FLOOR(RAND() * 60 * 60 * 24 * 31) SECOND),
      ELT(1 + FLOOR(RAND() * 4), 'view', 'click', 'purchase', 'signup'),
      1 + FLOOR(RAND() * 1000),
      JSON_OBJECT('source', ELT(1 + FLOOR(RAND() * 3), 'web', 'ios', 'android'),
                  'amount', ROUND(RAND() * 100, 2))
    );
    SET i = i + 1;
  END WHILE;
END$$
DELIMITER ;

CALL seed_events();
DROP PROCEDURE seed_events;
