export interface ScopeGrant {
  category: "DRUG" | "DEVICE";
  value: string;
  valid_from: string;
  valid_until: string;
  evidence_ref: string;
  party_id?: string;
  certificate_id?: string;
  certificate_version?: string;
}
export interface AdmissionPayload {
  enabled: boolean;
  own_scopes: ScopeGrant[];
  party_scopes: ScopeGrant[];
}
export interface AdmissionPolicy {
  version: string;
  active_version: string;
  payload: AdmissionPayload;
  active_payload: AdmissionPayload;
  has_draft: boolean;
  editors: string[];
  history: {
    action: string;
    created_at: string;
    detail: { reason: string; after: { version: string } };
  }[];
}
export const emptyAdmission = (): AdmissionPayload => ({
  enabled: false,
  own_scopes: [],
  party_scopes: [],
});
