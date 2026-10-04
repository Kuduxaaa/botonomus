// Botonomus diagnostic API tracer. Main-world hooks: observable by the page by design.
(() => {
  const ENDPOINT = '__BOTONOMUS_TRACE_ENDPOINT__';
  if (globalThis.__bnTraceInstalled) return;
  Object.defineProperty(globalThis, '__bnTraceInstalled', { value: true });
  const isWorker = typeof WorkerGlobalScope !== 'undefined' && self instanceof WorkerGlobalScope;
  const kind = !isWorker ? (self === self.top ? 'page' : 'frame')
    : typeof ServiceWorkerGlobalScope !== 'undefined' && self instanceof ServiceWorkerGlobalScope
      ? 'service-worker'
      : typeof SharedWorkerGlobalScope !== 'undefined' && self instanceof SharedWorkerGlobalScope
        ? 'shared-worker'
        : 'worker';
  let origin = '';
  try { origin = self.location.origin; } catch (e) { /* opaque */ }
  const LIMIT = 2000;
  const seen = new Set();
  let queue = [];
  let total = 0;

  const fnv = (s) => {
    let h = 0xcbf29ce484222325n;
    for (let i = 0; i < s.length; i += 1) {
      h ^= BigInt(s.charCodeAt(i));
      h = (h * 0x100000001b3n) & 0xffffffffffffffffn;
    }
    return h.toString(16).padStart(16, '0');
  };
  const bytes = (view) => {
    const b = new Uint8Array(view.buffer, view.byteOffset, view.byteLength);
    let s = '';
    for (let i = 0; i < b.length; i += 1) s += String.fromCharCode(b[i]);
    return 'h:' + fnv(s) + ':' + b.length;
  };
  const summarize = (v, depth = 0) => {
    try {
      if (v === undefined) return 'undefined';
      if (v === null || typeof v === 'number' || typeof v === 'boolean') return v;
      if (typeof v === 'string') return v.length > 200 ? 'h:' + fnv(v) + ':' + v.length : v;
      if (typeof v === 'bigint' || typeof v === 'symbol') return String(v);
      if (typeof v === 'function') return 'function';
      if (ArrayBuffer.isView(v)) return bytes(v);
      if (v instanceof ArrayBuffer) return bytes(new Uint8Array(v));
      if (depth > 1) return Object.prototype.toString.call(v);
      if (Array.isArray(v) || (typeof v.length === 'number' && typeof v.item === 'function')) {
        const s = JSON.stringify(Array.from(v).slice(0, 50).map((x) => summarize(x, depth + 1)));
        return s.length > 200 ? 'h:' + fnv(s) + ':' + v.length : JSON.parse(s);
      }
      const o = {};
      let n = 0;
      for (const k in v) {
        if (n > 40) break;
        n += 1;
        try {
          const x = v[k];
          if (typeof x !== 'function') o[k] = summarize(x, depth + 1);
        } catch (e) { /* skip unreadable members */ }
      }
      const s = JSON.stringify(o);
      return s.length > 200 ? 'h:' + fnv(s) : o;
    } catch (e) {
      return 'error';
    }
  };
  // Summarizing a value reads its (hooked) members; those reads are ours, not the page's.
  let busy = false;
  const record = (api, args, value) => {
    if (busy || total >= LIMIT) return;
    busy = true;
    try {
      let a;
      try { a = JSON.stringify(Array.from(args).map((x) => summarize(x, 1))); } catch (e) { a = '[]'; }
      if (a.length > 200) a = 'h:' + fnv(a);
      const key = api + '|' + a;
      if (seen.has(key)) return;
      seen.add(key);
      total += 1;
      queue.push({ api, context: kind, origin, args: a, value: summarize(value) });
    } finally {
      busy = false;
    }
  };
  // Keep the native name and arity so wrappers look like the functions they replace.
  const mimic = (wrapper, orig) => {
    for (const key of ['length', 'name']) {
      try {
        Object.defineProperty(wrapper, key, { ...Object.getOwnPropertyDescriptor(orig, key) });
      } catch (e) { /* keep the wrapper's own */ }
    }
  };
  // A non-configurable member cannot be hooked; skip it and keep tracing the rest.
  const define = (o, name, descriptor) => {
    try { Object.defineProperty(o, name, descriptor); } catch (e) { /* not hookable */ }
  };
  const owner = (obj, name) => {
    for (let o = obj; o; o = Object.getPrototypeOf(o)) {
      const d = Object.getOwnPropertyDescriptor(o, name);
      if (d) return [o, d];
    }
    return [null, null];
  };
  const originals = new WeakMap();
  const wrapMethod = (obj, name, label, keepValue = true) => {
    if (!obj) return;
    const [o, d] = owner(obj, name);
    if (!o || typeof d.value !== 'function') return;
    const orig = d.value;
    const wrapped = { [name](...args) {
      const r = Reflect.apply(orig, this, args);
      try {
        if (r && typeof r.then === 'function') {
          r.then((v) => record(label, args, keepValue ? v : 'object'),
            (e) => record(label, args, 'reject:' + (e && e.name)));
        } else {
          record(label, args, keepValue ? r : 'object');
        }
      } catch (e) { /* never break the page */ }
      return r;
    } }[name];
    mimic(wrapped, orig);
    originals.set(wrapped, orig);
    define(o, name, { ...d, value: wrapped });
  };
  const wrapGetter = (obj, name, label) => {
    if (!obj) return;
    const [o, d] = owner(obj, name);
    if (!o || typeof d.get !== 'function') return;
    const orig = d.get;
    const holder = { get [name]() {
      const r = Reflect.apply(orig, this, []);
      try { record(label, [], r); } catch (e) { /* never break the page */ }
      return r;
    } };
    const getter = Object.getOwnPropertyDescriptor(holder, name).get;
    mimic(getter, orig);
    originals.set(getter, orig);
    define(o, name, { ...d, get: getter });
  };

  const nativeToString = Function.prototype.toString;
  const patchedToString = { toString() {
    return Reflect.apply(nativeToString, originals.get(this) || this, []);
  } }.toString;
  mimic(patchedToString, nativeToString);
  originals.set(patchedToString, nativeToString);
  define(Function.prototype, 'toString', {
    ...Object.getOwnPropertyDescriptor(Function.prototype, 'toString'),
    value: patchedToString,
  });

  const proto = (ctor) => (globalThis[ctor] && globalThis[ctor].prototype) || null;
  const G = (ctor, names) => names.forEach((n) => wrapGetter(proto(ctor), n, ctor + '.' + n));
  const M = (ctor, names, keepValue = true) =>
    names.forEach((n) => wrapMethod(proto(ctor), n, ctor + '.' + n, keepValue));

  G('Navigator', ['userAgent', 'appVersion', 'platform', 'vendor', 'language', 'languages',
    'hardwareConcurrency', 'deviceMemory', 'maxTouchPoints', 'webdriver', 'plugins', 'mimeTypes',
    'pdfViewerEnabled', 'cookieEnabled', 'doNotTrack', 'connection', 'userAgentData',
    'globalPrivacyControl']);
  G('WorkerNavigator', ['userAgent', 'platform', 'language', 'languages', 'hardwareConcurrency',
    'deviceMemory', 'userAgentData', 'webdriver']);
  M('Navigator', ['getBattery', 'requestMediaKeySystemAccess', 'getGamepads', 'javaEnabled']);
  G('NavigatorUAData', ['brands', 'mobile', 'platform']);
  M('NavigatorUAData', ['getHighEntropyValues']);
  G('Screen', ['width', 'height', 'availWidth', 'availHeight', 'availLeft', 'availTop',
    'colorDepth', 'pixelDepth', 'isExtended']);
  G('ScreenOrientation', ['type', 'angle']);
  if (!isWorker) {
    ['devicePixelRatio', 'outerWidth', 'outerHeight', 'innerWidth', 'innerHeight', 'screenX',
      'screenY'].forEach((n) => wrapGetter(globalThis, n, 'window.' + n));
    wrapMethod(globalThis, 'matchMedia', 'window.matchMedia');
  }
  M('HTMLCanvasElement', ['toDataURL', 'toBlob']);
  M('HTMLCanvasElement', ['getContext'], false);
  M('CanvasRenderingContext2D', ['getImageData', 'measureText', 'isPointInPath', 'fillText',
    'strokeText']);
  M('OffscreenCanvas', ['convertToBlob']);
  M('OffscreenCanvas', ['getContext'], false);
  M('OffscreenCanvasRenderingContext2D', ['getImageData', 'measureText', 'fillText']);
  ['WebGLRenderingContext', 'WebGL2RenderingContext'].forEach((gl) =>
    M(gl, ['getParameter', 'getSupportedExtensions', 'getExtension', 'readPixels',
      'getShaderPrecisionFormat', 'getContextAttributes']));
  M('GPU', ['requestAdapter']);
  G('GPUAdapter', ['features', 'limits', 'info']);
  M('AudioBuffer', ['getChannelData', 'copyFromChannel']);
  M('AnalyserNode', ['getFloatFrequencyData', 'getByteFrequencyData', 'getFloatTimeDomainData']);
  M('OfflineAudioContext', ['startRendering']);
  G('BaseAudioContext', ['sampleRate']);
  G('AudioContext', ['baseLatency', 'outputLatency']);
  G('AudioDestinationNode', ['maxChannelCount']);
  ['DateTimeFormat', 'NumberFormat', 'Collator', 'PluralRules', 'RelativeTimeFormat',
    'ListFormat'].forEach((n) => {
    if (Intl[n]) wrapMethod(Intl[n].prototype, 'resolvedOptions', 'Intl.' + n + '.resolvedOptions');
  });
  M('Date', ['getTimezoneOffset']);
  M('FontFaceSet', ['check', 'load']);
  M('StorageManager', ['estimate', 'persisted', 'getDirectory']);
  M('MediaDevices', ['enumerateDevices']);
  M('MediaCapabilities', ['decodingInfo', 'encodingInfo']);
  M('HTMLMediaElement', ['canPlayType']);
  if (globalThis.MediaSource) {
    wrapMethod(MediaSource, 'isTypeSupported', 'MediaSource.isTypeSupported');
  }
  M('MediaKeySystemAccess', ['getConfiguration']);
  M('SpeechSynthesis', ['getVoices']);
  M('Permissions', ['query']);
  M('RTCPeerConnection', ['createOffer', 'createDataChannel']);
  G('RTCIceCandidate', ['candidate', 'address', 'type']);
  if (globalThis.Notification) wrapGetter(Notification, 'permission', 'Notification.permission');
  G('Performance', ['memory']);
  G('Document', ['visibilityState', 'hidden']);
  M('Document', ['hasFocus']);

  // Workers are not traced (see apitrace.py); a fetch from a debugger-attached
  // worker crashes Chrome's renderer, so never send from one.
  if (isWorker) return;
  // Beacons and keepalive fetches share a ~64 KB in-flight budget, so batches stay
  // well under it; a refused beacon falls back to an ordinary fetch.
  const CHUNK = 48 * 1024;
  const send = (body) => {
    try {
      const nav = self.navigator;
      if (nav && nav.sendBeacon && nav.sendBeacon(ENDPOINT, body)) return;
    } catch (e) { /* fall through to fetch */ }
    try {
      fetch(ENDPOINT, { method: 'POST', body, mode: 'no-cors' }).catch(() => {});
    } catch (e) { /* give up */ }
  };
  const flush = () => {
    let batch = [];
    let size = 2;
    for (const item of queue) {
      const length = JSON.stringify(item).length + 1;
      if (batch.length && size + length > CHUNK) {
        send(JSON.stringify(batch));
        batch = [];
        size = 2;
      }
      batch.push(item);
      size += length;
    }
    queue = [];
    if (batch.length) send(JSON.stringify(batch));
  };
  setInterval(flush, 500);
  if (typeof addEventListener === 'function') addEventListener('pagehide', flush);
})();
