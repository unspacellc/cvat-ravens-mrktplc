import { useNavigate, useOutletContext } from "react-router";

import { BatchWizard, Empty, type Me } from "~/features/labeling";

export default function NewBatchRoute() {
  const { me } = useOutletContext<{ me: Me }>();
  const navigate = useNavigate();
  if (!me.roles.some((r) => r === "admin" || r === "creator")) return <Empty title="You cannot create batches" hint="Ask an admin or batch creator." />;
  return <BatchWizard onCreated={(id) => navigate(`/batches/${id}`)} />;
}
