// Awtrix Studio — AWTRIX screen simulator.
//
// Turns an AWTRIX custom app JSON into 32×8 pixels and draws them on a
// canvas. It is an approximation (font, scrolling and effects are simplified):
// the "real screen" view mirrors the actual clock when exactness matters.
//
// Security: page JSON is treated as drawing data only. Colors are parsed into
// strict #RRGGBB strings, icons are only loaded from LaMetric by numeric ID
// or, by plain file name, as pixels from Home Assistant (see setClockIcons),
// and nothing is ever inserted into the DOM as HTML.

export const WIDTH = 32;
export const HEIGHT = 8;

const OFF = null;

// 3×5 font, AWTRIX shows text in capitals by default.
const FONT = {
  " ": ["0", "0", "0", "0", "0"],
  A: ["010", "101", "111", "101", "101"], B: ["110", "101", "110", "101", "110"],
  C: ["111", "100", "100", "100", "111"], D: ["110", "101", "101", "101", "110"],
  E: ["111", "100", "110", "100", "111"], F: ["111", "100", "110", "100", "100"],
  G: ["011", "100", "101", "101", "011"], H: ["101", "101", "111", "101", "101"],
  I: ["111", "010", "010", "010", "111"], J: ["001", "001", "001", "101", "010"],
  K: ["101", "101", "110", "101", "101"], L: ["100", "100", "100", "100", "111"],
  M: ["10001", "11011", "10101", "10001", "10001"], N: ["1001", "1101", "1011", "1001", "1001"],
  O: ["010", "101", "101", "101", "010"], P: ["110", "101", "110", "100", "100"],
  Q: ["010", "101", "101", "110", "011"], R: ["110", "101", "110", "101", "101"],
  S: ["011", "100", "010", "001", "110"], T: ["111", "010", "010", "010", "010"],
  U: ["101", "101", "101", "101", "111"], V: ["101", "101", "101", "101", "010"],
  W: ["10001", "10001", "10101", "11011", "10001"], X: ["101", "101", "010", "101", "101"],
  Y: ["101", "101", "010", "010", "010"], Z: ["111", "001", "010", "100", "111"],
  0: ["111", "101", "101", "101", "111"], 1: ["010", "110", "010", "010", "111"],
  2: ["111", "001", "111", "100", "111"], 3: ["111", "001", "111", "001", "111"],
  4: ["101", "101", "111", "001", "001"], 5: ["111", "100", "111", "001", "111"],
  6: ["111", "100", "111", "101", "111"], 7: ["111", "001", "001", "010", "010"],
  8: ["111", "101", "111", "101", "111"], 9: ["111", "101", "111", "001", "111"],
  ".": ["0", "0", "0", "0", "1"], ",": ["0", "0", "0", "1", "1"],
  ":": ["0", "1", "0", "1", "0"], ";": ["0", "1", "0", "1", "1"],
  "!": ["1", "1", "1", "0", "1"], "?": ["111", "001", "010", "000", "010"],
  "-": ["000", "000", "111", "000", "000"], "+": ["000", "010", "111", "010", "000"],
  "/": ["001", "001", "010", "100", "100"], "%": ["101", "001", "010", "100", "101"],
  "°": ["11", "11", "00", "00", "00"], "'": ["1", "1", "0", "0", "0"],
  '"': ["101", "101", "000", "000", "000"], "(": ["01", "10", "10", "10", "01"],
  ")": ["10", "01", "01", "01", "10"], _: ["000", "000", "000", "000", "111"],
  "=": ["000", "111", "000", "111", "000"], "<": ["001", "010", "100", "010", "001"],
  ">": ["100", "010", "001", "010", "100"], "#": ["101", "111", "101", "111", "101"],
  "*": ["000", "101", "010", "101", "000"], "€": ["011", "110", "100", "110", "011"],
  $: ["011", "110", "010", "011", "110"], "[": ["11", "10", "10", "10", "11"],
  "]": ["11", "01", "01", "01", "11"],
};

