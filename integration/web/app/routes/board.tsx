import { useOutletContext } from "react-router";

import { Board, type Me } from "~/features/labeling";

export default function BoardRoute() {
  const { me } = useOutletContext<{ me: Me }>();
  return <Board roles={me.roles} />;
}
