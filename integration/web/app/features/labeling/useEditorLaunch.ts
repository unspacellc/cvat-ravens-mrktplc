import { useMutation } from "@tanstack/react-query";

import { ApiError, useLabeling } from "./api";

const MESSAGES: Record<string, string> = {
  assignment_stale: "This job's assignment is still updating. Try again in a moment.",
  not_assigned: "This job is no longer assigned to you.",
  not_found: "This job is no longer available to you.",
  not_provisioned: "Your editor account is not ready yet. Ask an admin.",
};

export function launchMessage(e: unknown): string {
  if (e instanceof ApiError) return MESSAGES[e.code] ?? (e.status === 401 ? "Your session has expired. Sign in again." : e.message);
  return "Could not open the editor. Check your connection and try again.";
}

/** Server decides where the editor opens (allowlisted CVAT URL); the client only follows it. */
export function useEditorLaunch() {
  const { api, navigate } = useLabeling();
  return useMutation({
    mutationFn: async (jobId: string) => (await api.launch(jobId)).url,
    onSuccess: (url) => navigate(url),
  });
}
