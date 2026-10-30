/** @param {string} value @returns {URL | null} One DNS HTTPS origin without paths or wildcards. */
export function publicOrigin(value) {
  try {
    const url = new URL(value);
    const host = url.hostname;
    if (url.protocol !== "https:" || url.origin !== value || url.username || url.password ||
        host.length > 253 || !host.includes(".") ||
        !/^[a-z0-9]+(?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9]+(?:[a-z0-9-]*[a-z0-9])?)+$/.test(host) ||
        /^\d+(?:\.\d+)+$/.test(host)) return null;
    return url;
  } catch { return null; }
}
