//! Lenient SQLite timestamp decoding for INTEGER-millis columns.
//!
//! Some ops scripts historically wrote `datetime('now')` TEXT into columns that
//! the DAO reads as `Option<i64>`. rusqlite then fails with
//! `Invalid column type Text at index: N, name: created_at` and aborts app setup.
//! Decode both INTEGER and common TEXT forms so a bad row cannot brick startup.

use rusqlite::types::{FromSql, FromSqlResult, ValueRef};

/// Optional unix epoch milliseconds (or seconds coerced to ms).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct OptionalUnixMillis(pub Option<i64>);

impl FromSql for OptionalUnixMillis {
    fn column_result(value: ValueRef<'_>) -> FromSqlResult<Self> {
        Ok(Self(decode_optional_unix_millis(value)))
    }
}

/// Decode a timestamp cell. Unknown / unparsable TEXT becomes `None` (never an error).
pub fn decode_optional_unix_millis(value: ValueRef<'_>) -> Option<i64> {
    match value {
        ValueRef::Null => None,
        ValueRef::Integer(i) => Some(normalize_epoch_number(i)),
        ValueRef::Real(f) => Some(normalize_epoch_number(f as i64)),
        ValueRef::Text(bytes) => {
            let Ok(text) = std::str::from_utf8(bytes) else {
                return None;
            };
            parse_timestamp_text(text.trim())
        }
        ValueRef::Blob(_) => None,
    }
}

fn normalize_epoch_number(raw: i64) -> i64 {
    // Heuristic: values that look like seconds (before year ~2286 in ms) → ms.
    // 10_000_000_000 ≈ Sep 2001 in ms; seconds since 2001 are ~1e9.
    if (1_000_000_000..10_000_000_000).contains(&raw) {
        raw.saturating_mul(1000)
    } else {
        raw
    }
}

fn parse_timestamp_text(text: &str) -> Option<i64> {
    if text.is_empty() {
        return None;
    }
    if let Ok(n) = text.parse::<i64>() {
        return Some(normalize_epoch_number(n));
    }
    // SQLite datetime('now') / CURRENT_TIMESTAMP style.
    if let Ok(dt) = chrono::NaiveDateTime::parse_from_str(text, "%Y-%m-%d %H:%M:%S") {
        return Some(dt.and_utc().timestamp_millis());
    }
    if let Ok(dt) = chrono::NaiveDateTime::parse_from_str(text, "%Y-%m-%dT%H:%M:%S") {
        return Some(dt.and_utc().timestamp_millis());
    }
    if let Ok(dt) = chrono::DateTime::parse_from_rfc3339(text) {
        return Some(dt.timestamp_millis());
    }
    None
}

/// SQL that rewrites TEXT datetime/numeric cells into INTEGER millis for a column.
/// Safe to run repeatedly; no-ops when typeof is already integer/null.
pub fn normalize_text_millis_sql(table: &str, column: &str) -> String {
    // Only allow simple identifiers to keep this helper injection-safe.
    debug_assert!(
        table.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
            && column
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '_')
    );
    format!(
        "UPDATE {table}
         SET {column} = CASE
           WHEN typeof({column}) = 'text'
                AND {column} GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*'
             THEN CAST(strftime('%s', {column}) AS INTEGER) * 1000
           WHEN typeof({column}) = 'text'
                AND {column} GLOB '[0-9]*'
             THEN CASE
                    WHEN CAST({column} AS INTEGER) BETWEEN 1000000000 AND 9999999999
                      THEN CAST({column} AS INTEGER) * 1000
                    ELSE CAST({column} AS INTEGER)
                  END
           ELSE {column}
         END
         WHERE typeof({column}) = 'text'"
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::types::ValueRef;

    #[test]
    fn decodes_integer_millis() {
        assert_eq!(
            decode_optional_unix_millis(ValueRef::Integer(1_700_000_000_000)),
            Some(1_700_000_000_000)
        );
    }

    #[test]
    fn coerces_seconds_to_millis() {
        assert_eq!(
            decode_optional_unix_millis(ValueRef::Integer(1_700_000_000)),
            Some(1_700_000_000_000)
        );
    }

    #[test]
    fn decodes_sqlite_datetime_text() {
        let ms = decode_optional_unix_millis(ValueRef::Text(b"2026-07-25 12:25:03"));
        let expected = chrono::NaiveDateTime::parse_from_str("2026-07-25 12:25:03", "%Y-%m-%d %H:%M:%S")
            .unwrap()
            .and_utc()
            .timestamp_millis();
        assert_eq!(ms, Some(expected));
    }

    #[test]
    fn invalid_text_becomes_none_not_error() {
        assert_eq!(
            decode_optional_unix_millis(ValueRef::Text(b"not-a-date")),
            None
        );
        let parsed = OptionalUnixMillis::column_result(ValueRef::Text(b"not-a-date")).unwrap();
        assert_eq!(parsed.0, None);
    }
}
