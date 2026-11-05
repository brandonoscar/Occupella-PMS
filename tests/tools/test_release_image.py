"""The published image (release-image.yml): only a release tag publishes it, it holds no secret
(CLAUDE.md, rule 5), it is scanned as selfhost.yml scans the nightly build before it is pushed,
and it is signed keyless with Sigstore, with its SBOM attached, after it is pushed."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"
RELEASE = WORKFLOWS / "release-image.yml"


def workflow() -> str:
    return RELEASE.read_text()


def go_installs(text: str) -> list[str]:
    return sorted(re.findall(r"go install (\S+@v[\d.]+)", text))


def test_only_a_release_tag_publishes_it():
    text = workflow()
    on = text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert re.fullmatch(r'\s*push:\n\s*tags:\n\s*- "v\*\.\*\.\*"\n?', on), on
    for trigger in ("pull_request", "pull_request_target", "workflow_dispatch", "schedule"):
        assert trigger not in text, trigger


def test_it_holds_no_secret_and_only_the_permissions_it_needs():
    text = workflow()
    assert "secrets." not in text
    assert re.search(r"^permissions:\n  contents: read\n", text, re.M)
    job = sorted(re.findall(r"^      (\w[\w-]*): (read|write)", text, re.M))
    assert job == [("contents", "read"), ("id-token", "write"), ("packages", "write")]
    # The run's own token reaches docker on stdin, never on a command line.
    assert "REGISTRY_TOKEN: ${{ github.token }}" in text
    assert re.search(r'printf \'%s\' "\$REGISTRY_TOKEN" \| docker login .* --password-stdin', text)
    assert "--password " not in text and "-p " not in text


def test_every_action_is_pinned_to_a_commit():
    uses = re.findall(r"uses: (\S+)", workflow())
    assert uses, "no actions"
    for action in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", action), action


def test_it_scans_with_selfhost_ymls_tools_before_it_pushes():
    text = workflow()
    selfhost = (WORKFLOWS / "selfhost.yml").read_text()
    scanners = [tool for tool in go_installs(text) if "cosign" not in tool]
    assert scanners == go_installs(selfhost)
    assert "--only-fixed --fail-on high" in text
    assert text.index("grype\" \"sbom:") < text.index("docker push")


def test_it_signs_and_attests_what_it_pushed_keyless():
    text = workflow()
    (cosign,) = [tool for tool in go_installs(text) if "cosign" in tool]
    assert cosign.startswith("github.com/sigstore/cosign/v2/cmd/cosign@v2.")
    push = text.index("docker push")
    sign = text.index('"$cosign" sign --yes "$IMAGE_REF"')
    attest = text.index('"$cosign" attest --yes --type cyclonedx --predicate')
    assert push < sign < attest
    # Keyless: no key file, the run's OIDC identity instead.
    assert "--key" not in text
    assert "COSIGN_PRIVATE_KEY" not in text


def test_the_release_doc_says_how_to_check_the_signature():
    doc = (ROOT / "docs/release.md").read_text()
    assert "release-image.yml" in doc
    assert "cosign verify" in doc
    assert "--certificate-oidc-issuer https://token.actions.githubusercontent.com" in doc
