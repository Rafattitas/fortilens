// This adapter runs only in an explicitly selected Grafana document. It leaves
// the OS, parent window, saved dashboard and stored observation timestamps alone.
(() => {
  'use strict';
  if (!location.pathname.startsWith('/grafana/') || window.__fortilensMonitoringClock) return;
  const serverNow = Number(document.currentScript?.dataset.serverTimeMs);
  const NativeDate = window.Date;
  const difference = serverNow - NativeDate.now();
  if (!Number.isFinite(serverNow) || serverNow <= 0 || !Number.isFinite(difference) ||
      Math.abs(difference) <= 60000 || Math.abs(difference) > 86400000) return;
  const started = performance.now();
  const now = () => Math.trunc(serverNow + performance.now() - started);
  const PageDate = new Proxy(NativeDate, {
    apply() { return new NativeDate(now()).toString(); },
    construct(target, args, newTarget) {
      return Reflect.construct(target, args.length ? args : [now()], newTarget);
    },
    get(target, key, receiver) {
      return key === 'now' ? now : Reflect.get(target, key, receiver);
    },
  });
  // Preserve normal Date identity checks and subclass construction in this page.
  Object.defineProperty(NativeDate.prototype, 'constructor', {
    value: PageDate, configurable: true, writable: true,
  });
  window.Date = PageDate;
  window.__fortilensMonitoringClock = Object.freeze({
    applied: true, offsetSeconds: Math.round(difference / 1000),
  });
})();
