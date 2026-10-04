import { useEffect, useState } from "react";
import { api } from "@/utils/api";
import { getSettings, saveSettings, Settings } from "@/utils/settings";

export default function SettingsView() {
  const [s, setS] = useState<Settings | null>(null);
  const [status, setStatus] = useState("");

  useEffect(() => {
    void getSettings().then(setS);
  }, []);

  if (!s) return null;

  async function save() {
    const cleaned = { ...s!, token: s!.token.trim(), serverUrl: s!.serverUrl.trim() };
    setS(cleaned);
    await saveSettings(cleaned);
    // authenticated check: /health passes without a token and would
    // green-light a bad one
    try {
      await api.pages();
      setStatus("saved — server reachable, token valid ✓");
    } catch (e) {
      setStatus(
        String(e).includes("401")
          ? "saved, but the token is WRONG — re-copy it exactly"
          : "saved, but server is not reachable",
      );
    }
  }

  return (
    <div className="settings">
      <label>
        Server URL
        <input
          value={s.serverUrl}
          onChange={(e) => setS({ ...s, serverUrl: e.target.value })}
        />
      </label>
      <label>
        Device token
        <input
          type="password"
          value={s.token}
          onChange={(e) => setS({ ...s, token: e.target.value })}
          placeholder="from scripts/new_device.py"
        />
      </label>
      <label>
        Extra blocked domains (one per line)
        <textarea
          rows={4}
          value={s.extraBlocked.join("\n")}
          onChange={(e) =>
            setS({
              ...s,
              extraBlocked: e.target.value
                .split("\n")
                .map((d) => d.trim().toLowerCase())
                .filter(Boolean),
            })
          }
        />
      </label>
      <label className="row">
        <input
          type="checkbox"
          checked={s.paused}
          onChange={(e) => setS({ ...s, paused: e.target.checked })}
        />
        Pause indexing
      </label>
      <button onClick={save}>Save</button>
      {status && <div className="hint">{status}</div>}
    </div>
  );
}
