// A phone number as a tel: link (ADR 0010, rule 6): the phone dials it, the PMS calls no one.
export function PhoneLink({ phone }: { phone: string }) {
  return (
    <a className="font-medium text-iris hover:text-iris-hover hover:underline" href={`tel:${phone.replace(/[^+0-9]/g, "")}`}>
      {phone}
    </a>
  );
}
