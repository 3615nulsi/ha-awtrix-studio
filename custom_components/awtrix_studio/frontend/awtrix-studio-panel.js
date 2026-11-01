// Awtrix Studio — sidebar panel.
//
// Plain Web Component, no build step and no dependency. Security rules:
// - the DOM is built with createElement/textContent only, never innerHTML;
// - user and clock data are only ever set as text, attribute values or canvas
//   colors (parsed into strict #RRGGBB first);
// - every action goes through admin-only WebSocket commands validated on the
//   server side.

import { addIndicators, clockIcon, drawMatrix, parseColor, renderPage, screenToPixels, setClockIcons, WIDTH, HEIGHT } from "./matrix.js";
import { translator } from "./i18n.js";
import { MAX_STOPS, PRESETS, colorAt, cssGradient, formStops, presetCss, rgbToHex, suggestion } from "./gradients.js";

const PREVIEW_DEBOUNCE = 350;
const MAX_IMPORT_BYTES = 512 * 1024;
const LAMETRIC_THUMB = "https://developer.lametric.com/content/apps/icon_thumbs/";
const ICON_SEARCH_DEBOUNCE = 400;
const MIRROR_STALE_MS = 5000;

// A simple app shows up to MAX_SEGMENTS entities on one line. The form holds
// the one being edited at its top level (so every field works unchanged) and
// all of them in `segments`.
const MAX_SEGMENTS = 5;
const SEGMENT_KEYS = ["entity_id", "label", "color", "decimals", "show_unit", "hide_when_zero", "threshold_1", "color_1",
  "threshold_2", "color_2", "color_mode", "gradient", "_presetTouched", "gradient_min", "gradient_max", "gradient_stops"];

// AWTRIX 3 background effects, palettes and weather overlays.
const EFFECTS = ["BrickBreaker", "Checkerboard", "ColorWaves", "Fade", "Fireworks", "LookingEyes", "Matrix", "MovingLine", "Pacifica",
  "PingPong", "Plasma", "PlasmaCloud", "Radar", "Ripple", "Snake", "SwirlIn", "SwirlOut", "TheaterChase", "TwinklingStars"];
const PALETTES = ["Cloud", "Lava", "Ocean", "Forest", "Stripe", "Party", "Heat", "Rainbow"];
const OVERLAYS = ["clear", "snow", "rain", "drizzle", "storm", "thunder", "frost"];
const BUILTIN_APPS = ["Time", "Date", "Temperature", "Humidity", "Battery"];

const STARTER_APP_TEMPLATE = `{#- Render a JSON object (one page) or a list of objects
    (pages that alternate). Any key of the AWTRIX custom app API works. -#}
{{ [
  {"text": now().strftime('%H:%M'), "color": "#FFFFFF"},
  {"text": states('sun.sun'), "color": "#FFB74D"}
] | tojson }}`;

const STARTER_INDICATOR_TEMPLATE = `{#- Render {"color": ...} to light the indicator
    (optionally with "blink" or "fade" in ms), or {} to turn it off. -#}
{%- if is_state('sun.sun', 'below_horizon') -%}
{"color": "#FF8C00", "fade": 2000}
{%- else -%}
{}
{%- endif -%}`;

const NG_STARTER_APP_TEMPLATE = `{#- Render a JSON object (one page) or a list of objects
    (pages that alternate). Any key of the AWTRIX NG pushed app API works;
    unknown keys make the clock refuse the app. -#}
{{ [
  {"text": now().strftime('%H:%M'), "textColor": "#FFFFFF"},
  {"text": states('sun.sun'), "textColor": "#FFB74D"}
] | tojson }}`;

const NG_STARTER_INDICATOR_TEMPLATE = `{#- Render {"color": ...} to light the indicator
    (optionally with "blinkMs" or "fadeMs"), or {} to turn it off. -#}
{%- if is_state('sun.sun', 'below_horizon') -%}
{"color": "#FF8C00", "fadeMs": 2000}
{%- else -%}
{}
{%- endif -%}`;

// ------------------------------------------------------------------ helpers

function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === "value") el.value = value;
    else if (key === "checked") el.checked = Boolean(value);
    else el.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

const SVG_NS = "http://www.w3.org/2000/svg";
const ICON_PATHS = {
  menu: "M3 6h18M3 12h18M3 18h18",
  back: "M15 18l-6-6 6-6",
  plus: "M12 5v14M5 12h14",
  prev: "M15 18l-6-6 6-6",
  next: "M9 6l6 6-6 6",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7",
  download: "M12 4v11M7 10l5 5 5-5M5 20h14",
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-4-4",
  close: "M6 6l12 12M18 6L6 18",
  upload: "M12 16V5M7 10l5-5 5 5M5 20h14",
  up: "M6 15l6-6 6 6",
  down: "M6 9l6 6 6-6",
  reorder: "M7 4v16M4 7l3-3 3 3M17 20V4M14 17l3 3 3-3",
};
function icon(name) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("class", "icon");
  const path = document.createElementNS(SVG_NS, "path");
  path.setAttribute("d", ICON_PATHS[name]);
  svg.append(path);
  return svg;
}

function clear(el) {
  while (el.firstChild) el.firstChild.remove();
}

function toHex(value, fallback) {
  return parseColor(value, fallback).toLowerCase();
}

function numberOrEmpty(value) {
  if (value === "" || value === null || value === undefined) return "";
  const n = Number(value);
  return Number.isFinite(n) ? n : "";
}

/** The loop as it will look: like `desired_loop` on the server, plus hidden Studio apps. */
function arrangedLoop(loop, order, studio) {
  const current = loop || [];
  const known = new Set([...current, ...studio]);
  const result = order.filter((name) => known.has(name));
  const placed = new Set(result);
  current.forEach((name, index) => {
    if (placed.has(name)) return;
    let position = 0;
    for (let i = index - 1; i >= 0; i--) {
      if (placed.has(current[i])) { position = result.indexOf(current[i]) + 1; break; }
    }
    result.splice(position, 0, name);
    placed.add(name);
  });
  studio.filter((name) => !placed.has(name)).sort().forEach((name) => result.push(name));
  return result;
}

function unsubscribe(promise) {
  if (promise) promise.then((unsub) => unsub()).catch(() => {});
}