function glyph(ch) {
  const plain = ch.normalize("NFD").replace(/[̀-ͯ]/g, "").toUpperCase();
  return FONT[plain] || FONT[ch] || FONT["?"];
}

/** Parse an AWTRIX color ("#RRGGBB", "RRGGBB", [r, g, b] or 0xRRGGBB). */
export function parseColor(value, fallback = "#FFFFFF") {
  if (Array.isArray(value) && value.length === 3 && value.every((c) => Number.isInteger(c))) {
    return "#" + value.map((c) => Math.max(0, Math.min(255, c)).toString(16).padStart(2, "0")).join("").toUpperCase();
  }
  if (typeof value === "number" && Number.isInteger(value) && value >= 0 && value <= 0xffffff) {
    return "#" + value.toString(16).padStart(6, "0").toUpperCase();
  }
  if (typeof value === "string") {
    const hex = value.startsWith("#") ? value.slice(1) : value;
    if (/^[0-9a-fA-F]{6}$/.test(hex)) return "#" + hex.toUpperCase();
  }
  return fallback;
}

function isBlack(color) {
  return color === "#000000";
}

function textWidth(chars) {
  return chars.reduce((w, ch) => w + glyph(ch)[0].length + 1, 0) - 1;
}

function hueColor(hue) {
  const f = (n) => {
    const k = (n + hue / 60) % 6;
    return Math.round(255 * (1 - Math.max(0, Math.min(k, 4 - k, 1))));
  };
  return parseColor([f(5), f(3), f(1)]);
}

function mixColors(from, to, ratio) {
  const a = parseInt(from.slice(1), 16), b = parseInt(to.slice(1), 16);
  const channel = (shift) => Math.round(((a >> shift) & 255) * (1 - ratio) + ((b >> shift) & 255) * ratio);
  return parseColor([channel(16), channel(8), channel(0)]);
}

/** Read an AWTRIX NG page with the AWTRIX 3 names the simulator draws. */
function fromNG(page) {
  const ng = ["textColor", "backgroundColor", "progressColor", "progressTrackColor", "textOffsetX", "textAlign"];
  if (!ng.some((key) => key in page)) return page;
  const out = { ...page };
  if (page.textColor === "palette") {
    if (page.palette === "Rainbow") out.rainbow = true;
    else if (Array.isArray(page.palette) && page.palette.length >= 2) out.gradient = [page.palette[0], page.palette[page.palette.length - 1]];
  } else if (page.textColor !== undefined) out.color = page.textColor;
  if (page.backgroundColor !== undefined) out.background = page.backgroundColor;
  if (page.progressColor !== undefined) out.progressC = page.progressColor;
  if (page.progressTrackColor !== undefined) out.progressBC = page.progressTrackColor;
  if (Number.isFinite(page.textOffsetX)) out.textOffset = page.textOffsetX;
  if (page.textAlign === "start") out.center = false;
  return out;
}

// AWTRIX NG draw commands are arrays named in full; AWTRIX 3 uses objects
// keyed by a short code. Both are drawn by the same code.
const NG_DRAW = { pixel: "dp", line: "dl", rect: "dr", rectFill: "df", circle: "dc", circleFill: "dfc", text: "dt", bitmap: "db" };
const MAX_BITMAP = 64 * 32;

function bitmapColors(data, count) {
  if (Array.isArray(data)) return data.slice(0, count).map((v) => parseColor(v, "#000000"));
  if (typeof data !== "string" || data.length > MAX_BITMAP * 4) return [];
  // Base64 RGB888 (AWTRIX NG): three bytes per pixel.
  let bytes;
  try { bytes = atob(data); } catch (_err) { return []; }
  const colors = [];
  for (let i = 0; i + 2 < bytes.length && colors.length < count; i += 3) {
    colors.push(parseColor([bytes.charCodeAt(i), bytes.charCodeAt(i + 1), bytes.charCodeAt(i + 2)]));
  }
  return colors;
}

