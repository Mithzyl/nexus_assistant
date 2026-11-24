const DEFAULT_DESTINATION = "/today";
const VALIDATION_ORIGIN = "https://assistant.invalid";

/**
 * Accept only a same-origin absolute path, with its query and fragment.
 * @param {unknown} value
 */
export function loginDestination(value) {
  if (typeof value !== "string" || !value.startsWith("/") || /[\x00-\x1f\x7f]/.test(value)) {
    return DEFAULT_DESTINATION;
  }

  const [path] = value.split(/[?#]/, 1);
  if (path.startsWith("//") || /\\|%(?:25)*(?:2f|5c)/i.test(path)) {
    return DEFAULT_DESTINATION;
  }

  const destination = new URL(value, VALIDATION_ORIGIN);
  if (
    destination.origin !== VALIDATION_ORIGIN ||
    destination.pathname.startsWith("//") ||
    ["/login", "/nexus-callback"].includes(destination.pathname.replace(/\/$/, ""))
  ) {
    return DEFAULT_DESTINATION;
  }
  return destination.pathname + destination.search + destination.hash;
}

/**
 * Decode the outer login parameter once, leaving nested URL parameters intact.
 * @param {string} search
 */
export function loginDestinationFromSearch(search) {
  return loginDestination(new URLSearchParams(search).get("next"));
}

/**
 * Encode the complete validated target into the login link.
 * @param {string | null | undefined} target
 */
export function loginHref(target) {
  return "/login?" + new URLSearchParams({ next: loginDestination(target) }).toString();
}
