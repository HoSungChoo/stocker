"""CLI: python -m stocker.api [--host 127.0.0.1] [--port 8000] [--reload]"""

from __future__ import annotations

import typer

cli = typer.Typer(add_completion=False, help="stocker HTTP API (uvicorn)")


@cli.command()
def main(
    host: str = typer.Option("127.0.0.1", help="바인드 주소 (컨테이너에서는 0.0.0.0)"),
    port: int = typer.Option(8000, help="포트"),
    reload: bool = typer.Option(False, help="코드 변경 시 자동 재시작 (개발용)"),
    log_level: str = typer.Option("info", help="uvicorn 로그 레벨"),
) -> None:
    import uvicorn

    uvicorn.run("stocker.api.app:app", host=host, port=port, reload=reload, log_level=log_level)


if __name__ == "__main__":
    cli()