/**
 * Render one AWTRIX page into pixels, on a grid of `size` (32 × 8 unless
 * told otherwise; a TC002 draws apps on 26 × 8 at double size).
 * Returns {pixels, icon, overflow, effect, width, height}.
 */
export function renderPage(page, size = {}) {
  const W = size.width ?? WIDTH;
  const H = size.height ?? HEIGHT;
  const pixels = new Array(W * H).fill(OFF);
  const result = { pixels, icon: null, overflow: false, effect: null, width: W, height: H };
  if (!page || typeof page !== "object" || Array.isArray(page)) return result;
  page = fromNG(page);

  const set = (x, y, color) => {
    x = Math.round(x);
    y = Math.round(y);
    if (x < 0 || x >= W || y < 0 || y >= H) return;
    pixels[y * W + x] = isBlack(color) ? OFF : color;
  };

  // Effects and overlays are animations: only named, not drawn.
  if (typeof page.effect === "string" && page.effect) result.effect = page.effect;
  else if (typeof page.overlay === "string" && page.overlay) result.effect = page.overlay;

  if (page.background !== undefined) {
    const bg = parseColor(page.background, "#000000");
    for (let i = 0; i < pixels.length; i++) pixels[i] = isBlack(bg) ? OFF : bg;
  }

  const hasIcon = page.icon !== undefined && page.icon !== null && page.icon !== "";
  if (hasIcon) result.icon = String(page.icon);
  const areaStart = hasIcon ? 9 : 0;
  const areaWidth = W - areaStart;

  // Text: a string, or segments [{t, c}].
  const base = parseColor(page.color, "#FFFFFF");
  const segments = Array.isArray(page.text)
    ? page.text.filter((s) => s && typeof s === "object").map((s) => ({ t: String(s.t ?? s.text ?? ""), c: parseColor(s.c ?? s.color, base) }))
    : page.text !== undefined && page.text !== null
      ? [{ t: String(page.text), c: base }]
      : [];
  const chars = [];
  segments.forEach((s) => Array.from(s.t).forEach((ch) => chars.push({ ch, c: s.c })));
  // Rainbow and two-color gradients replace the text color, letter by letter.
  const gradient = Array.isArray(page.gradient) && page.gradient.length === 2
    ? page.gradient.map((c) => parseColor(c, base)) : null;
  if (page.rainbow === true) chars.forEach((c, i) => { c.c = hueColor((i * 360) / Math.max(chars.length, 1)); });
  else if (gradient) chars.forEach((c, i) => { c.c = mixColors(gradient[0], gradient[1], chars.length > 1 ? i / (chars.length - 1) : 0); });
  const top = page.topText === true ? 0 : 1;
  if (chars.length) {
    const w = textWidth(chars.map((c) => c.ch));
    const offset = Number.isFinite(page.textOffset) ? page.textOffset : 0;
    const center = page.center !== false;
    let x = areaStart + offset;
    if (w > areaWidth - offset) result.overflow = true;
    else if (center) x = areaStart + offset + Math.floor((areaWidth - offset - w) / 2);
    chars.forEach(({ ch, c }) => {
      const g = glyph(ch);
      g.forEach((row, y) => Array.from(row).forEach((bit, dx) => { if (bit === "1") set(x + dx, top + y, c); }));
      x += g[0].length + 1;
    });
  }

  // Progress bar on the bottom row.
  if (Number.isFinite(page.progress)) {
    const fill = parseColor(page.progressC, "#00FF00");
    const back = parseColor(page.progressBC, "#FFFFFF");
    const span = areaWidth;
    const lit = Math.round((Math.max(0, Math.min(100, page.progress)) / 100) * span);
    for (let i = 0; i < span; i++) set(areaStart + i, H - 1, i < lit ? fill : back);
  }

  // Drawing primitives.
  const line = (x0, y0, x1, y1, c) => {
    let dx = Math.abs(x1 - x0), dy = -Math.abs(y1 - y0);
    const sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
    let err = dx + dy, guard = 0;
    for (;;) {
      set(x0, y0, c);
      if ((x0 === x1 && y0 === y1) || ++guard > 200) break;
      const e2 = 2 * err;
      if (e2 >= dy) { err += dy; x0 += sx; }
      if (e2 <= dx) { err += dx; y0 += sy; }
    }
  };
  const circle = (cx, cy, r, c, filled) => {
    for (let y = -r; y <= r; y++) {
      for (let x = -r; x <= r; x++) {
        const d = x * x + y * y;
        if (filled ? d <= r * r : Math.abs(d - r * r) <= r) set(cx + x, cy + y, c);
      }
    }
  };
  const nums = (a, n) => a.length >= n && a.slice(0, n).every((v) => Number.isFinite(v));
  (Array.isArray(page.draw) ? page.draw : []).slice(0, 500).forEach((cmd) => {
    if (!cmd || typeof cmd !== "object") return;
    let kind, a;
    if (Array.isArray(cmd)) {
      // ["pixels", color, x1, y1, x2, y2, …] has no AWTRIX 3 counterpart.
      if (cmd[0] === "pixels") {
        const c = parseColor(cmd[1]);
        for (let i = 2; i + 1 < cmd.length && i < 2 + 2 * MAX_BITMAP; i += 2) if (Number.isFinite(cmd[i]) && Number.isFinite(cmd[i + 1])) set(cmd[i], cmd[i + 1], c);
        return;
      }
      kind = NG_DRAW[cmd[0]];
      a = cmd.slice(1);
    } else {
      [kind] = Object.keys(cmd);
      a = cmd[kind];
    }
    if (!kind || !Array.isArray(a)) return;
    if (kind === "dp" && nums(a, 2)) set(a[0], a[1], parseColor(a[2]));
    else if (kind === "dl" && nums(a, 4)) line(a[0], a[1], a[2], a[3], parseColor(a[4]));
    else if (kind === "dr" && nums(a, 4)) {
      const c = parseColor(a[4]);
      line(a[0], a[1], a[0] + a[2] - 1, a[1], c); line(a[0], a[1] + a[3] - 1, a[0] + a[2] - 1, a[1] + a[3] - 1, c);
      line(a[0], a[1], a[0], a[1] + a[3] - 1, c); line(a[0] + a[2] - 1, a[1], a[0] + a[2] - 1, a[1] + a[3] - 1, c);
    } else if (kind === "df" && nums(a, 4)) {
      const c = parseColor(a[4]);
      for (let x = 0; x < Math.min(a[2], W); x++) for (let y = 0; y < Math.min(a[3], H); y++) set(a[0] + x, a[1] + y, c);
    } else if (kind === "dc" && nums(a, 3)) circle(a[0], a[1], Math.min(a[2], 16), parseColor(a[3]), false);
    else if (kind === "dfc" && nums(a, 3)) circle(a[0], a[1], Math.min(a[2], 16), parseColor(a[3]), true);
    else if (kind === "dt" && nums(a, 2)) {
      let x = a[0];
      const c = parseColor(a[3]);
      Array.from(String(a[2] ?? "")).slice(0, 40).forEach((ch) => {
        const g = glyph(ch);
        g.forEach((row, y) => Array.from(row).forEach((bit, dx) => { if (bit === "1") set(x + dx, a[1] + y, c); }));
        x += g[0].length + 1;
      });
    } else if (kind === "db" && nums(a, 4) && a[2] > 0 && a[3] > 0) {
      const w = Math.min(a[2], 64), hgt = Math.min(a[3], 32);
      bitmapColors(a[4], w * hgt).forEach((c, i) => set(a[0] + (i % w), a[1] + Math.floor(i / w), c));
    }
  });
  return result;
}

