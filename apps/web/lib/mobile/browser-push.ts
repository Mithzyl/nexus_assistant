/** Browser subscription lifecycle; authentication stays in the same-origin httpOnly session. */
const SUBSCRIPTION_ID = "assistant-browser-push-subscription";

/** Decode the public P-256 application key required by PushManager. */
function applicationKey(value: string): Uint8Array {
  return Uint8Array.from(atob(value.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - value.length % 4) % 4)), (c) => c.charCodeAt(0));
}

/** Fail explicitly on network/configuration errors instead of displaying an enabled state. */
async function call(path: string, method = "GET", body?: unknown) {
  const response = await fetch(`/api/pa/browser-notifications/${path}`, {
    method, cache: "no-store", headers: { "Content-Type": "application/json" },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  if (!response.ok) throw new Error("浏览器通知设置未能保存，请稍后重试。");
  return response.json();
}

/** Return actual local permission and subscription state without requesting permission. */
export async function browserPushStatus() {
  if (!window.isSecureContext || !("Notification" in window) || !("serviceWorker" in navigator) || !("PushManager" in window)) {
    return { supported: false, enabled: false, permission: "unsupported", configured: false };
  }
  const config = await call("config");
  const registration = await navigator.serviceWorker.getRegistration("/");
  const subscription = await registration?.pushManager.getSubscription();
  return { supported: true, enabled: Boolean(subscription && config.subscription_ids.includes(localStorage.getItem(SUBSCRIPTION_ID))) && Notification.permission === "granted",
    permission: Notification.permission, configured: Boolean(config.configured) };
}

/** Ask on an explicit user click, subscribe to real push, and bind it to the signed-in account. */
export async function enableBrowserPush(onProgress?: (message: string) => void) {
  if (!("Notification" in window) || !("serviceWorker" in navigator) || !("PushManager" in window)) throw new Error("当前浏览器不支持后台推送。");
  onProgress?.("请在浏览器弹窗中允许通知。");
  const permission = await Notification.requestPermission();
  if (permission !== "granted") throw new Error("请在浏览器的网站设置中允许通知。");
  const config = await call("config");
  if (!config.configured || !config.public_key) throw new Error("服务端尚未配置浏览器推送。");
  onProgress?.("正在连接浏览器推送服务…");
  await navigator.serviceWorker.register("/assistant-push-sw.js", { scope: "/" });
  const registration = await navigator.serviceWorker.ready;
  const subscription = await registration.pushManager.getSubscription() ?? await registration.pushManager.subscribe({
    userVisibleOnly: true, applicationServerKey: applicationKey(config.public_key) as BufferSource,
  });
  onProgress?.("正在保存通知设置…");
  const data = subscription.toJSON();
  const saved = await call("subscriptions", "POST", { endpoint: subscription.endpoint, keys: data.keys });
  localStorage.setItem(SUBSCRIPTION_ID, saved.subscription_id);
}

/** Unsubscribe the browser first, so logout cannot leave a usable capability for the old account. */
export async function disableBrowserPush() {
  const identity = localStorage.getItem(SUBSCRIPTION_ID);
  if ("serviceWorker" in navigator) {
    const registration = await navigator.serviceWorker.getRegistration("/");
    const subscription = await registration?.pushManager.getSubscription();
    if (subscription && !await subscription.unsubscribe()) throw new Error("浏览器通知尚未关闭，请重试。");
  }
  if (identity) await call(`subscriptions/${encodeURIComponent(identity)}`, "DELETE");
  localStorage.removeItem(SUBSCRIPTION_ID);
}
