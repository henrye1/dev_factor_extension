// A small SVG line chart: one y-axis, thin lines, crosshair with a tooltip listing every
// series at that x, a legend that toggles series, and end labels when four or fewer are shown.

import { h, clear } from "./ui.js";

const NS = "http://www.w3.org/2000/svg";

function s(tag, attrs, text) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
  if (text !== undefined) el.textContent = text;
  return el;
}

function niceStep(span, target) {
  const raw = span / target;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const f = raw / mag;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * mag;
}

function linearTicks(lo, hi, target = 5) {
  if (!(hi > lo)) { const pad = Math.abs(lo) * 0.05 || 0.01; lo -= pad; hi += pad; }
  const step = niceStep(hi - lo, target);
  const a = Math.floor(lo / step) * step, b = Math.ceil(hi / step) * step;
  const ticks = [];
  for (let v = a; v <= b + step * 1e-9; v += step) ticks.push(Math.abs(v) < step * 1e-9 ? 0 : v);
  return { lo: a, hi: b, ticks, step };
}

function decimals(step) {
  return Math.max(0, Math.min(6, -Math.floor(Math.log10(step) + 1e-9)));
}

export function lineChart(host, spec) {
  // spec: {title, subtitle, control, series:[{name,color,dash,dots,pts:[[x,y]]}], xLabel, yLog, yFmt, refs:[{x,label}], note}
  clear(host);
  host.classList.add("chart");
  const hidden = new Set();
  const svg = s("svg", { role: "img", tabindex: "0", "aria-label": spec.title });
  const tip = h("div", { class: "tip", hidden: true });
  const legend = h("div", { class: "legend" });
  host.append(
    h("div", { class: "head" },
      h("div", null, h("h3", null, spec.title), spec.subtitle ? h("div", { class: "sub" }, spec.subtitle) : null),
      spec.control ? h("div", { class: "ctl" }, spec.control) : null),
    svg, legend, tip);

  const yFmtDefault = spec.yLog ? (v) => Number((v * 100).toPrecision(1)).toString() + "%" : null;
  let geo = null;          // geometry of the last draw, used by the pointer handlers
  let cursor = -1;

  function visible() { return spec.series.filter((d) => !hidden.has(d.name)); }

  function drawLegend() {
    clear(legend);
    if (spec.series.length < 2) return;
    for (const d of spec.series) {
      const key = h("span", { class: "key" + (d.dots ? " dot" : d.dash ? " dash" : "") });
      key.style.color = d.color;
      key.style.borderTopColor = d.color;
      legend.append(h("button", {
        type: "button", "aria-pressed": String(!hidden.has(d.name)), title: "Show or hide this series",
        onclick: () => { hidden.has(d.name) ? hidden.delete(d.name) : hidden.add(d.name); drawLegend(); draw(); },
      }, key, d.name));
    }
  }

  function draw() {
    clear(svg);
    const W = Math.max(320, svg.clientWidth || host.clientWidth - 30 || 600);
    const H = 300;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    const vis = visible();
    const labelEnds = vis.length <= 4 && vis.length >= 2 && W >= 560;
    const longest = Math.max(0, ...vis.map((d) => d.name.length));
    const m = { l: 56, r: labelEnds ? Math.min(190, 26 + longest * 6.2) : 14, t: 12, b: 36 };
    const pw = W - m.l - m.r, ph = H - m.t - m.b;

    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (const d of vis) for (const [x, y] of d.pts) {
      if (y === null || y === undefined || (spec.yLog && y <= 0)) continue;
      if (x < x0) x0 = x; if (x > x1) x1 = x;
      if (y < y0) y0 = y; if (y > y1) y1 = y;
    }
    if (!isFinite(x0)) {
      svg.append(s("text", { x: W / 2, y: H / 2, "text-anchor": "middle" }, "Nothing to plot"));
      geo = null;
      return;
    }
    if (x1 === x0) x1 = x0 + 1;

    let yTicks, sy, yFmt, yMin = -Infinity;
    if (spec.yLog) {
      // show at most six decades below the largest value; anything smaller runs off the bottom
      const b = Math.ceil(Math.log10(y1) - 1e-9);
      const a = Math.min(b - 1, Math.max(Math.floor(Math.log10(y0)), b - 6));
      yMin = Math.pow(10, a);
      yTicks = [];
      for (let e = a; e <= b; e++) yTicks.push(Math.pow(10, e));
      sy = (v) => m.t + ph - ((Math.log10(v) - a) / (b - a)) * ph;
      yFmt = spec.yFmt || yFmtDefault;
    } else {
      const t = linearTicks(y0, y1);
      yTicks = t.ticks;
      sy = (v) => m.t + ph - ((v - t.lo) / (t.hi - t.lo)) * ph;
      const dec = decimals(t.step);
      yFmt = spec.yFmt || ((v) => v.toFixed(dec));
    }
    const sx = (v) => m.l + ((v - x0) / (x1 - x0)) * pw;

    for (const v of yTicks) {
      const y = sy(v);
      svg.append(s("line", { class: "grid", x1: m.l, x2: m.l + pw, y1: y, y2: y }));
      svg.append(s("text", { x: m.l - 8, y: y + 4, "text-anchor": "end" }, yFmt(v)));
    }
    const xt = linearTicks(x0, x1, Math.max(3, Math.floor(pw / 80)));
    for (const v of xt.ticks) {
      if (v < x0 - 1e-9 || v > x1 + 1e-9) continue;
      const x = sx(v);
      svg.append(s("line", { class: "axisline", x1: x, x2: x, y1: m.t + ph, y2: m.t + ph + 4 }));
      svg.append(s("text", { x, y: m.t + ph + 17, "text-anchor": "middle" }, String(Math.round(v))));
    }
    svg.append(s("line", { class: "axisline", x1: m.l, x2: m.l + pw, y1: m.t + ph, y2: m.t + ph }));
    if (spec.xLabel) svg.append(s("text", { x: m.l + pw / 2, y: H - 3, "text-anchor": "middle" }, spec.xLabel));

    for (const ref of spec.refs || []) {
      if (ref.x === null || ref.x === undefined || ref.x < x0 || ref.x > x1) continue;
      const x = sx(ref.x);
      svg.append(s("line", { x1: x, x2: x, y1: m.t, y2: m.t + ph, stroke: "#7b869c", "stroke-width": 1, "stroke-dasharray": "2 4" }));
      const right = x > m.l + pw - 90;
      svg.append(s("text", { x: x + (right ? -5 : 5), y: m.t + 10, "text-anchor": right ? "end" : "start" }, ref.label));
    }

    const clipId = "clip" + Math.random().toString(36).slice(2, 9);
    const clip = s("clipPath", { id: clipId });
    clip.append(s("rect", { x: m.l, y: m.t - 3, width: pw + 3, height: ph + 3 }));
    svg.append(clip);
    const plot = s("g", { "clip-path": `url(#${clipId})` });
    svg.append(plot);
    const ends = [];
    for (const d of vis) {
      let path = "", pen = false, last = null;
      for (const [x, y] of d.pts) {
        if (y === null || y === undefined || (spec.yLog && y <= 0)) { pen = false; continue; }
        const px = sx(x), py = sy(y);
        if (d.dots) plot.append(s("circle", { cx: px, cy: py, r: 2.2, fill: d.color }));
        else path += (pen ? "L" : "M") + px.toFixed(1) + " " + py.toFixed(1);
        pen = true;
        last = [px, py];
      }
      if (!d.dots && path) {
        plot.append(s("path", {
          d: path, fill: "none", stroke: d.color, "stroke-width": d.width || 2,
          "stroke-linejoin": "round", "stroke-linecap": "round", ...(d.dash ? { "stroke-dasharray": "5 4" } : {}),
        }));
      }
      // only a series that reaches the right edge inside the plot gets an end label
      if (last && last[0] > m.l + pw - 4 && last[1] <= m.t + ph + 1) ends.push({ name: d.name, y: last[1], x: last[0], color: d.color });
    }
    if (labelEnds) {
      ends.sort((a, b) => a.y - b.y);
      for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 13) ends[i].y = ends[i - 1].y + 13;
      const over = ends.length ? ends[ends.length - 1].y - (m.t + ph) : 0;
      if (over > 0) for (const e of ends) e.y -= over;
      for (const e of ends) {
        svg.append(s("line", { x1: m.l + pw + 4, x2: m.l + pw + 14, y1: e.y, y2: e.y, stroke: e.color, "stroke-width": 2 }));
        svg.append(s("text", { class: "endlabel", x: m.l + pw + 18, y: e.y + 4 }, e.name));
      }
    }

    // crosshair layer
    const xsSet = new Set();
    for (const d of vis) for (const [x, y] of d.pts) if (y !== null && y !== undefined) xsSet.add(x);
    const xs = [...xsSet].sort((a, b) => a - b);
    const maps = vis.map((d) => new Map(d.pts));
    const cross = s("line", { class: "cross", y1: m.t, y2: m.t + ph, visibility: "hidden" });
    const marks = s("g", {});
    svg.append(cross, marks);
    const hit = s("rect", { x: m.l, y: m.t, width: pw, height: ph, fill: "transparent" });
    svg.append(hit);
    geo = { xs, maps, vis, sx, sy, cross, marks, m, pw, ph, W, yFmt, x0, x1, yMin };
    if (cursor >= 0) show(Math.min(cursor, xs.length - 1));
    hit.addEventListener("pointermove", (ev) => {
      const rect = svg.getBoundingClientRect();
      const vx = ((ev.clientX - rect.left) / rect.width) * W;
      const target = x0 + ((vx - m.l) / pw) * (x1 - x0);
      let lo = 0, hi = xs.length - 1;
      while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (xs[mid] < target) lo = mid; else hi = mid; }
      show(Math.abs(xs[lo] - target) <= Math.abs(xs[hi] - target) ? lo : hi);
    });
    hit.addEventListener("pointerleave", hide);
  }

  function show(i) {
    if (!geo || !geo.xs.length) return;
    cursor = i;
    const { xs, maps, vis, sx, sy, cross, marks, m, W, yFmt } = geo;
    const x = xs[i], px = sx(x);
    cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("visibility", "visible");
    clear(marks);
    clear(tip);
    tip.append(h("div", { class: "t" }, `${spec.xLabel || "x"} ${x}`));
    const fullFmt = spec.tipFmt || ((v) => (spec.yLog ? (v * 100).toPrecision(3) + "%" : v.toFixed(4)));
    vis.forEach((d, k) => {
      const y = maps[k].get(x);
      if (y === null || y === undefined) return;
      if (!(spec.yLog && (y <= 0 || y < geo.yMin))) {
        marks.append(s("circle", { cx: px, cy: sy(y), r: 4, fill: d.color, stroke: "#fcfcfb", "stroke-width": 2 }));
      }
      const key = h("i");
      key.style.borderTopColor = d.color;
      tip.append(h("div", { class: "r" }, h("span", { class: "k" }, key, d.name), h("b", null, fullFmt(y))));
    });
    tip.hidden = false;
    const rect = svg.getBoundingClientRect(), hostRect = host.getBoundingClientRect();
    const left = rect.left - hostRect.left + (px / W) * rect.width;
    const flip = left > hostRect.width * 0.6;
    tip.style.top = rect.top - hostRect.top + m.t + 6 + "px";
    tip.style.left = flip ? "" : left + 12 + "px";
    tip.style.right = flip ? hostRect.width - left + 12 + "px" : "";
  }

  function hide() {
    cursor = -1;
    tip.hidden = true;
    if (geo) { geo.cross.setAttribute("visibility", "hidden"); clear(geo.marks); }
  }

  svg.addEventListener("keydown", (ev) => {
    if (!geo || !geo.xs.length) return;
    if (ev.key === "ArrowRight") show(Math.min(geo.xs.length - 1, cursor < 0 ? 0 : cursor + 1));
    else if (ev.key === "ArrowLeft") show(Math.max(0, cursor < 0 ? geo.xs.length - 1 : cursor - 1));
    else if (ev.key === "Escape") hide();
    else return;
    ev.preventDefault();
  });
  svg.addEventListener("blur", hide);

  drawLegend();
  draw();
  const ro = new ResizeObserver(() => draw());
  ro.observe(host);
  return { redraw: draw, update(next) { Object.assign(spec, next); drawLegend(); draw(); } };
}

// Fixed colours by entity. The three tail shapes take the first three categorical slots.
export const COLORS = {
  file: "#7b869c", exp: "#2a78d6", power: "#eb6834", logn: "#1baf7a", final: "#14213d",
  scenario: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"],
};
