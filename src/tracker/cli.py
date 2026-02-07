# src/tracker/cli.py
"""CLI commands for PropertyTracker."""

import logging
import sys
import hashlib
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import click
import yaml
from dotenv import load_dotenv

from tracker.db import Database
from tracker.ingest.downloader import download_psi_archive, extract_archive, get_data_path
from tracker.ingest.parser import parse_all_csv_files
from tracker.ingest.domain_sold import fetch_sold_listings  # noqa: F401 - used by match-provisional
from tracker.ingest.matcher import match_provisional_to_vg
from tracker.ingest.google_search import fetch_sold_listings_google
from tracker.compute.segments import (
    init_segments,
    get_segment_for_sale,
    get_proxy_segments,
    get_target_segments,
    SEGMENTS,
)
from tracker.compute.metrics import (
    compute_all_metrics,
    save_metrics_to_db,
    get_new_sales_since_date,
)
from tracker.compute.gap_tracker import compute_gap_tracker
from tracker.compute.equity import compute_affordability_gap
from tracker.compute.segments import get_segment
from tracker.notify.telegram import (
    TelegramConfig,
    send_monthly_report,
    send_ingest_failure_alert,
    format_monthly_report,
    format_simple_report,
    send_simple_report,
    send_review_digest,
    compute_segment_position,
)

# Load .env file
load_dotenv()

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)


def _stable_sale_id(prefix: str, address_normalised: str) -> str:
    """Build a deterministic ID from normalised address text."""
    digest = hashlib.sha256(address_normalised.encode('utf-8')).hexdigest()[:20]
    return f"{prefix}-{digest}"


def load_config(config_path: str = 'config.yml') -> dict:
    """Load configuration from YAML file.

    Financial values can be overridden via environment variables:
    - SAVINGS_BALANCE: Override savings.current_balance
    - SAVINGS_MONTHLY: Override savings.monthly_contribution
    - PPOR_DEBT: Override ppor.debt
    - IP_DEBT: Override investment_property.debt
    """
    import os

    path = Path(config_path)
    if not path.exists():
        raise click.ClickException(f"Config file not found: {config_path}")

    try:
        with open(path) as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise click.ClickException(f"Invalid YAML in config file: {e}")

    if config is None:
        raise click.ClickException("Config file is empty")

    # Override with environment variables (for CI/CD security)
    if os.environ.get('SAVINGS_BALANCE'):
        config.setdefault('savings', {})['current_balance'] = int(os.environ['SAVINGS_BALANCE'])
    if os.environ.get('SAVINGS_MONTHLY'):
        config.setdefault('savings', {})['monthly_contribution'] = int(os.environ['SAVINGS_MONTHLY'])
    if os.environ.get('PPOR_DEBT'):
        config.setdefault('ppor', {})['debt'] = int(os.environ['PPOR_DEBT'])
    if os.environ.get('IP_DEBT'):
        config.setdefault('investment_property', {})['debt'] = int(os.environ['IP_DEBT'])

    return config


@click.group()
@click.option('--config', '-c', default='config.yml', help='Path to config file')
@click.option('--db', default='data/tracker.db', help='Path to database')
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose output')
@click.pass_context
def cli(ctx, config, db, verbose):
    """PropertyTracker: Sydney property equity and affordability monitor."""
    ctx.ensure_object(dict)
    ctx.obj['config_path'] = config
    ctx.obj['db_path'] = db
    ctx.obj['verbose'] = verbose

    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)


@cli.command()
@click.pass_context
def status(ctx):
    """Show system status and last run info."""
    db = Database(ctx.obj['db_path'])
    db.init_schema()

    click.echo("PropertyTracker v0.2.0")
    click.echo(f"Database: {ctx.obj['db_path']}")

    # Load config and init segments
    try:
        config = load_config(ctx.obj['config_path'])
        init_segments(config)
        click.echo(f"Segments loaded: {len(SEGMENTS)}")
    except Exception as e:
        click.echo(f"Config: Error loading ({e})")

    # Check last successful run
    last_run = db.get_last_successful_run()
    if last_run:
        click.echo(f"Last successful run: {last_run['completed_at']}")
        click.echo(f"  Type: {last_run['run_type']}")
        click.echo(f"  Records: {last_run.get('records_processed', 'N/A')}")
    else:
        click.echo("No successful runs yet")

    # Count records
    sales_count = db.query("SELECT COUNT(*) as n FROM raw_sales")[0]['n']
    click.echo(f"Sales records: {sales_count:,}")

    db.close()


@cli.command()
@click.option('--force', '-f', is_flag=True, help='Force re-download')
@click.pass_context
def ingest(ctx, force):
    """Download and ingest NSW property sales data."""
    db = Database(ctx.obj['db_path'])
    db.init_schema()

    # Load config and init segments
    config = load_config(ctx.obj['config_path'])
    init_segments(config)

    run_id = db.start_run('ingest', 'cli')
    total_inserted = 0

    try:
        click.echo("Downloading PSI archive...")
        data_path = get_data_path()
        archive = download_psi_archive(data_path, force=force)
        click.echo(f"Archive: {archive}")

        click.echo("Extracting archive...")
        extracted = extract_archive(archive)
        click.echo(f"Extracted {len(extracted)} files")

        click.echo("Parsing sales data...")
        for record in parse_all_csv_files(data_path):
            # Assign segment
            segment = get_segment_for_sale(
                record['suburb'],
                record['property_type']
            )

            # Insert into database
            inserted = db.upsert_raw_sales([record])
            total_inserted += inserted

        click.echo(f"Inserted {total_inserted:,} new records")

        db.complete_run(
            run_id,
            status='success',
            records_processed=total_inserted,
            records_inserted=total_inserted,
        )

    except Exception as e:
        logger.exception("Ingest failed")
        db.complete_run(run_id, status='failed', error_message=str(e))

        # Try to send failure alert
        try:
            telegram_config = TelegramConfig.from_env()
            send_ingest_failure_alert(telegram_config, str(e))
        except Exception as alert_error:
            logger.warning(f"Failed to send alert: {alert_error}")

        raise click.ClickException(f"Ingest failed: {e}")

    finally:
        db.close()


