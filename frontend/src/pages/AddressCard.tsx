import { useState } from "react";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Card } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import type { Location, PropertyAddress } from "../rentals";
import type { Recorder } from "./useRecord";
import { useRecord } from "./useRecord";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

// A property's street address and where it is on a map (migration 0109). Find on map asks the
// server's geocoder, sending the property's address alone; the point opens in OpenStreetMap.
export function AddressCard({ propertyId, address, location, canLocate, onChange }: {
  propertyId: string;
  address: PropertyAddress | null;
  location: Location | null;
  canLocate: boolean;
  onChange: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const { said, error, record } = useRecord(onChange);
  const recordThenClose: Recorder = async (path, body, done) => {
    const recorded = await record(path, body, done);
    if (recorded) setEditing(false);
    return recorded;
  };

  return (
    <Card title="Address">
      <div className="flex flex-col gap-3">
        <Said>{said}</Said>
        {address === null ? (
          <p className="text-base text-ink-muted">No address yet.</p>
        ) : (
          <address className="text-base not-italic" data-testid="property-address">
            <span className="block">{address.line_1}</span>
            {address.line_2 && <span className="block">{address.line_2}</span>}
            <span className="block">{`${address.city}, ${address.state} ${address.zip}`}</span>
          </address>
        )}
        {location !== null && (
          <p className="text-sm text-ink-muted" data-testid="property-location">
            {`On the map at ${location.latitude}, ${location.longitude}, as the geocoder matched it: ${location.matched}. `}
            <a className={LINK} href={location.map_url} target="_blank" rel="noopener noreferrer">
              Open in OpenStreetMap
            </a>
          </p>
        )}
        {address !== null && location === null && canLocate && (
          <p className="text-sm text-ink-muted">Not found on a map yet.</p>
        )}
        {!editing && <Notice tone="error">{error}</Notice>}
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={() => setEditing(true)}>{address ? "Edit address" : "Add address"}</Button>
          {address !== null && canLocate && (
            <Button variant="secondary" onClick={() => record(`/api/properties/${propertyId}/locate`, {}, () => "Found on the map.")}>
              Find on map
            </Button>
          )}
        </div>
      </div>
      {editing && (
        <SidePanel title={address ? "Edit address" : "Add address"} onClose={() => setEditing(false)}>
          <AddressForm propertyId={propertyId} address={address} record={recordThenClose} />
          <Notice tone="error">{error}</Notice>
        </SidePanel>
      )}
    </Card>
  );
}

function AddressForm({ propertyId, address, record }: {
  propertyId: string; address: PropertyAddress | null; record: Recorder;
}) {
  const [line1, setLine1] = useState(address?.line_1 ?? "");
  const [line2, setLine2] = useState(address?.line_2 ?? "");
  const [city, setCity] = useState(address?.city ?? "");
  const [state, setState] = useState(address?.state ?? "");
  const [zip, setZip] = useState(address?.zip ?? "");
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      const body = { line_1: line1, line_2: line2.trim() || null, city, state, zip };
      return record(`/api/properties/${propertyId}/address`, body, () => "Address saved.");
    }}>
      <p className="text-sm text-ink-muted">The home's street address, whole. A new address is found on a map again.</p>
      <Field label="Street" id="address-line-1" required maxLength={200} autoComplete="address-line1" value={line1}
        onChange={(e) => setLine1(e.target.value)} />
      <Field label="Building or unit (optional)" id="address-line-2" maxLength={200} autoComplete="address-line2"
        value={line2} onChange={(e) => setLine2(e.target.value)} />
      <Field label="City" id="address-city" required maxLength={100} autoComplete="address-level2" value={city}
        onChange={(e) => setCity(e.target.value)} />
      <Field label="State (two letters)" id="address-state" required maxLength={2} autoComplete="address-level1"
        value={state} onChange={(e) => setState(e.target.value)} />
      <Field label="ZIP code" id="address-zip" required maxLength={10} inputMode="numeric" autoComplete="postal-code"
        value={zip} onChange={(e) => setZip(e.target.value)} />
      <div><Button type="submit">Save address</Button></div>
    </Form>
  );
}