const STYLE = `
[hidden] { display: none !important; }
:host { display: block; min-height: 100%; background: var(--primary-background-color, #f4f5f7);
  color: var(--primary-text-color, #1f2328); font-family: var(--ha-font-family-body, Roboto, system-ui, sans-serif); }
* { box-sizing: border-box; }
.toolbar { height: 56px; display: flex; align-items: center; gap: 8px; padding: 0 12px;
  background: var(--app-header-background-color, var(--primary-color, #03a9f4));
  color: var(--app-header-text-color, #fff); position: sticky; top: 0; z-index: 2; }
.toolbar h1 { margin: 0 8px; font-size: 20px; font-weight: 400; flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.toolbar select { font: inherit; font-size: 14px; padding: 6px 10px; border-radius: 8px; border: none; max-width: 50vw; }
.icon { width: 22px; height: 22px; fill: none; stroke: currentColor; stroke-width: 2.2; stroke-linecap: round; stroke-linejoin: round; }
.icon-btn { width: 44px; height: 44px; border: none; background: none; color: inherit; border-radius: 50%; display: inline-flex;
  align-items: center; justify-content: center; cursor: pointer; }
.icon-btn:hover { background: rgba(127,127,127,.15); }
main { max-width: 1240px; margin: 0 auto; padding: 20px 16px 48px; display: flex; flex-direction: column; gap: 20px; }
.card { background: var(--card-background-color, #fff); border: 1px solid var(--divider-color, #e3e5e8);
  border-radius: var(--ha-card-border-radius, 12px); }
.pad { padding: 18px; }
h2 { margin: 0; font-size: 17px; font-weight: 500; }
.muted { color: var(--secondary-text-color, #5f6368); font-size: 13px; }
.row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.between { justify-content: space-between; }
.btn { font: inherit; font-size: 14px; font-weight: 500; padding: 8px 14px; min-height: 40px; border-radius: 20px; cursor: pointer;
  border: 1px solid var(--divider-color, #c4c7cc); background: var(--card-background-color, #fff); color: var(--primary-text-color, #1f2328);
  display: inline-flex; align-items: center; gap: 6px; }
.btn:hover { filter: brightness(.96); }
.btn.primary { background: var(--primary-color, #0277bd); border-color: transparent; color: var(--text-primary-color, #fff); }
.btn.danger { color: var(--error-color, #db4437); }
.btn.small { min-height: 34px; padding: 6px 12px; font-size: 13px; }
.btn[disabled] { opacity: .5; cursor: default; }
.screen { background: #0b0b0d; border-radius: 12px; padding: 12px; display: inline-flex; box-shadow: inset 0 0 0 2px #26262b; max-width: 100%; overflow: hidden; }
.screen canvas { display: block; max-width: 100%; height: auto !important; image-rendering: pixelated; }
.live { display: flex; flex-wrap: wrap; gap: 24px; align-items: center; }
.live-info { flex: 1 1 260px; display: flex; flex-direction: column; gap: 12px; }
dl { margin: 0; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 20px; }
dt { color: var(--secondary-text-color, #5f6368); font-size: 13px; }
dd { margin: 2px 0 0; font-weight: 500; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 14px; }
.app { padding: 12px; display: flex; flex-direction: column; gap: 10px; }
.app.current { border-color: var(--primary-color, #0277bd); }
.app .screen { padding: 8px; justify-content: center; }
.app.dim .screen { opacity: .45; }
.name { font-family: var(--ha-font-family-code, "Roboto Mono", monospace); font-weight: 500; }
.chip { font-size: 12px; padding: 3px 9px; border-radius: 10px; background: rgba(127,127,127,.15); }
.chip.simple { background: rgba(46,125,50,.15); }
.chip.template { background: rgba(94,53,177,.15); }
.status { font-size: 13px; font-weight: 500; }
.status.ok { color: var(--success-color, #2e7d32); }
.status.screen-on { color: var(--primary-color, #0277bd); }
.status.error { color: var(--error-color, #db4437); }
.switch { width: 40px; height: 24px; border-radius: 12px; border: none; padding: 3px; cursor: pointer; flex: none;
  background: var(--disabled-color, #9aa0a6); display: flex; justify-content: flex-start; }
.switch[aria-checked="true"] { background: var(--primary-color, #0277bd); justify-content: flex-end; }
.switch span { width: 18px; height: 18px; border-radius: 50%; background: #fff; display: block; }
.indicators { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 12px; }
.indicator { padding: 14px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.indicator .grow { flex: 1 1 140px; min-width: 0; }
.indicator.free { border-style: dashed; }
.swatch { width: 26px; height: 26px; border-radius: 6px; flex: none; background: #26262b; }
.editor { display: flex; flex-wrap: wrap; gap: 20px; align-items: flex-start; }
.editor > .form { flex: 1 1 520px; min-width: 0; display: flex; flex-direction: column; gap: 16px; }
.editor > .preview { flex: 1 1 420px; min-width: 0; display: flex; flex-direction: column; gap: 16px; }
.tabs { display: inline-flex; border: 1px solid var(--divider-color, #c4c7cc); border-radius: 20px; overflow: hidden; align-self: flex-start; }
.tabs button { font: inherit; font-size: 14px; font-weight: 500; padding: 8px 18px; border: none; background: none; color: inherit; cursor: pointer; min-height: 40px; }
.tabs button[aria-selected="true"] { background: rgba(3,169,244,.18); color: var(--primary-color, #0277bd); }
.segment-tabs { max-width: 100%; overflow-x: auto; }
.segment-tabs button { white-space: nowrap; max-width: 180px; overflow: hidden; text-overflow: ellipsis; }
.fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px 18px; }
.field { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.field.wide { grid-column: 1 / -1; }
.field label, .field .label { font-size: 14px; font-weight: 500; }
.field input[type=text], .field input[type=number], .field select, .field textarea {
  font: inherit; font-size: 15px; padding: 9px 11px; border-radius: 8px; width: 100%;
  border: 1px solid var(--divider-color, #c4c7cc); background: var(--card-background-color, #fff); color: var(--primary-text-color, #1f2328); }
.field textarea { font-family: var(--ha-font-family-code, "Roboto Mono", monospace); font-size: 13px; line-height: 1.55; min-height: 280px; resize: vertical; }
.field input[type=color] { width: 56px; height: 38px; padding: 2px; border-radius: 8px; border: 1px solid var(--divider-color, #c4c7cc); background: none; }
.check { display: flex; gap: 10px; align-items: flex-start; font-size: 15px; }
.check input { width: 20px; height: 20px; margin: 1px 0 0; flex: none; }
.error-text { color: var(--error-color, #db4437); font-size: 13px; min-height: 0; }
fieldset { border: 1px solid var(--divider-color, #e3e5e8); border-radius: 10px; padding: 12px 14px; margin: 0; display: flex; flex-direction: column; gap: 10px; }
legend { font-size: 14px; font-weight: 500; padding: 0 6px; }
pre { margin: 0; padding: 12px; border-radius: 8px; font-family: var(--ha-font-family-code, "Roboto Mono", monospace); font-size: 12px; line-height: 1.5;
  white-space: pre-wrap; word-break: break-word; max-height: 260px; overflow: auto; background: rgba(127,127,127,.1); }
.pills { display: flex; gap: 6px; flex-wrap: wrap; }
.pill { font-size: 12px; padding: 4px 10px; border-radius: 12px; background: rgba(127,127,127,.15); }
.pill.active { background: var(--primary-color, #0277bd); color: var(--text-primary-color, #fff); }
.banner { padding: 12px 16px; border-radius: 10px; background: rgba(219,68,55,.12); color: var(--error-color, #db4437); }
.feedback { font-size: 13px; color: var(--secondary-text-color, #5f6368); min-height: 18px; }
.color-body { display: flex; flex-direction: column; gap: 12px; }
.gradient-editor { display: flex; flex-direction: column; gap: 12px; }
.presets { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px; }
.preset { font: inherit; font-size: 13px; text-align: left; display: flex; flex-direction: column; gap: 6px; padding: 8px; cursor: pointer;
  border-radius: 10px; border: 1px solid var(--divider-color, #c4c7cc); background: var(--card-background-color, #fff); color: inherit; }
.preset[aria-checked="true"] { border-color: var(--primary-color, #0277bd); box-shadow: 0 0 0 1px var(--primary-color, #0277bd); }
.preset-swatch { height: 14px; border-radius: 7px; display: block; }
.preset-swatch.custom { background: repeating-linear-gradient(45deg, rgba(127,127,127,.25) 0 6px, rgba(127,127,127,.08) 6px 12px); }
.stops { display: flex; flex-direction: column; gap: 8px; }
.stop { flex-wrap: nowrap; }
.field .stop input.stop-value { width: 120px; flex: 0 0 120px; font: inherit; font-size: 15px; padding: 8px 10px; border-radius: 8px;
  border: 1px solid var(--divider-color, #c4c7cc); background: var(--card-background-color, #fff); color: inherit; }
.field .stop input[type=color] { width: 56px; height: 38px; padding: 2px; border-radius: 8px; border: 1px solid var(--divider-color, #c4c7cc); background: none; }
.gradient-track { position: relative; height: 22px; margin: 4px 11px 0; }
.gradient-bar { position: absolute; inset: 4px 0; border-radius: 7px; }
.gradient-bar.empty { background: rgba(127,127,127,.15); }
.gradient-marker { position: absolute; top: 0; width: 22px; height: 22px; margin-left: -11px; border-radius: 50%;
  border: 3px solid var(--card-background-color, #fff); box-shadow: 0 0 0 1px rgba(0,0,0,.35); }
.transfer { display: flex; flex-direction: column; gap: 14px; }
.pick-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 8px 16px; }
.pick { align-items: center; }
.export-output, .transfer textarea { font-family: var(--ha-font-family-code, "Roboto Mono", monospace); font-size: 13px; padding: 10px; border-radius: 8px; width: 100%;
  border: 1px solid var(--divider-color, #c4c7cc); background: var(--card-background-color, #fff); color: inherit; resize: vertical; min-height: 120px; }
.import-list { display: flex; flex-direction: column; gap: 10px; }
.import-item { display: flex; flex-direction: column; gap: 10px; }
.import-item.dim { opacity: .6; }
.import-item .field { max-width: 360px; }
.import-item input[type=text], .import-item select { font: inherit; font-size: 15px; padding: 8px 10px; border-radius: 8px;
  border: 1px solid var(--divider-color, #c4c7cc); background: var(--card-background-color, #fff); color: inherit; }
.warn { font-size: 13px; color: var(--warning-color, #b26a00); }
.notice { color: var(--success-color, #2e7d32); }
details summary { cursor: pointer; font-size: 13px; }
details pre { margin-top: 8px; }
.icon-row { flex-wrap: nowrap; }
.icon-row input { flex: 1; min-width: 0; }
.icon-preview { width: 38px; height: 38px; flex: none; border-radius: 6px; image-rendering: pixelated; background: #0b0b0d; }
.picker-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,.45); z-index: 10; display: flex; align-items: center; justify-content: center; padding: 16px; }
.picker { background: var(--card-background-color, #fff); color: var(--primary-text-color, #1f2328); border-radius: 14px; width: min(760px, 100%);
  max-height: min(720px, 100%); display: flex; flex-direction: column; gap: 12px; padding: 18px; box-shadow: 0 12px 40px rgba(0,0,0,.35); }
.picker-search { font: inherit; font-size: 15px; padding: 10px 12px; border-radius: 8px; border: 1px solid var(--divider-color, #c4c7cc);
  background: var(--card-background-color, #fff); color: inherit; }
.picker-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(104px, 1fr)); gap: 8px; overflow: auto; min-height: 120px; }
.picker-item { font: inherit; display: flex; flex-direction: column; align-items: center; gap: 4px; padding: 10px 6px; border-radius: 10px; cursor: pointer;
  border: 1px solid var(--divider-color, #e3e5e8); background: none; color: inherit; min-width: 0; }
.picker-item:hover, .picker-item:focus-visible { border-color: var(--primary-color, #0277bd); }
.picker-item img { width: 32px; height: 32px; image-rendering: pixelated; background: #0b0b0d; border-radius: 4px; }
.picker-name { font-size: 12px; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.picker-id { font-size: 11px; }
.display-group { border: 1px solid var(--divider-color, #e3e5e8); border-radius: 10px; padding: 10px 12px; }
.display-group summary { font-size: 14px; font-weight: 500; }
.display-group[open] summary { margin-bottom: 12px; }
.order-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.order-item { display: flex; align-items: center; gap: 10px; padding: 6px 8px 6px 12px; border-radius: 10px; cursor: grab;
  border: 1px solid var(--divider-color, #e3e5e8); background: var(--card-background-color, #fff); }
.order-item.dragging { opacity: .4; }
.order-item.absent .name { color: var(--secondary-text-color, #5f6368); }
.order-item .grip { color: var(--secondary-text-color, #5f6368); font-size: 18px; line-height: 1; }
.order-item .grow { flex: 1; min-width: 0; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.order-pos { width: 22px; text-align: right; font-variant-numeric: tabular-nums; }
.icon-btn.small { width: 36px; height: 36px; }
.icon-btn[disabled] { opacity: .3; cursor: default; }
@media (max-width: 600px) { .fields { grid-template-columns: minmax(0, 1fr); } dl { grid-template-columns: minmax(0, 1fr); } }
`;

// -------------------------------------------------------------------- panel

