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
