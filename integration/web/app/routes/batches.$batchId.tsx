import { useParams } from "react-router";

import { BatchDetail } from "~/features/labeling";

export default function BatchRoute() {
  return <BatchDetail batchId={useParams().batchId!} />;
}