@cli.command('ingest-domain')
@click.pass_context
def ingest_domain(ctx):
    """Fetch sold listings from Domain API for all segments."""
    import os
    db = Database(ctx.obj['db_path'])
    db.init_schema()

    config = load_config(ctx.obj['config_path'])
    init_segments(config)

    api_key = os.getenv('DOMAIN_API_KEY')
    if not api_key:
        click.echo("DOMAIN_API_KEY not set, skipping Domain ingest")
        return

    run_id = db.start_run('ingest-domain', 'cli')
    total_inserted = 0

    try:
        segments_config = config.get('segments', {})
        for seg_code, seg_def in segments_config.items():
            suburbs = seg_def.get('suburbs', [])
            prop_type = seg_def.get('property_type', 'house')
            for suburb in suburbs:
                postcode_rows = db.query(
                    "SELECT DISTINCT postcode FROM raw_sales WHERE LOWER(suburb) = LOWER(?) LIMIT 1",
                    (suburb,)
                )
                postcode = str(int(float(postcode_rows[0]['postcode']))) if postcode_rows else ''

                click.echo(f"Fetching Domain sold listings for {suburb} ({prop_type})...")
                listings = fetch_sold_listings(
                    suburb=suburb.title(),
                    property_type=prop_type,
                    postcode=postcode,
                    api_key=api_key,
                )

                if listings:
                    inserted = db.upsert_provisional_sales(listings)
                    total_inserted += inserted
                    click.echo(f"  {len(listings)} found, {inserted} new")
                else:
                    click.echo(f"  No sold listings found")

        click.echo(f"Total: {total_inserted} new provisional sales")
        db.complete_run(run_id, status='success', records_inserted=total_inserted)

    except Exception as e:
        logger.exception("Domain ingest failed")
        db.complete_run(run_id, status='failed', error_message=str(e))
        raise click.ClickException(f"Domain ingest failed: {e}")


@cli.command('ingest-domain-scrape')
@click.pass_context
def ingest_domain_scrape(ctx):
    """Scrape Domain.com.au sold listings with headless browser (Playwright)."""
    from tracker.ingest.domain_scraper import fetch_sold_listings_scrape

    config = load_config(ctx.obj['config_path'])
    init_segments(config)

    db = Database(ctx.obj['db_path'])
    db.init_schema()

    run_id = db.start_run('ingest-domain-scrape', 'cli')
    total_inserted = 0

    try:
        segments_config = config.get('segments', {})
        for seg_code, seg_def in segments_config.items():
            suburbs = seg_def.get('suburbs', [])
            prop_type = seg_def.get('property_type', 'house')
            for suburb in suburbs:
                postcode_rows = db.query(
                    "SELECT DISTINCT postcode FROM raw_sales "
                    "WHERE LOWER(suburb) = LOWER(?) LIMIT 1",
                    (suburb,)
                )
                postcode = (
                    str(int(float(postcode_rows[0]['postcode'])))
                    if postcode_rows else ''
                )

                click.echo(
                    f"Scraping Domain sold listings for {suburb} ({prop_type})..."
                )
                listings = fetch_sold_listings_scrape(
                    suburb=suburb.title(),
                    property_type=prop_type,
                    postcode=postcode,
                )

                if listings:
                    inserted = db.upsert_provisional_sales(listings)
                    total_inserted += inserted
                    click.echo(f"  {len(listings)} found, {inserted} new")
                else:
                    click.echo(f"  No sold listings found")

        click.echo(
            f"Total: {total_inserted} new provisional sales from Domain scrape"
        )
        db.complete_run(
            run_id, status='success', records_inserted=total_inserted
        )

    except Exception as e:
        logger.exception("Domain scrape ingest failed")
        db.complete_run(run_id, status='failed', error_message=str(e))
        raise click.ClickException(f"Domain scrape failed: {e}")


