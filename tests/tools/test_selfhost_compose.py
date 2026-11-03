"""compose.selfhost.yaml, the file a company runs the PMS from (docs/install.md): checks that
read it as text, as Docker Compose would take it."""

from pathlib import Path

COMPOSE = Path(__file__).resolve().parents[2] / "compose.selfhost.yaml"


def service(name: str) -> str:
    """One service's block: its own line and every line indented under it."""
    lines = COMPOSE.read_text().splitlines()
    start = lines.index(f"  {name}:")
    block = [lines[start]]
    for line in lines[start + 1 :]:
        if line.strip() and not line.startswith("    "):
            break
        block.append(line)
    return "\n".join(block)


def test_the_app_trusts_the_proxy_only_because_the_server_alone_reaches_it():
    app = service("app")

    # The proxy names each visitor, so a signature's evidence keeps the signer's address...
    assert 'PMS_TRUSTED_PROXIES: "*"' in app
    # ...which is safe only while the app's port is published on the server's own 127.0.0.1.
    assert '- "127.0.0.1:8000:8000"' in app
    assert "0.0.0.0:8000" not in app and '- "8000:8000"' not in app


def test_the_database_is_backed_up_every_day_into_a_folder_git_ignores():
    backup = service("backup")

    assert 'entrypoint: ["/scripts/backup_daily.sh"]' in backup
    assert 'command: ["/backups"]' in backup
    assert "- ./scripts:/scripts:ro" in backup
    assert "- ./backups:/backups" in backup
    assert "restart: unless-stopped" in backup
    # The same Postgres image as the database, so pg_dump matches its server.
    db_image = service("db").split("image: ")[1].split("\n")[0]
    assert f"image: {db_image}" in backup
    # The backups hold everything the PMS keeps: never committed.
    assert "/backups/" in (COMPOSE.parent / ".gitignore").read_text().splitlines()
