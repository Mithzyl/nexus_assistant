/* Receive encrypted browser push even after every Assistant tab is closed. */

/** Activate this worker without caching authenticated pages or business responses. */
self.addEventListener("install", (event) => event.waitUntil(self.skipWaiting()));
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

/** Show each server-issued occurrence and separately acknowledge successful display. */
self.addEventListener("push", (event) => {
  event.waitUntil((async () => {
    const data = event.data ? event.data.json() : {};
    const url = typeof data.url === "string" && data.url.startsWith("/") && !data.url.startsWith("//") ? data.url : "/today";
    await self.registration.showNotification(data.title || "生活助手提醒", {
      body: data.body || "有一项安排到时间了。", tag: data.tag || data.delivery_id,
      icon: "/icon-192.png", data: { url, deliveryId: data.delivery_id },
    });
    if (data.delivery_id) {
      try {
        await fetch(`/api/pa/browser-notifications/receipts/${encodeURIComponent(data.delivery_id)}`, {
          method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: "{}",
        });
      } catch { /* The notification is visible; a missing acknowledgement is not transport failure. */ }
    }
  })());
});

/** Reopen the relevant Assistant record using only same-origin navigation. */
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil((async () => {
    const value = event.notification.data?.url;
    const url = new URL(typeof value === "string" ? value : "/today", self.location.origin);
    if (url.origin !== self.location.origin) return;
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const existing = windows.find((client) => new URL(client.url).origin === url.origin);
    if (existing) { await existing.navigate(url.href); await existing.focus(); }
    else await self.clients.openWindow(url.href);
  })());
});
