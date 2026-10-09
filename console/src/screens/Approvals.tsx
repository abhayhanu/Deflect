import { api } from "../api";
import { ApprovalCard } from "../components/ApprovalCard";
import { Empty, Problem } from "../components/bits";
import { rupees } from "../format";
import { go, useLoad } from "../hooks";
import type { Config } from "../types";

interface Props {
  config: Config;
  approver: string;
  onApprover: (name: string) => void;
}

export function Approvals({ config, approver, onApprover }: Props) {
  const { data, error, loading } = useLoad(api.approvals, [], 5000);
  return (
    <section>
      <h1>Approval queue</h1>
      <p className="lede">
        A refund above {rupees(config.auto_approve_refund_inr)} is never issued by the agent alone. The run stops, is saved, and waits here
        until a person decides.
      </p>
      <Problem error={error} />
      {!loading && data?.length === 0 && <Empty>Nothing is waiting for a person right now.</Empty>}
      {data?.map((approval) => (
        <ApprovalCard
          key={approval.approval_id}
          approval={approval}
          approver={approver}
          ceiling={config.auto_approve_refund_inr}
          onApprover={onApprover}
          onDecided={(ticketId) => go(`/tickets/${encodeURIComponent(ticketId)}`)}
        />
      ))}
    </section>
  );
}
