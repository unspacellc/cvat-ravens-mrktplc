import { useOutletContext, useParams } from "react-router";

import { BatchDetail, type Me } from "~/features/labeling";

export default function BatchRoute() {
  const { me } = useOutletContext<{ me: Me }>();
  return <BatchDetail batchId={useParams().batchId!} roles={me.roles} />;
}
