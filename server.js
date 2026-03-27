import express from "express";
import fetch from "node-fetch";
import { v4 as uuidv4 } from "uuid";
import path from "path";
import { fileURLToPath } from "url";
import fs from "fs";
import multer from "multer";
import vision from "@google-cloud/vision";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();
const PORT = process.env.PORT || 3000;
const ESP32_HOST = process.env.ESP32_HOST || "http://192.168.2.114/";
const client = new vision.ImageAnnotatorClient();

// store latest image in-memory and on disk
let latest = {
  id: null,
  mime: "image/jpeg",
  buffer: null,
  labels: [],
  decision: null, // "trash" | "valuables"
  when: null,
};

// Simple static front-end (no frameworks)
app.get("/", (_req, res) => {
  res.type("html").send(`
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 2rem; }
    button { padding: .7rem 1rem; font-size: 1rem; cursor: pointer; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; margin-top: 1rem; }
    img { width: 100%; height: auto; border-radius: 12px; box-shadow: 0 6px 24px rgba(0,0,0,.1); }
    .card { border: 1px solid #e5e7eb; padding: 1rem; border-radius: 12px; }
    .pill { display:inline-block; padding: .2rem .6rem; border-radius:100px; border:1px solid #e5e7eb; margin:.2rem .2rem 0 0; }
    .result { font-weight: 700; font-size: 1.1rem; }
    .muted { color: #6b7280; font-size: .9rem; }
  </style>
</head>
<body>
  <h1>Smart Sorter</h1>
  <p class="muted">ESP32: <code>${ESP32_HOST}</code></p>
  <button id="capture">📸 Capture Image</button>
  <span id="status" class="muted" style="margin-left: .75rem;"></span>

  <div class="row">
    <div class="card">
      <h3>Latest Image</h3>
      <div id="imgwrap">
        <p class="muted">No image yet.</p>
      </div>
    </div>
    <div class="card">
      <h3>Vision Results</h3>
      <div id="labels"><p class="muted">Top-5 labels will appear here.</p></div>
      <p class="result" id="decision"></p>
      <p class="muted" id="timestamp"></p>
    </div>
  </div>

  <script>
    async function refresh() {
      const r = await fetch('/latest.json', { cache: 'no-store' });
      if (!r.ok) return;
      const j = await r.json();
      const imgwrap = document.getElementById('imgwrap');
      const labels = document.getElementById('labels');
      const decision = document.getElementById('decision');
      const ts = document.getElementById('timestamp');

      if (j && j.id) {
        imgwrap.innerHTML = '<img src="/latest.jpg?x=' + Date.now() + '" alt="latest" />';
        if (j.labels && j.labels.length) {
          labels.innerHTML = j.labels.map(l =>
            '<div class="pill">' + l.description + ' (' + (l.score*100).toFixed(1) + '%)</div>'
          ).join('');
        } else {
          labels.innerHTML = '<p class="muted">No labels.</p>';
        }
        if (j.decision) {
          decision.textContent = '➡ Sorted to: ' + (j.decision === 'valuables' ? 'Valuables Bin (wire/cable)' : 'Trash Bin');
        } else {
          decision.textContent = '';
        }
        ts.textContent = j.when ? new Date(j.when).toLocaleString() : '';
      }
    }

    document.getElementById('capture').addEventListener('click', async () => {
      const status = document.getElementById('status');
      status.textContent = 'Capturing...';
      try {
        const r = await fetch('/capture', { method: 'POST' });
        const j = await r.json();
        if (j.error) throw new Error(j.error);
        status.textContent = 'Done.';
      } catch (e) {
        status.textContent = 'Error: ' + e.message;
      }
      await refresh();
    });

    refresh();
  </script>
</body>
</html>
  `);
});

// Return latest JSON metadata
app.get("/latest.json", (_req, res) => {
  const { id, labels, decision, when } = latest;
  res.json({ id, labels, decision, when });
});

// Return latest JPEG
app.get("/latest.jpg", (_req, res) => {
  if (!latest.buffer) return res.status(404).send("No image captured yet.");
  res.setHeader("Content-Type", latest.mime);
  res.send(latest.buffer);
});

// (Optional) manual upload endpoint, if you want to test Vision without ESP32
const upload = multer({ storage: multer.memoryStorage() });
app.post("/upload", upload.single("image"), async (req, res) => {
  try {
    if (!req.file) return res.status(400).json({ error: "No image file uploaded" });
    const buffer = req.file.buffer;
    const analysis = await analyzeWithVision(buffer);
    const decision = decideBin(analysis);
    latest = {
      id: uuidv4(),
      mime: req.file.mimetype || "image/jpeg",
      buffer,
      labels: analysis,
      decision,
      when: Date.now(),
    };
    return res.json({ ok: true, latest: { ...latest, buffer: undefined } });
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: String(err) });
  }
});

// Main flow: Capture from ESP32, analyze, command servo
app.post("/capture", async (_req, res) => {
  try {
    // 1) Pull a fresh JPEG from ESP32
    const imgResp = await fetch(`${ESP32_HOST}/capture`, { method: "GET" });
    if (!imgResp.ok) throw new Error(`ESP32 /capture failed: ${imgResp.status}`);
    const contentType = imgResp.headers.get("content-type") || "image/jpeg";
    const buffer = Buffer.from(await imgResp.arrayBuffer());

    // 2) Send to Vision
    const analysis = await analyzeWithVision(buffer);

    // 3) Decide bin
    const decision = decideBin(analysis);

    // 4) Command ESP32 servo
    const actionResp = await fetch(`${ESP32_HOST}/sort?bin=${decision}`, { method: "GET" });
    if (!actionResp.ok) console.warn("ESP32 /sort failed:", actionResp.status);

    // 5) Save latest
    latest = {
      id: uuidv4(),
      mime: contentType,
      buffer,
      labels: analysis,
      decision,
      when: Date.now(),
    };

    res.json({ ok: true, id: latest.id, decision, labels: analysis });
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: String(err) });
  }
});

// --- Helpers ---

async function analyzeWithVision(buffer) {
  // Google Cloud Vision: LABEL_DETECTION
  // Make sure GOOGLE_APPLICATION_CREDENTIALS is set to your service account JSON file.
  const [result] = await client.labelDetection({ image: { content: buffer } });
  const labels = (result.labelAnnotations || [])
    .slice(0, 5)
    .map(l => ({
      description: l.description,
      score: Number(l.score || 0),
    }));
  return labels;
}

// Simple rule-based bin decision:
// If any label looks like "wire/cable/cord", treat as valuables; otherwise trash.
function decideBin(labels) {
  const text = labels.map(l => l.description.toLowerCase()).join(" | ");
  const wirey = /(wire|cable|cord|connector|plug|loom|wiring|usb|hdmi|ethernet)/i.test(text);
  if (wirey) return "valuables";
  // Add a few "trash-ish" helpers (still default to trash if no wire terms)
  // You can refine this list over time based on your real-world data.
  return "trash";
}

app.listen(PORT, () => {
  console.log(`Smart Sorter server listening on http://localhost:${PORT}`);
  console.log(`Expecting ESP32 at ${ESP32_HOST}`);
});

