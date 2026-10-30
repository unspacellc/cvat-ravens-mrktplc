import { index, layout, route, type RouteConfig } from "@react-router/dev/routes";

export default [
  route("login", "routes/login.tsx"),
  route("logout", "routes/logout.tsx"),
  route("bff/labeling/*", "routes/bff.labeling.$.tsx"),
  layout("routes/app.tsx", [
    index("routes/_index.tsx"),
    route("my-jobs", "routes/my-jobs.tsx"),
    route("board", "routes/board.tsx"),
    route("batches", "routes/batches.tsx"),
    route("batches/new", "routes/batches.new.tsx"),
    route("batches/:batchId", "routes/batches.$batchId.tsx"),
    route("metrics", "routes/metrics.tsx"),
    route("select-tenant", "routes/select-tenant.tsx"),
  ]),
] satisfies RouteConfig;
