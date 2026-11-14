// Tests of the preview renderer (frontend/matrix.js), run with `node --test`.
//
// They check robust properties (sizes, which pixels are lit and in which
// color), not the exact glyphs of the fonts.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  HEIGHT,
  WIDTH,
  drawMatrix,
  parseColor,
  renderPage,
} from "../../custom_components/awtrix_studio/frontend/matrix.js";

/** The set of colors lit in `pixels`. */
function litColors(pixels) {
  return new Set(pixels.filter((p) => p !== null));
}

/** The lit columns of a W-wide grid. */
function litColumns(result) {
  const columns = new Set();
  result.pixels.forEach((p, i) => {
    if (p !== null) columns.add(i % result.width);
  });
  return [...columns].sort((a, b) => a - b);
}

/** The color of the pixel at (x, y). */
function at(result, x, y) {
  return result.pixels[y * result.width + x];
}

test("colors are parsed strictly", () => {
  assert.equal(parseColor("#ff8800"), "#FF8800");
  assert.equal(parseColor("00ff00"), "#00FF00");
  assert.equal(parseColor([255, 0, 300]), "#FF00FF");
  assert.equal(parseColor(0x0000ff), "#0000FF");
  assert.equal(parseColor("red"), "#FFFFFF");
  assert.equal(parseColor("javascript:alert(1)", "#000000"), "#000000");
});

test("plain text on the AWTRIX 3 grid", () => {
  const result = renderPage({ text: "HI", color: "#FF0000" });
  assert.equal(result.width, WIDTH);
  assert.equal(result.height, HEIGHT);
  assert.equal(result.scale, 1);
  assert.equal(result.pixels.length, 32 * 8);
  assert.deepEqual([...litColors(result.pixels)], ["#FF0000"]);
  // Centered: lit columns on both sides of the middle, none at the edges.
  const columns = litColumns(result);
  assert.ok(columns[0] > 5 && columns.at(-1) < 26);
  assert.equal(result.overflow, false);
  // The top row stays dark (text starts on row 1 by default).
  for (let x = 0; x < WIDTH; x++) assert.equal(at(result, x, 0), null);
});

test("white by default, overflow for long text", () => {
  assert.deepEqual([...litColors(renderPage({ text: "A" }).pixels)], ["#FFFFFF"]);
  assert.equal(renderPage({ text: "A VERY LONG TEXT THAT SCROLLS" }).overflow, true);
  // Nothing to draw: all dark.
  assert.equal(litColors(renderPage({}).pixels).size, 0);
  assert.equal(litColors(renderPage(null).pixels).size, 0);
});

test("AWTRIX 3 fragments keep their colors, in order", () => {
  const result = renderPage({
    text: [
      { t: "AB", c: "FF0000" },
      { t: "CD", c: "00FF00" },
    ],
  });
  assert.deepEqual(litColors(result.pixels), new Set(["#FF0000", "#00FF00"]));
  const reds = litColumns({ ...result, pixels: result.pixels.map((p) => (p === "#FF0000" ? p : null)) });
  const greens = litColumns({ ...result, pixels: result.pixels.map((p) => (p === "#00FF00" ? p : null)) });
  assert.ok(reds.at(-1) < greens[0]);
});

test("AWTRIX NG fragments: a piece without a color is white", () => {
  const result = renderPage({
    text: [{ text: "A", color: [0, 0, 255] }, { text: "B" }],
    textColor: "#FF0000",
  });
  assert.deepEqual(litColors(result.pixels), new Set(["#0000FF", "#FFFFFF"]));
});

test("AWTRIX NG keys are read: textColor and backgroundColor", () => {
  const result = renderPage({ text: "A", textColor: "#00FF00", backgroundColor: "#000080" });
  const colors = litColors(result.pixels);
  assert.ok(colors.has("#00FF00"));
  assert.ok(colors.has("#000080"));
  // The background fills every unlit pixel.
  assert.ok(result.pixels.every((p) => p !== null));
});

test("an icon moves the text right of the icon area", () => {
  const result = renderPage({ text: "A", icon: "2422", center: false });
  assert.equal(result.icon, "2422");
  const columns = litColumns(result);
  assert.ok(columns[0] >= 9, `text starts at ${columns[0]}`);
  // Without the icon, the same text starts at the left edge.
  assert.equal(litColumns(renderPage({ text: "A", center: false }))[0], 0);
});

