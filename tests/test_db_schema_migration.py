"""Tests for DB schema additions: listing_url, source_site, price_withheld."""

import sqlite3
import uuid

import pytest


def _get_columns(db, table_name: str) -> set:
    """Return set of column names for a table."""
    rows = db.query(f"PRAGMA table_info({table_name})")
    return {r["name"] for r in rows}


class TestSaleClassificationsSchema:
    """Tests for sale_classifications table schema changes."""

    def test_listing_url_column_exists(self, db):
        columns = _get_columns(db, "sale_classifications")
        assert "listing_url" in columns

    def test_listing_url_accepts_value(self, db):
        db.execute(
            """INSERT INTO sale_classifications (sale_id, address, listing_url)
               VALUES (?, ?, ?)""",
            ("test-sale-1", "1 Test St", "https://www.domain.com.au/1-test-st-123456"),
        )
        rows = db.query(
            "SELECT listing_url FROM sale_classifications WHERE sale_id = ?",
            ("test-sale-1",),
        )
        assert rows[0]["listing_url"] == "https://www.domain.com.au/1-test-st-123456"

    def test_listing_url_nullable(self, db):
        db.execute(
            """INSERT INTO sale_classifications (sale_id, address)
               VALUES (?, ?)""",
            ("test-sale-2", "2 Test St"),
        )
        rows = db.query(
            "SELECT listing_url FROM sale_classifications WHERE sale_id = ?",
            ("test-sale-2",),
        )
        assert rows[0]["listing_url"] is None


class TestProvisionalSalesSchema:
    """Tests for provisional_sales table schema changes."""

    def test_listing_url_column_exists(self, db):
        columns = _get_columns(db, "provisional_sales")
        assert "listing_url" in columns

    def test_source_site_column_exists(self, db):
        columns = _get_columns(db, "provisional_sales")
        assert "source_site" in columns

    def test_price_withheld_status_accepted(self, db):
        sale_id = str(uuid.uuid4())[:8]
        db.execute(
            """INSERT INTO provisional_sales (id, source, suburb, status)
               VALUES (?, ?, ?, ?)""",
            (sale_id, "google_search", "LANE COVE", "price_withheld"),
        )
        rows = db.query(
            "SELECT status FROM provisional_sales WHERE id = ?",
            (sale_id,),
        )
        assert rows[0]["status"] == "price_withheld"

    def test_listing_url_and_source_site_accept_values(self, db):
        sale_id = str(uuid.uuid4())[:8]
        db.execute(
            """INSERT INTO provisional_sales
               (id, source, suburb, listing_url, source_site)
               VALUES (?, ?, ?, ?, ?)""",
            (
                sale_id,
                "google_search",
                "LANE COVE",
                "https://www.realestate.com.au/sold/property-unit-nsw-lane+cove-123",
                "realestate.com.au",
            ),
        )
        rows = db.query(
            "SELECT listing_url, source_site FROM provisional_sales WHERE id = ?",
            (sale_id,),
        )
        assert rows[0]["listing_url"] == "https://www.realestate.com.au/sold/property-unit-nsw-lane+cove-123"
        assert rows[0]["source_site"] == "realestate.com.au"

    def test_invalid_status_rejected(self, db):
        sale_id = str(uuid.uuid4())[:8]
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """INSERT INTO provisional_sales (id, source, suburb, status)
                   VALUES (?, ?, ?, ?)""",
                (sale_id, "test", "TEST", "invalid_status"),
            )

    def test_existing_statuses_still_work(self, db):
        """Ensure all original status values still work."""
        for status in ("unconfirmed", "confirmed", "superseded"):
            sale_id = str(uuid.uuid4())[:8]
            db.execute(
                """INSERT INTO provisional_sales (id, source, suburb, status)
                   VALUES (?, ?, ?, ?)""",
                (sale_id, "test", "TEST", status),
            )
            rows = db.query(
                "SELECT status FROM provisional_sales WHERE id = ?",
                (sale_id,),
            )
            assert rows[0]["status"] == status


class TestMigrationOnExistingDb:
    """Test that _migrate_schema adds columns to pre-existing tables."""

    def test_migration_adds_listing_url_to_sale_classifications(self, temp_db):
        """Simulate an old DB without listing_url and verify migration adds it."""
        from tracker.db import Database

        # Create a DB with the old schema (no listing_url in sale_classifications)
        conn = sqlite3.connect(temp_db)
        conn.execute("""
            CREATE TABLE sale_classifications (
                sale_id TEXT PRIMARY KEY,
                address TEXT NOT NULL,
                review_notes TEXT,
                use_in_median BOOLEAN DEFAULT FALSE
            )
        """)
        # Also create provisional_sales with old schema
        conn.execute("""
            CREATE TABLE provisional_sales (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                suburb TEXT NOT NULL,
                status TEXT DEFAULT 'unconfirmed',
                raw_json TEXT,
                ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()

        # Now open with Database which should run migrations
        db = Database(temp_db)
        db._migrate_schema()

        columns = _get_columns(db, "sale_classifications")
        assert "listing_url" in columns
        db.close()

    def test_migration_adds_columns_to_provisional_sales(self, temp_db):
        """Simulate an old DB without listing_url/source_site and verify migration."""
        from tracker.db import Database

        conn = sqlite3.connect(temp_db)
        conn.execute("""
            CREATE TABLE sale_classifications (
                sale_id TEXT PRIMARY KEY,
                address TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE provisional_sales (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                suburb TEXT NOT NULL,
                status TEXT DEFAULT 'unconfirmed',
                raw_json TEXT,
                ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()

        db = Database(temp_db)
        db._migrate_schema()

        columns = _get_columns(db, "provisional_sales")
        assert "listing_url" in columns
        assert "source_site" in columns
        db.close()

    def test_migration_is_idempotent(self, db):
        """Running _migrate_schema twice should not fail."""
        db._migrate_schema()
        db._migrate_schema()
        # If we get here without error, migration is idempotent
        columns_sc = _get_columns(db, "sale_classifications")
        columns_ps = _get_columns(db, "provisional_sales")
        assert "listing_url" in columns_sc
        assert "listing_url" in columns_ps
        assert "source_site" in columns_ps