/** The pixels of each indicator, on the right edge of a grid. */
function indicatorPixels(number, w, h) {
  const middle = Math.floor(h / 2);
  return {
    1: [[w - 1, 0], [w - 2, 0], [w - 1, 1]],
    2: [[w - 1, middle - 1], [w - 1, middle]],
    3: [[w - 1, h - 1], [w - 2, h - 1], [w - 1, h - 2]],
  }[number] || [];
}

/** Paint indicator pages ({number: page}) over rendered pixels. */
export function addIndicators(pixels, indicators, width = WIDTH, height = HEIGHT) {
  Object.entries(indicators || {}).forEach(([number, page]) => {
    if (!page || page.color === undefined) return;
    const color = parseColor(page.color, "#000000");
    if (isBlack(color)) return;
    indicatorPixels(number, width, height).forEach(([x, y]) => { pixels[y * width + x] = color; });
  });
  return pixels;
}

/** Convert the clock's real screen (width × height integers) into pixels. */
export function screenToPixels(values, width = WIDTH, height = HEIGHT) {
  if (!Array.isArray(values) || values.length !== width * height) return new Array(width * height).fill(OFF);
  return values.map((v) => (Number.isInteger(v) && v > 0 ? parseColor(v) : OFF));
}

// Icons stored on the clock, by plain file name. The panel gives a loader per
// clock (key), which resolves to {pixels, large} from Home Assistant: 64 RGB
// integers, or null when the clock has no such icon, and for a 16 × 16 icon
// its 256 pixels as is. A failed request is tried again later.
const CLOCK_ICON_RE = /^[A-Za-z0-9_-]{1,64}$/;
const CLOCK_ICON_RETRY = 30000;
const CLOCK_ICONS = new Map();
let clockIcons = { key: null, loader: null };