@cli.command('ingest-google')
@click.option('--segment', help='Specific segment to ingest (optional)')
@click.option('--enrich', is_flag=True, help='Run LLM agent for incomplete data')
@click.pass_context
def ingest_google(ctx, segment: Optional[str], enrich: bool):
    """Ingest sold listings from Google search."""
    from tracker.ingest.llm_agent import extract_listing_details
    import os

    config = load_config(ctx.obj['config_path'])
    init_segments(config)

    # Filter segments: only those requiring manual review
    segments_to_process = []
    for seg_code, seg in SEGMENTS.items():
        if segment and seg_code != segment:
            continue
        if seg.require_manual_review:
            segments_to_process.append((seg_code, seg))

    if not segments_to_process:
        click.echo("No segments configured for Google search ingest")
        return

    anthropic_key = os.getenv('ANTHROPIC_API_KEY') if enrich else None
    total_ingested = 0

    with Database(config.get('database', {}).get('path', ctx.obj['db_path'])) as db:
        # Clean up bad records from previous runs
        cleaned = db.cleanup_provisional_sales()
        if cleaned:
            click.echo(f"Cleaned up {cleaned} bad provisional records")

        for seg_code, seg in segments_to_process:
            click.echo(f"Ingesting {seg.display_name}...")

            # ONE DDG search per segment using shortest suburb name.
            # DDG only allows ~2 requests per IP before rate-limiting,
            # so "revesby" captures both Revesby and Revesby Heights.
            search_suburb = min(seg.suburbs, key=len)

            # Query database for postcode
            postcode_rows = db.query(
                "SELECT DISTINCT postcode FROM raw_sales WHERE LOWER(suburb) = LOWER(?) LIMIT 1",
                (search_suburb,)
            )
            postcode = str(int(float(postcode_rows[0]['postcode']))) if postcode_rows else ''

            # Source 1: DuckDuckGo search
            try:
                results = fetch_sold_listings_google(
                    suburb=search_suburb,
                    property_type=seg.property_type,
                    postcode=postcode,
                    bedrooms=seg.bedrooms,
                    bathrooms=seg.bathrooms,
                )

                if results:
                    # Convert to provisional_sales format
                    sales = []
                    for listing in results:
                        sale_id = _stable_sale_id('google', listing['address_normalised'])

                        status = 'price_withheld' if listing.get('price_withheld', False) else 'unconfirmed'

                        sale = {
                            'id': sale_id,
                            'source': 'google',
                            'unit_number': listing.get('unit_number'),
                            'house_number': listing.get('house_number', ''),
                            'street_name': listing.get('street_name', ''),
                            'suburb': listing.get('suburb', search_suburb),
                            'postcode': listing.get('postcode', postcode),
                            'property_type': seg.property_type,
                            'sold_price': listing.get('sold_price'),
                            'sold_date': listing.get('sold_date'),
                            'bedrooms': listing.get('bedrooms'),
                            'bathrooms': listing.get('bathrooms'),
                            'car_spaces': listing.get('car_spaces'),
                            'address_normalised': listing['address_normalised'],
                            'listing_url': listing.get('listing_url', ''),
                            'source_site': listing.get('source_site', ''),
                            'status': status,
                            'raw_json': __import__('json').dumps(listing),
                        }

                        # Optionally enrich with LLM
                        if enrich and anthropic_key and not listing.get('sold_price'):
                            listing_url = listing.get('listing_url')
                            if listing_url:
                                details = extract_listing_details(listing_url, search_suburb, anthropic_key)
                                if details:
                                    sale['sold_price'] = details.get('price')
                                    sale['bedrooms'] = sale['bedrooms'] or details.get('bedrooms')
                                    sale['bathrooms'] = sale['bathrooms'] or details.get('bathrooms')

                        sales.append(sale)

                    count = db.upsert_provisional_sales(sales)
                    total_ingested += count
                    click.echo(f"  DDG {search_suburb}: {count} new sales")

            except Exception as e:
                click.echo(f"  DDG {search_suburb}: Error - {e}", err=True)

        # Run cleanup again after ingest to collapse duplicates created in this run.
        cleaned_post = db.cleanup_provisional_sales()
        if cleaned_post:
            click.echo(f"Post-ingest cleanup: removed {cleaned_post} duplicate/bad records")

    click.echo(f"\nTotal ingested: {total_ingested} sales")


@cli.command('match-provisional')
@click.pass_context
def match_provisional(ctx):
    """Match provisional Domain sales to VG records."""
    db = Database(ctx.obj['db_path'])
    db.init_schema()

    click.echo("Matching provisional sales to VG records...")
    matched = match_provisional_to_vg(db)
    click.echo(f"Matched {matched} provisional sales")


@cli.command()
@click.option('--date', '-d', 'ref_date', default=None, help='Reference date (YYYY-MM-DD)')
@click.pass_context
def compute(ctx, ref_date):
    """Compute metrics for all segments."""
    db = Database(ctx.obj['db_path'])
    db.init_schema()

    run_id = db.start_run('compute', 'cli')

    try:
        config = load_config(ctx.obj['config_path'])
        init_segments(config)

        if ref_date:
            reference_date = datetime.strptime(ref_date, '%Y-%m-%d').date()
        else:
            reference_date = date.today().replace(day=1)

        click.echo(f"Computing metrics for {reference_date}...")

        # Get thresholds from config
        thresholds = config.get('thresholds', {})

        # Compute all segment metrics
        metrics = compute_all_metrics(db, reference_date, thresholds)

        # Save to database
        saved = save_metrics_to_db(db, metrics)
        click.echo(f"Saved {saved} metric records")

        # Display results
        for code, result in metrics.items():
            if result.is_suppressed:
                click.echo(f"  {code}: SUPPRESSED ({result.suppression_reason})")
            else:
                yoy = f"{result.yoy_pct:+.1f}%" if result.yoy_pct else "N/A"
                click.echo(
                    f"  {code}: ${result.median_price:,} ({yoy}, n={result.sample_size})"
                )

        db.complete_run(run_id, status='success', records_processed=len(metrics))

    except Exception as e:
        logger.exception("Compute failed")
        db.complete_run(run_id, status='failed', error_message=str(e))
        raise click.ClickException(f"Compute failed: {e}")

    finally:
        db.close()


@cli.command()
@click.option('--date', '-d', 'ref_date', default=None, help='Reference date (YYYY-MM-DD)')
@click.option('--dry-run', is_flag=True, help='Print message without sending')
@click.option('--format', '-f', 'report_format', default=None,
              type=click.Choice(['simple', 'detailed']),
              help='Report format (overrides config)')
@click.pass_context
def notify(ctx, ref_date, dry_run, report_format):
    """Send report via Telegram."""
    db = Database(ctx.obj['db_path'])
    db.init_schema()

    try:
        config = load_config(ctx.obj['config_path'])
        init_segments(config)

        if ref_date:
            reference_date = datetime.strptime(ref_date, '%Y-%m-%d').date()
        else:
            reference_date = date.today()

        # Determine report format
        report_config = config.get('report', {})
        fmt = report_format or report_config.get('format', 'simple')

        if fmt == 'simple':
            _send_simple_report(db, config, reference_date, dry_run)
        else:
            _send_detailed_report(db, config, reference_date, dry_run)

        # Log successful run
        if not dry_run:
            run_id = db.start_run('notify', 'cli')
            db.complete_run(run_id, 'success')

    except Exception as e:
        logger.exception("Notify failed")
        raise click.ClickException(f"Notify failed: {e}")

    finally:
        db.close()


