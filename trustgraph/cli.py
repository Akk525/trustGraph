from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Load .env before any Gemini/env-var logic runs.
# override=False means existing shell exports take precedence over .env values.
load_dotenv(override=False)

from trustgraph.graph import run_workflow
from trustgraph.models import RiskLevel

app = typer.Typer(
    name="trustgraph",
    help="Detect CrossCurve-style trust-boundary vulnerabilities in Solidity contracts.",
    no_args_is_help=True,
)
console = Console()


@app.callback()
def _root() -> None:
    """TrustGraph — Solidity trust-boundary vulnerability scanner."""


class ReportFormat(str, Enum):
    markdown = "markdown"
    json = "json"
    both = "both"


@app.command()
def audit(
    path: Annotated[
        str,
        typer.Argument(help="Path to a .sol file or directory of Solidity source files."),
    ],
    generate_test: Annotated[
        bool,
        typer.Option("--generate-test/--no-generate-test", help="Generate Foundry exploit PoC tests."),
    ] = True,
    run_foundry: Annotated[
        bool,
        typer.Option("--run-foundry/--no-run-foundry", help="Execute `forge test` after generating tests."),
    ] = False,
    report_format: Annotated[
        ReportFormat,
        typer.Option("--report-format", help="Output format for the report."),
    ] = ReportFormat.markdown,
    output_dir: Annotated[
        str,
        typer.Option("--output-dir", help="Directory to write reports and generated tests."),
    ] = "trustgraph-output",
    no_ai: Annotated[
        bool,
        typer.Option("--no-ai", help="Disable LLM calls; use deterministic heuristics only."),
    ] = False,
) -> None:
    """Audit Solidity contracts for unguarded trust-boundary violations."""

    resolved = Path(path).resolve()
    if not resolved.exists():
        console.print(f"[red]Error:[/red] path does not exist: {path}")
        raise typer.Exit(1)

    console.print(
        Panel.fit(
            f"[bold cyan]TrustGraph[/bold cyan] — trust boundary auditor\n"
            f"Scanning: [yellow]{resolved}[/yellow]",
            border_style="cyan",
        )
    )

    config = {
        "path": str(resolved),
        "generate_test": generate_test,
        "run_foundry": run_foundry,
        "report_format": report_format.value,
        "output_dir": str(Path(output_dir).resolve()),
        "no_ai": no_ai,
    }

    with console.status("[cyan]Running TrustGraph workflow…[/cyan]", spinner="dots"):
        try:
            state = run_workflow(config)
        except Exception as exc:
            console.print(f"[red]Workflow error:[/red] {exc}")
            raise typer.Exit(1) from exc

    _print_summary(state)

    if state.get("errors"):
        console.print("\n[yellow]Warnings / Errors:[/yellow]")
        for err in state["errors"]:
            console.print(f"  [dim]{err}[/dim]")

    for rp in state.get("report_paths", []):
        console.print(f"\n[green]Report saved:[/green] {rp}")


if __name__ == "__main__":
    app()


def _print_summary(state: dict) -> None:
    findings = state.get("findings", [])
    if not findings:
        console.print("\n[green]No significant findings.[/green]")
        return

    table = Table(title="Findings", show_lines=True)
    table.add_column("Sev.", style="bold", width=12)
    table.add_column("File", overflow="fold")
    table.add_column("Function", style="cyan")
    table.add_column("Category")
    table.add_column("Exploit Test")

    for fd in findings:
        level = fd.get("risk_level", "?")
        colour = {
            RiskLevel.CRITICAL.value: "red",
            RiskLevel.MEDIUM.value: "yellow",
            RiskLevel.INFORMATIONAL.value: "blue",
        }.get(level, "white")

        sr = fd.get("scan_result", {})
        fi = sr.get("function_info", {})
        ta = fd.get("trust_assumption", {})
        exploit = Path(fd["exploit_path"]).name if fd.get("exploit_path") else "—"
        file_str = f"{Path(fi.get('file','?')).name}:{fi.get('line','?')}"

        table.add_row(
            f"[{colour}]{level}[/{colour}]",
            file_str,
            fi.get("name", "?"),
            ta.get("category", "?"),
            exploit,
        )

    console.print()
    console.print(table)

    critical = sum(1 for f in findings if f.get("risk_level") == RiskLevel.CRITICAL.value)
    medium = sum(1 for f in findings if f.get("risk_level") == RiskLevel.MEDIUM.value)
    console.print(
        f"\nSummary: [red]{critical} Critical[/red]  [yellow]{medium} Medium[/yellow]  "
        f"[dim]({len(findings)} total findings)[/dim]"
    )