/** Set where icons stored on the clock come from (null: nowhere). */
export function setClockIcons(key, loader) {
  if (key === clockIcons.key) return;
  clockIcons = { key, loader };
  CLOCK_ICONS.clear();
}

/** Return the pixels (#RRGGBB or null) of an icon stored on the clock, or null until known. */
export function clockIcon(name, onReady) {
  const entry = loadClockIcon(String(name ?? ""), onReady);
  return entry ? entry.pixels : null;
}

function loadClockIcon(name, onReady) {
  if (!clockIcons.loader || !CLOCK_ICON_RE.test(name)) return null;
  let entry = CLOCK_ICONS.get(name);
  if (!entry || (entry.failedAt && Date.now() - entry.failedAt > CLOCK_ICON_RETRY)) {
    entry = { readable: true, pixels: undefined, failedAt: 0, waiting: [] };
    CLOCK_ICONS.set(name, entry);
    clockIcons.loader(name).then((result) => {
      entry.pixels = colors(result?.pixels, 64);
      entry.large = entry.pixels && colors(result?.large, 256);
      entry.waiting.splice(0).forEach((cb) => cb());
    }, () => { entry.failedAt = Date.now(); entry.waiting.length = 0; });
  }
  if (entry.pixels === undefined) {
    if (onReady && !entry.failedAt) entry.waiting.push(onReady);
    return null;
  }
  return entry.pixels ? entry : null;
}

function colors(values, count) {
  return Array.isArray(values) && values.length === count
    ? values.map((v) => (Number.isInteger(v) && v > 0 ? parseColor(v) : OFF))
    : null;
}

// LaMetric icons, by numeric ID. Loaded with CORS when the server allows it
// (drawn pixel by pixel), else drawn as a plain image over the icon area.
const ICONS = new Map();
function loadIcon(id, onReady) {
  id = String(id).replace(/^lametric:/, "");
  if (!/^\d{1,7}$/.test(id)) return loadClockIcon(id, onReady);
  let entry = ICONS.get(id);
  if (!entry) {
    entry = { image: null, readable: false, waiting: [] };
    ICONS.set(id, entry);
    const url = `https://developer.lametric.com/content/apps/icon_thumbs/${id}`;
    const tryLoad = (cors) => {
      const img = new Image();
      if (cors) img.crossOrigin = "anonymous";
      img.onload = () => {
        entry.image = img;
        entry.readable = cors;
        entry.waiting.splice(0).forEach((cb) => cb());
      };
      img.onerror = () => { if (cors) tryLoad(false); };
      img.src = url;
    };
    tryLoad(true);
  }
  if (!entry.image && onReady) entry.waiting.push(onReady);
  return entry.image ? entry : null;
}

