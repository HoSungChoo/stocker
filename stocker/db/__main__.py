import typer

from stocker.db import get_engine, init_db

app = typer.Typer(help="stocker DB 관리", no_args_is_help=True)


@app.callback()
def _main() -> None:
    """stocker DB 관리."""


@app.command()
def init() -> None:
    """스키마 생성 (멱등)."""
    init_db(get_engine())
    typer.echo("schema initialized")


if __name__ == "__main__":
    app()