def _send_simple_report(db: Database, config: dict, reference_date: date, dry_run: bool):
    """Send the simplified sales + position report."""
    from datetime import timedelta

    report_config = config.get('report', {})
    show_proxies = report_config.get('show_proxies', ['revesby_houses', 'wollstonecraft_units'])

    # Use schedule-based lookback, not "last notify run".
    # Manual reruns on the same day should still show the configured window.
    schedule_cfg = config.get('schedule', {})
    freq = str(schedule_cfg.get('frequency', 'weekly')).lower()
    lookback_days = {
        'weekly': 7,
        'fortnightly': 14,
        'monthly': 31,
    }.get(freq, 7)
    last_report_date = reference_date - timedelta(days=lookback_days)

    click.echo(f"Finding sales since {last_report_date}...")

    # Compute metrics for medians
    thresholds = config.get('thresholds', {})
    metrics = compute_all_metrics(db, reference_date, thresholds)

    # Get new sales for all segments (proxies + targets), including provisional
    show_targets = report_config.get('show_targets', [])
    new_sales = {}
    for segment_code in show_proxies + show_targets:
        sales = get_new_sales_since_date(db, segment_code, last_report_date)
        new_sales[segment_code] = sales
        confirmed = sum(1 for s in sales if s.source == 'confirmed')
        unconfirmed = sum(1 for s in sales if s.source == 'unconfirmed')
        click.echo(f"  {segment_code}: {len(sales)} new sales ({confirmed} confirmed, {unconfirmed} unconfirmed)")

    # Compute positions for each segment
    positions = {}
    ip_debt = config.get('investment_property', {}).get('debt', 0)
    ppor_debt = config.get('ppor', {}).get('debt', 0)
    haircut = config.get('investment_property', {}).get('valuation_haircut', {}).get('base', 0.95)
    lvr_cap = config.get('investment_property', {}).get('refinance_lvr_cap', 0.80)
    selling_cost_rate = config.get('ppor', {}).get('selling_cost_rate', 0.02)

    for segment_code in show_proxies:
        metric = metrics.get(segment_code)
        if not metric:
            continue

        # Determine if PPOR or IP based on segment
        is_ppor = 'wollstonecraft' in segment_code or 'ppor' in segment_code.lower()
        debt = ppor_debt if is_ppor else ip_debt

        positions[segment_code] = compute_segment_position(
            metric,
            debt=debt,
            is_ppor=is_ppor,
            haircut=haircut,
            lvr_cap=lvr_cap,
            selling_cost_rate=selling_cost_rate,
        )

    # Clean up bad provisional records before displaying
    db.cleanup_provisional_sales()

    # Format report (new_sales already includes provisional sales inline)
    period_str = reference_date.strftime('%b %-d, %Y')
    message = format_simple_report(
        new_sales, positions, period_str, config,
    )

    if dry_run:
        click.echo("\n--- DRY RUN ---")
        click.echo(message)
        click.echo("--- END ---\n")
    else:
        telegram_config = TelegramConfig.from_env()
        success = send_simple_report(
            telegram_config, new_sales, positions, period_str, config,
        )

        if success:
            click.echo("Report sent successfully!")
        else:
            raise click.ClickException("Failed to send report")


def _send_detailed_report(db: Database, config: dict, reference_date: date, dry_run: bool):
    """Send the full detailed report with gap tracker and affordability."""
    period_str = reference_date.strftime('%B %Y')
    click.echo(f"Preparing detailed report for {period_str}...")

    # Get thresholds from config
    thresholds = config.get('thresholds', {})

    # Compute metrics
    metrics = compute_all_metrics(db, reference_date, thresholds)

    # Get gap tracker config
    gap_config = config.get('gap_tracker', {})
    proxy_codes = gap_config.get('proxy_segments', ['revesby_houses', 'wollstonecraft_units'])
    target_code = gap_config.get('target_segment', 'lane_cove_houses')

    # Get proxy and target metrics for gap tracker
    proxy_metrics = {code: metrics[code] for code in proxy_codes if code in metrics}
    target_metric = metrics.get(target_code)

    if not target_metric:
        raise click.ClickException(f"Target segment '{target_code}' not found in metrics")

    # Compute gap tracker
    gap_tracker = compute_gap_tracker(proxy_metrics, target_metric, config)

    # Get values for affordability calculation
    ip_metric = metrics.get('revesby_houses')
    ppor_metric = metrics.get('wollstonecraft_units')

    # Check we have valid data
    missing_data = []
    if not ip_metric or ip_metric.is_suppressed:
        missing_data.append("Revesby houses")
    if not ppor_metric or ppor_metric.is_suppressed:
        missing_data.append("Wollstonecraft units")
    if not target_metric or target_metric.is_suppressed:
        missing_data.append(f"Target ({target_code})")

    if missing_data:
        click.echo(f"Warning: Missing or suppressed data for: {', '.join(missing_data)}")
        click.echo("Cannot compute full affordability analysis.")
        return

    # Compute affordability gap
    affordability = compute_affordability_gap(
        config,
        ip_metric.median_price,
        ppor_metric.median_price,
        target_metric.median_price,
    )

    if dry_run:
        message = format_monthly_report(metrics, gap_tracker, affordability, period_str, config)
        click.echo("\n--- DRY RUN ---")
        click.echo(message)
        click.echo("--- END ---\n")
    else:
        telegram_config = TelegramConfig.from_env()
        success = send_monthly_report(
            telegram_config,
            metrics,
            gap_tracker,
            affordability,
            period_str,
            config,
        )

        if success:
            click.echo("Report sent successfully!")
        else:
            raise click.ClickException("Failed to send report")


@cli.command()
@click.option('--force', '-f', is_flag=True, help='Force re-download')
@click.option('--dry-run', is_flag=True, help='Skip sending notification')
@click.pass_context
def run(ctx, force, dry_run):
    """Run full pipeline: ingest, compute, notify."""
    click.echo("=== PropertyTracker Full Run ===")
    click.echo("")

    click.echo("Step 1/3: Ingest")
    ctx.invoke(ingest, force=force)
    click.echo("")

    click.echo("Step 2/3: Compute")
    ctx.invoke(compute)
    click.echo("")

    click.echo("Step 3/3: Notify")
    ctx.invoke(notify, dry_run=dry_run)
    click.echo("")

    click.echo("=== Complete ===")