function iconPixels(entry) {
  if (!entry.readable) return null;
  if (entry.pixels !== undefined) return entry.pixels;
  try {
    const c = document.createElement("canvas");
    c.width = 8; c.height = 8;
    const ctx = c.getContext("2d");
    ctx.drawImage(entry.image, 0, 0, 8, 8);
    const data = ctx.getImageData(0, 0, 8, 8).data;
    entry.pixels = [];
    for (let i = 0; i < 64; i++) {
      const [r, g, b, a] = data.slice(i * 4, i * 4 + 4);
      entry.pixels.push(a < 64 || (r + g + b) < 24 ? OFF : parseColor([r, g, b]));
    }
  } catch (_err) {
    entry.pixels = null;
  }
  return entry.pixels;
}

/**
 * Draw pixels on a canvas. `options`: {cell, gap, icon, offColor, glow,
 * width, height, scale}; the size defaults to the 32 × 8 of AWTRIX 3. With
 * `scale` 2 each pixel lights 2 × 2 LEDs, like apps on a TC002.
 * The canvas is redrawn when an icon finishes loading.
 */
export function drawMatrix(canvas, pixels, options = {}) {
  const cols = options.width ?? WIDTH;
  const rows = options.height ?? HEIGHT;
  const scale = options.scale ?? 1;
  const leds = cols * scale, ledRows = rows * scale;
  const cell = options.cell ?? 10;
  const gap = options.gap ?? 2;
  const ratio = window.devicePixelRatio || 1;
  const width = leds * cell + (leds - 1) * gap;
  const height = ledRows * cell + (ledRows - 1) * gap;
  if (canvas.width !== Math.round(width * ratio)) {
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);

  let drawn = pixels.slice();
  let overlay = null;
  // A 16 × 16 icon of the clock, drawn LED by LED where apps are doubled.
  let large = null;
  if (options.icon) {
    const entry = loadIcon(options.icon, () => drawMatrix(canvas, pixels, options));
    if (entry?.large && scale === 2) large = entry.large;
    if (entry) {
      const icon = iconPixels(entry);
      if (icon) {
        for (let y = 0; y < 8; y++) for (let x = 0; x < 8; x++) if (icon[y * 8 + x]) drawn[y * cols + x] = icon[y * 8 + x];
      } else {
        overlay = entry.image;
      }
    }
  }

  const radius = Math.max(1, cell / 4);
  for (let y = 0; y < ledRows; y++) {
    for (let x = 0; x < leds; x++) {
      const color = large && x < 16 && y < 16
        ? large[y * 16 + x]
        : drawn[Math.floor(y / scale) * cols + Math.floor(x / scale)];
      ctx.fillStyle = color || options.offColor || "#1A1A1E";
      if (color && options.glow) { ctx.shadowColor = color; ctx.shadowBlur = cell / 2; } else { ctx.shadowBlur = 0; }
      const px = x * (cell + gap), py = y * (cell + gap);
      ctx.beginPath();
      ctx.roundRect ? ctx.roundRect(px, py, cell, cell, radius) : ctx.rect(px, py, cell, cell);
      ctx.fill();
    }
  }
  ctx.shadowBlur = 0;
  if (overlay) {
    ctx.imageSmoothingEnabled = false;
    const size = 8 * scale * cell + (8 * scale - 1) * gap;
    ctx.drawImage(overlay, 0, 0, size, size);
  }
}
