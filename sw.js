// Network requests remain online; this worker only handles optional notifications.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", event => event.waitUntil(self.clients.claim()));
self.addEventListener("push", event => {
  let payload = {};
  try { payload = event.data?.json() || {}; } catch (error) { /* Show a generic notification. */ }
  let target = new URL("/", self.location.origin);
  try {
    const candidate = new URL(payload.url || "/", self.location.origin);
    if (candidate.origin === self.location.origin) target = candidate;
  } catch (error) { /* Keep the site's home page. */ }
  event.waitUntil(self.registration.showNotification(payload.title || "تحديث على مقطعك", {
    body: payload.body || "افتح الموقع للاطلاع على النتيجة.",
    lang: "ar", dir: "rtl", tag: payload.tag || "halalstream-job",
    icon: "/assets/purification-water.png", data: { url: target.href }
  }));
});
self.addEventListener("notificationclick", event => {
  event.notification.close();
  event.waitUntil((async () => {
    const target = new URL(event.notification.data?.url || "/", self.location.origin);
    if (target.origin !== self.location.origin) return;
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const client of windows) {
      if (new URL(client.url).origin === target.origin) {
        await client.navigate(target.href);
        await client.focus();
        return;
      }
    }
    await self.clients.openWindow(target.href);
  })());
});