@cli.command()
@click.pass_context
def check_samples(ctx):
    """Check sample sizes for filtered segments."""
    db = Database(ctx.obj['db_path'])
    db.init_schema()

    config = load_config(ctx.obj['config_path'])
    init_segments(config)

    click.echo("Checking sample sizes for all segments...\n")

    for code, segment in SEGMENTS.items():
        # Build query with filters
        suburbs = list(segment.suburbs)
        placeholders = ','.join(['?' for _ in suburbs])

        query = f"""
            SELECT COUNT(*) as n
            FROM raw_sales
            WHERE LOWER(suburb) IN ({placeholders})
              AND property_type = ?
              AND purchase_price > 0
        """
        params = list(suburbs) + [segment.property_type]

        # Add area filter if specified
        if segment.area_min is not None:
            query += " AND area_sqm >= ?"
            params.append(segment.area_min)
        if segment.area_max is not None:
            query += " AND area_sqm <= ?"
            params.append(segment.area_max)

        # Add street filter if specified
        if segment.streets:
            street_list = list(segment.streets)
            street_placeholders = ','.join(['?' for _ in street_list])
            query += f" AND LOWER(street_name) IN ({street_placeholders})"
            params.extend(street_list)

        result = db.query(query, tuple(params))
        count = result[0]['n'] if result else 0

        # Get filter description
        filter_desc = segment.get_filter_description()

        click.echo(f"{segment.display_name}:")
        click.echo(f"  Total records: {count:,}")
        if filter_desc:
            click.echo(f"  Filters: {filter_desc}")

        # Check last 6 months
        from datetime import date, timedelta
        six_months_ago = (date.today() - timedelta(days=180)).isoformat()
        query_recent = query + " AND contract_date >= ?"
        params_recent = params + [six_months_ago]

        result_recent = db.query(query_recent, tuple(params_recent))
        count_recent = result_recent[0]['n'] if result_recent else 0
        click.echo(f"  Last 6 months: {count_recent}")

        # Warning if sample size is low
        if count_recent < 8:
            click.echo(f"  *** LOW SAMPLE SIZE - may need to widen filters ***")
        click.echo("")

    db.close()


@cli.command()
@click.option('--segment', default='revesby_houses', help='Segment to enrich')
@click.option('--limit', default=50, help='Max sales to process')
@click.option('--api-key', envvar='DOMAIN_API_KEY', help='Domain API key')
@click.pass_context
def enrich(ctx, segment, limit, api_key):
    """Enrich and classify sales for comparable review."""
    from tracker.enrich.pipeline import process_pending_sales

    config = load_config(ctx.obj['config_path'])
    init_segments(config)

    db = Database(db_path=ctx.obj['db_path'])
    db.init_schema()

    processed = process_pending_sales(db, segment, api_key=api_key, limit=limit)
    click.echo(f"Processed {processed} sales for {segment}")

    db.close()


@cli.command()
@click.option('--segment', default='revesby_houses', help='Segment to show pending')
@click.pass_context
def pending(ctx, segment):
    """Show sales pending review."""
    config = load_config(ctx.obj['config_path'])
    init_segments(config)

    db = Database(db_path=ctx.obj['db_path'])

    rows = db.query("""
        SELECT sc.sale_id, sc.address, sc.zoning, sc.year_built, r.purchase_price, r.area_sqm
        FROM sale_classifications sc
        JOIN raw_sales r ON sc.sale_id = r.dealing_number
        WHERE sc.review_status = 'pending'
          AND sc.is_auto_excluded = 0
        ORDER BY r.contract_date DESC
        LIMIT 20
    """)

    if not rows:
        click.echo("No sales pending review")
        db.close()
        return

    click.echo(f"{len(rows)} sales pending review:\n")
    for i, row in enumerate(rows, 1):
        click.echo(f"{i}. {row['address']}")
        area_str = f"{row['area_sqm']:.0f}sqm" if row['area_sqm'] else "N/A"
        click.echo(f"   ${row['purchase_price']:,} | {area_str} | {row['zoning'] or 'Unknown'} | {row['year_built'] or 'Year unknown'}")
        click.echo()

    db.close()


@cli.command('review-send')
@click.option('--segment', default='revesby_houses', help='Segment to send for review')
@click.option('--limit', default=50, help='Max sales to send')
@click.option('--dry-run', is_flag=True, help='Print message without sending')
@click.pass_context
def review_send(ctx, segment, limit, dry_run):
    """Send pending sales to Telegram for review."""
    from tracker.review.telegram import format_review_message
    from tracker.notify.telegram import TelegramConfig, send_message

    config = load_config(ctx.obj['config_path'])
    init_segments(config)

    db = Database(db_path=ctx.obj['db_path'])

    # Get segment config for filters
    seg = SEGMENTS.get(segment)
    if not seg:
        raise click.ClickException(f"Unknown segment: {segment}")

    # Build query for pending sales
    suburbs = list(seg.suburbs)
    placeholders = ','.join(['?' for _ in suburbs])

    query = f"""
        SELECT
            sc.sale_id,
            r.house_number || ' ' || r.street_name as address,
            r.purchase_price as price,
            r.area_sqm,
            sc.zoning,
            sc.year_built,
            r.contract_date
        FROM sale_classifications sc
        JOIN raw_sales r ON sc.sale_id = r.dealing_number
        WHERE sc.review_status = 'pending'
          AND sc.is_auto_excluded = 0
          AND LOWER(r.suburb) IN ({placeholders})
          AND r.property_type = ?
    """
    params = list(suburbs) + [seg.property_type]

    # Add area filter if specified
    if seg.area_min is not None:
        query += " AND r.area_sqm >= ?"
        params.append(seg.area_min)
    if seg.area_max is not None:
        query += " AND r.area_sqm <= ?"
        params.append(seg.area_max)

    query += " ORDER BY r.contract_date DESC LIMIT ?"
    params.append(limit)

    rows = db.query(query, tuple(params))

    if not rows:
        click.echo("No sales pending review")
        db.close()
        return

    # Convert to list of dicts
    sales = [dict(row) for row in rows]
    click.echo(f"Found {len(sales)} sales pending review")

    # Format message
    message = format_review_message(sales)

    if dry_run:
        click.echo("\n--- DRY RUN ---")
        click.echo(message)
        click.echo("--- END ---\n")
        click.echo(f"\nTo apply a response, run:")
        click.echo(f"  propertytracker review-apply --response 'y' * {len(sales)}")
    else:
        telegram_config = TelegramConfig.from_env()
        success = send_message(telegram_config, message)

        if success:
            click.echo("Review message sent to Telegram!")
            click.echo(f"\nReply with {len(sales)} characters (y/n for each sale)")
            click.echo(f"Then run: propertytracker review-apply --response '<your_response>'")
        else:
            raise click.ClickException("Failed to send Telegram message")

    db.close()


