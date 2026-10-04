import { defineConfig } from "wxt";

export default defineConfig({
  modules: ["@wxt-dev/module-react"],
  manifest: {
    name: "Browser Memory",
    description:
      "Semantic memory over pages you actually read. Ask your browser what you saw.",
    permissions: [
      "tabs",
      "storage",
      "alarms",
      "webNavigation",
      "scripting",
      "sidePanel",
    ],
    host_permissions: ["http://*/*", "https://*/*"],
    action: { default_title: "Browser Memory" },
  },
});
