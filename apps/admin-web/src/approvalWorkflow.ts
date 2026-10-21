export interface PurchaseApprovalFlow {
  id: string;
  state: string;
  round: number;
  revision_no: number;
  policy_version: string;
  definition: {
    name: string;
    design_version: string;
    steps: { id: string; name: string; role: string; mode: string }[];
  };
  step_index: number;
  can_approve: boolean;
  can_resubmit: boolean;
  decisions: {
    action: string;
    reason: string;
    actor: string;
    at: string;
    step_index: number;
    round: number;
    role: string | null;
  }[];
  previous?: PurchaseApprovalFlow[];
}