@cli.command('review-apply')
@click.option('--segment', default='revesby_houses', help='Segment to apply reviews to')
@click.option('--response', '-r', required=True, help='Review response (y/n string)')
@click.pass_context
def review_apply(ctx, segment, response):
    """Apply review response to pending sales.

    Response is a string of y/n characters, one per sale.
    Example: --response 'ynyynyyy'
    """
    config = load_config(ctx.obj['config_path'])
    init_segments(config)

    db = Database(db_path=ctx.obj['db_path'])

    # Get segment config for filters
    seg = SEGMENTS.get(segment)
    if not seg:
        raise click.ClickException(f"Unknown segment: {segment}")

    # Build query for pending sales (same order as review-send)
    suburbs = list(seg.suburbs)
    placeholders = ','.join(['?' for _ in suburbs])

    query = f"""
        SELECT
            sc.sale_id,
            r.house_number || ' ' || r.street_name as address,
            r.purchase_price
        FROM sale_classifications sc
        JOIN raw_sales r ON sc.sale_id = r.dealing_number
        WHERE sc.review_status = 'pending'
          AND sc.is_auto_excluded = 0
          AND LOWER(r.suburb) IN ({placeholders})
          AND r.property_type = ?
    """
    params = list(suburbs) + [seg.property_type]

    if seg.area_min is not None:
        query += " AND r.area_sqm >= ?"
        params.append(seg.area_min)
    if seg.area_max is not None:
        query += " AND r.area_sqm <= ?"
        params.append(seg.area_max)

    query += " ORDER BY r.contract_date DESC"

    rows = db.query(query, tuple(params))

    if not rows:
        click.echo("No sales pending review")
        db.close()
        return

    # Clean response
    response = response.strip().lower()

    if len(response) != len(rows):
        click.echo(f"Error: Response has {len(response)} chars, but {len(rows)} sales pending")
        click.echo(f"\nExpected response: {'y' * len(rows)} (all yes) or similar")
        click.echo("\nSales pending:")
        for i, row in enumerate(rows, 1):
            click.echo(f"  {i}. {row['address']} - ${row['purchase_price']:,}")
        db.close()
        return

    # Apply reviews
    approved = 0
    rejected = 0

    for i, row in enumerate(rows):
        sale_id = row['sale_id']
        choice = response[i]

        if choice == 'y':
            db.execute("""
                UPDATE sale_classifications
                SET review_status = 'comparable', use_in_median = 1
                WHERE sale_id = ?
            """, (sale_id,))
            approved += 1
        else:
            db.execute("""
                UPDATE sale_classifications
                SET review_status = 'not_comparable', use_in_median = 0
                WHERE sale_id = ?
            """, (sale_id,))
            rejected += 1

    click.echo(f"\nApproved as comparable: {approved}")
    click.echo(f"Rejected: {rejected}")

    # Show summary
    summary = db.query("""
        SELECT COUNT(*) as n, AVG(r.purchase_price) as avg_price
        FROM sale_classifications sc
        JOIN raw_sales r ON sc.sale_id = r.dealing_number
        WHERE sc.review_status = 'comparable'
          AND sc.use_in_median = 1
    """)

    if summary and summary[0]['n'] > 0:
        click.echo(f"\nTotal verified comparables: {summary[0]['n']}")
        click.echo(f"Average price: ${int(summary[0]['avg_price']):,}")

    db.close()


