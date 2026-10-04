// User settings, persisted in chrome.storage.local.

export interface Settings {
  serverUrl: string;
  token: string;
  extraBlocked: string[]; // user-added blocked domains
  paused: boolean;
}

const DEFAULTS: Settings = {
  serverUrl: "http://localhost:8000",
  token: "",
  extraBlocked: [],
  paused: false,
};

export async function getSettings(): Promise<Settings> {
  const stored = await chrome.storage.local.get("settings");
  return { ...DEFAULTS, ...(stored.settings ?? {}) };
}

export async function saveSettings(patch: Partial<Settings>): Promise<Settings> {
  const next = { ...(await getSettings()), ...patch };
  await chrome.storage.local.set({ settings: next });
  return next;
}