class AwtrixStudioPanel extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._ready = false;
    this._clocks = [];
    this._entryId = null;
    this._snapshot = null;
    this._view = { name: "overview" };
    this._cards = new Map();
    this._structure = "";
    this._screen = { pixels: null, at: 0 };
    this._subs = {};
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first && this.isConnected) this._init();
  }

  get hass() {
    return this._hass;
  }

  set narrow(value) {
    this._narrow = value;
    if (this._menuButton) this._menuButton.hidden = !value;
  }

  set route(_value) {}
  set panel(_value) {}

  connectedCallback() {
    if (!this._hass) return;
    if (!this._ready) this._init();
    else this._resume();
  }

  disconnectedCallback() {
    Object.keys(this._subs).forEach((key) => this._stop(key));
    clearInterval(this._cycleTimer);
    clearTimeout(this._previewTimer);
  }

  // ---------------------------------------------------------------- setup

  async _init() {
    this._ready = true;
    this.t = translator(this._hass.locale?.language || this._hass.language);
    const root = this.shadowRoot || this.attachShadow({ mode: "open" });
    clear(root);
    root.append(h("style", {}, STYLE));

    this._menuButton = h("button", { class: "icon-btn", "aria-label": this.t("menu"), onClick: () => this._toggleMenu() }, icon("menu"));
    this._menuButton.hidden = !this._narrow;
    this._backButton = h("button", { class: "icon-btn", "aria-label": this.t("back"), onClick: () => this._showOverview() }, icon("back"));
    this._backButton.hidden = true;
    this._title = h("h1", {}, this.t("title"));
    this._clockSelect = h("select", { "aria-label": this.t("clock"), onChange: (e) => this._selectClock(e.target.value) });
    root.append(h("div", { class: "toolbar" }, this._menuButton, this._backButton, this._title, this._clockSelect));
    this._main = h("main");
    root.append(this._main);

    try {
      this._clocks = await this._hass.callWS({ type: "awtrix_studio/clocks" });
    } catch (err) {
      this._clocks = [];
      this._banner(this.t("errors.request_failed", { detail: err.message || err.code || "" }));
    }
    clear(this._clockSelect);
    this._clocks.forEach((clock) => this._clockSelect.append(h("option", { value: clock.entry_id }, `${clock.title} · ${clock.firmware.toUpperCase()}`)));
    this._clockSelect.hidden = this._clocks.length < 2;

    if (!this._clocks.length) {
      clear(this._main);
      this._main.append(h("div", { class: "card pad" }, this.t("no_clock")));
      return;
    }
    let saved = null;
    try { saved = window.localStorage.getItem("awtrix_studio.clock"); } catch (_err) { saved = null; }
    const initial = this._clocks.find((c) => c.entry_id === saved) || this._clocks.find((c) => c.loaded) || this._clocks[0];
    this._selectClock(initial.entry_id);
  }

  _resume() {
    if (this._entryId) {
      this._subscribeSnapshot();
      if (this._view.name === "overview") this._subscribeScreen();
      else this._refreshPreview();
    }
  }

  _toggleMenu() {
    this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true }));
  }

  _selectClock(entryId) {
    this._entryId = entryId;
    this._clockSelect.value = entryId;
    try { window.localStorage.setItem("awtrix_studio.clock", entryId); } catch (_err) { /* private mode */ }
    this._snapshot = null;
    this._structure = "";
    this._screen = { pixels: null, at: 0 };
    this._showOverview();
    this._subscribeSnapshot();
  }

  // -------------------------------------------------------- subscriptions

  _stop(key) {
    unsubscribe(this._subs[key]);
    delete this._subs[key];
  }

  _subscribe(key, message, callback) {
    this._stop(key);
    this._subs[key] = this._hass.connection.subscribeMessage(callback, message);
    this._subs[key].catch((err) => {
      if (key !== "screen") this._banner(this.t("errors.request_failed", { detail: err.message || err.code || "" }));
    });
  }

  _subscribeSnapshot() {
    this._subscribe("snapshot", { type: "awtrix_studio/subscribe", entry_id: this._entryId }, (snapshot) => {
      this._snapshot = snapshot;
      this._setClockIcons(snapshot);
      if (this._view.name === "overview") this._updateOverview();
    });
  }

  _subscribeScreen() {
    if (!this._snapshot?.features?.screen) return;
    this._subscribe("screen", { type: "awtrix_studio/screen", entry_id: this._entryId }, (event) => {
      const width = Number.isInteger(event.width) ? event.width : WIDTH;
      const height = Number.isInteger(event.height) ? event.height : HEIGHT;
      this._screen = { pixels: screenToPixels(event.pixels, width, height), width, height, at: Date.now() };
      this._drawMirror();
      if (this._view.name === "edit" && this._previewTab === "real") this._drawPreview();
    });
  }

  async _call(message) {
    try {
      return await this._hass.callWS({ entry_id: this._entryId, ...message });
    } catch (err) {
      this._banner(this.t("errors.request_failed", { detail: err.message || err.code || "" }));
      return null;
    }
  }

  _banner(text) {
    if (!this._main) return;
    const banner = h("div", { class: "banner", role: "alert" }, text);
    this._main.prepend(banner);
    setTimeout(() => banner.remove(), 8000);
  }

  // ------------------------------------------------------------- overview

  _showOverview() {
    this._closeIconPicker();
    this._view = { name: "overview" };
    this._stop("preview");
    clearInterval(this._cycleTimer);
    this._backButton.hidden = true;
    this._title.textContent = this.t("title");
    this._clockSelect.hidden = this._clocks.length < 2;
    this._structure = "";
    this._cards = new Map();
    clear(this._main);

    this._mirrorCanvas = h("canvas", { role: "img", "aria-label": this.t("live_mirror") });
    this._mirrorCaption = h("span", { class: "muted" }, this.t("live_waiting"));
    this._currentApp = h("dd", {}, "—");
    this._rotation = h("dd", {}, "—");
    this._navRow = h("div", { class: "row" },
      h("button", { class: "btn", onClick: () => this._call({ type: "awtrix_studio/navigate", forward: false }) }, icon("prev"), this.t("previous_app")),
      h("button", { class: "btn", onClick: () => this._call({ type: "awtrix_studio/navigate", forward: true }) }, this.t("next_app"), icon("next")),
      h("button", { class: "btn", onClick: () => this._call({ type: "awtrix_studio/republish" }) }, icon("refresh"), this.t("republish")),
    );
    this._notLoaded = h("div", { class: "banner" }, this.t("not_loaded"));
    this._notLoaded.hidden = true;

    this._grid = h("div", { class: "grid" });
    this._indicatorRow = h("div", { class: "indicators" });

    this._main.append(
      this._notLoaded,
      h("section", { class: "card pad live", "aria-label": this.t("live_title") },
        h("div", { class: "row", style: "flex-direction:column;align-items:flex-start;gap:8px" },
          h("div", { class: "screen" }, this._mirrorCanvas), this._mirrorCaption),
        h("div", { class: "live-info" },
          h("h2", {}, this.t("live_title")),
          h("dl", {}, h("div", {}, h("dt", {}, this.t("current_app")), this._currentApp),
            h("div", {}, h("dt", {}, this.t("in_rotation")), this._rotation)),
          this._navRow)),
      h("section", { class: "row between" },
        h("h2", {}, this.t("apps")),
        h("div", { class: "row" },
          h("button", { class: "btn", onClick: () => this._openOrder() }, icon("reorder"), this.t("reorder")),
          h("button", { class: "btn", onClick: () => this._openImport() }, icon("upload"), this.t("import")),
          h("button", { class: "btn", onClick: () => this._openExport() }, icon("download"), this.t("export")),
          h("button", { class: "btn primary", onClick: () => this._openEditor("app", null) }, icon("plus"), this.t("add_app")))),
      this._grid,
      h("h2", {}, this.t("indicators")),
      this._indicatorRow,
    );
    this._drawMirror();
    if (this._snapshot) this._updateOverview();
  }

  _items(kind) {
    return (this._snapshot?.items || []).filter((item) => item.kind === kind);
  }

  _indicatorPages() {
    const pages = {};
    this._items("indicator").forEach((item) => {
      if (item.enabled && item.pages.length) pages[item.data.indicator] = item.pages[0];
    });
    return pages;
  }

  _updateOverview() {
    const snapshot = this._snapshot;
    if (!snapshot || this._view.name !== "overview") return;
    this._notLoaded.hidden = snapshot.loaded;
    this._navRow.hidden = !snapshot.features?.navigate;
    if (!this._subs.screen && snapshot.features?.screen) this._subscribeScreen();

    const apps = this._items("app");
    this._currentApp.textContent = snapshot.current_app || this.t("unknown");
    this._rotation.textContent = `${apps.filter((a) => a.enabled && a.pages.length).length} / ${apps.length}`;

    const structure = snapshot.items.map((i) => `${i.subentry_id}:${i.kind}:${i.data.mode}`).join("|") + `#${(snapshot.order || []).join("|")}`;
    if (structure !== this._structure) {
      this._structure = structure;
      this._buildCards(apps);
      this._buildIndicators();
    }
    apps.forEach((item) => this._updateCard(item));
    this._updateIndicators();
    this._drawMirror();
  }

  _buildCards(apps) {
    clear(this._grid);
    this._cards = new Map();
    // Cards follow the loop order; apps it does not name come last.
    const order = this._snapshot?.order || [];
    const rank = (item) => { const i = order.indexOf(item.data.app_name); return i < 0 ? Infinity : i; };
    apps
      .slice()
      .sort((a, b) => rank(a) - rank(b) || a.data.app_name.localeCompare(b.data.app_name))
      .forEach((item) => {
        const name = item.data.app_name;
        const canvas = h("canvas", { role: "img", "aria-label": this.t("thumbnail", { name }) });
        const toggle = h("button", { class: "switch", role: "switch", "aria-label": this.t("enable", { name }),
          onClick: () => this._call({ type: "awtrix_studio/enable", subentry_id: item.subentry_id, enabled: !this._findItem(item.subentry_id)?.enabled }) },
          h("span"));
        const status = h("span", { class: "status" });
        const show = h("button", { class: "btn small", onClick: () => this._call({ type: "awtrix_studio/show", app_name: name }) }, this.t("show_now"));
        const root = h("article", { class: "card app" },
          h("div", { class: "screen" }, canvas),
          h("div", { class: "row between" },
            h("div", { class: "row" }, h("span", { class: "name" }, name),
              h("span", { class: `chip ${item.data.mode}` }, this.t(item.data.mode))),
            toggle),
          h("div", { class: "row between" }, status,
            h("div", { class: "row" }, show,
              h("button", { class: "btn small", onClick: () => this._openEditor("app", item.subentry_id) }, this.t("edit")))));
        this._cards.set(item.subentry_id, { root, canvas, toggle, status, show });
        this._grid.append(root);
      });
  }

  _updateCard(item) {
    const card = this._cards.get(item.subentry_id);
    if (!card) return;
    const page = item.pages[item.page_index] || item.pages[0];
    const grid = this._appGrid();
    const rendered = renderPage(page, grid);
    drawMatrix(card.canvas, rendered.pixels, { ...this._cells(6, grid.width * grid.scale), gap: 1, icon: rendered.icon, ...grid });
    card.toggle.setAttribute("aria-checked", item.enabled ? "true" : "false");
    const visible = item.enabled && item.pages.length > 0;
    card.root.classList.toggle("dim", !visible);
    card.root.classList.toggle("current", item.on_screen);
    card.show.disabled = !visible;
    let text, kind = "";
    if (!item.enabled) text = this.t("disabled");
    else if (item.error) { text = this.t("failing"); kind = "error"; }
    else if (item.clock_error) { text = this.t("clock_refused"); kind = "error"; }
    else if (!item.pages.length) text = this.t("hidden");
    else if (item.on_screen) { text = `${this.t("on_screen")}${item.pages.length > 1 ? " · " + this.t("pages_n", { n: item.pages.length }) : ""}`; kind = "screen-on"; }
    else { text = `${this.t("visible")}${item.pages.length > 1 ? " · " + this.t("pages_n", { n: item.pages.length }) : ""}`; kind = "ok"; }
    card.status.textContent = text;
    card.status.className = `status ${kind}`;
    if (item.error || item.clock_error) card.status.title = item.error || item.clock_error; else card.status.removeAttribute("title");
  }

  _buildIndicators() {
    clear(this._indicatorRow);
    this._indicatorCards = new Map();
    ["1", "2", "3"].forEach((n) => {
      const item = this._items("indicator").find((i) => i.data.indicator === n);
      if (!item) {
        this._indicatorRow.append(h("div", { class: "card indicator free" }, h("span", { class: "swatch" }),
          h("div", { class: "row between grow" },
            h("div", {}, h("strong", {}, this.t("indicator_n", { n })), h("div", { class: "muted" }, this.t("indicator_free"))),
            h("button", { class: "btn small", onClick: () => this._openEditor("indicator", null, n) }, this.t("configure")))));
        return;
      }
      const swatch = h("span", { class: "swatch" });
      const state = h("div", { class: "muted" });
      const toggle = h("button", { class: "switch", role: "switch", "aria-label": this.t("enable", { name: this.t("indicator_n", { n }) }),
        onClick: () => this._call({ type: "awtrix_studio/enable", subentry_id: item.subentry_id, enabled: !this._findItem(item.subentry_id)?.enabled }) }, h("span"));
      this._indicatorRow.append(h("div", { class: "card indicator" }, swatch,
        h("div", { class: "grow" }, h("strong", {}, this.t("indicator_n", { n })), state),
        toggle,
        h("button", { class: "btn small", onClick: () => this._openEditor("indicator", item.subentry_id) }, this.t("edit"))));
      this._indicatorCards.set(item.subentry_id, { swatch, state, toggle });
    });
  }

  _updateIndicators() {
    this._items("indicator").forEach((item) => {
      const card = this._indicatorCards?.get(item.subentry_id);
      if (!card) return;
      const page = item.pages[0];
      const lit = item.enabled && page && page.color !== undefined && parseColor(page.color, "#000000") !== "#000000";
      card.swatch.style.background = lit ? parseColor(page.color) : "";
      card.state.textContent = `${this.t(item.data.mode)} · ${!item.enabled ? this.t("disabled") : lit ? this.t("lit") : this.t("off")}`;
      card.toggle.setAttribute("aria-checked", item.enabled ? "true" : "false");
    });
  }

  /** The grid apps are drawn on, and how many LEDs each of its pixels lights. */
  _appGrid() {
    const display = this._snapshot?.display || {};
    const scale = display.scale || 1;
    return {
      width: display.width ? Math.floor(display.width / scale) : WIDTH,
      height: display.height ? Math.floor(display.height / scale) : HEIGHT,
      scale,
    };
  }

  /** Cell and gap sizes giving a display `leds` wide about the room of a 32-LED one. */
  _cells(cell, leds) {
    const gap = cell > 8 ? 2 : 1;
    if (leds <= WIDTH) return { cell, gap };
    return { cell: Math.max(2, Math.floor(((cell + gap) * WIDTH) / leds) - 1), gap: 1 };
  }

  _isNG() {
    return this._snapshot?.firmware === "awtrix_ng";
  }

  _findItem(subentryId) {
    return (this._snapshot?.items || []).find((i) => i.subentry_id === subentryId);
  }

  _simulatedScreen() {
    const snapshot = this._snapshot;
    const current = this._items("app").find((a) => a.on_screen && a.pages.length);
    const grid = this._appGrid();
    const rendered = renderPage(current ? current.pages[current.page_index] || current.pages[0] : null, grid);
    addIndicators(rendered.pixels, this._indicatorPages(), grid.width, grid.height);
    return snapshot ? rendered : { pixels: new Array(grid.width * grid.height).fill(null), icon: null };
  }

  _drawMirror() {
    if (!this._mirrorCanvas) return;
    const fresh = this._screen.pixels && Date.now() - this._screen.at < MIRROR_STALE_MS;
    if (fresh) {
      // A large screen (TC002, 52 × 16) gets smaller cells to fit.
      drawMatrix(this._mirrorCanvas, this._screen.pixels, { ...this._cells(12, this._screen.width), glow: true,
        width: this._screen.width, height: this._screen.height });
      this._mirrorCaption.textContent = this.t("live_mirror");
    } else {
      const rendered = this._simulatedScreen();
      const grid = this._appGrid();
      drawMatrix(this._mirrorCanvas, rendered.pixels, { ...this._cells(12, grid.width * grid.scale), icon: rendered.icon, glow: true, ...grid });
      this._mirrorCaption.textContent = this._snapshot?.features?.screen ? this.t("live_waiting") : this.t("simulated_note");
    }
  }

  // ------------------------------------------------------ export / import

  _openPage(name, title) {
    this._view = { name };
    this._stop("screen");
    this._stop("preview");
    clearInterval(this._cycleTimer);
    this._backButton.hidden = false;
    this._clockSelect.hidden = true;
    this._title.textContent = title;
    clear(this._main);
  }

  _itemLabel(item) {
    return item.kind === "app" ? item.data.app_name : this.t("indicator_n", { n: item.data.indicator });
  }

  _openExport(preselected) {
    this._openPage("export", this.t("export_title"));
    const items = [...this._items("app"), ...this._items("indicator")];
    const selected = new Set(preselected || items.map((i) => i.subentry_id));
    const feedback = h("div", { class: "feedback", role: "status" });
    const output = h("textarea", { class: "export-output", readonly: true, rows: 10, spellcheck: "false", "aria-label": this.t("export_content") });
    output.hidden = true;
    const list = h("div", { class: "pick-list" });
    const boxes = [];
    items.forEach((item) => {
      const box = h("input", { type: "checkbox", checked: selected.has(item.subentry_id),
        onChange: (e) => { if (e.target.checked) selected.add(item.subentry_id); else selected.delete(item.subentry_id); } });
      boxes.push([box, item]);
      list.append(h("label", { class: "check pick" }, box,
        h("span", { class: "name" }, this._itemLabel(item)),
        h("span", { class: `chip ${item.data.mode}` }, this.t(item.data.mode))));
    });
    const setAll = (value) => boxes.forEach(([box, item]) => {
      box.checked = value;
      if (value) selected.add(item.subentry_id); else selected.delete(item.subentry_id);
    });

    const fetchDocument = async () => {
      if (!selected.size) { feedback.textContent = this.t("export_none"); return null; }
      const ids = items.filter((i) => selected.has(i.subentry_id)).map((i) => i.subentry_id);
      const doc = await this._call({ type: "awtrix_studio/export", subentry_ids: ids });
      if (!doc) return null;
      const text = JSON.stringify(doc, null, 2);
      output.value = text;
      const single = ids.length === 1 ? this._itemLabel(items.find((i) => i.subentry_id === ids[0])) : this._snapshot?.prefix || "clock";
      return { text, name: `awtrix-studio-${String(single).replace(/[^A-Za-z0-9_-]+/g, "_")}.json`, count: ids.length };
    };
    const download = async () => {
      const result = await fetchDocument();
      if (!result) return;
      const url = URL.createObjectURL(new Blob([result.text], { type: "application/json" }));
      const link = h("a", { href: url, download: result.name, hidden: true });
      this.shadowRoot.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
      feedback.textContent = this.t("export_downloaded", { n: result.count });
    };
    const copy = async () => {
      const result = await fetchDocument();
      if (!result) return;
      try {
        await navigator.clipboard.writeText(result.text);
        feedback.textContent = this.t("export_copied", { n: result.count });
      } catch (_err) {
        // No clipboard access (insecure context, some apps): let the user copy.
        output.hidden = false;
        output.focus();
        output.select();
        feedback.textContent = this.t("export_copy_manual");
      }
    };

    this._main.append(h("section", { class: "card pad transfer" },
      h("h2", {}, this.t("export_title")),
      h("div", { class: "muted" }, this.t("export_help")),
      items.length ? h("div", { class: "row" },
        h("button", { class: "btn small", onClick: () => setAll(true) }, this.t("select_all")),
        h("button", { class: "btn small", onClick: () => setAll(false) }, this.t("select_none"))) : "",
      items.length ? list : h("div", { class: "muted" }, this.t("export_empty")),
      h("div", { class: "row" },
        h("button", { class: "btn primary", onClick: download, disabled: !items.length }, icon("download"), this.t("export_download")),
        h("button", { class: "btn", onClick: copy, disabled: !items.length }, this.t("export_copy")),
        h("button", { class: "btn", onClick: () => this._showOverview() }, this.t("cancel"))),
      feedback, output));
  }

  _openImport() {
    this._openPage("import", this.t("import_title"));
    this._import = { document: null, choices: null, plans: [] };
    const fileInput = h("input", { type: "file", accept: ".json,application/json", id: "import-file",
      onChange: async (e) => {
        const file = e.target.files && e.target.files[0];
        if (!file) return;
        if (file.size > MAX_IMPORT_BYTES) { this._importError("too_big"); return; }
        paste.value = await file.text();
        this._analyze(paste.value);
      } });
    const paste = h("textarea", { id: "import-paste", rows: 6, spellcheck: "false", placeholder: this.t("import_paste_placeholder") });
    this._importStatus = h("div", { class: "status", role: "status" });
    this._importList = h("div", { class: "import-list" });
    this._importButton = h("button", { class: "btn primary", onClick: () => this._runImport(), hidden: true }, this.t("import"));
    this._main.append(
      h("section", { class: "card pad transfer" },
        h("h2", {}, this.t("import_title")),
        h("div", { class: "muted" }, this.t("import_help")),
        h("div", { class: "field" }, h("label", { for: "import-file" }, this.t("import_file")), fileInput),
        h("div", { class: "field" }, h("label", { for: "import-paste" }, this.t("import_paste")), paste),
        h("div", { class: "row" },
          h("button", { class: "btn", onClick: () => this._analyze(paste.value) }, this.t("import_analyze")),
          h("button", { class: "btn", onClick: () => this._showOverview() }, this.t("cancel"))),
        this._importStatus),
      this._importList,
      h("div", { class: "row" }, this._importButton));
  }

  _importError(key) {
    this._importStatus.textContent = this.t(`import_errors.${key}`);
    this._importStatus.className = "status error";
    clear(this._importList);
    this._importButton.hidden = true;
  }

  async _analyze(text) {
    if (!text || !text.trim()) { this._importError("empty"); return; }
    if (text.length > MAX_IMPORT_BYTES) { this._importError("too_big"); return; }
    let document;
    try { document = JSON.parse(text); } catch (_err) { this._importError("not_json"); return; }
    if (!document || typeof document !== "object" || Array.isArray(document)) { this._importError("not_an_export"); return; }
    this._import = { document, choices: null, plans: [] };
    await this._planImport();
  }

  async _planImport() {
    const { document, choices } = this._import;
    const message = { type: "awtrix_studio/import", document, dry_run: true };
    if (choices) message.choices = choices;
    const result = await this._call(message);
    if (!result) return;
    if (result.error) { this._importError(result.error); return; }
    this._import.plans = result.items;
    if (!choices) this._import.choices = result.items.map((p) => ({ action: p.error ? "skip" : p.action, target: p.target || "" }));
    this._renderImportPlans();
  }

  _renderImportPlans() {
    const { plans, choices } = this._import;
    clear(this._importList);
    plans.forEach((plan, index) => this._importList.append(this._importRow(plan, choices[index])));
    const count = plans.filter((p) => p.action !== "skip" && !p.error).length;
    this._importStatus.textContent = this.t("import_ready", { n: count, total: plans.length });
    this._importStatus.className = "status ok";
    this._importButton.hidden = false;
    this._importButton.disabled = count === 0;
    this._importButton.textContent = this.t("import_n", { n: count });
  }

  _importRow(plan, choice) {
    const replan = () => {
      clearTimeout(this._importTimer);
      this._importTimer = setTimeout(() => this._planImport(), 300);
    };
    const invalid = plan.kind === null || (plan.error && !["app_name_exists", "indicator_in_use"].includes(plan.error));
    const include = h("input", { type: "checkbox", checked: choice.action !== "skip", disabled: invalid,
      "aria-label": this.t("import_include", { name: plan.name || "?" }),
      onChange: (e) => { choice.action = e.target.checked ? "add" : "skip"; replan(); } });
    const title = h("div", { class: "row" }, include,
      h("span", { class: "name" }, plan.kind === "indicator" ? this.t("indicator_n", { n: plan.name }) : plan.name || "?"),
      plan.mode ? h("span", { class: `chip ${plan.mode}` }, this.t(plan.mode)) : "",
      plan.kind ? h("span", { class: "chip" }, this.t(plan.kind === "app" ? "kind_app" : "kind_indicator")) : "");
    const row = h("div", { class: `card pad import-item${choice.action === "skip" ? " dim" : ""}` }, title);

    if (!invalid && choice.action !== "skip") {
      let target;
      if (plan.kind === "app") {
        target = h("input", { type: "text", value: choice.target, maxlength: 32, spellcheck: "false",
          onInput: (e) => { choice.target = e.target.value; if (choice.action === "replace") choice.action = "add"; replan(); } });
      } else {
        target = h("select", { onChange: (e) => { choice.target = e.target.value; choice.action = "add"; replan(); } },
          ...["1", "2", "3"].map((n) => h("option", { value: n }, this.t(`indicator_${n}`))));
        target.value = choice.target;
      }
      row.append(h("div", { class: "field" }, h("label", {}, this.t(plan.kind === "app" ? "import_as" : "import_on")), target));
      const taken = plan.error === "app_name_exists" || plan.error === "indicator_in_use" || choice.action === "replace";
      if (taken) {
        row.append(h("label", { class: "check" },
          h("input", { type: "checkbox", checked: choice.action === "replace",
            onChange: (e) => { choice.action = e.target.checked ? "replace" : "add"; replan(); } }),
          h("span", {}, this.t(plan.kind === "app" ? "import_replace_app" : "import_replace_indicator"))));
      }
    }
    if (plan.error && !(choice.action === "replace" && plan.replaces)) {
      row.append(h("div", { class: "error-text" }, this.t(`errors.${plan.error}`) + (plan.detail ? ` ${plan.detail}` : "")));
    }
    if (plan.replaces && choice.action === "replace") row.append(h("div", { class: "muted" }, this.t("import_will_replace")));
    if (plan.warnings.includes("other_firmware")) row.append(h("div", { class: "warn" }, this.t("import_other_firmware")));
    if (plan.missing_entities.length) {
      row.append(h("div", { class: "warn" }, this.t("import_missing", { list: plan.missing_entities.join(", ") })));
    }
    if (plan.template) {
      row.append(h("details", {}, h("summary", {}, this.t("import_show_template")), h("pre", {}, plan.template)));
    }
    return row;
  }

  async _runImport() {
    const { document, choices } = this._import;
    this._importButton.disabled = true;
    const result = await this._call({ type: "awtrix_studio/import", document, choices });
    if (!result) { this._importButton.disabled = false; return; }
    if (result.error) { this._importError(result.error); return; }
    this._showOverview();
    this._main.prepend(h("div", { class: "card pad notice", role: "status" }, this.t("import_done", { n: result.imported })));
  }

  // ----------------------------------------------------------- loop order

  async _openOrder() {
    this._openPage("order", this.t("order_title"));
    const studio = this._items("app").map((i) => i.data.app_name);
    const status = h("div", { class: "muted", role: "status" }, this.t("order_loading"));
    const list = h("ol", { class: "order-list", "aria-label": this.t("order_title") });
    const feedback = h("div", { class: "feedback", role: "status" });
    const save = h("button", { class: "btn primary", disabled: true }, this.t("save"));
    this._main.append(h("section", { class: "card pad transfer" },
      h("h2", {}, this.t("order_title")),
      h("div", { class: "muted" }, this.t("order_help")),
      status, list,
      h("div", { class: "row" }, save, h("button", { class: "btn", onClick: () => this._showOverview() }, this.t("cancel"))),
      feedback));

    const result = await this._call({ type: "awtrix_studio/loop" });
    if (this._view.name !== "order") return;
    const loop = result ? result.loop : null;
    const inLoop = new Set(loop || []);
    let names = arrangedLoop(loop, this._snapshot?.order || [], studio);
    status.textContent = loop ? "" : this.t("order_unreachable");
    status.hidden = Boolean(loop);

    const kind = (name) => (studio.includes(name) ? "studio" : BUILTIN_APPS.includes(name) ? "builtin" : "other");
    const label = (name) => (kind(name) === "builtin" ? this.t(`builtin_apps.${name}`) : name);
    let dragged = null;
    const move = (from, to) => {
      if (to < 0 || to >= names.length || from === to) return;
      const [name] = names.splice(from, 1);
      names.splice(to, 0, name);
      save.disabled = false;
      feedback.textContent = "";
      render();
      const button = list.children[to]?.querySelector(from < to ? "[data-dir=down]" : "[data-dir=up]");
      if (button && !button.disabled) button.focus();
    };
    const render = () => {
      clear(list);
      names.forEach((name, index) => {
        const item = h("li", { class: `order-item${loop && !inLoop.has(name) ? " absent" : ""}`, draggable: "true" },
          h("span", { class: "grip", "aria-hidden": "true" }, "⋮⋮"),
          h("span", { class: "muted order-pos" }, String(index + 1)),
          h("div", { class: "grow" },
            h("span", { class: "name" }, label(name)),
            h("span", { class: `chip${kind(name) === "studio" ? " simple" : ""}` }, this.t(`order_${kind(name)}`)),
            loop && !inLoop.has(name) ? h("span", { class: "muted" }, this.t("order_not_in_loop")) : null),
          h("button", { class: "icon-btn small", "data-dir": "up", disabled: index === 0, "aria-label": this.t("move_up", { name: label(name) }),
            onClick: () => move(index, index - 1) }, icon("up")),
          h("button", { class: "icon-btn small", "data-dir": "down", disabled: index === names.length - 1,
            "aria-label": this.t("move_down", { name: label(name) }), onClick: () => move(index, index + 1) }, icon("down")));
        item.addEventListener("dragstart", (e) => { dragged = index; item.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; });
        item.addEventListener("dragend", () => { dragged = null; item.classList.remove("dragging"); });
        item.addEventListener("dragover", (e) => { if (dragged !== null) e.preventDefault(); });
        item.addEventListener("drop", (e) => { e.preventDefault(); if (dragged !== null) move(dragged, index); });
        list.append(item);
      });
    };
    render();
    save.addEventListener("click", async () => {
      save.disabled = true;
      const response = await this._call({ type: "awtrix_studio/order", order: names });
      if (!response) { save.disabled = false; return; }
      names = arrangedLoop(loop, response.order, studio);
      feedback.textContent = this.t("order_saved");
    });
  }

  // --------------------------------------------------------------- editor

  _openEditor(kind, subentryId, presetIndicator) {
    const item = subentryId ? this._findItem(subentryId) : null;
    this._view = { name: "edit", kind, subentryId, item };
    this._previewTab = "sim";
    this._preview = { pages: [], error: null, template: null };
    this._pageIndex = 0;
    this._stop("screen");
    this._backButton.hidden = false;
    this._clockSelect.hidden = true;
    this._form = this._initialForm(kind, item, presetIndicator);
    if (kind === "app") this._title.textContent = item ? `${this.t("edit_app")} ${item.data.app_name}` : this.t("new_app");
    else this._title.textContent = item ? this.t("edit_indicator", { n: item.data.indicator }) : this.t("new_indicator");
    this._renderEditor();
    this._refreshPreview();
  }

  _initialForm(kind, item, presetIndicator) {
    const d = item ? item.data : {};
    if (kind === "app") {
      const segments = [d, ...(Array.isArray(d.segments) ? d.segments : [])].map((s) => this._segmentForm(s));
      return {
        mode: d.mode || "simple",
        app_name: d.app_name || "",
        ...segments[0],
        segments,
        segment: 0,
        separator: typeof d.separator === "string" ? d.separator : " ",
        icon: d.icon || "",
        progress: d.progress ?? false,
        progress_entity: d.progress_entity || "",
        progress_min: d.progress_min ?? 0,
        progress_max: d.progress_max ?? 100,
        progress_same_color: !d.progress_color,
        progress_color: toHex(d.progress_color, "#4FC3F7"),
        progress_bg: toHex(d.progress_bg, "#2E2E2E"),
        template: d.template || (this._isNG() ? NG_STARTER_APP_TEMPLATE : STARTER_APP_TEMPLATE),
        page_interval: d.page_interval ?? 1.5,
        cycle_only_on_screen: d.cycle_only_on_screen ?? true,
        hide_when_empty: d.hide_when_empty ?? true,
        lifetime: d.lifetime ?? 0,
        display: this._displayForm(d.display || {}),
      };
    }
    const used = this._items("indicator").map((i) => i.data.indicator);
    const free = ["1", "2", "3"].filter((n) => !used.includes(n));
    return {
      mode: d.mode || "simple",
      indicator: d.indicator || presetIndicator || free[0] || "1",
      entity_id: d.entity_id || "",
      active_state: d.active_state || "on",
      color: toHex(d.color, "#FF8C00"),
      effect: d.effect || "none",
      effect_interval: d.effect_interval ?? 1000,
      color_mode: d.color_mode || "state",
      ...this._gradientForm(d),
      template: d.template || (this._isNG() ? NG_STARTER_INDICATOR_TEMPLATE : STARTER_INDICATOR_TEMPLATE),
    };
  }

  /** The fields of one entity of a simple app. */
  _segmentForm(d) {
    return {
      entity_id: d.entity_id || "",
      label: d.label || "",
      color: toHex(d.color, "#FFFFFF"),
      decimals: d.decimals ?? "",
      show_unit: d.show_unit ?? true,
      hide_when_zero: d.hide_when_zero ?? false,
      threshold_1: d.threshold_1 ?? "",
      color_1: toHex(d.color_1, "#FFB74D"),
      threshold_2: d.threshold_2 ?? "",
      color_2: toHex(d.color_2, "#FF6B5B"),
      color_mode: d.color_mode || (d.threshold_1 != null || d.threshold_2 != null ? "steps" : "fixed"),
      ...this._gradientForm(d),
    };
  }

  /** Store the entity being edited back into the list. */
  _syncSegment() {
    const f = this._form;
    SEGMENT_KEYS.forEach((key) => { f.segments[f.segment][key] = f[key]; });
  }

  _selectSegment(index) {
    const f = this._form;
    this._syncSegment();
    f.segment = index;
    SEGMENT_KEYS.forEach((key) => { f[key] = f.segments[index][key]; });
    this._renderFields();
  }

  _addSegment() {
    this._syncSegment();
    this._form.segments.push(this._segmentForm({}));
    this._selectSegment(this._form.segments.length - 1);
    this._fieldsBox.querySelector("#f-entity_id")?.focus();
    this._schedulePreview();
  }

  _removeSegment() {
    const f = this._form;
    f.segments.splice(f.segment, 1);
    const index = Math.min(f.segment, f.segments.length - 1);
    f.segment = index;
    SEGMENT_KEYS.forEach((key) => { f[key] = f.segments[index][key]; });
    this._renderFields();
    this._schedulePreview();
  }

  _gradientForm(d) {
    return {
      gradient: d.gradient || "high_bad",
      _presetTouched: Boolean(d.gradient),
      gradient_min: d.gradient_min ?? "",
      gradient_max: d.gradient_max ?? "",
      gradient_stops: (d.gradient_stops || []).map((stop) => ({ value: stop.value, color: toHex(stop.color, "#FFFFFF") })),
    };
  }

  _displayForm(d) {
    const settings = d.effectSettings || {};
    return {
      duration: d.duration ?? "",
      repeat: d.repeat ?? "",
      scrollSpeed: d.scrollSpeed ?? "",
      noScroll: d.noScroll ?? false,
      pushIcon: String(d.pushIcon ?? 0),
      textCase: String(d.textCase ?? 0),
      center: d.center ?? true,
      topText: d.topText ?? false,
      textOffset: d.textOffset ?? "",
      style: d.rainbow ? "rainbow" : d.gradient ? "gradient" : d.blinkText ? "blink" : d.fadeText ? "fade" : "none",
      gradient_from: toHex(d.gradient?.[0], "#FF0000"),
      gradient_to: toHex(d.gradient?.[1], "#0000FF"),
      style_interval: d.blinkText ?? d.fadeText ?? 500,
      background_on: d.background !== undefined,
      background: toHex(d.background, "#000000"),
      effect: d.effect || "",
      effect_speed: settings.speed ?? "",
      effect_palette: settings.palette || "",
      effect_blend: settings.blend === undefined ? "" : String(settings.blend),
      overlay: d.overlay || "",
      lifetimeMode: String(d.lifetimeMode ?? 0),
      save: d.save ?? false,
    };
  }

  /** AWTRIX keys of the display options; the server drops the defaults. */
  _displayPayload() {
    const d = this._form.display;
    const out = {
      duration: numberOrEmpty(d.duration), repeat: numberOrEmpty(d.repeat), scrollSpeed: numberOrEmpty(d.scrollSpeed),
      noScroll: d.noScroll, pushIcon: Number(d.pushIcon), textCase: Number(d.textCase), center: d.center, topText: d.topText,
      textOffset: numberOrEmpty(d.textOffset), lifetimeMode: Number(d.lifetimeMode), save: d.save,
    };
    if (d.style === "rainbow") out.rainbow = true;
    else if (d.style === "gradient") out.gradient = [d.gradient_from, d.gradient_to];
    else if (d.style === "blink") out.blinkText = numberOrEmpty(d.style_interval);
    else if (d.style === "fade") out.fadeText = numberOrEmpty(d.style_interval);
    if (d.background_on) out.background = d.background;
    if (d.effect) {
      out.effect = d.effect;
      const settings = {};
      if (numberOrEmpty(d.effect_speed) !== "") settings.speed = numberOrEmpty(d.effect_speed);
      if (d.effect_palette.trim()) settings.palette = d.effect_palette.trim();
      if (d.effect_blend !== "") settings.blend = d.effect_blend === "true";
      out.effectSettings = settings;
    }
    if (d.overlay) out.overlay = d.overlay;
    return out;
  }

  _progressPayload() {
    const f = this._form;
    if (!f.progress) return { progress: false };
    const payload = { progress: true, progress_entity: f.progress_entity.trim(), progress_min: numberOrEmpty(f.progress_min),
      progress_max: numberOrEmpty(f.progress_max), progress_bg: f.progress_bg };
    if (!f.progress_same_color) payload.progress_color = f.progress_color;
    return payload;
  }

  _gradientPayload(f = this._form) {
    if (f.color_mode !== "gradient") return { color_mode: f.color_mode };
    if (f.gradient === "custom") {
      return { color_mode: "gradient", gradient: "custom",
        gradient_stops: f.gradient_stops.map((stop) => ({ value: numberOrEmpty(stop.value), color: stop.color })) };
    }
    return { color_mode: "gradient", gradient: f.gradient, gradient_min: numberOrEmpty(f.gradient_min), gradient_max: numberOrEmpty(f.gradient_max) };
  }

  _segmentPayload(s) {
    return { entity_id: s.entity_id, label: s.label, color: s.color, decimals: numberOrEmpty(s.decimals),
      show_unit: s.show_unit, hide_when_zero: s.hide_when_zero,
      threshold_1: numberOrEmpty(s.threshold_1), color_1: s.color_1, threshold_2: numberOrEmpty(s.threshold_2), color_2: s.color_2,
      ...this._gradientPayload(s) };
  }

  _payload() {
    const f = this._form;
    const { kind } = this._view;
    if (kind === "app" && f.mode === "simple") {
      this._syncSegment();
      const [first, ...next] = f.segments.map((s) => this._segmentPayload(s));
      return { mode: "simple", app_name: f.app_name, ...first, segments: next, separator: f.separator, icon: f.icon,
        ...this._progressPayload(), display: this._displayPayload() };
    }
    if (kind === "app") {
      return { mode: "template", app_name: f.app_name, template: f.template, page_interval: numberOrEmpty(f.page_interval),
        cycle_only_on_screen: f.cycle_only_on_screen, hide_when_empty: f.hide_when_empty, lifetime: numberOrEmpty(f.lifetime),
        display: this._displayPayload() };
    }
    if (f.mode === "simple") {
      return { mode: "simple", indicator: f.indicator, entity_id: f.entity_id, active_state: f.active_state, color: f.color,
        effect: f.effect, effect_interval: numberOrEmpty(f.effect_interval), ...this._gradientPayload() };
    }
    return { mode: "template", indicator: f.indicator, template: f.template };
  }

  _renderEditor() {
    clear(this._main);
    const { kind, item } = this._view;
    this._errors = {};
    this._fieldsBox = h("div", { class: "fields" });
    this._modeTabs = h("div", { class: "tabs", role: "tablist", "aria-label": this.t("mode") });
    ["simple", "template"].forEach((mode) => this._modeTabs.append(
      h("button", { role: "tab", "aria-selected": this._form.mode === mode ? "true" : "false", onClick: () => this._setMode(mode) }, this.t(mode))));
    this._saveFeedback = h("div", { class: "feedback", role: "status" });

    const actions = h("div", { class: "row" },
      h("button", { class: "btn primary", onClick: () => this._save() }, this.t("save")),
      h("button", { class: "btn", onClick: () => this._showOverview() }, this.t("cancel")),
      item ? h("button", { class: "btn", onClick: () => this._openExport([item.subentry_id]) }, icon("download"), this.t("export")) : null,
      item ? h("button", { class: "btn danger", onClick: () => this._delete() }, this.t("delete")) : null);

    // Preview column
    this._previewCanvas = h("canvas", { role: "img", "aria-label": this.t("preview") });
    this._previewTabs = h("div", { class: "tabs", role: "tablist", "aria-label": this.t("preview") });
    [["sim", "simulated"], ["real", "real_screen"]].forEach(([id, label]) => this._previewTabs.append(
      h("button", { role: "tab", "aria-selected": this._previewTab === id ? "true" : "false", onClick: () => this._setPreviewTab(id) }, this.t(label))));
    this._previewTabs.hidden = !this._snapshot?.features?.screen;
    this._pills = h("div", { class: "pills" });
    this._previewNote = h("div", { class: "muted" });
    this._previewStatus = h("div", { class: "status" });
    this._previewDetail = h("div", { class: "muted" });
    this._json = h("pre", { "aria-label": this.t("json_title") });
    this._generated = h("pre", { "aria-label": this.t("generated_title") });
    this._generatedBox = h("div", { class: "card pad", style: "display:flex;flex-direction:column;gap:10px" },
      h("div", { class: "row between" }, h("h2", {}, this.t("generated_title")),
        h("button", { class: "btn small", onClick: () => this._convert() }, this.t("convert"))),
      this._generated);
    this._testFeedback = h("div", { class: "feedback", role: "status" });

    this._main.append(
      h("div", { class: "row between" }, this._modeTabs, actions),
      this._saveFeedback,
      h("div", { class: "editor" },
        h("section", { class: "card pad form", "aria-label": kind === "app" ? this.t("edit_app") : this.t("indicator") }, this._fieldsBox),
        h("section", { class: "preview" },
          h("div", { class: "card pad", style: "display:flex;flex-direction:column;gap:12px" },
            h("div", { class: "row between" }, h("h2", {}, this.t("preview")), this._previewTabs),
            h("div", { class: "screen", style: "align-self:center" }, this._previewCanvas),
            this._pills, this._previewNote, this._previewStatus, this._previewDetail,
            h("div", { class: "row" }, h("button", { class: "btn", onClick: () => this._test() }, this.t("test")),
              h("span", { class: "muted" }, this.t("test_note"))),
            this._testFeedback),
          h("div", { class: "card pad", style: "display:flex;flex-direction:column;gap:10px" }, h("h2", {}, this.t("json_title")), this._json),
          this._generatedBox)));
    this._renderFields();
    this._drawPreview();
  }

  _field(key, label, control, help, wide) {
    const error = h("div", { class: "error-text", role: "alert" });
    this._errors[key] = error;
    return h("div", { class: `field${wide ? " wide" : ""}` },
      h("label", { for: `f-${key}` }, label), control, help ? h("div", { class: "muted" }, help) : null, error);
  }

  _input(key, type = "text", extra = {}) {
    return h("input", { id: `f-${key}`, type, value: this._form[key], ...extra,
      onInput: (e) => {
        this._form[key] = e.target.value;
        if (key === "entity_id") this._entityChanged();
        this._schedulePreview();
      } });
  }

  _entityChanged() {
    const f = this._form;
    if (f.color_mode !== "gradient" || f.gradient === "custom" || !this._hass.states[f.entity_id]) { this._updateGradientBar(); return; }
    const hadRange = f.gradient_min !== "" || f.gradient_max !== "";
    this._suggestRange();
    if (!hadRange && (f.gradient_min !== "" || f.gradient_max !== "")) {
      const body = this._fieldsBox.querySelector(".color-body");
      if (body) this._renderColorBody(body);
    } else {
      this._updateGradientBar();
    }
  }

  _check(key, label, help) {
    const error = h("div", { class: "error-text", role: "alert" });
    this._errors[key] = error;
    return h("div", { class: "field" },
      h("label", { class: "check" },
        h("input", { type: "checkbox", checked: this._form[key], onChange: (e) => { this._form[key] = e.target.checked; this._schedulePreview(); } }),
        h("span", {}, label, help ? h("span", { class: "muted", style: "display:block" }, help) : null)), error);
  }

  _entityInput(key = "entity_id", placeholder = this.t("entity_placeholder")) {
    const listId = `awtrix-studio-entities-${key}`;
    const list = h("datalist", { id: listId });
    Object.keys(this._hass.states).sort().slice(0, 5000).forEach((id) => list.append(h("option", { value: id })));
    return h("div", {}, this._input(key, "text", { list: listId, autocomplete: "off", spellcheck: "false", placeholder }), list);
  }

  // ------------------------------------------------------------- icons

  _iconField() {
    const f = this._form;
    this._iconPreview = h("canvas", { class: "icon-preview", width: 8, height: 8, "aria-hidden": "true" });
    const input = this._input("icon", "text", { maxlength: 64, spellcheck: "false", placeholder: "lametric:2422" });
    input.addEventListener("input", () => this._drawIconPreview());
    this._iconInput = input;
    const control = h("div", { class: "row icon-row" }, this._iconPreview, input,
      h("button", { class: "btn small", type: "button", onClick: () => this._openIconPicker() }, icon("search"), this.t("icon_pick")));
    this._drawIconPreview();
    return this._field("icon", this.t("icon"), control, this.t("icon_help_studio"), true);
  }

  async _drawIconPreview() {
    const canvas = this._iconPreview;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#0b0b0d";
    ctx.fillRect(0, 0, 8, 8);
    // A bare number is a LaMetric icon too, unless the clock has that file.
    const value = this._form.icon || "";
    const match = /^(?:lametric:)?(\d{1,7})$/.exec(value);
    canvas.title = match ? "" : this.t("icon_on_clock");
    if (!match) {
      (clockIcon(value, () => this._drawIconPreview()) || []).forEach((color, i) => {
        if (!color) return;
        ctx.fillStyle = color;
        ctx.fillRect(i % 8, Math.floor(i / 8), 1, 1);
      });
      return;
    }
    const pixels = await this._iconPixels(Number(match[1]));
    if (!pixels || this._form.icon !== value) return;
    pixels.forEach((value, i) => {
      if (!Number.isInteger(value) || value <= 0) return;
      ctx.fillStyle = parseColor(value);
      ctx.fillRect(i % 8, Math.floor(i / 8), 1, 1);
    });
  }

  // Icons stored on the clock are read through Home Assistant, per clock.
  _setClockIcons(snapshot) {
    const entryId = snapshot.entry_id;
    if (!snapshot.features?.icon_files || !snapshot.loaded) return setClockIcons(null, null);
    setClockIcons(entryId, (name) => this._hass.callWS({ type: "awtrix_studio/icons/clock", entry_id: entryId, name }));
  }

  async _iconPixels(id) {
    this._iconCache = this._iconCache || new Map();
    if (!this._iconCache.has(id)) {
      this._iconCache.set(id, this._hass.callWS({ type: "awtrix_studio/icons/get", icon_id: id })
        .then((r) => (Array.isArray(r.pixels) && r.pixels.length === 64 ? r.pixels : null))
        .catch(() => null));
    }
    return this._iconCache.get(id);
  }

  _openIconPicker() {
    this._closeIconPicker();
    const search = h("input", { type: "search", class: "picker-search", placeholder: this.t("icon_search_placeholder"),
      "aria-label": this.t("icon_search_placeholder"), maxlength: 64 });
    const grid = h("div", { class: "picker-grid", role: "listbox", "aria-label": this.t("icon_pick") });
    const status = h("div", { class: "muted", role: "status" });
    const more = h("button", { class: "btn small", type: "button", hidden: true }, this.t("icon_more"));
    const state = { query: "", page: 1, token: 0 };

    const run = async (append) => {
      const token = ++state.token;
      status.textContent = this.t("icon_searching");
      more.hidden = true;
      let result;
      try {
        result = await this._hass.callWS({ type: "awtrix_studio/icons/search", query: state.query, page: state.page });
      } catch (err) {
        if (token === state.token) status.textContent = this.t("icon_unavailable");
        return;
      }
      if (token !== state.token) return;
      if (!append) clear(grid);
      (result.icons || []).forEach((item) => {
        const id = Number(item.id);
        if (!Number.isInteger(id) || id <= 0) return;
        const img = h("img", { src: `${LAMETRIC_THUMB}${id}`, alt: "", width: 32, height: 32, loading: "lazy" });
        grid.append(h("button", { class: "picker-item", type: "button", role: "option", title: String(item.name || id),
          onClick: () => this._pickIcon(id) },
          img, h("span", { class: "picker-name" }, String(item.name || id)),
          h("span", { class: "muted picker-id" }, `${id}${item.animated ? " · " + this.t("icon_animated") : ""}`)));
      });
      status.textContent = grid.children.length ? "" : this.t("icon_none");
      more.hidden = !result.more;
    };
    search.addEventListener("input", () => {
      clearTimeout(this._iconSearchTimer);
      this._iconSearchTimer = setTimeout(() => { state.query = search.value.trim(); state.page = 1; run(false); }, ICON_SEARCH_DEBOUNCE);
    });
    more.addEventListener("click", () => { state.page += 1; run(true); });

    const dialog = h("div", { class: "picker", role: "dialog", "aria-modal": "true", "aria-label": this.t("icon_pick") },
      h("div", { class: "row between" }, h("h2", {}, this.t("icon_pick")),
        h("button", { class: "icon-btn", type: "button", "aria-label": this.t("cancel"), onClick: () => this._closeIconPicker() }, icon("close"))),
      h("div", { class: "muted" }, this.t("icon_picker_help")),
      search, status, grid, more);
    this._picker = h("div", { class: "picker-backdrop", onClick: (e) => { if (e.target === this._picker) this._closeIconPicker(); } }, dialog);
    this._picker.addEventListener("keydown", (e) => { if (e.key === "Escape") this._closeIconPicker(); });
    this.shadowRoot.append(this._picker);
    search.focus();
    run(false);
  }

  _closeIconPicker() {
    clearTimeout(this._iconSearchTimer);
    if (this._picker) this._picker.remove();
    this._picker = null;
  }

  _pickIcon(id) {
    this._form.icon = `lametric:${id}`;
    if (this._iconInput) this._iconInput.value = this._form.icon;
    this._closeIconPicker();
    this._drawIconPreview();
    this._schedulePreview();
  }

  // ---------------------------------------------------------- progress

  _progressSection() {
    const f = this._form;
    const body = h("div", { class: "color-body" });
    const render = () => {
      clear(body);
      if (!f.progress) return;
      const sameColor = h("label", { class: "check" },
        h("input", { type: "checkbox", checked: f.progress_same_color,
          onChange: (e) => { f.progress_same_color = e.target.checked; render(); this._schedulePreview(); } }),
        h("span", {}, this.t("progress_same_color")));
      body.append(
        this._field("progress_entity", this.t("progress_entity"), this._entityInput("progress_entity", this.t("progress_entity_placeholder")), this.t("progress_entity_help"), true),
        h("div", { class: "fields" },
          this._field("progress_min", this.t("progress_min"), this._input("progress_min", "number", { step: "any" })),
          this._field("progress_max", this.t("progress_max"), this._input("progress_max", "number", { step: "any" }))),
        sameColor,
        h("div", { class: "row" },
          f.progress_same_color ? "" : this._field("progress_color", this.t("progress_color"), this._input("progress_color", "color")),
          this._field("progress_bg", this.t("progress_bg"), this._input("progress_bg", "color"))));
    };
    const toggle = h("label", { class: "check" },
      h("input", { type: "checkbox", checked: f.progress,
        onChange: (e) => { f.progress = e.target.checked; render(); this._schedulePreview(); } }),
      h("span", {}, this.t("progress_show"), h("span", { class: "muted", style: "display:block" }, this.t("progress_help"))));
    render();
    return h("fieldset", { class: "field wide" }, h("legend", {}, this.t("progress")), toggle, body);
  }

  _renderFields() {
    clear(this._fieldsBox);
    this._errors = {};
    const { kind, item } = this._view;
    const f = this._form;
    const box = this._fieldsBox;
    Array.from(this._modeTabs.children).forEach((tab, i) => tab.setAttribute("aria-selected", ["simple", "template"][i] === f.mode ? "true" : "false"));

    if (kind === "app" && !item) box.append(this._field("app_name", this.t("app_name"), this._input("app_name", "text", { maxlength: 32, spellcheck: "false" }), this.t("app_name_help"), true));
    if (kind === "indicator" && !item) {
      const used = this._items("indicator").map((i) => i.data.indicator);
      const select = h("select", { id: "f-indicator", onChange: (e) => { f.indicator = e.target.value; this._schedulePreview(); } },
        ...["1", "2", "3"].filter((n) => !used.includes(n) || n === f.indicator).map((n) => h("option", { value: n }, this.t(`indicator_${n}`))));
      select.value = f.indicator;
      box.append(this._field("indicator", this.t("indicator"), select, null, true));
    }

    if (f.mode === "template") {
      const textarea = h("textarea", { id: "f-template", spellcheck: "false", rows: 16,
        onInput: (e) => { f.template = e.target.value; this._schedulePreview(); } });
      textarea.value = f.template;
      box.append(this._field("template", this.t("template_label"), textarea, kind === "app" ? this.t("template_help") : this.t("template_help_indicator"), true));
      if (kind === "app") {
        box.append(
          this._field("page_interval", this.t("page_interval"), this._input("page_interval", "number", { min: 0.5, max: 60, step: 0.1 })),
          this._field("lifetime", this.t("lifetime"), this._input("lifetime", "number", { min: 0, max: 86400, step: 1 })),
          this._check("cycle_only_on_screen", this.t("cycle_only_on_screen"), this.t("cycle_help")),
          this._check("hide_when_empty", this.t("hide_when_empty"), this.t("hide_help")),
          this._displaySection());
      }
      return;
    }

    if (kind === "app") {
      box.append(this._segmentsSection(), this._iconField());
      if (f.segments.length > 1) {
        box.append(this._field("separator", this.t("separator"), this._input("separator", "text", { maxlength: 8, spellcheck: "false" }),
          this.t("separator_help")));
      }
      box.append(this._progressSection(), this._displaySection());
    } else {
      box.append(this._field("entity_id", this.t("entity"), this._entityInput(), null, true));
      const effect = h("select", { id: "f-effect", onChange: (e) => { f.effect = e.target.value; this._schedulePreview(); } },
        ...["none", "blink", "fade"].map((v) => h("option", { value: v }, this.t(`effect_${v}`))));
      effect.value = f.effect;
      box.append(
        this._colorSection(["state", "gradient"], "indicator_lights"),
        this._field("effect", this.t("effect"), effect),
        this._field("effect_interval", this.t("effect_interval"), this._input("effect_interval", "number", { min: 100, max: 10000, step: 100 })));
    }
  }

  // ------------------------------------------------- entities of a line

  /** The entities of a simple app: tabs to pick one, and its fields. */
  _segmentsSection() {
    const f = this._form;
    const many = f.segments.length > 1;
    const name = (s, i) => s.label || s.entity_id.split(".").pop() || this.t("segment_n", { n: i + 1 });
    const fieldset = h("fieldset", { class: "field wide" }, h("legend", {}, this.t(many ? "entities_line" : "entity")));
    if (many) {
      const tabs = h("div", { class: "tabs segment-tabs", role: "tablist", "aria-label": this.t("entities_line") });
      f.segments.forEach((s, i) => tabs.append(h("button", { role: "tab", type: "button", "aria-selected": i === f.segment ? "true" : "false",
        onClick: () => { if (i !== f.segment) this._selectSegment(i); } }, `${i + 1}. ${name(s, i)}`)));
      // Tab names follow what is typed, without rebuilding the form.
      fieldset.addEventListener("input", () => {
        this._syncSegment();
        tabs.children[f.segment].textContent = `${f.segment + 1}. ${name(f.segments[f.segment], f.segment)}`;
      });
      fieldset.append(tabs);
      if (this._isNG()) fieldset.append(h("div", { class: "muted" }, this.t("segments_ng_note")));
    }
    fieldset.append(h("div", { class: "fields" },
      this._field("entity_id", this.t("entity"), this._entityInput(), null, true),
      this._field("label", this.t("label"), this._input("label", "text", { maxlength: 64 }), this.t("label_help")),
      this._field("color", this.t("text_color"), this._input("color", "color")),
      this._field("decimals", this.t("decimals"), this._input("decimals", "number", { min: 0, max: 4, step: 1 }), this.t("decimals_help")),
      this._check("show_unit", this.t("show_unit")),
      this._check("hide_when_zero", this.t("hide_when_zero"), many ? this.t("hide_when_zero_segment") : null),
      this._colorSection(["fixed", "steps", "gradient"], "color_by_value")));
    fieldset.append(h("div", { class: "row" },
      f.segments.length < MAX_SEGMENTS
        ? h("button", { class: "btn small", type: "button", onClick: () => this._addSegment() }, icon("plus"), this.t("add_segment")) : null,
      many ? h("button", { class: "btn small", type: "button", onClick: () => this._removeSegment() }, this.t("remove_segment")) : null));
    return fieldset;
  }

  // ---------------------------------------------------- display options

  _displaySection() {
    const fieldset = h("fieldset", { class: "field wide" });
    // Groups stay open or closed when the section is rebuilt.
    this._displayOpen = this._displayOpen || { timing: true };
    const render = () => {
      clear(fieldset);
      Object.keys(this._errors).filter((key) => key.startsWith("display.")).forEach((key) => delete this._errors[key]);
      fieldset.append(h("legend", {}, this.t("display")), h("div", { class: "muted" }, this.t("display_help")),
        this._displayGroup("timing", this._displayTiming()),
        this._displayGroup("text", this._displayText(render)),
        this._displayGroup("background", this._displayBackground(render)),
        // AWTRIX NG has no `save`: only template apps keep something here.
        this._isNG() && this._form.mode !== "template" ? null : this._displayGroup("advanced", this._displayAdvanced()));
    };
    render();
    return fieldset;
  }

  _displayGroup(name, body) {
    const details = h("details", { class: "display-group", open: Boolean(this._displayOpen[name]) },
      h("summary", {}, this.t(`display_${name}`)), body);
    details.addEventListener("toggle", () => { this._displayOpen[name] = details.open; });
    return details;
  }

  /** A display option control; errors come back as `display.<awtrix key>`. */
  _dField(key, label, control, help, wide) {
    const error = h("div", { class: "error-text", role: "alert" });
    this._errors[`display.${key}`] = error;
    return h("div", { class: `field${wide ? " wide" : ""}` },
      h("label", { for: `d-${key}` }, label), control, help ? h("div", { class: "muted" }, help) : null, error);
  }

  _dInput(key, type = "text", extra = {}, onChange) {
    const d = this._form.display;
    return h("input", { id: `d-${key}`, type, value: d[key], ...extra,
      onInput: (e) => { d[key] = e.target.value; if (onChange) onChange(); this._schedulePreview(); } });
  }

  _dSelect(key, options, onChange) {
    const d = this._form.display;
    const select = h("select", { id: `d-${key}`, onChange: (e) => { d[key] = e.target.value; if (onChange) onChange(); this._schedulePreview(); } },
      ...options.map(([value, label]) => h("option", { value }, label)));
    select.value = d[key];
    return select;
  }

  _dCheck(key, label, help, onChange) {
    const d = this._form.display;
    const error = h("div", { class: "error-text", role: "alert" });
    this._errors[`display.${key}`] = error;
    return h("div", { class: "field" },
      h("label", { class: "check" },
        h("input", { type: "checkbox", checked: d[key], onChange: (e) => { d[key] = e.target.checked; if (onChange) onChange(); this._schedulePreview(); } }),
        h("span", {}, label, help ? h("span", { class: "muted", style: "display:block" }, help) : null)), error);
  }

  _displayTiming() {
    return h("div", { class: "fields" },
      this._dField("duration", this.t("duration"), this._dInput("duration", "number", { min: 1, max: 3600, step: 1, placeholder: this.t("clock_default") }),
        this.t("duration_help")),
      this._dField("repeat", this.t("repeat"), this._dInput("repeat", "number", { min: -1, max: 100, step: 1, placeholder: this.t("no_limit") }),
        this.t("repeat_help")),
      this._dField("scrollSpeed", this.t("scroll_speed"), this._dInput("scrollSpeed", "number", { min: 10, max: 500, step: 10, placeholder: "100" }),
        this.t("scroll_speed_help")),
      this._dField("pushIcon", this.t("push_icon"), this._dSelect("pushIcon", [0, 1, 2].map((n) => [String(n), this.t(`push_icon_${n}`)]))),
      this._dCheck("noScroll", this.t("no_scroll")));
  }

  _displayText(rerender) {
    const d = this._form.display;
    const styles = ["none", "rainbow", "gradient", "blink", "fade"].map((v) => [v, this.t(`text_style_${v}`)]);
    const style = this._dField("style", this.t("text_style"), this._dSelect("style", styles, rerender),
      this.t(this._isNG() ? "text_style_help_ng" : "text_style_help"));
    // The server reports a style conflict on the key it refused.
    ["rainbow", "gradient", "blinkText", "fadeText"].forEach((key) => { this._errors[`display.${key}`] = this._errors["display.style"]; });
    let styleOptions = null;
    if (d.style === "gradient") {
      styleOptions = h("div", { class: "row" },
        this._dField("gradient_from", this.t("gradient_from"), this._dInput("gradient_from", "color")),
        this._dField("gradient_to", this.t("gradient_to"), this._dInput("gradient_to", "color")));
    } else if (d.style === "blink" || d.style === "fade") {
      styleOptions = this._dField("style_interval", this.t("style_interval"), this._dInput("style_interval", "number", { min: 50, max: 60000, step: 50 }));
    }
    return h("div", { class: "fields" },
      this._dField("textCase", this.t("text_case"), this._dSelect("textCase", [0, 1, 2].map((n) => [String(n), this.t(`text_case_${n}`)]))),
      this._dField("textOffset", this.t("text_offset"), this._dInput("textOffset", "number", { min: -32, max: 32, step: 1, placeholder: "0" }),
        this.t("text_offset_help")),
      this._dCheck("center", this.t("center")),
      this._isNG() ? null : this._dCheck("topText", this.t("top_text")),
      style, styleOptions || h("div"));
  }

  _displayBackground(rerender) {
    const d = this._form.display;
    // Names the clock announces (AWTRIX NG), else the documented ones.
    const offered = this._snapshot?.capabilities || {};
    const palettes = h("datalist", { id: "awtrix-studio-palettes" }, ...(offered.palettes?.length ? offered.palettes : PALETTES).map((name) => h("option", { value: name })));
    const effects = offered.effects?.length ? offered.effects : EFFECTS;
    const overlays = this._isNG() ? (offered.overlays?.length ? offered.overlays : OVERLAYS.filter((name) => name !== "clear")) : OVERLAYS;
    const speed = this._isNG()
      ? this._dInput("effect_speed", "number", { min: 0.1, max: 10, step: 0.1, placeholder: "1" })
      : this._dInput("effect_speed", "number", { min: 1, max: 20, step: 1, placeholder: this.t("effect_default") });
    const effectFields = d.effect ? [
      this._dField("speed", this.t(this._isNG() ? "effect_speed_ng" : "effect_speed"), speed,
        this.t(this._isNG() ? "effect_speed_help_ng" : "effect_speed_help")),
      this._dField("palette", this.t("effect_palette"),
        h("div", {}, this._dInput("effect_palette", "text", { list: "awtrix-studio-palettes", maxlength: 32, spellcheck: "false",
          placeholder: this.t("effect_default") }), palettes), this.t("effect_palette_help")),
      this._dField("blend", this.t("effect_blend"), this._dSelect("effect_blend",
        [["", this.t("effect_default")], ["true", this.t("yes")], ["false", this.t("no")]])),
    ] : [];
    return h("div", { class: "fields" },
      this._dCheck("background_on", this.t("background_on"), null, rerender),
      d.background_on ? this._dField("background", this.t("background_color"), this._dInput("background", "color")) : h("div"),
      this._dField("effect", this.t("effect_bg"), this._dSelect("effect", [["", this.t("none")], ...effects.map((name) => [name, name])], rerender),
        this.t("effect_bg_help")),
      this._dField("overlay", this.t("overlay"), this._dSelect("overlay", [["", this.t("none")], ...overlays.map((name) => [name, this.t(`overlay_${name}`) === `overlay_${name}` ? name : this.t(`overlay_${name}`)])]),
        this.t("overlay_help")),
      ...effectFields);
  }

  _displayAdvanced() {
    const template = this._form.mode === "template";
    return h("div", { class: "fields" },
      template ? this._dField("lifetimeMode", this.t("lifetime_mode"),
        this._dSelect("lifetimeMode", [0, 1].map((n) => [String(n), this.t(`lifetime_mode_${n}`)])), this.t("lifetime_mode_help")) : null,
      this._isNG() ? null : this._dCheck("save", this.t("save_on_clock"), this.t("save_on_clock_help")));
  }

  // ------------------------------------------------- color by value

  _colorSection(modes, legend) {
    const tabs = h("div", { class: "tabs", role: "tablist", "aria-label": this.t(legend) });
    const body = h("div", { class: "color-body" });
    const select = (mode) => {
      this._form.color_mode = mode;
      Array.from(tabs.children).forEach((tab, i) => tab.setAttribute("aria-selected", modes[i] === mode ? "true" : "false"));
      if (mode === "gradient") this._suggestRange();
      this._renderColorBody(body);
      this._schedulePreview();
    };
    modes.forEach((mode) => tabs.append(h("button", { role: "tab", "aria-selected": this._form.color_mode === mode ? "true" : "false",
      onClick: () => select(mode) }, this.t(`color_mode_${mode}`))));
    this._renderColorBody(body);
    return h("fieldset", { class: "field wide" }, h("legend", {}, this.t(legend)), tabs, body);
  }

  _renderColorBody(body) {
    clear(body);
    const f = this._form;
    const error = (key) => {
      const el = h("div", { class: "error-text", role: "alert" });
      this._errors[key] = el;
      return el;
    };
    if (f.color_mode === "fixed") {
      body.append(h("div", { class: "muted" }, this.t("color_fixed_help")));
    } else if (f.color_mode === "state") {
      body.append(h("div", { class: "fields" },
        this._field("active_state", this.t("active_state"), this._input("active_state", "text", { maxlength: 64 })),
        this._field("color", this.t("color"), this._input("color", "color"))));
    } else if (f.color_mode === "steps") {
      const row = (n, label) => {
        const err = error(`color_${n}`);
        this._errors[`threshold_${n}`] = err;
        return h("div", { class: "row" },
          h("label", { for: `f-threshold_${n}` }, label),
          h("div", { class: "field", style: "width:110px" }, this._input(`threshold_${n}`, "number", { step: "any" })),
          h("span", { "aria-hidden": "true" }, "→"),
          h("div", { class: "field" }, this._input(`color_${n}`, "color", { "aria-label": `${label} · ${this.t("color")}` })),
          err);
      };
      body.append(h("div", { class: "muted" }, this.t("color_steps_help")), row(1, this.t("above")), row(2, this.t("then_above")));
    } else {
      body.append(this._gradientEditor(error));
    }
  }

  _gradientEditor(error) {
    const f = this._form;
    const wrap = h("div", { class: "gradient-editor" });
    const presets = h("div", { class: "presets", role: "radiogroup", "aria-label": this.t("gradient") });
    [...Object.keys(PRESETS), "custom"].forEach((name) => {
      const swatch = h("span", { class: "preset-swatch" + (name === "custom" ? " custom" : "") });
      if (name !== "custom") swatch.style.background = presetCss(name);
      presets.append(h("button", { class: "preset", role: "radio", "aria-checked": f.gradient === name ? "true" : "false",
        onClick: () => this._pickGradient(name, wrap) }, swatch, h("span", {}, this.t(`gradient_${name}`))));
    });
    wrap.append(presets, error("gradient"));

    if (f.gradient === "custom") {
      const list = h("div", { class: "stops" });
      f.gradient_stops.forEach((stop, i) => {
        list.append(h("div", { class: "row stop" },
          h("input", { type: "number", step: "any", value: stop.value, "aria-label": this.t("stop_value", { n: i + 1 }), class: "stop-value",
            onInput: (e) => { stop.value = e.target.value; this._updateGradientBar(); this._schedulePreview(); } }),
          h("input", { type: "color", value: stop.color, "aria-label": this.t("stop_color", { n: i + 1 }),
            onInput: (e) => { stop.color = e.target.value; this._updateGradientBar(); this._schedulePreview(); } }),
          f.gradient_stops.length > 2 ? h("button", { class: "btn small", "aria-label": this.t("remove_stop", { n: i + 1 }),
            onClick: () => { f.gradient_stops.splice(i, 1); this._rerenderGradient(wrap); this._schedulePreview(); } }, "✕") : null));
      });
      wrap.append(h("div", { class: "muted" }, this.t("custom_help")), list,
        f.gradient_stops.length < MAX_STOPS ? h("button", { class: "btn small", style: "align-self:flex-start", onClick: () => this._addStop(wrap) },
          icon("plus"), this.t("add_stop")) : "",
        error("gradient_stops"));
    } else {
      wrap.append(h("div", { class: "fields" },
        this._field("gradient_min", this.t("gradient_min"), this._input("gradient_min", "number", { step: "any" }), this.t("gradient_min_help")),
        this._field("gradient_max", this.t("gradient_max"), this._input("gradient_max", "number", { step: "any" }), this.t("gradient_max_help"))));
    }

    this._gradientBar = h("div", { class: "gradient-bar" });
    this._gradientMarker = h("div", { class: "gradient-marker" });
    this._gradientLabels = h("div", { class: "row between muted" });
    this._gradientNow = h("div", { class: "muted" });
    wrap.append(h("div", { class: "gradient-track" }, this._gradientBar, this._gradientMarker), this._gradientLabels, this._gradientNow);
    // Inputs re-render the bar on every keystroke without rebuilding the form.
    wrap.addEventListener("input", () => this._updateGradientBar());
    this._updateGradientBar();
    return wrap;
  }

  _rerenderGradient(wrap) {
    const parent = wrap.parentNode;
    if (!parent) return;
    this._renderColorBody(parent);
  }

  _pickGradient(name, wrap) {
    const f = this._form;
    if (name === "custom" && f.gradient !== "custom" && !f.gradient_stops.length) {
      // Start from what is on screen: the current preset, or a simple range.
      const stops = formStops(f) || formStops({ ...f, gradient: f.gradient in PRESETS ? f.gradient : "high_bad", gradient_min: 0, gradient_max: 100 });
      f.gradient_stops = stops.map(([value, color]) => ({ value: Math.round(value * 1000) / 1000, color: rgbToHex(color) }));
    }
    f.gradient = name;
    f._presetTouched = true;
    if (name !== "custom") this._suggestRange();
    this._rerenderGradient(wrap);
    this._schedulePreview();
  }

  _addStop(wrap) {
    const stops = this._form.gradient_stops;
    const last = stops[stops.length - 1];
    const prev = stops[stops.length - 2];
    const step = last && prev ? Number(last.value) - Number(prev.value) : 10;
    stops.push({ value: last ? Number(last.value) + (Number.isFinite(step) && step > 0 ? step : 10) : 0, color: last ? last.color : "#ffffff" });
    this._rerenderGradient(wrap);
    this._schedulePreview();
  }

  /** Fill an empty range from the entity's kind (°C, ppm, %…). */
  _suggestRange() {
    const f = this._form;
    if (f.gradient_min !== "" || f.gradient_max !== "") return;
    const hint = suggestion(this._hass.states[f.entity_id]);
    if (!hint) return;
    const [preset, low, high] = hint;
    if (f.color_mode === "gradient" && !f._presetTouched) f.gradient = preset;
    f.gradient_min = low;
    f.gradient_max = high;
  }

  _updateGradientBar() {
    if (!this._gradientBar) return;
    const stops = formStops(this._form);
    if (!stops) {
      this._gradientBar.style.background = "";
      this._gradientBar.classList.add("empty");
      this._gradientMarker.hidden = true;
      clear(this._gradientLabels);
      this._gradientNow.textContent = this.t("gradient_incomplete");
      return;
    }
    this._gradientBar.classList.remove("empty");
    this._gradientBar.style.background = cssGradient(stops);
    clear(this._gradientLabels);
    const low = stops[0][0];
    const high = stops[stops.length - 1][0];
    this._gradientLabels.append(h("span", {}, String(+low.toFixed(3))), h("span", {}, String(+high.toFixed(3))));
    const state = this._hass.states[this._form.entity_id];
    const value = state ? Number(state.state) : NaN;
    if (!state || state.state === "" || !Number.isFinite(value)) {
      this._gradientMarker.hidden = true;
      this._gradientNow.textContent = state ? this.t("gradient_not_number") : "";
      return;
    }
    const ratio = Math.max(0, Math.min(1, (value - low) / (high - low)));
    this._gradientMarker.hidden = false;
    this._gradientMarker.style.left = `${(ratio * 100).toFixed(2)}%`;
    this._gradientMarker.style.background = rgbToHex(colorAt(stops, value));
    const unit = state.attributes?.unit_of_measurement || "";
    this._gradientNow.textContent = this.t("gradient_now", { value: `${state.state}${unit ? ` ${unit}` : ""}` });
  }

  _setMode(mode) {
    if (this._form.mode === mode) return;
    this._form.mode = mode;
    this._renderFields();
    this._refreshPreview();
  }

  _convert() {
    if (!this._preview.template) return;
    this._form.template = this._preview.template;
    this._setMode("template");
  }

  _schedulePreview() {
    clearTimeout(this._previewTimer);
    this._previewTimer = setTimeout(() => this._refreshPreview(), PREVIEW_DEBOUNCE);
  }

  _refreshPreview() {
    if (this._view.name !== "edit") return;
    const { kind, subentryId } = this._view;
    const message = { type: "awtrix_studio/preview", entry_id: this._entryId, kind, data: this._payload() };
    if (subentryId) message.subentry_id = subentryId;
    this._subscribe("preview", message, (event) => {
      this._preview = { pages: event.pages || [], error: event.error, detail: event.detail, template: event.template || null };
      if (this._pageIndex >= this._preview.pages.length) this._pageIndex = 0;
      this._restartCycle();
      this._drawPreview();
    });
  }

  _restartCycle() {
    clearInterval(this._cycleTimer);
    const count = this._preview.pages.length;
    if (count < 2) return;
    const seconds = this._view.kind === "app" && this._form.mode === "template" ? Number(this._form.page_interval) || 1.5 : 1.5;
    this._cycleTimer = setInterval(() => {
      this._pageIndex = (this._pageIndex + 1) % count;
      this._drawPreview();
    }, Math.max(500, seconds * 1000));
  }

  _setPreviewTab(id) {
    this._previewTab = id;
    Array.from(this._previewTabs.children).forEach((tab, i) => tab.setAttribute("aria-selected", ["sim", "real"][i] === id ? "true" : "false"));
    if (id === "real") this._subscribeScreen(); else this._stop("screen");
    this._drawPreview();
  }

  _drawPreview() {
    if (this._view.name !== "edit" || !this._previewCanvas) return;
    const { kind } = this._view;
    const pages = this._preview.pages;
    const page = pages[this._pageIndex] || pages[0];

    if (this._previewTab === "real") {
      const pixels = this._screen.pixels || new Array(WIDTH * HEIGHT).fill(null);
      const size = this._screen.pixels ? { width: this._screen.width, height: this._screen.height } : {};
      drawMatrix(this._previewCanvas, pixels, { ...this._cells(11, size.width || WIDTH), glow: true, ...size });
      this._previewNote.textContent = this._screen.pixels ? this.t("live_mirror") : this.t("live_waiting");
    } else if (kind === "indicator") {
      const grid = this._appGrid();
      const pixels = addIndicators(new Array(grid.width * grid.height).fill(null), page ? { [this._form.indicator]: page } : {}, grid.width, grid.height);
      drawMatrix(this._previewCanvas, pixels, { ...this._cells(11, grid.width * grid.scale), glow: true, ...grid });
      this._previewNote.textContent = this.t("simulated_note");
    } else {
      const grid = this._appGrid();
      const rendered = renderPage(page, grid);
      drawMatrix(this._previewCanvas, rendered.pixels, { ...this._cells(11, grid.width * grid.scale), icon: rendered.icon, glow: true, ...grid });
      this._previewNote.textContent = [rendered.overflow ? this.t("overflow_note") : this.t("simulated_note"),
        rendered.effect ? this.t("effect_not_simulated", { name: rendered.effect }) : ""].filter(Boolean).join(" ");
    }

    clear(this._pills);
    if (pages.length > 1) pages.forEach((_p, i) => this._pills.append(h("span", { class: `pill${i === this._pageIndex ? " active" : ""}` }, this.t("page_n", { n: i + 1 }))));

    if (this._preview.error) {
      this._previewStatus.textContent = this.t(`errors.${this._preview.error}`);
      this._previewStatus.className = "status error";
      this._previewDetail.textContent = this._preview.detail || "";
    } else {
      this._previewStatus.textContent = pages.length ? this.t("valid", { n: pages.length }) : this.t("valid_empty");
      this._previewStatus.className = "status ok";
      // The saved version may have been refused by the clock (AWTRIX NG says why).
      const refused = this._view.subentryId ? this._findItem(this._view.subentryId)?.clock_error : null;
      this._previewDetail.textContent = refused ? this.t("clock_refused_detail", { detail: refused }) : "";
    }
    this._json.textContent = page ? JSON.stringify(page, null, 2) : "—";
    this._generatedBox.hidden = !(this._form.mode === "simple" && this._preview.template);
    this._generated.textContent = this._preview.template || "";
  }

  _showErrors(errors, detail) {
    // An error about one entity of a line opens that entity first; the app's
    // own fields describe the first one, the others are `segments.<i>.<field>`.
    const f = this._form;
    const entries = Object.entries(errors || {}).map(([field, key]) => {
      const match = /^segments\.(\d+)\.(.+)$/.exec(field);
      if (match) return { segment: Number(match[1]) + 1, field: match[2], key };
      const segment = f.segments && SEGMENT_KEYS.includes(field) ? 0 : null;
      return { segment, field, key };
    });
    const inSegment = entries.find((e) => e.segment !== null);
    if (inSegment && !entries.some((e) => e.segment === f.segment)) this._selectSegment(inSegment.segment);
    Object.values(this._errors).forEach((el) => { el.textContent = ""; });
    let shown = false;
    entries.forEach(({ segment, field, key }) => {
      const target = segment === null || segment === f.segment ? this._errors[field] : null;
      const text = this.t(`errors.${key}`) + (detail && (key === "invalid_template" || key === "invalid_pages") ? ` ${detail}` : "");
      if (target) {
        target.textContent = text;
        shown = true;
        const group = target.closest("details");
        if (group) group.open = true;
      } else {
        this._saveFeedback.textContent = text;
      }
    });
    if (shown) this._saveFeedback.textContent = "";
  }

  async _save() {
    const { kind, subentryId } = this._view;
    const message = { type: "awtrix_studio/save", kind, data: this._payload() };
    if (subentryId) message.subentry_id = subentryId;
    const result = await this._call(message);
    if (!result) return;
    if (result.errors) { this._showErrors(result.errors, result.detail); return; }
    this._showOverview();
  }

  async _delete() {
    const { item, kind } = this._view;
    const name = kind === "app" ? item.data.app_name : this.t("indicator_n", { n: item.data.indicator });
    if (!window.confirm(this.t("delete_confirm", { name }))) return;
    const result = await this._call({ type: "awtrix_studio/delete", subentry_id: item.subentry_id });
    if (result !== null) this._showOverview();
  }

  async _test() {
    const { kind, subentryId } = this._view;
    const data = this._payload();
    if (kind === "app" && !subentryId && !data.app_name) { this._showErrors({ app_name: "invalid_app_name" }); return; }
    const message = { type: "awtrix_studio/test", kind, data, page: this._pageIndex };
    if (subentryId) message.subentry_id = subentryId;
    const result = await this._call(message);
    if (!result) return;
    if (result.errors) { this._showErrors(result.errors, result.detail); return; }
    this._testFeedback.textContent = this.t("test_sent");
    setTimeout(() => { this._testFeedback.textContent = ""; }, 4000);
  }
}

if (!customElements.get("awtrix-studio-panel")) {
  customElements.define("awtrix-studio-panel", AwtrixStudioPanel);
}