@cli.command('report')
@click.option('--date', '-d', 'ref_date', default=None, help='Reference date (YYYY-MM-DD)')
@click.option('--detailed', is_flag=True, help='Show detailed report with all sales')
@click.pass_context
def report(ctx, ref_date, detailed):
    """Generate and display property report."""
    from tracker.compute.time_adjust import compute_time_adjusted_median

    db = Database(ctx.obj['db_path'])
    db.init_schema()

    try:
        config = load_config(ctx.obj['config_path'])
        init_segments(config)

        if ref_date:
            reference_date = datetime.strptime(ref_date, '%Y-%m-%d').date()
        else:
            reference_date = date.today().replace(day=1)

        # Get growth rates from config
        time_config = config.get('time_adjustment', {})
        growth_rates = time_config.get('segment_growth_rates', {})
        default_rate = time_config.get('default_growth_rate', 0.07)

        # Get thresholds from config
        thresholds = config.get('thresholds', {})

        click.echo(f"PropertyTracker Report - {reference_date.strftime('%B %Y')}")
        click.echo("=" * 50)
        click.echo()

        # Compute metrics with growth rates
        metrics = compute_all_metrics(db, reference_date, thresholds, growth_rates)

        # Display Your Properties
        click.echo("YOUR PROPERTIES")
        click.echo("-" * 30)

        proxy_codes = ['revesby_houses', 'wollstonecraft_units']
        for code in proxy_codes:
            if code in metrics:
                m = metrics[code]
                if m.is_suppressed:
                    click.echo(f"{m.display_name}: Suppressed ({m.suppression_reason})")
                else:
                    yoy = f"{m.yoy_pct:+.1f}%" if m.yoy_pct else "N/A"
                    click.echo(f"{m.display_name}:")
                    click.echo(f"  Median: ${m.median_price:,} ({yoy})")
                    if m.time_adjusted_median:
                        click.echo(f"  Time-adjusted: ${m.time_adjusted_median:,}")
                        click.echo(f"  Range: ${m.time_adjusted_low:,} - ${m.time_adjusted_high:,}")
                        click.echo(f"  Verified comparables: {m.verified_sample_size}")
                    click.echo()

        # Display Target Markets
        click.echo("\nTARGET MARKETS")
        click.echo("-" * 30)

        target_codes = ['lane_cove_houses', 'chatswood_houses']
        for code in target_codes:
            if code in metrics:
                m = metrics[code]
                if m.is_suppressed:
                    click.echo(f"{m.display_name}: Suppressed ({m.suppression_reason})")
                else:
                    yoy = f"{m.yoy_pct:+.1f}%" if m.yoy_pct else "N/A"
                    click.echo(f"{m.display_name}: ${m.median_price:,} ({yoy}, n={m.sample_size})")

        click.echo()

        # Compute affordability if we have data
        ip_metric = metrics.get('revesby_houses')
        ppor_metric = metrics.get('wollstonecraft_units')
        target_metric = metrics.get('lane_cove_houses')

        if (ip_metric and not ip_metric.is_suppressed and
            ppor_metric and not ppor_metric.is_suppressed and
            target_metric and not target_metric.is_suppressed):

            click.echo("AFFORDABILITY GAP")
            click.echo("-" * 30)

            affordability = compute_affordability_gap(
                config,
                ip_metric.median_price,
                ppor_metric.median_price,
                target_metric.median_price,
            )

            base = affordability.base
            click.echo(f"Target: Lane Cove @ ${target_metric.median_price:,}")
            click.echo(f"Total needed: ${base.total_purchase_cost:,} (inc stamp duty)")
            click.echo(f"Your cash: ${base.total_cash:,}")
            click.echo(f"Gap: ${base.affordability_gap:,}")

            if affordability.months_to_close_gap:
                years = affordability.months_to_close_gap // 12
                months = affordability.months_to_close_gap % 12
                click.echo(f"Time to close: ~{years}y {months}m")

    finally:
        db.close()


@cli.command('review-buttons')
@click.option('--segment', default='wollstonecraft_units', help='Segment to send for review')
@click.option('--limit', default=10, help='Max sales to send')
@click.option('--dry-run', is_flag=True, help='Print without sending')
@click.pass_context
def review_buttons(ctx, segment, limit, dry_run):
    """Send pending sales to Telegram with inline Yes/No buttons."""
    config = load_config(ctx.obj['config_path'])
    init_segments(config)

    db = Database(db_path=ctx.obj['db_path'])

    seg = SEGMENTS.get(segment)
    if not seg:
        raise click.ClickException(f"Unknown segment: {segment}")

    # Get pending provisional sales (recent sold listings from Domain API)
    suburbs = list(seg.suburbs)
    placeholders = ','.join(['?' for _ in suburbs])

    query = f"""
        SELECT
            id as sale_id,
            COALESCE(house_number, '') || ' ' || COALESCE(street_name, '') as address,
            suburb,
            sold_price as price,
            NULL as area_sqm,
            NULL as zoning,
            NULL as year_built,
            listing_url,
            bedrooms,
            bathrooms,
            car_spaces,
            source_site,
            sold_date,
            'provisional' as source_type
        FROM provisional_sales
        WHERE review_status = 'pending'
          AND review_sent_at IS NULL
          AND status = 'unconfirmed'
          AND sold_price > 0
          AND LOWER(suburb) IN ({placeholders})
          AND property_type = ?
          AND date(sold_date) >= date('now', '-30 days')
    """
    params = list(suburbs) + [seg.property_type]

    # Apply segment-specific filters available on provisional_sales
    if seg.bedrooms is not None:
        query += " AND bedrooms = ?"
        params.append(seg.bedrooms)
    if seg.bathrooms is not None:
        query += " AND bathrooms = ?"
        params.append(seg.bathrooms)
    if seg.car_spaces is not None:
        query += " AND car_spaces = ?"
        params.append(seg.car_spaces)

    query += " ORDER BY sold_date DESC LIMIT ?"
    params.append(limit)

    all_rows = db.query(query, tuple(params))

    if not all_rows:
        click.echo("No sales pending review")
        db.close()
        return

    click.echo(f"Sending {len(all_rows)} recent sold listings for review...")

    if dry_run:
        for row in all_rows:
            price = row['price'] or 0
            click.echo(f"  Would send: {row['address'].strip()}, {row['suburb']} - ${price:,}")
        db.close()
        return

    telegram_config = TelegramConfig.from_env()

    # Build sales list with required fields
    sales_list = []
    for row in all_rows:
        # Use beds/baths/car info
        parts = []
        if row['bedrooms'] is not None:
            parts.append(f"{row['bedrooms']}bed")
        if row['bathrooms'] is not None:
            parts.append(f"{row['bathrooms']}bath")
        if row['car_spaces'] is not None:
            parts.append(f"{row['car_spaces']}car")
        zoning_label = "/".join(parts) if parts else "Details unknown"
        # Show sold date
        if row['sold_date']:
            from datetime import datetime as dt
            try:
                d = dt.strptime(row['sold_date'], '%Y-%m-%d')
                year_built_label = f"Sold {d.strftime('%-d %b %Y')}"
            except (ValueError, TypeError):
                year_built_label = f"Sold {row['sold_date']}"
        else:
            year_built_label = row['source_site'] or "Domain"

        sales_list.append({
            'sale_id': row['sale_id'],
            'address': f"{row['address'].strip()}, {row['suburb']}",
            'price': row['price'],
            'area_sqm': row['area_sqm'],
            'zoning_label': zoning_label,
            'year_built_label': year_built_label,
            'listing_url': row['listing_url'],
            'source_type': row['source_type'],
        })

    # Split into chunks of max 5 sales
    chunk_size = 5
    total_sent = 0

    for i in range(0, len(sales_list), chunk_size):
        chunk = sales_list[i:i + chunk_size]
        success = send_review_digest(telegram_config, seg.display_name, chunk, segment)

        if success:
            # Mark all sales in this chunk as sent
            now = datetime.now(timezone.utc).isoformat()
            for sale in chunk:
                db.execute(
                    "UPDATE provisional_sales SET review_sent_at = ? WHERE id = ?",
                    (now, sale['sale_id'])
                )
                total_sent += 1
            click.echo(f"  Sent digest with {len(chunk)} sales")

    click.echo(f"\nSent {total_sent}/{len(all_rows)} sales in {(len(sales_list) + chunk_size - 1) // chunk_size} digest(s)")
    db.close()


