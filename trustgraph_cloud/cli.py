from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from trustgraph_cloud import cli_client as api
from trustgraph_cloud import cli_config as cfg

app = typer.Typer(
    name="trustgraph-cloud",
    help="TrustGraph Cloud CLI — submit and manage hosted Solidity audits.",
    no_args_is_help=True,
)
console = Console()

api_key_app = typer.Typer(
    name="api-key",
    help="Manage API keys.",
    no_args_is_help=True,
)
app.add_typer(api_key_app, name="api-key")

_IGNORED_DIRS = {".git", "node_modules", "out", "cache", ".venv", "__pycache__"}
_IGNORED_SUFFIXES = {".pyc"}
_IGNORED_NAMES = {".DS_Store"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _zip_folder(path: Path) -> bytes:
    """Zip a directory, excluding build artifacts and VCS folders."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(path.rglob("*")):
            if f.is_dir():
                continue
            rel = f.relative_to(path)
            # Exclude if any parent directory component is in the ignore set
            if set(rel.parts[:-1]) & _IGNORED_DIRS:
                continue
            if f.suffix in _IGNORED_SUFFIXES or f.name in _IGNORED_NAMES:
                continue
            zf.write(f, rel)
    return buf.getvalue()


def _require_auth() -> tuple[str, str]:
    api_url = cfg.get_api_url()
    token = cfg.get_token()
    if not api_url:
        console.print(
            "[red]No API URL configured.[/red] "
            "Run: [cyan]trustgraph-cloud login[/cyan]  "
            "or set [cyan]TRUSTGRAPH_API_URL[/cyan]."
        )
        raise typer.Exit(1)
    if not token:
        console.print(
            "[red]Not authenticated.[/red] "
            "Run: [cyan]trustgraph-cloud login[/cyan]  "
            "or set [cyan]TRUSTGRAPH_API_KEY[/cyan] / [cyan]TRUSTGRAPH_API_TOKEN[/cyan]."
        )
        raise typer.Exit(1)
    return api_url, token


def _status_colour(status: str) -> str:
    return {
        "queued": "yellow",
        "running": "cyan",
        "succeeded": "green",
        "failed": "red",
    }.get(status, "white")


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------

@app.command()
def login(
    api_url: Annotated[
        Optional[str],
        typer.Option("--api-url", envvar="TRUSTGRAPH_API_URL", help="API base URL."),
    ] = None,
    email: Annotated[
        Optional[str],
        typer.Option("--email", help="Account email."),
    ] = None,
    password: Annotated[
        Optional[str],
        typer.Option("--password", help="Account password."),
    ] = None,
) -> None:
    """Authenticate and save credentials to ~/.trustgraph/config.json."""
    if not api_url:
        api_url = typer.prompt(
            "API URL (e.g. http://trustgraph-api-xxxx.us-east-1.elb.amazonaws.com)"
        )
    if not email:
        email = typer.prompt("Email")
    if not password:
        password = typer.prompt("Password", hide_input=True)

    try:
        result = api.login(api_url, email, password)
    except api.CloudAPIError as e:
        console.print(f"[red]Login failed:[/red] {e.detail}")
        raise typer.Exit(1)

    cfg.save_login(api_url, result["access_token"])
    console.print(
        f"[green]Logged in.[/green] "
        f"Token expires in {result.get('expires_in', '?')}s. "
        f"Config saved to [cyan]~/.trustgraph/config.json[/cyan]"
    )


# ---------------------------------------------------------------------------
# api-key create
# ---------------------------------------------------------------------------

@api_key_app.command("create")
def api_key_create(
    name: Annotated[str, typer.Option("--name", help="Human-readable label for this key.")],
) -> None:
    """Create a new API key. The raw key is shown once — save it immediately."""
    api_url, token = _require_auth()
    try:
        result = api.create_api_key(api_url, token, name)
    except api.CloudAPIError as e:
        console.print(f"[red]Error:[/red] {e.detail}")
        raise typer.Exit(1)

    console.print(f"\n[bold green]API key created.[/bold green]")
    console.print(f"[dim]Name:[/dim]   {result['name']}")
    console.print(f"[dim]Prefix:[/dim] {result['key_prefix']}")
    console.print(f"\n[bold yellow]Raw key (shown once — save it now):[/bold yellow]")
    console.print(f"[cyan]{result['raw_key']}[/cyan]\n")


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------

@app.command()
def audit(
    path: Annotated[
        str,
        typer.Argument(help="Local directory or .sol file to audit."),
    ],
    wait: Annotated[
        bool,
        typer.Option("--wait/--no-wait", help="Poll until job completes."),
    ] = False,
    poll_interval: Annotated[
        int,
        typer.Option("--poll-interval", hidden=True, help="Seconds between status polls."),
    ] = 5,
) -> None:
    """Zip a local Solidity project, upload to S3, and submit a cloud audit."""
    src = Path(path).resolve()
    if not src.exists():
        console.print(f"[red]Error:[/red] path does not exist: {path}")
        raise typer.Exit(1)

    api_url, token = _require_auth()

    with console.status("[cyan]Zipping source...[/cyan]"):
        if src.is_file():
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.write(src, src.name)
            zip_bytes = buf.getvalue()
            filename = src.stem + ".zip"
        else:
            zip_bytes = _zip_folder(src)
            filename = src.name + ".zip"

    console.print(f"[dim]Zipped {len(zip_bytes):,} bytes ({filename})[/dim]")

    with console.status("[cyan]Requesting upload URL...[/cyan]"):
        try:
            presign = api.presigned_upload(api_url, token, filename)
        except api.CloudAPIError as e:
            console.print(f"[red]Presign error:[/red] {e.detail}")
            raise typer.Exit(1)

    with console.status("[cyan]Uploading to S3...[/cyan]"):
        try:
            api.upload_to_s3(presign["upload_url"], zip_bytes)
        except api.CloudAPIError as e:
            console.print(f"[red]Upload failed:[/red] {e.detail}")
            raise typer.Exit(1)

    console.print("[dim]Upload complete.[/dim]")

    with console.status("[cyan]Submitting audit...[/cyan]"):
        try:
            job = api.submit_audit(api_url, token, presign["input_s3_key"])
        except api.CloudAPIError as e:
            console.print(f"[red]Submit error:[/red] {e.detail}")
            raise typer.Exit(1)

    console.print(f"[green]Job submitted:[/green] {job['job_id']}")

    if not wait:
        console.print(
            f"\nPoll: [cyan]trustgraph-cloud status {job['job_id']}[/cyan]"
        )
        console.print(
            f"Download: [cyan]trustgraph-cloud download {job['job_id']}[/cyan]"
        )
        return

    _poll_until_done(api_url, token, job["job_id"], poll_interval)


def _poll_until_done(api_url: str, token: str, job_id: str, interval: int) -> None:
    terminal = {"succeeded", "failed"}
    with console.status("[cyan]Waiting...[/cyan]") as spin:
        while True:
            try:
                job = api.get_job(api_url, token, job_id)
            except api.CloudAPIError as e:
                console.print(f"[red]Poll error:[/red] {e.detail}")
                raise typer.Exit(1)

            s = job["status"]
            spin.update(f"[cyan]{s}...[/cyan]")
            if s in terminal:
                break
            time.sleep(interval)

    colour = _status_colour(job["status"])
    console.print(f"Job [{colour}]{job['status']}[/{colour}]")

    if job["status"] == "failed":
        console.print(f"[red]Error:[/red] {job.get('error_message', 'unknown')}")
        raise typer.Exit(1)

    fs = job.get("findings_summary")
    if fs:
        console.print(
            f"Findings: [red]{fs.get('critical', 0)} critical[/red]  "
            f"[yellow]{fs.get('medium', 0)} medium[/yellow]  "
            f"[dim]{fs.get('total', 0)} total[/dim]"
        )
    console.print(
        f"\nDownload: [cyan]trustgraph-cloud download {job['job_id']}[/cyan]"
    )


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------

def _print_jobs_table(jobs_list: list[dict]) -> None:
    table = Table(show_lines=False)
    table.add_column("Job ID", style="cyan", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Input type")
    table.add_column("Created at")
    table.add_column("Artifacts", justify="right")
    for j in jobs_list:
        s = j["status"]
        c = _status_colour(s)
        table.add_row(
            j["job_id"],
            f"[{c}]{s}[/{c}]",
            j.get("input_type", "—"),
            j.get("created_at", "—")[:19].replace("T", " "),
            str(j.get("artifact_count", 0)),
        )
    console.print(table)


@app.command()
def jobs(
    limit: Annotated[
        int,
        typer.Option("--limit", help="Max results per page."),
    ] = 20,
    cursor: Annotated[
        Optional[str],
        typer.Option("--cursor", help="Pagination cursor from a previous response."),
    ] = None,
    all_pages: Annotated[
        bool,
        typer.Option("--all", help="Follow all cursor pages and print every job."),
    ] = False,
) -> None:
    """List your recent audit jobs, newest first."""
    api_url, token = _require_auth()

    if all_pages:
        all_jobs: list[dict] = []
        current_cursor: Optional[str] = cursor
        while True:
            try:
                result = api.list_jobs(api_url, token, limit=limit, cursor=current_cursor)
            except api.CloudAPIError as e:
                console.print(f"[red]Error:[/red] {e.detail}")
                raise typer.Exit(1)
            all_jobs.extend(result.get("jobs", []))
            current_cursor = result.get("next_cursor")
            if not current_cursor:
                break
        if not all_jobs:
            console.print("[dim]No jobs found.[/dim]")
            return
        _print_jobs_table(all_jobs)
        return

    try:
        result = api.list_jobs(api_url, token, limit=limit, cursor=cursor)
    except api.CloudAPIError as e:
        console.print(f"[red]Error:[/red] {e.detail}")
        raise typer.Exit(1)

    jobs_list = result.get("jobs", [])
    if not jobs_list:
        console.print("[dim]No jobs found.[/dim]")
        return

    _print_jobs_table(jobs_list)

    nc = result.get("next_cursor")
    if nc:
        console.print(
            f"[dim]More results available. Use: --cursor {nc}[/dim]"
        )
    elif result.get("has_more"):
        # Offset-mode fallback (backward compat with older API responses)
        shown = result.get("offset", 0) + len(jobs_list)
        console.print(
            f"[dim]Showing {shown}/{result.get('total', '?')}. "
            f"Use --offset {shown} for the next page.[/dim]"
        )


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

@app.command()
def status(
    job_id: Annotated[str, typer.Argument(help="Job ID to query.")],
) -> None:
    """Show status and metadata for a single audit job."""
    api_url, token = _require_auth()
    try:
        job = api.get_job(api_url, token, job_id)
    except api.CloudAPIError as e:
        if e.status_code == 404:
            console.print(f"[red]Job not found:[/red] {job_id}")
        else:
            console.print(f"[red]Error:[/red] {e.detail}")
        raise typer.Exit(1)

    s = job["status"]
    colour = _status_colour(s)
    console.print(f"Job ID:   [cyan]{job['job_id']}[/cyan]")
    console.print(f"Status:   [{colour}]{s}[/{colour}]")
    console.print(f"Created:  {job.get('created_at', '?')[:19].replace('T', ' ')}")
    if job.get("started_at"):
        console.print(f"Started:  {job['started_at'][:19].replace('T', ' ')}")
    if job.get("completed_at"):
        console.print(f"Done:     {job['completed_at'][:19].replace('T', ' ')}")
    if job.get("error_message"):
        console.print(f"[red]Error:[/red]   {job['error_message']}")
    fs = job.get("findings_summary")
    if fs:
        console.print(
            f"Findings: [red]{fs.get('critical', 0)} critical[/red]  "
            f"[yellow]{fs.get('medium', 0)} medium[/yellow]  "
            f"[dim]{fs.get('total', 0)} total[/dim]"
        )
    names = job.get("artifact_names", [])
    if names:
        console.print(f"Artifacts: {', '.join(names)}")


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------

@app.command()
def download(
    job_id: Annotated[str, typer.Argument(help="Job ID to download artifacts for.")],
    out: Annotated[
        str,
        typer.Option("--out", help="Output directory."),
    ] = "./trustgraph-report",
) -> None:
    """Download all artifacts for a completed audit job."""
    api_url, token = _require_auth()

    try:
        result = api.list_artifacts(api_url, token, job_id)
    except api.CloudAPIError as e:
        if e.status_code == 404:
            console.print(f"[red]Job not found:[/red] {job_id}")
        else:
            console.print(f"[red]Error:[/red] {e.detail}")
        raise typer.Exit(1)

    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)

    artifacts = result.get("artifacts", [])
    if not artifacts:
        console.print("[yellow]No artifacts available for this job.[/yellow]")
        return

    saved = 0
    for art in artifacts:
        presigned = art.get("presigned_url")
        local_path = art.get("path")

        with console.status(f"[cyan]Downloading {art['name']}...[/cyan]"):
            try:
                if presigned:
                    content = api.download_artifact(presigned)
                elif local_path:
                    content = Path(local_path).read_bytes()
                else:
                    console.print(f"[yellow]Skipping {art['name']} — no download URL.[/yellow]")
                    continue
            except (api.CloudAPIError, OSError) as e:
                console.print(f"[red]Failed to download {art['name']}:[/red] {e}")
                continue

        dest = out_dir / art["name"]
        dest.write_bytes(content)
        console.print(f"[green]Saved:[/green] {dest}  ({len(content):,} bytes)")
        saved += 1

    console.print(
        f"\n[green]Done.[/green] {saved}/{len(artifacts)} artifact(s) saved to [cyan]{out_dir}[/cyan]"
    )


if __name__ == "__main__":
    app()
