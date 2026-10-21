import type { BadgeTone } from "./components/Badge";

// The PMS's own signing page (ADR 0008; src/pms/api/signing.py): what a signer's link shows.

// Where the link stands: open to sign, signed, asked to sign on paper, run out, or its offer
// withdrawn.
export type SigningStatus = "open" | "signed" | "paper" | "expired" | "void";

export type SigningView = {
  company: string;
  document_title: string;
  home: string;
  starts_on: string;
  ends_on: string | null;
  monthly_rent: string;
  signer: string;
  role: "tenant" | "guarantor";
  document_sha256: string;
  disclosure: string;
  disclosure_sha256: string;
  status: SigningStatus;
  consented: boolean;
  opened: boolean;
  signed_at: string | null;
  signers: { name: string; role: "tenant" | "guarantor"; signed: boolean }[];
};

// Where a signer stands, as staff see it on the draft lease's page.
export type Progress = "waiting" | "agreed" | "opened" | "paper" | "signed";

export const PROGRESS: Record<Progress, { word: string; tone: BadgeTone }> = {
  waiting: { word: "Not started", tone: "neutral" },
  agreed: { word: "Agreed to e-sign", tone: "info" },
  opened: { word: "Opened the lease", tone: "info" },
  paper: { word: "Asked for paper", tone: "caution" },
  signed: { word: "Signed", tone: "positive" },
};
