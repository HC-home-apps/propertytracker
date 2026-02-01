# src/tracker/cli.py
"""CLI commands for PropertyTracker."""

import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import click
import yaml
from dotenv import load_dotenv

from tracker.db import Database
from tracker.ingest.downloader import download_psi_archive, extract_archive, get_data_path
from tracker.ingest.parser import parse_all_csv_files
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
)
from tracker.compute.gap_tracker import compute_gap_tracker
from tracker.compute.equity import compute_affordability_gap
from tracker.notify.telegram import (
    TelegramConfig,
    send_monthly_report,
    send_ingest_failure_alert,
    format_monthly_report,
)

# Load .env file
load_dotenv()

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)


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
@click.pass_context
def notify(ctx, ref_date, dry_run):
    """Send report via Telegram."""
    db = Database(ctx.obj['db_path'])
    db.init_schema()

    try:
        config = load_config(ctx.obj['config_path'])
        init_segments(config)

        if ref_date:
            reference_date = datetime.strptime(ref_date, '%Y-%m-%d').date()
        else:
            reference_date = date.today().replace(day=1)

        period_str = reference_date.strftime('%B %Y')
        click.echo(f"Preparing report for {period_str}...")

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

    except Exception as e:
        logger.exception("Notify failed")
        raise click.ClickException(f"Notify failed: {e}")

    finally:
        db.close()


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


if __name__ == '__main__':
    cli()
