import click

from aprsd.cli_helper import AliasedGroup
from aprsd.main import cli


@cli.group(cls=AliasedGroup, aliases=['rich'], help="APRSD Extension to create textual rich CLI versions of aprsd commands")
@click.pass_context
def rich(ctx):
    pass
