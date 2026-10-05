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
    // an authenticated call: it pairs with the server automatically if
    // there is no token yet, or the saved one is no longer accepted
    try {
      await api.pages();
      setStatus("saved — connected to the server ✓");
    } catch (e) {
      setStatus(
        String(e).includes("403")
          ? "saved, but this server is paired with a different extension"
          : "saved, but the server is not reachable — is it running?",
      );
    }
    setS(await getSettings()); // show the token pairing just stored
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
        Access token (set automatically)
        <input
          type="password"
          value={s.token}
          onChange={(e) => setS({ ...s, token: e.target.value })}
          placeholder="pairs with the server on first use"
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
