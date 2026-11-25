import { apiRequest, getJson } from "./api";

export interface ServiceHeartbeat {
  name: string;
  status: string | null;
  detail: string | null;
  lastBeat: string | null;
}

export interface ServiceState {
  service: string;
  label: string;
  description: string;
  pauseWarning: string;
  enabled: boolean;
  updatedBy: string | null;
  updatedAt: string | null;
  heartbeats: ServiceHeartbeat[];
}

export async function fetchServices(signal?: AbortSignal): Promise<ServiceState[]> {
  const data = await getJson<{ services?: unknown }>("/api/settings/services", signal);
  if (!Array.isArray(data.services)) throw new Error("Unexpected service control response");
  return data.services as ServiceState[];
}

export async function setServiceEnabled(service: string, enabled: boolean): Promise<void> {
  await apiRequest(`/api/settings/services/${encodeURIComponent(service)}`, {
    method: "PUT",
    json: { enabled },
  });
}
