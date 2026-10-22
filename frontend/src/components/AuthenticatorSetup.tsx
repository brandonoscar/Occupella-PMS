import qrcode from "qrcode-generator";

// An authenticator app reads the setup link as a QR code; the key is shown too, to type in.
export function qrDataUrl(uri: string): string {
  const code = qrcode(0, "M");
  code.addData(uri);
  code.make();
  return code.createDataURL(4, 8);
}

export function secretOf(uri: string): string {
  return new URL(uri).searchParams.get("secret") ?? "";
}

// The authenticator to set up: staff's from their invite, an owner's or a tenant's two-step
// sign-in from the portal, the sign-in page or their invite. The QR code, and the key under it.
export function AuthenticatorSetup({ uri }: { uri: string }) {
  return (
    <>
      <img src={qrDataUrl(uri)} alt="Authenticator setup code" className="self-start rounded-sm" />
      <code className="font-mono text-sm break-all text-ink-muted">{secretOf(uri)}</code>
    </>
  );
}
