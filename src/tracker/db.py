# src/tracker/db.py
"""SQLite database operations for PropertyTracker."""

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional


class Database:
    """SQLite database wrapper with schema management."""

    def __init__(self, db_path: str = "data/tracker.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensure connection is closed."""
        self.close()
        return False

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def query(self, sql: str, params: tuple = ()) -> List[dict]:
        """Execute query and return results as list of dicts."""
        conn = self._get_conn()
        cursor = conn.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]

    def execute(self, sql: str, params: tuple = ()) -> int:
        """Execute SQL and return rowcount."""
        conn = self._get_conn()
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor.rowcount

    def executemany(self, sql: str, params_list: List[tuple]) -> int:
        """Execute SQL with many parameter sets."""
        conn = self._get_conn()
        cursor = conn.executemany(sql, params_list)
        conn.commit()
        return cursor.rowcount

    def list_tables(self) -> List[str]:
        """Return list of table names."""
        rows = self.query(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        return [r['name'] for r in rows]

    def init_schema(self):
        """Create all tables if they don't exist."""
        conn = self._get_conn()

        # 1. raw_sales - Core sales data from NSW Valuer General
        conn.execute("""
            CREATE TABLE IF NOT EXISTS raw_sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dealing_number TEXT NOT NULL,
                property_id TEXT DEFAULT '',
                unit_number TEXT,
                house_number TEXT,
                street_name TEXT NOT NULL,
                suburb TEXT NOT NULL,
                postcode TEXT NOT NULL,
                area_sqm REAL,
                zone_code TEXT,
                nature_of_property TEXT,
                strata_lot_number TEXT,
                contract_date DATE NOT NULL,
                settlement_date DATE,
                purchase_price INTEGER NOT NULL,
                property_type TEXT CHECK(property_type IN ('house', 'unit', 'land', 'other')),
                district_code INTEGER NOT NULL,
                source_file TEXT,
                ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(dealing_number, property_id)
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_raw_sales_suburb_date
            ON raw_sales(suburb, contract_date)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_raw_sales_postcode
            ON raw_sales(postcode)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_raw_sales_type_suburb
            ON raw_sales(property_type, suburb, contract_date)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_raw_sales_contract_date
            ON raw_sales(contract_date)
        """)

        # 2. property_meta - Enriched property metadata (bedrooms, etc.)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS property_meta (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                normalised_address TEXT NOT NULL UNIQUE,
                bedrooms INTEGER,
                bathrooms INTEGER,
                car_spaces INTEGER,
                quality_tier TEXT CHECK(quality_tier IN ('A', 'B', 'C')),
                source_url TEXT,
                extraction_method TEXT,
                confidence_score REAL CHECK(confidence_score BETWEEN 0 AND 1),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                verified_by TEXT
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_property_meta_confidence
            ON property_meta(confidence_score)
        """)

        # 3. comp_universe - Comparable properties for tracking
        conn.execute("""
            CREATE TABLE IF NOT EXISTS comp_universe (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                building_address TEXT NOT NULL,
                unit_pattern TEXT,
                suburb TEXT NOT NULL,
                segment TEXT NOT NULL,
                quality_tier TEXT CHECK(quality_tier IN ('A', 'B', 'C')) NOT NULL,
                quality_notes TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_by TEXT DEFAULT 'manual'
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_comp_universe_segment
            ON comp_universe(segment, is_active)
        """)

        # 4. monthly_metrics - Computed metrics per segment/period
        conn.execute("""
            CREATE TABLE IF NOT EXISTS monthly_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                period_start DATE NOT NULL,
                period_end DATE NOT NULL,
                period_type TEXT CHECK(period_type IN ('monthly', 'quarterly', '6month')) NOT NULL,
                segment TEXT NOT NULL,
                median_price INTEGER,
                sample_size INTEGER NOT NULL,
                yoy_pct REAL,
                rolling_median_3m INTEGER,
                rolling_sample_3m INTEGER,
                spread_vs_target_pct REAL,
                spread_vs_target_dollars INTEGER,
                is_suppressed BOOLEAN DEFAULT FALSE,
                suppression_reason TEXT,
                computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(period_start, segment)
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_monthly_metrics_segment
            ON monthly_metrics(segment, period_start)
        """)

        # 5. review_queue - Items needing human review
        conn.execute("""
            CREATE TABLE IF NOT EXISTS review_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                table_name TEXT NOT NULL,
                record_id INTEGER NOT NULL,
                issue_type TEXT NOT NULL,
                issue_details TEXT,
                status TEXT CHECK(status IN ('pending', 'resolved', 'dismissed')) DEFAULT 'pending',
                resolution TEXT,
                resolved_at TIMESTAMP,
                resolved_by TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                priority INTEGER DEFAULT 5,
                UNIQUE(table_name, record_id, issue_type)
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_review_queue_status
            ON review_queue(status, priority)
        """)

        # 6. run_log - Pipeline execution tracking
        conn.execute("""
            CREATE TABLE IF NOT EXISTS run_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL UNIQUE,
                run_type TEXT NOT NULL,
                started_at TIMESTAMP NOT NULL,
                completed_at TIMESTAMP,
                duration_seconds REAL,
                status TEXT CHECK(status IN ('running', 'success', 'failed', 'partial')) NOT NULL,
                error_message TEXT,
                records_processed INTEGER,
                records_inserted INTEGER,
                records_updated INTEGER,
                trigger TEXT,
                git_sha TEXT
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_run_log_status
            ON run_log(status, started_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_run_log_type_date
            ON run_log(run_type, started_at)
        """)

        # 7. sale_classifications - Comparable review tracking
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sale_classifications (
                sale_id TEXT PRIMARY KEY,
                address TEXT NOT NULL,
                zoning TEXT,
                year_built INTEGER,
                has_duplex_keywords BOOLEAN DEFAULT FALSE,
                is_auto_excluded BOOLEAN DEFAULT FALSE,
                auto_exclude_reason TEXT,
                review_status TEXT DEFAULT 'pending'
                    CHECK(review_status IN ('pending', 'comparable', 'not_comparable')),
                reviewed_at TIMESTAMP,
                review_sent_at TIMESTAMP,
                review_notes TEXT,
                listing_url TEXT,
                use_in_median BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sale_classifications_status
            ON sale_classifications(review_status, is_auto_excluded)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sale_classifications_median
            ON sale_classifications(use_in_median)
        """)

        # 8. provisional_sales - Sold listings from Domain API (unconfirmed)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS provisional_sales (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                unit_number TEXT,
                house_number TEXT,
                street_name TEXT,
                suburb TEXT NOT NULL,
                postcode TEXT,
                property_type TEXT CHECK(property_type IN ('house', 'unit', 'land', 'other')),
                sold_price INTEGER,
                sold_date DATE,
                bedrooms INTEGER,
                bathrooms INTEGER,
                car_spaces INTEGER,
                address_normalised TEXT,
                matched_dealing_number TEXT,
                status TEXT DEFAULT 'unconfirmed'
                    CHECK(status IN ('unconfirmed', 'confirmed', 'superseded', 'price_withheld')),
                raw_json TEXT,
                listing_url TEXT,
                source_site TEXT,
                ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_provisional_sales_status
            ON provisional_sales(status)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_provisional_sales_suburb
            ON provisional_sales(suburb, sold_date)
        """)

        conn.commit()
        self._migrate_schema()

    def _migrate_schema(self):
        """Apply incremental schema migrations for existing databases."""
        conn = self._get_conn()

        # Migrate sale_classifications: add review_sent_at
        cursor = conn.execute("PRAGMA table_info(sale_classifications)")
        sc_columns = {row[1] for row in cursor.fetchall()}
        if 'review_sent_at' not in sc_columns:
            conn.execute("ALTER TABLE sale_classifications ADD COLUMN review_sent_at TIMESTAMP")

        # Migrate sale_classifications: add listing_url
        if 'listing_url' not in sc_columns:
            conn.execute("ALTER TABLE sale_classifications ADD COLUMN listing_url TEXT")

        # Migrate provisional_sales: add bedrooms, bathrooms, car_spaces
        cursor = conn.execute("PRAGMA table_info(provisional_sales)")
        ps_columns = {row[1] for row in cursor.fetchall()}
        for col in ['bedrooms', 'bathrooms', 'car_spaces']:
            if col not in ps_columns:
                conn.execute(f"ALTER TABLE provisional_sales ADD COLUMN {col} INTEGER")

        # Migrate provisional_sales: add listing_url, source_site
        if 'listing_url' not in ps_columns:
            conn.execute("ALTER TABLE provisional_sales ADD COLUMN listing_url TEXT")
        if 'source_site' not in ps_columns:
            conn.execute("ALTER TABLE provisional_sales ADD COLUMN source_site TEXT")

        conn.commit()

    def upsert_raw_sales(self, sales: List[dict]) -> int:
        """Insert sales, ignoring duplicates. Returns count of new records."""
        if not sales:
            return 0

        sql = """
            INSERT OR IGNORE INTO raw_sales (
                dealing_number, property_id, unit_number, house_number,
                street_name, suburb, postcode, area_sqm, zone_code,
                nature_of_property, strata_lot_number, contract_date,
                settlement_date, purchase_price, property_type,
                district_code, source_file
            ) VALUES (
                :dealing_number, :property_id, :unit_number, :house_number,
                :street_name, :suburb, :postcode, :area_sqm, :zone_code,
                :nature_of_property, :strata_lot_number, :contract_date,
                :settlement_date, :purchase_price, :property_type,
                :district_code, :source_file
            )
        """

        conn = self._get_conn()
        cursor = conn.cursor()
        inserted = 0

        for sale in sales:
            # Normalize property_id to empty string if None for UNIQUE constraint
            normalized_sale = dict(sale)
            if normalized_sale.get('property_id') is None:
                normalized_sale['property_id'] = ''
            cursor.execute(sql, normalized_sale)
            if cursor.rowcount > 0:
                inserted += 1

        conn.commit()
        return inserted

    def start_run(self, run_type: str, trigger: str) -> str:
        """Start a new run and return the run_id."""
        run_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()

        self.execute(
            """
            INSERT INTO run_log (run_id, run_type, started_at, status, trigger)
            VALUES (?, ?, ?, 'running', ?)
            """,
            (run_id, run_type, now, trigger)
        )
        return run_id

    def complete_run(
        self,
        run_id: str,
        status: str,
        error_message: str = None,
        records_processed: int = None,
        records_inserted: int = None,
        records_updated: int = None,
    ):
        """Complete a run with final status and stats."""
        now = datetime.now(timezone.utc)
        started = self.query(
            "SELECT started_at FROM run_log WHERE run_id = ?", (run_id,)
        )
        if started:
            started_at = datetime.fromisoformat(started[0]['started_at'])
            duration = (now - started_at).total_seconds()
        else:
            duration = None

        self.execute(
            """
            UPDATE run_log SET
                completed_at = ?,
                duration_seconds = ?,
                status = ?,
                error_message = ?,
                records_processed = ?,
                records_inserted = ?,
                records_updated = ?
            WHERE run_id = ?
            """,
            (
                now.isoformat(),
                duration,
                status,
                error_message,
                records_processed,
                records_inserted,
                records_updated,
                run_id,
            )
        )

    def get_last_successful_run(self, run_type: str = None) -> Optional[dict]:
        """Get most recent successful run, optionally filtered by type."""
        if run_type:
            rows = self.query(
                """
                SELECT * FROM run_log
                WHERE status = 'success' AND run_type = ?
                ORDER BY completed_at DESC LIMIT 1
                """,
                (run_type,)
            )
        else:
            rows = self.query(
                """
                SELECT * FROM run_log
                WHERE status = 'success'
                ORDER BY completed_at DESC LIMIT 1
                """
            )
        return rows[0] if rows else None

    def upsert_provisional_sales(self, sales: list) -> int:
        """Insert provisional sales, ignoring duplicates. Returns count of new records."""
        if not sales:
            return 0

        sql = """
            INSERT OR IGNORE INTO provisional_sales (
                id, source, unit_number, house_number, street_name,
                suburb, postcode, property_type, sold_price, sold_date,
                bedrooms, bathrooms, car_spaces,
                address_normalised, listing_url, source_site, status, raw_json
            ) VALUES (
                :id, :source, :unit_number, :house_number, :street_name,
                :suburb, :postcode, :property_type, :sold_price, :sold_date,
                :bedrooms, :bathrooms, :car_spaces,
                :address_normalised, :listing_url, :source_site, :status, :raw_json
            )
        """

        conn = self._get_conn()
        cursor = conn.cursor()
        inserted = 0

        for sale in sales:
            cursor.execute(sql, sale)
            if cursor.rowcount > 0:
                inserted += 1

        conn.commit()
        return inserted

    def get_unconfirmed_provisional_sales(self, suburb: str = None) -> list:
        """Get unconfirmed provisional sales, optionally filtered by suburb."""
        if suburb:
            return self.query(
                """SELECT * FROM provisional_sales
                   WHERE status = 'unconfirmed' AND LOWER(suburb) = LOWER(?)
                   ORDER BY sold_date DESC""",
                (suburb,)
            )
        return self.query(
            """SELECT * FROM provisional_sales
               WHERE status = 'unconfirmed'
               ORDER BY sold_date DESC"""
        )

    def mark_provisional_confirmed(self, provisional_id: str, dealing_number: str):
        """Link a provisional sale to a VG record."""
        self.execute(
            """UPDATE provisional_sales
               SET status = 'confirmed', matched_dealing_number = ?
               WHERE id = ?""",
            (dealing_number, provisional_id)
        )

    def get_unconfirmed_provisional_sales_filtered(
        self,
        suburb: str = None,
        property_type: str = None,
        bedrooms: int = None,
        bathrooms: int = None,
        car_spaces: int = None,
        price_min: int = None,
        price_max: int = None,
    ) -> list:
        """Get unconfirmed provisional sales with optional filtering."""
        query = "SELECT * FROM provisional_sales WHERE status = 'unconfirmed'"
        params = []

        if suburb:
            query += " AND LOWER(suburb) = LOWER(?)"
            params.append(suburb)
        if property_type:
            query += " AND property_type = ?"
            params.append(property_type)
        if bedrooms is not None:
            query += " AND bedrooms = ?"
            params.append(bedrooms)
        if bathrooms is not None:
            query += " AND bathrooms = ?"
            params.append(bathrooms)
        if car_spaces is not None:
            query += " AND car_spaces = ?"
            params.append(car_spaces)
        if price_min is not None:
            query += " AND sold_price >= ?"
            params.append(price_min)
        if price_max is not None:
            query += " AND sold_price <= ?"
            params.append(price_max)

        query += " ORDER BY sold_date DESC"
        return self.query(query, tuple(params))
