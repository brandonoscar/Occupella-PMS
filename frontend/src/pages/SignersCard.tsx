import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import { Card } from "../components/Shell";
import { day } from "../money";
import type { SignerAccount } from "../signers";
import { signerLine, standingTone } from "../signers";
import type { Member } from "../staff";
import { holds } from "../staff";
import { useRecord } from "./useRecord";
import { Said } from "../components/Said";
import { Form } from "../components/Form";

// Who may pay money out of each trust bank account: its broker on file and the signers the broker
// authorized in writing, licensed or bonded. Once an account has its broker on file, the
// database refuses a payout from it by anyone else. An admin names the broker; the broker adds
// and takes away signers.
export function SignersCard({ me, members }: { me: Me; members: Member[] }) {
  const [accounts, setAccounts] = useState<SignerAccount[] | null>(null);
  const [error, setError] = useState("");
  const { said, error: refused, record } = useRecord(refresh);
  const today = day(new Date());

  function refresh() {
    api
      .get<SignerAccount[]>("/api/signers")
      .then(setAccounts)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }
  useEffect(refresh, []);

  return (
    <Card title="Who signs each trust bank account">
      {accounts === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <ul className="flex flex-col divide-y divide-line">
          {accounts.map((account) => (
            <li key={account.bank_account_id} className="flex flex-col gap-2 py-3" aria-label={account.bank_account}>
              <p className="font-medium">{account.bank_account}</p>
              {account.signers.length === 0 ? (
                <p className="text-sm text-ink-subtle">No broker on file: an admin names the broker.</p>
              ) : (
                <ul className="flex flex-col gap-1 text-sm">
                  {account.signers.map((signer) => (
                    <li key={signer.staff_id} className="flex flex-wrap items-center gap-2">
                      <span>{signerLine(signer)}</span>
                      <Badge tone={standingTone(signer.standing)}>{signer.standing}</Badge>
                      {signer.role === "signer" && (
                        <Button variant="secondary"
                          says={`${signer.name} can no longer pay out of this account. Their earlier authorization stays in the history.`}
                          onClick={() =>
                          record("/api/signers", {
                            bank_account_id: account.bank_account_id, staff_id: signer.staff_id,
                            role: "signer", granted: false, authorized_on: today,
                          }, () => `${signer.name} no longer signs ${account.bank_account}.`)}>
                          Take away {signer.name}
                        </Button>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      )}
      {accounts !== null && accounts.length > 0 && (
        <SignerForm accounts={accounts} members={members} admin={holds(me, "admin")} today={today}
          record={record} />
      )}
      <Said>{said}</Said>
      <Notice tone="error">{refused}</Notice>
    </Card>
  );
}

function SignerForm({ accounts, members, admin, today, record }: {
  accounts: SignerAccount[];
  members: Member[];
  admin: boolean;
  today: string;
  record: ReturnType<typeof useRecord>["record"];
}) {
  const [account, setAccount] = useState(accounts[0]?.bank_account_id ?? "");
  const [staff, setStaff] = useState(members[0]?.staff_id ?? "");
  const [role, setRole] = useState<"broker" | "signer">(admin ? "broker" : "signer");
  const [license, setLicense] = useState("");
  const [bond, setBond] = useState("");
  const [expires, setExpires] = useState("");
  const [on, setOn] = useState(today);
  const name = members.find((m) => m.staff_id === staff)?.name ?? "";
  const accountName = accounts.find((a) => a.bank_account_id === account)?.bank_account ?? "";
  return (
    <Form
      className="mt-4 flex flex-wrap items-end gap-3"
      onSubmit={async (event) => {
        event.preventDefault();
        const body = {
          bank_account_id: account, staff_id: staff, role, granted: true,
          license_number: license || null, bond_amount: bond || null, bond_expires_on: expires || null,
          authorized_on: on,
        };
        const done = () =>
          role === "broker" ? `${name} is the broker of ${accountName}.` : `${name} signs ${accountName}.`;
        if (await record("/api/signers", body, done)) {
          setLicense("");
          setBond("");
          setExpires("");
        }
      }}
    >
      <Select label="Trust bank account" id="signer-account" value={account} onChange={(e) => setAccount(e.target.value)}>
        {accounts.map((a) => (
          <option key={a.bank_account_id} value={a.bank_account_id}>{a.bank_account}</option>
        ))}
      </Select>
      <Select label="Staff member" id="signer-staff" value={staff} onChange={(e) => setStaff(e.target.value)}>
        {members.map((m) => (
          <option key={m.staff_id} value={m.staff_id}>{m.name}</option>
        ))}
      </Select>
      <Select label="As" id="signer-role" value={role} onChange={(e) => setRole(e.target.value as "broker" | "signer")}>
        {admin && <option value="broker">the broker</option>}
        <option value="signer">a signer</option>
      </Select>
      <Field label="License number" id="signer-license" maxLength={50} value={license}
        onChange={(e) => setLicense(e.target.value)} />
      <Field label="Or bonded for" id="signer-bond" inputMode="decimal" value={bond}
        onChange={(e) => setBond(e.target.value)} />
      <Field label="Bond runs out" id="signer-bond-expires" type="date" value={expires}
        onChange={(e) => setExpires(e.target.value)} />
      <Field label="Authorized in writing on" id="signer-on" type="date" required value={on}
        onChange={(e) => setOn(e.target.value)} />
      <Button type="submit" says={role === "broker"
        ? "Makes them this account's broker, replacing any on file. Only they and their signers may then pay out of it."
        : "Lets them pay out of this account while licensed or bonded. Nothing is paid now."}>Record</Button>
    </Form>
  );
}