def _is_provisional_id(sale_id: str) -> bool:
    """Check if sale_id is from a provisional source (google- or domain- prefix)."""
    return sale_id.startswith('google-') or sale_id.startswith('domain-')


@cli.command('review-poll')
@click.pass_context
def review_poll(ctx):
    """Poll for and process button responses from Telegram."""
    from tracker.notify.telegram import (
        TelegramConfig,
        get_callback_updates,
        answer_callback_query,
        edit_message_remove_buttons,
    )

    config = load_config(ctx.obj['config_path'])
    init_segments(config)

    db = Database(db_path=ctx.obj['db_path'])
    telegram_config = TelegramConfig.from_env()

    click.echo("Polling for review responses...")

    updates = get_callback_updates(telegram_config)
    processed = 0
    max_update_id = None

    for update in updates:
        update_id = update.get('update_id')
        if max_update_id is None or update_id > max_update_id:
            max_update_id = update_id

        callback = update.get('callback_query')
        if not callback:
            continue

        callback_id = callback.get('id')
        data = callback.get('data', '')

        # Parse callback data: "review:SEGMENT:SALE_ID:yes/no" or "review:SEGMENT:all:yes/no"
        parts = data.split(':')
        if len(parts) != 4 or parts[0] != 'review':
            continue

        _, segment_code, sale_id, response = parts

        if response == 'yes':
            status = 'comparable'
            use_in_median = 1
            response_text = "Marked as comparable"
        else:
            status = 'not_comparable'
            use_in_median = 0
            response_text = "Marked as not comparable"

        # Update database
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()

        # Handle bulk "all" button
        if sale_id == 'all':
            # Extract sale IDs from the message's inline keyboard
            msg = callback.get('message', {})
            reply_markup = msg.get('reply_markup', {})
            inline_keyboard = reply_markup.get('inline_keyboard', [])

            sale_ids = []
            for row in inline_keyboard:
                for button in row:
                    button_data = button.get('callback_data', '')
                    button_parts = button_data.split(':')
                    # Extract individual sale IDs (skip 'all' buttons)
                    if len(button_parts) == 4 and button_parts[0] == 'review' and button_parts[2] != 'all':
                        sale_ids.append(button_parts[2])

            # Update all sales (route to correct table by ID type)
            for sid in sale_ids:
                if _is_provisional_id(sid):
                    result = db.execute("""
                        UPDATE provisional_sales
                        SET review_status = ?,
                            use_in_median = ?,
                            reviewed_at = ?
                        WHERE id = ?
                    """, (status, use_in_median, now, sid))
                else:
                    result = db.execute("""
                        UPDATE sale_classifications
                        SET review_status = ?,
                            use_in_median = ?,
                            reviewed_at = ?,
                            updated_at = ?
                        WHERE sale_id = ?
                    """, (status, use_in_median, now, now, sid))

                if result > 0:
                    processed += 1
                    click.echo(f"  {sid}: {status}")

            response_text = f"Marked all {len(sale_ids)} sales as {status}"
        else:
            # Single sale update (route to correct table by ID type)
            if _is_provisional_id(sale_id):
                result = db.execute("""
                    UPDATE provisional_sales
                    SET review_status = ?,
                        use_in_median = ?,
                        reviewed_at = ?
                    WHERE id = ?
                """, (status, use_in_median, now, sale_id))
            else:
                result = db.execute("""
                    UPDATE sale_classifications
                    SET review_status = ?,
                        use_in_median = ?,
                        reviewed_at = ?,
                        updated_at = ?
                    WHERE sale_id = ?
                """, (status, use_in_median, now, now, sale_id))

            if result > 0:
                processed += 1
                click.echo(f"  {sale_id}: {status}")

        # Acknowledge callback
        answer_callback_query(telegram_config, callback_id, response_text)

        # Remove buttons from the message and show result
        msg = callback.get('message', {})
        chat_id = msg.get('chat', {}).get('id')
        message_id = msg.get('message_id')
        original_text = msg.get('text', '')
        if chat_id and message_id:
            verdict = "YES - Comparable" if response == 'yes' else "NO - Not comparable"
            if sale_id == 'all':
                # Bulk verdict for digest
                new_text = original_text + f"\n\n<b>All marked: {verdict}</b>"
            else:
                # Single sale verdict
                new_text = original_text.replace(
                    "Is this comparable to your property?",
                    f"<b>{verdict}</b>",
                )
            edit_message_remove_buttons(
                telegram_config, chat_id, message_id, new_text=new_text,
            )

    # Clear processed updates by requesting with offset
    if max_update_id is not None:
        get_callback_updates(telegram_config, offset=max_update_id + 1)

    click.echo(f"\nProcessed {processed} responses")
    db.close()


if __name__ == '__main__':
    cli()