test("progress bar on the bottom row", () => {
  const result = renderPage({ progress: 50, progressC: "#00FF00", progressBC: "#FF0000" });
  const bottom = Array.from({ length: WIDTH }, (_, x) => at(result, x, HEIGHT - 1));
  assert.equal(bottom.filter((c) => c === "#00FF00").length, 16);
  assert.equal(bottom.filter((c) => c === "#FF0000").length, 16);
  assert.equal(bottom[0], "#00FF00");
  assert.equal(bottom[31], "#FF0000");
  // Only the bottom row is drawn.
  assert.ok(result.pixels.slice(0, WIDTH * (HEIGHT - 1)).every((p) => p === null));

  // NG names, beside an icon: the bar starts after the icon.
  const ng = renderPage({ icon: "1", progress: 100, progressColor: "#0000FF" });
  const row = Array.from({ length: WIDTH }, (_, x) => at(ng, x, HEIGHT - 1));
  assert.ok(row.slice(0, 9).every((c) => c === null));
  assert.ok(row.slice(9).every((c) => c === "#0000FF"));
});

test("TC002: apps on a 26 x 8 grid drawn at double size", () => {
  const size = { width: 26, height: 8, scale: 2 };
  const result = renderPage({ text: "1", color: "#FF0000" }, size);
  assert.equal(result.width, 26);
  assert.equal(result.height, 8);
  assert.equal(result.scale, 2);
  assert.equal(result.pixels.length, 26 * 8);

  // Each pixel of the grid lights 2 x 2 LEDs of the 52 x 16 display.
  globalThis.window = { devicePixelRatio: 1 };
  const fills = [];
  let style = null;
  let last = null;
  const ctx = {
    setTransform() {},
    clearRect() {},
    beginPath() {},
    roundRect(x, y) { last = [x, y]; },
    rect(x, y) { last = [x, y]; },
    fill() { fills.push({ at: last, color: style }); },
    set fillStyle(value) { style = value; },
    get fillStyle() { return style; },
  };
  const canvas = { width: 0, height: 0, style: {}, getContext: () => ctx };
  drawMatrix(canvas, result.pixels, { ...size, cell: 1, gap: 0, offColor: "off" });
  assert.equal(fills.length, 52 * 16);
  const led = (x, y) => fills.find((f) => f.at[0] === x && f.at[1] === y).color;
  result.pixels.forEach((color, i) => {
    const x = i % 26;
    const y = Math.floor(i / 26);
    for (const [dx, dy] of [[0, 0], [1, 0], [0, 1], [1, 1]]) {
      assert.equal(led(2 * x + dx, 2 * y + dy), color || "off");
    }
  });
});

test("TC002: an NG layout uses the whole 52 x 16 display", () => {
  const result = renderPage(
    {
      layout: {
        regions: [
          { box: [0, 0, 52, 8], text: "UP", color: "#FF0000" },
          { box: [0, 8, 52, 8], text: "DOWN", color: "#00FF00" },
          { box: [60, 0, 10, 10], text: "OUT" },
        ],
      },
    },
    { width: 26, height: 8, scale: 2 },
  );
  assert.equal(result.width, 52);
  assert.equal(result.height, 16);
  assert.equal(result.scale, 1);
  assert.deepEqual(litColors(result.pixels), new Set(["#FF0000", "#00FF00"]));
  // Each text stays in its own box: red on top, green below.
  result.pixels.forEach((color, i) => {
    const y = Math.floor(i / 52);
    if (color === "#FF0000") assert.ok(y < 8);
    if (color === "#00FF00") assert.ok(y >= 8);
  });
});

test("an NG layout region with a progress bar and an icon", () => {
  const result = renderPage(
    {
      layout: {
        regions: [
          { box: [0, 0, 16, 16], icon: "2422" },
          { box: [16, 12, 36, 4], progress: 50, color: "#00FF00", trackColor: "#FF0000" },
        ],
      },
    },
    { width: 26, height: 8, scale: 2 },
  );
  assert.deepEqual(result.icons, [{ name: "2422", x: 0, y: 0, w: 16, h: 16 }]);
  assert.equal(at(result, 16, 12), "#00FF00");
  assert.equal(at(result, 51, 15), "#FF0000");
  assert.equal(at(result, 16, 11), null);
});
