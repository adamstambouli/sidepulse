#!/usr/bin/env python3
"""Generate a local HTML preview of every SidePulse LED state.

The palette and timings are read from sidepulse.led_status at generation time,
so the preview cannot drift from the programs the device actually receives.

    python3 scripts/preview_leds.py && open examples/led_preview.html
"""
from __future__ import annotations

import colorsys
import json
from pathlib import Path

from sidepulse.led_status import (
    ASK_AMBER,
    BLOCKED_RED,
    DONE_GREEN,
    FLEET_COLORS,
    FLEET_EMPTY,
    FLEET_MIN_STAGGER_WIDTH,
    FLEET_PULSE_MS,
    FLEET_STAGGER_MS,
    IDLE_DIM,
    WORKING_BLUE,
    FleetBand,
    LedDisplayState,
    fleet_band_widths,
    fleet_program,
    program_for_display_state,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = REPO_ROOT / "examples" / "led_preview.html"


def hue_of(hex_color: str) -> int:
    red, green, blue = (int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return round(colorsys.rgb_to_hsv(red, green, blue)[0] * 360)


def aggregate_specs() -> list[dict]:
    """Per-LED timelines for the whole-bar states, matching program_for_display_state."""
    working = [
        {"pulses": [{"color": WORKING_BLUE, "duration": 1200, "delay": 140 * i}]}
        for i in range(8)
    ]
    return [
        {
            "key": "idle",
            "label": "Idle",
            "note": "No recent agent work. Barely visible on purpose.",
            "cycle": 6000,
            "leds": [{"pulses": [{"color": IDLE_DIM, "duration": 6000, "delay": 0}]}] * 8,
            "program": program_for_display_state(LedDisplayState.IDLE),
        },
        {
            "key": "working",
            "label": "Working",
            "note": "Slow travelling wave. Needs nothing from you.",
            "cycle": 1200 + 140 * 7,
            "leds": working,
            "program": program_for_display_state(LedDisplayState.WORKING),
        },
        {
            "key": "done",
            "label": "Done",
            "note": "Steady and still. Finished, go look.",
            "cycle": 1000,
            "leds": [{"steady": DONE_GREEN}] * 8,
            "program": program_for_display_state(LedDisplayState.DONE),
        },
        {
            "key": "ask",
            "label": "Ask",
            "note": "Faster breath. Waiting on your input.",
            "cycle": 700,
            "leds": [{"pulses": [{"color": ASK_AMBER, "duration": 700, "delay": 0}]}] * 8,
            "program": program_for_display_state(LedDisplayState.ASK),
        },
        {
            "key": "blocked",
            "label": "Blocked",
            "note": "Double blink then a pause. Something broke.",
            "cycle": 2060,
            "leds": [
                {
                    "pulses": [
                        {"color": BLOCKED_RED, "duration": 420, "delay": 0},
                        {"color": BLOCKED_RED, "duration": 420, "delay": 540},
                    ]
                }
            ]
            * 8,
            "program": program_for_display_state(LedDisplayState.BLOCKED),
        },
    ]


def fleet_examples() -> list[dict]:
    states = [
        LedDisplayState.WORKING,
        LedDisplayState.DONE,
        LedDisplayState.ASK,
        LedDisplayState.BLOCKED,
    ]
    samples = [
        ["working"],
        ["working", "done"],
        ["working", "done", "ask"],
        ["done", "working", "blocked", "working"],
        ["working"] * 8,
    ]
    out = []
    for combo in samples:
        picked = [LedDisplayState(name) for name in combo]
        widths = fleet_band_widths(len(picked))
        bands = [FleetBand(state, width) for state, width in zip(picked, widths)]
        out.append({"states": combo, "program": fleet_program(bands)})
    _ = states
    return out


def build_config() -> dict:
    return {
        "palette": [
            {"key": key.value, "color": color, "hue": hue_of(color)}
            for key, color in FLEET_COLORS.items()
        ],
        "fleetColors": {key.value: color for key, color in FLEET_COLORS.items()},
        "fleetPulseMs": {key.value: value for key, value in FLEET_PULSE_MS.items()},
        "fleetStaggerMs": {key.value: value for key, value in FLEET_STAGGER_MS.items()},
        "minStaggerWidth": FLEET_MIN_STAGGER_WIDTH,
        "empty": FLEET_EMPTY,
        "aggregate": aggregate_specs(),
        "examples": fleet_examples(),
    }


HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SidePulse LED States</title>
<style>
  :root {
    --bg: #0b0d12; --panel: #141821; --edge: #232838;
    --text: #e8ecf5; --muted: #8b93a7; --accent: #0066FF;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 32px 24px 72px;
    background: var(--bg); color: var(--text);
    font: 15px/1.55 ui-sans-serif, -apple-system, "SF Pro Text", system-ui, sans-serif;
  }
  .wrap { max-width: 900px; margin: 0 auto; }
  h1 { font-size: 24px; margin: 0 0 4px; letter-spacing: -0.01em; }
  h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.08em;
       color: var(--muted); margin: 40px 0 14px; font-weight: 600; }
  .sub { color: var(--muted); margin: 0 0 8px; }
  .panel { background: var(--panel); border: 1px solid var(--edge);
           border-radius: 12px; padding: 18px; margin-bottom: 12px; }
  .row { display: flex; align-items: center; gap: 20px; flex-wrap: wrap; }
  .bar { display: flex; gap: 8px; padding: 14px 16px; background: #05060a;
         border-radius: 10px; border: 1px solid #1c2130; }
  .led { width: 30px; height: 30px; border-radius: 50%; background: #000;
         border: 1px solid #1c2130; transition: none; }
  .meta { flex: 1; min-width: 200px; }
  .meta .name { font-weight: 600; font-size: 16px; }
  .meta .note { color: var(--muted); font-size: 13.5px; }
  pre { margin: 12px 0 0; padding: 12px 14px; background: #05060a; color: #9fb2d4;
        border: 1px solid #1c2130; border-radius: 8px; overflow-x: auto;
        font: 12.5px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
  .controls { display: flex; gap: 10px; align-items: center; flex-wrap: wrap;
              margin-bottom: 16px; }
  select, button {
    background: #1b2030; color: var(--text); border: 1px solid var(--edge);
    border-radius: 8px; padding: 7px 11px; font-size: 14px; font-family: inherit;
  }
  button { cursor: pointer; }
  button:hover { border-color: var(--accent); }
  button.preset { font-size: 13px; }
  .agent { display: flex; flex-direction: column; gap: 5px; }
  .agent label { font-size: 11px; color: var(--muted); text-transform: uppercase;
                 letter-spacing: 0.05em; }
  .swatches { display: flex; gap: 10px; flex-wrap: wrap; }
  .sw { display: flex; align-items: center; gap: 9px; background: var(--panel);
        border: 1px solid var(--edge); border-radius: 9px; padding: 8px 13px; }
  .dot { width: 17px; height: 17px; border-radius: 50%; }
  .sw code { color: var(--muted); font-size: 12.5px; }
  .hint { color: var(--muted); font-size: 13px; margin-top: 10px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>SidePulse LED states</h1>
  <p class="sub">Generated from <code>sidepulse.led_status</code>. Animations mirror
     the <code>LEDS.LED</code> programs the device receives.</p>

  <h2>Palette</h2>
  <div class="swatches" id="swatches"></div>

  <h2>Fleet mode &mdash; try combinations</h2>
  <div class="controls" id="controls"></div>
  <div class="panel">
    <div class="row">
      <div class="bar" id="fleetBar"></div>
      <div class="meta">
        <div class="name" id="fleetName">1 agent</div>
        <div class="note" id="fleetNote"></div>
      </div>
    </div>
    <pre id="fleetProgram"></pre>
  </div>
  <p class="hint">Bands of 3+ LEDs animate as a travelling wave; 1&ndash;2 LEDs
     pulse in unison. Done never animates &mdash; stillness is the signal.</p>

  <h2>Single state &mdash; whole bar</h2>
  <div id="aggregate"></div>
</div>

<script id="cfg" type="application/json">__CONFIG__</script>
<script>
const CFG = JSON.parse(document.getElementById('cfg').textContent);
const STATES = ['working', 'done', 'ask', 'blocked', 'idle'];

function hexToRgb(hex) {
  return [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
}

// Mirrors the DSL: `pulse` rises and falls over its duration after its delay.
function amountAt(t, pulses) {
  let best = 0;
  for (const p of pulses) {
    const local = t - p.delay;
    if (local >= 0 && local <= p.duration) {
      best = Math.max(best, Math.sin(Math.PI * local / p.duration) ** 2);
    }
  }
  return best;
}

function paint(el, spec, t) {
  let rgb, alpha;
  if (spec.steady) {
    rgb = hexToRgb(spec.steady);
    alpha = 1;
  } else if (spec.pulses && spec.pulses.length) {
    const a = amountAt(t, spec.pulses);
    rgb = hexToRgb(spec.pulses[0].color).map(c => Math.round(c * a));
    alpha = a;
  } else {
    rgb = [0, 0, 0];
    alpha = 0;
  }
  el.style.background = `rgb(${rgb[0]},${rgb[1]},${rgb[2]})`;
  el.style.boxShadow = alpha > 0.06
    ? `0 0 ${5 + 15 * alpha}px rgba(${rgb[0]},${rgb[1]},${rgb[2]},${0.75 * alpha})`
    : 'none';
}

function makeBar(container, count) {
  container.innerHTML = '';
  const leds = [];
  for (let i = 0; i < count; i++) {
    const d = document.createElement('div');
    d.className = 'led';
    container.appendChild(d);
    leds.push(d);
  }
  return leds;
}

/* ---- whole-bar states ---- */
const aggregateHost = document.getElementById('aggregate');
const aggregateViews = CFG.aggregate.map(spec => {
  const panel = document.createElement('div');
  panel.className = 'panel';
  panel.innerHTML = `
    <div class="row">
      <div class="bar"></div>
      <div class="meta">
        <div class="name">${spec.label}</div>
        <div class="note">${spec.note}</div>
      </div>
    </div>
    <pre>${spec.program.replace(/</g, '&lt;')}</pre>`;
  aggregateHost.appendChild(panel);
  return { spec, leds: makeBar(panel.querySelector('.bar'), 8) };
});

/* ---- fleet builder ---- */
// Mirrors fleet_band_widths(): leftovers go to the earliest bands.
function bandWidths(count, ledCount = 8) {
  if (count <= 0) return [];
  count = Math.min(count, ledCount);
  const base = Math.floor(ledCount / count), extra = ledCount % count;
  return Array.from({ length: count }, (_, i) => base + (i < extra ? 1 : 0));
}

function fleetSpecs(states) {
  // Mirrors fleet_bands_for_statuses(): identical states merge into one band.
  if (states.length && new Set(states).size === 1) states = [states[0]];
  const widths = bandWidths(states.length);
  const leds = [];
  states.forEach((state, i) => {
    const duration = CFG.fleetPulseMs[state];
    const width = widths[i];
    const stagger = width >= CFG.minStaggerWidth ? (CFG.fleetStaggerMs[state] || 0) : 0;
    for (let o = 0; o < width; o++) {
      if (duration === undefined) {
        leds.push({ steady: CFG.fleetColors[state] });
      } else {
        leds.push({ pulses: [{ color: CFG.fleetColors[state], duration, delay: o * stagger }] });
      }
    }
  });
  while (leds.length < 8) leds.push({});
  let cycle = 1000;
  for (const l of leds) {
    for (const p of (l.pulses || [])) cycle = Math.max(cycle, p.delay + p.duration);
  }
  return { leds, cycle, widths };
}

function fleetProgramText(states) {
  const { leds } = fleetSpecs(states);
  const bases = [], pulses = [];
  leds.forEach((l, i) => {
    if (l.steady) { bases.push(`${i}:${l.steady}`); return; }
    bases.push(`${i}:${CFG.empty}`);
    if (l.pulses) {
      const p = l.pulses[0];
      pulses.push(`${i}:${p.color} ${p.duration}ms pulse${p.delay ? ' ' + p.delay + 'ms' : ''}`);
    }
  });
  const lines = [bases.join('; ')];
  if (pulses.length) { lines.push(pulses.join('; ')); lines.push('repeat'); }
  return lines.join('\\n');
}

let agentStates = ['working', 'done'];
const controls = document.getElementById('controls');
const fleetBarEl = document.getElementById('fleetBar');
let fleetLeds = makeBar(fleetBarEl, 8);
let fleetCurrent = fleetSpecs(agentStates);

function renderControls() {
  controls.innerHTML = '';
  const countWrap = document.createElement('div');
  countWrap.className = 'agent';
  countWrap.innerHTML = '<label>Agents</label>';
  const countSel = document.createElement('select');
  for (let n = 1; n <= 8; n++) {
    const o = document.createElement('option');
    o.value = n; o.textContent = n;
    if (n === agentStates.length) o.selected = true;
    countSel.appendChild(o);
  }
  countSel.onchange = () => {
    const n = parseInt(countSel.value, 10);
    while (agentStates.length < n) agentStates.push('working');
    agentStates.length = n;
    renderControls(); refreshFleet();
  };
  countWrap.appendChild(countSel);
  controls.appendChild(countWrap);

  agentStates.forEach((state, i) => {
    const wrap = document.createElement('div');
    wrap.className = 'agent';
    wrap.innerHTML = `<label>Agent ${i + 1}</label>`;
    const sel = document.createElement('select');
    STATES.forEach(s => {
      const o = document.createElement('option');
      o.value = s; o.textContent = s;
      if (s === state) o.selected = true;
      sel.appendChild(o);
    });
    sel.onchange = () => { agentStates[i] = sel.value; refreshFleet(); };
    wrap.appendChild(sel);
    controls.appendChild(wrap);
  });

  const presets = [
    ['All working', ['working', 'working', 'working']],
    ['Two done, one busy', ['working', 'done', 'done']],
    ['One needs input', ['working', 'ask', 'done']],
    ['One broke', ['working', 'blocked', 'done']],
  ];
  presets.forEach(([label, states]) => {
    const b = document.createElement('button');
    b.className = 'preset';
    b.textContent = label;
    b.onclick = () => { agentStates = states.slice(); renderControls(); refreshFleet(); };
    controls.appendChild(b);
  });
}

function refreshFleet() {
  fleetCurrent = fleetSpecs(agentStates);
  fleetLeds = makeBar(fleetBarEl, 8);
  document.getElementById('fleetName').textContent =
    `${agentStates.length} agent${agentStates.length > 1 ? 's' : ''}`;
  document.getElementById('fleetNote').textContent =
    `bands: ${fleetCurrent.widths.join(' / ')} LEDs`;
  document.getElementById('fleetProgram').textContent = fleetProgramText(agentStates);
}

/* ---- palette ---- */
const sw = document.getElementById('swatches');
CFG.palette.forEach(p => {
  const el = document.createElement('div');
  el.className = 'sw';
  el.innerHTML = `<span class="dot" style="background:${p.color}"></span>
                  <span>${p.key}</span><code>${p.color} &middot; ${p.hue}&deg;</code>`;
  sw.appendChild(el);
});

renderControls();
refreshFleet();

function frame(now) {
  for (const view of aggregateViews) {
    const t = now % view.spec.cycle;
    view.leds.forEach((el, i) => paint(el, view.spec.leds[i] || {}, t));
  }
  const ft = now % fleetCurrent.cycle;
  fleetLeds.forEach((el, i) => paint(el, fleetCurrent.leds[i] || {}, ft));
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
</script>
</body>
</html>
"""


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    html = HTML.replace("__CONFIG__", json.dumps(build_config()))
    OUTPUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    main()
