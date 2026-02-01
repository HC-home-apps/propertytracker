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
from tracker.compute.segments import SEGMENTS, get_segment_for_sale, get_outpacing_pairs
from tracker.compute.metrics import (
    compute_all_metrics,
    compute_outpacing_metrics,
    save_metrics_to_db,
)
from tracker.compute.equity import compute_affordability_gap
from tracker.notify.telegram import (
    TelegramConfig,
    send_monthly_report,
    send_ingest_failure_alert,
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

    click.echo("PropertyTracker v0.1.0")
    click.echo(f"Database: {ctx.obj['db_path']}")

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
            config = TelegramConfig.from_env()
            send_ingest_failure_alert(config, str(e))
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
    """Send monthly report via Telegram."""
    db = Database(ctx.obj['db_path'])
    db.init_schema()

    try:
        config = load_config(ctx.obj['config_path'])

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

        # Compute outpacing
        outpacing = []
        for proxy_code, target_code in get_outpacing_pairs():
            if proxy_code in metrics and target_code in metrics:
                op = compute_outpacing_metrics(
                    metrics[proxy_code],
                    metrics[target_code],
                )
                outpacing.append(op)

        # Compute affordability gap
        ip_value = metrics.get('revesby_houses')
        ppor_value = metrics.get('wollstonecraft_units')
        target_value = metrics.get(config.get('targets', {}).get('primary', 'lane_cove_houses'))

        if ip_value and ppor_value and target_value and not any(
            m.is_suppressed for m in [ip_value, ppor_value, target_value]
        ):
            affordability = compute_affordability_gap(
                config,
                ip_value.median_price,
                ppor_value.median_price,
                target_value.median_price,
            )
        else:
            click.echo("Warning: Cannot compute affordability - missing or suppressed metrics")
            return

        if dry_run:
            from tracker.notify.telegram import format_monthly_report
            message = format_monthly_report(metrics, outpacing, affordability, period_str)
            click.echo("\n--- DRY RUN ---")
            click.echo(message)
            click.echo("--- END ---\n")
        else:
            telegram_config = TelegramConfig.from_env()
            success = send_monthly_report(
                telegram_config,
                metrics,
                outpacing,
                affordability,
                period_str,
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


if __name__ == '__main__':
    cli()
