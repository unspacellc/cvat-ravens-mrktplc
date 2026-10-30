import { useOutletContext } from "react-router";

import { Batches, type Me } from "~/features/labeling";

export default function BatchesRoute() {
  const { me } = useOutletContext<{ me: Me }>();
  return <Batches roles={me.roles} />;
}
