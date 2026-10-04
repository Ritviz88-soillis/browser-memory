// Privacy gates: everything here runs BEFORE any page content is touched.

// Domains never indexed by default: banking, auth, mail, health. The user can
// extend this list in Settings; the server enforces its own blocklist too.
const DEFAULT_BLOCKED = [
  "accounts.google.com",
  "mail.google.com",
  "outlook.live.com",
  "outlook.office.com",
  "web.whatsapp.com",
  "netbanking",
  "onlinesbi.sbi",
  "hdfcbank.com",
  "icicibank.com",
  "axisbank.com",
  "paypal.com",
  "healthcare",
  "patient",
];

// URL path fragments that indicate auth/payment flows on any site.
const BLOCKED_PATH_HINTS = [
  "/login",
  "/signin",
  "/sign-in",
  "/signup",
  "/register",
  "/checkout",
  "/payment",
  "/password",
  "/oauth",
  "/account/",
];

export function urlPassesGates(rawUrl: string, extraBlocked: string[]): boolean {
  let url: URL;
  try {
    url = new URL(rawUrl);
  } catch {
    return false;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return false;

  const host = url.hostname.toLowerCase();
  const blocked = [...DEFAULT_BLOCKED, ...extraBlocked];
  if (blocked.some((b) => host === b || host.endsWith("." + b) || host.includes(b)))
    return false;

  const path = url.pathname.toLowerCase();
  if (BLOCKED_PATH_HINTS.some((h) => path.includes(h))) return false;

  return true;
}
