import click

@click.group()
def cli():
    """PropertyTracker: Sydney property equity and affordability monitor."""
    pass

@cli.command()
def status():
    """Show system status."""
    click.echo("PropertyTracker v0.1.0")
    click.echo("Status: Not yet configured")

if __name__ == '__main__':
    cli()
