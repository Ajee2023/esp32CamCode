import express from "express";
import { spawn } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import { DatabaseSync } from "node:sqlite";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const classificationDb = new DatabaseSync(path.join(__dirname, "classification-summary.sqlite"));

loadEnvFile(path.join(__dirname, ".env"));
initClassificationDb();

const app = express();
const PORT = Number(process.env.PORT || 3400);
const GEMINI_API_KEY = process.env.GEMINI_API_KEY || "YOUR_GEMINI_API_KEY";
const GEMINI_MODEL = process.env.GEMINI_MODEL || "gemini-3.1-flash-lite";
const EDGE_IMPULSE_API_KEY = process.env.EDGE_IMPULSE_API_KEY || "";
const EDGE_IMPULSE_PROJECT_ID = String(process.env.EDGE_IMPULSE_PROJECT_ID || "").trim();
const EDGE_IMPULSE_IMPULSE_ID = String(process.env.EDGE_IMPULSE_IMPULSE_ID || "").trim();
const EDGE_IMPULSE_MODE = normalizeEdgeImpulseMode(process.env.EDGE_IMPULSE_MODE || "auto");
const EDGE_IMPULSE_OFFLINE_URL = String(process.env.EDGE_IMPULSE_OFFLINE_URL || "").trim();
const EDGE_IMPULSE_REQUIRED = parseBool(process.env.EDGE_IMPULSE_REQUIRED, false);
const EDGE_IMPULSE_DECISION_POLICY = normalizeDecisionPolicy(process.env.EDGE_IMPULSE_DECISION_POLICY || "gemini-primary");
const EDGE_IMPULSE_LABEL = process.env.EDGE_IMPULSE_LABEL || "Edge Impulse";
const EDGE_IMPULSE_CONFIDENCE_THRESHOLD = clampNumber(process.env.EDGE_IMPULSE_CONFIDENCE_THRESHOLD, 30, 0, 100);
const EDGE_IMPULSE_REVIEW_UPLOAD = parseBool(process.env.EDGE_IMPULSE_REVIEW_UPLOAD, true);
const EDGE_IMPULSE_REVIEW_CATEGORY = normalizeEdgeImpulseReviewCategory(process.env.EDGE_IMPULSE_REVIEW_CATEGORY || "training");
const CAMERA_SOURCE = process.env.CAMERA_SOURCE || "browser";
const CAMERA_CAPTURE_URL = process.env.CAMERA_CAPTURE_URL || "http://192.168.137.9/capture";
const CAMERA_LABEL = CAMERA_SOURCE === "browser" ? "Laptop HD Camera" : "ESP32-CAM";
const SUPPORTED_UPLOAD_TYPES = new Set(["image/jpeg", "image/png"]);
const ESP32_S3_CONTROL_URL = process.env.ESP32_S3_CONTROL_URL || "http://192.168.137.154";
const WHEEL_NUDGE_MS = clampInt(process.env.WHEEL_NUDGE_MS, 60, 60, 1000);
const WHEEL_SPEED = clampInt(process.env.WHEEL_SPEED, 170, 80, 255);
const WHEEL_BLUE_NUDGES = clampNumber(process.env.WHEEL_BLUE_NUDGES, 7.5, 1, 40);
const WHEEL_GREEN_NUDGES = clampInt(process.env.WHEEL_GREEN_NUDGES, 5, 1, 40);
const WHEEL_BLUE_HOME_NUDGES = clampInt(process.env.WHEEL_BLUE_HOME_NUDGES, 5, 1, 40);
const WHEEL_GREEN_HOME_NUDGES = clampNumber(process.env.WHEEL_GREEN_HOME_NUDGES, 6.5, 1, 40);
const WHEEL_PARTIAL_NUDGE_MIN_MS = 30;
const WHEEL_PAUSE_MS = clampInt(process.env.WHEEL_PAUSE_MS, 150, 50, 1000);
const WHEEL_LEFT_DIRECTION = String(process.env.WHEEL_LEFT_DIRECTION || "reverse").toLowerCase() === "forward" ? "forward" : "reverse";
const WHEEL_RIGHT_DIRECTION = String(process.env.WHEEL_RIGHT_DIRECTION || "forward").toLowerCase() === "reverse" ? "reverse" : "forward";

const GEMINI_PROMPT =
  "Classify the main visible waste item in this image for a smart trash sorter. " +
  "Choose exactly one category from: Garbage, Recycle, Organic, Unknown. " +
  "Use Unknown when the image has no clear waste item or cannot be confidently mapped to Garbage, Recycle, or Organic. " +
  "Return only JSON with category, confidenceLevel, confidenceScore, and rationale fields. " +
  "confidenceLevel must be High, Medium, or Low. confidenceScore must be a number from 0 to 100. " +
  "For common plastic, metal, paper, cardboard, or glass packaging choose Recycle. " +
  "For food scraps or compostable plant matter choose Organic. " +
  "For dirty mixed waste or items not accepted by recycling/compost choose Garbage. " +
  "Keep rationale under 140 characters.";

const progressClients = new Set();
let latestRun = createRunState(null, "idle", "Ready");

app.use(express.json({ limit: "8mb" }));
app.use(express.static(path.join(__dirname, "public"), {
  etag: false,
  lastModified: false,
  maxAge: 0,
  setHeaders(res) {
    res.setHeader("Cache-Control", "no-store, max-age=0");
  }
}));

app.get("/api/config", (_req, res) => {
  res.json({
    cameraMode: CAMERA_SOURCE,
    cameraLabel: CAMERA_LABEL,
    browserCameraEnabled: CAMERA_SOURCE === "browser",
    legacyCameraCaptureUrl: CAMERA_CAPTURE_URL,
    cameraCaptureUrl: CAMERA_CAPTURE_URL,
    esp32S3ControlUrl: ESP32_S3_CONTROL_URL,
    geminiModel: GEMINI_MODEL,
    hasGeminiKey: GEMINI_API_KEY !== "YOUR_GEMINI_API_KEY",
    aiMode: buildAiModelLabel(),
    edgeImpulse: getEdgeImpulseConfigStatus(),
    sorter: {
      home: "Garbage",
      recycle: "Blue Left",
      organic: "Green Right",
      nudgeMs: WHEEL_NUDGE_MS,
      speed: WHEEL_SPEED,
      blueNudges: WHEEL_BLUE_NUDGES,
      blueHomeNudges: WHEEL_BLUE_HOME_NUDGES,
      greenNudges: WHEEL_GREEN_NUDGES,
      greenHomeNudges: WHEEL_GREEN_HOME_NUDGES,
      leftDirection: WHEEL_LEFT_DIRECTION,
      rightDirection: WHEEL_RIGHT_DIRECTION
    }
  });
});

app.get("/api/latest", (_req, res) => {
  res.json(latestRun);
});
app.get("/api/classifications", (_req, res) => {
  res.json({ ok: true, entries: listClassificationRecords() });
});

app.patch("/api/classifications/:id", (req, res) => {
  try {
    const entry = updateClassificationRecord(req.params.id, req.body || {});
    res.json({ ok: true, entry });
  } catch (error) {
    const status = error.code === "NOT_FOUND" ? 404 : 400;
    res.status(status).json({ ok: false, error: error.message || String(error) });
  }
});

app.put("/api/classifications/:id", (req, res) => {
  try {
    const entry = updateClassificationRecord(req.params.id, req.body || {});
    res.json({ ok: true, entry });
  } catch (error) {
    const status = error.code === "NOT_FOUND" ? 404 : 400;
    res.status(status).json({ ok: false, error: error.message || String(error) });
  }
});

app.delete("/api/classifications/:id", (req, res) => {
  try {
    deleteClassificationRecord(req.params.id);
    res.json({ ok: true });
  } catch (error) {
    const status = error.code === "NOT_FOUND" ? 404 : 400;
    res.status(status).json({ ok: false, error: error.message || String(error) });
  }
});

app.get("/api/telemetry", async (_req, res) => {
  try {
    const telemetry = await getEsp32Telemetry();
    res.json({ ok: true, ...telemetry });
  } catch (error) {
    res.status(500).json({ ok: false, error: error.message || String(error) });
  }
});

app.get("/api/events", (req, res) => {
  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  res.flushHeaders?.();

  progressClients.add(res);
  sendEvent(res, "snapshot", latestRun);

  req.on("close", () => {
    progressClients.delete(res);
  });
});

app.post("/api/classify-upload", async (req, res) => {
  const runId = `run-${Date.now()}`;
  latestRun = createRunState(runId);

  broadcast("snapshot", latestRun);

  try {
    await setProgress("Preparing", "Laptop Camera", "Starting classification flow");
    await tryUpdateLcd("Preparing", "Laptop Camera");

    await setProgress("Capturing", "Laptop Camera", "Receiving browser camera frame");
    await tryUpdateLcd("Capturing", "Laptop Camera");

    const image = parseUploadedImage(req.body);
    const sorterSettings = normalizeSorterSettings(req.body?.sorterSettings);
    latestRun.imageDataUrl = `data:${image.mime};base64,${image.base64}`;
    broadcast("snapshot", latestRun);

    await setProgress("Captured", `${image.bytes} bytes`, "Frame received from laptop camera");
    await setProgress("Classifying", "Gemini AI", "Sending image to Gemini");
    await tryUpdateLcd("Classifying", "Gemini AI");

    const classification = await classifyWithAiReview(image.base64, image.mime);
    const category = classification.category;
    latestRun.category = category;
    latestRun.confidenceLevel = classification.confidenceLevel;
    latestRun.confidenceScore = classification.confidenceScore;
    latestRun.rationale = classification.rationale;
    latestRun.gemini = classification.gemini;
    latestRun.edgeImpulse = classification.edgeImpulse;
    latestRun.aiDecision = classification.decision;
    latestRun.message = `Classified as ${category}`;
    latestRun.summaryEntry = createClassificationRecord({
      aiModel: classification.aiModel,
      classification: category,
      imageDataUrl: latestRun.imageDataUrl,
      confidenceLevel: classification.confidenceLevel,
      confidenceScore: classification.confidenceScore,
      rationale: classification.rationale,
      geminiConfidenceLevel: classification.gemini?.confidenceLevel,
      geminiConfidenceScore: classification.gemini?.confidenceScore,
      edgeImpulseConfidenceLevel: classification.edgeImpulse?.confidenceLevel,
      edgeImpulseConfidenceScore: classification.edgeImpulse?.confidenceScore
    });
    await setProgress("Classified", `${category} ${formatConfidenceSummary(classification)}`, classification.rationale || `Gemini classified the item as ${category}`);
    await tryUpdateLcd("AI RESULT", category);

    const sortResult = await sortAndOpenForCategory(category, sorterSettings);

    latestRun.status = "complete";
    latestRun.message = sortResult.skipped
      ? `Classified as ${category}; manual review needed`
      : `Classified as ${category}; sorted and returned home`;
    await setProgress(
      "Complete",
      category,
      sortResult.skipped
        ? "Unknown item held for manual review; no bin movement or lid opening"
        : "Classification complete, lid closed, wheel returned home"
    );
    if (!sortResult.skipped) {
      await tryUpdateLcd("Ready", "Garbage Home");
    }
    broadcast("snapshot", latestRun);

    res.json({
      ok: true,
      category,
      run: latestRun
    });
  } catch (error) {
    latestRun.status = "error";
    latestRun.message = error.message || String(error);
    await tryUpdateLcd("Test failed", safeLcdText(latestRun.message));
    broadcast("snapshot", latestRun);
    res.status(500).json({ ok: false, error: latestRun.message, run: latestRun });
  }
});
app.post("/api/run-classification", async (_req, res) => {
  const runId = `run-${Date.now()}`;
  latestRun = createRunState(runId);

  broadcast("snapshot", latestRun);

  try {
    await setProgress("Preparing", "Motor disabled", "Starting test mode");
    await tryUpdateLcd("Preparing", "Motor disabled");

    const image = await captureImage();
    latestRun.imageDataUrl = `data:${image.mime};base64,${image.base64}`;
    broadcast("snapshot", latestRun);

    await setProgress("Classifying", "Gemini AI", "Sending image to Gemini");
    await tryUpdateLcd("Classifying", "Gemini AI");

    const classification = await classifyWithAiReview(image.base64, image.mime);
    const category = classification.category;
    latestRun.category = category;
    latestRun.confidenceLevel = classification.confidenceLevel;
    latestRun.confidenceScore = classification.confidenceScore;
    latestRun.rationale = classification.rationale;
    latestRun.gemini = classification.gemini;
    latestRun.edgeImpulse = classification.edgeImpulse;
    latestRun.aiDecision = classification.decision;
    latestRun.message = `Classified as ${category}`;
    latestRun.summaryEntry = createClassificationRecord({
      aiModel: classification.aiModel,
      classification: category,
      imageDataUrl: latestRun.imageDataUrl,
      confidenceLevel: classification.confidenceLevel,
      confidenceScore: classification.confidenceScore,
      rationale: classification.rationale,
      geminiConfidenceLevel: classification.gemini?.confidenceLevel,
      geminiConfidenceScore: classification.gemini?.confidenceScore,
      edgeImpulseConfidenceLevel: classification.edgeImpulse?.confidenceLevel,
      edgeImpulseConfidenceScore: classification.edgeImpulse?.confidenceScore
    });
    await setProgress("Classified", `${category} ${formatConfidenceSummary(classification)}`, classification.rationale || `Gemini classified the item as ${category}`);
    await tryUpdateLcd("AI RESULT", category);

    const sortResult = await sortAndOpenForCategory(category);

    latestRun.status = "complete";
    latestRun.message = sortResult.skipped
      ? `Classified as ${category}; manual review needed`
      : `Classified as ${category}; sorted and returned home`;
    await setProgress(
      "Complete",
      category,
      sortResult.skipped
        ? "Unknown item held for manual review; no bin movement or lid opening"
        : "Classification complete, lid closed, wheel returned home"
    );
    if (!sortResult.skipped) {
      await tryUpdateLcd("Ready", "Garbage Home");
    }
    broadcast("snapshot", latestRun);

    res.json({
      ok: true,
      category,
      run: latestRun
    });
  } catch (error) {
    latestRun.status = "error";
    latestRun.message = error.message || String(error);
    await tryUpdateLcd("Test failed", safeLcdText(latestRun.message));
    broadcast("snapshot", latestRun);
    res.status(500).json({ ok: false, error: latestRun.message, run: latestRun });
  }
});

app.post("/api/lcd", async (req, res) => {
  const line1 = String(req.body?.line1 || "Waste Sorter");
  const line2 = String(req.body?.line2 || "Ready");
  await updateLcd(line1, line2);
  res.json({ ok: true });
});

app.post("/api/motor", handleMotorRequest);
app.post("/api/future/motor", handleMotorRequest);
app.post("/api/motor-raw", handleRawMotorRequest);

async function handleRawMotorRequest(req, res) {
  try {
    const result = await rotateMotorRaw(req.body || {});
    res.json({ ok: true, message: "Raw wheel command complete", esp32: result });
  } catch (error) {
    await tryUpdateLcd("Raw failed", safeLcdText(error.message));
    res.status(500).json({ ok: false, error: error.message || String(error) });
  }
}

async function handleMotorRequest(req, res) {
  const body = req.body || {};
  const direction = String(body.direction || "").toLowerCase();

  try {
    if (direction === "stop") {
      const result = await rotateMotor({ ...body, direction: "stop", ms: 100, speed: 0 });
      await tryUpdateLcd("Wheel", "Stopped");
      res.json({ ok: true, message: "Wheel motor stopped", esp32: result });
      return;
    }

    const ms = clampInt(body.ms, 800, 100, 5000);
    const speed = clampInt(body.speed, 255, 0, 255);
    await tryUpdateLcd("Wheel", "Starting");
    const result = await rotateMotor({ ...body, ms, speed });
    res.json({ ok: true, message: "Wheel motor command sent", ms, speed, esp32: result });
  } catch (error) {
    await tryUpdateLcd("Wheel failed", safeLcdText(error.message));
    res.status(500).json({ ok: false, error: error.message || String(error) });
  }
}

app.post("/api/lid", async (_req, res) => {
  try {
    await openLid();
    res.json({ ok: true, message: "Lid opened and closed" });
  } catch (error) {
    res.status(500).json({ ok: false, error: error.message || String(error) });
  }
});

function initClassificationDb() {
  classificationDb.exec(`
    CREATE TABLE IF NOT EXISTS classifications (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at TEXT NOT NULL,
      updated_at TEXT,
      ai_model TEXT NOT NULL,
      classification TEXT NOT NULL,
      image_data_url TEXT,
      confidence_level TEXT,
      confidence_score REAL,
      gemini_confidence_level TEXT,
      gemini_confidence_score REAL,
      edge_impulse_confidence_level TEXT,
      edge_impulse_confidence_score REAL,
      rationale TEXT
    )
  `);
  ensureClassificationColumns();
}

function ensureClassificationColumns() {
  const existingColumns = new Set(
    classificationDb.prepare("PRAGMA table_info(classifications)").all().map(column => column.name)
  );
  const additions = [
    ["gemini_confidence_level", "gemini_confidence_level TEXT"],
    ["gemini_confidence_score", "gemini_confidence_score REAL"],
    ["edge_impulse_confidence_level", "edge_impulse_confidence_level TEXT"],
    ["edge_impulse_confidence_score", "edge_impulse_confidence_score REAL"]
  ];

  for (const [name, ddl] of additions) {
    if (!existingColumns.has(name)) {
      classificationDb.exec(`ALTER TABLE classifications ADD COLUMN ${ddl}`);
    }
  }
}

function classificationSelectFields() {
  return `id, created_at, updated_at, ai_model, classification, image_data_url, confidence_level, confidence_score, gemini_confidence_level, gemini_confidence_score, edge_impulse_confidence_level, edge_impulse_confidence_score, rationale`;
}

function listClassificationRecords() {
  return classificationDb
    .prepare(`
      SELECT ${classificationSelectFields()}
      FROM classifications
      ORDER BY datetime(created_at) DESC, id DESC
    `)
    .all()
    .map(classificationRowToJson);
}

function getClassificationRecord(id) {
  const entryId = normalizeEntryId(id);
  const row = classificationDb
    .prepare(`
      SELECT ${classificationSelectFields()}
      FROM classifications
      WHERE id = ?
    `)
    .get(entryId);
  return row ? classificationRowToJson(row) : null;
}

function createClassificationRecord(data) {
  const finalScore = normalizeConfidenceScore(data.confidenceScore);
  const geminiScore = normalizeConfidenceScore(data.geminiConfidenceScore ?? data.confidenceScore);
  const edgeScore = normalizeConfidenceScore(data.edgeImpulseConfidenceScore);
  const result = classificationDb
    .prepare(`
      INSERT INTO classifications (
        created_at,
        ai_model,
        classification,
        image_data_url,
        confidence_level,
        confidence_score,
        gemini_confidence_level,
        gemini_confidence_score,
        edge_impulse_confidence_level,
        edge_impulse_confidence_score,
        rationale
      )
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `)
    .run(
      new Date().toISOString(),
      String(data.aiModel || GEMINI_MODEL),
      normalizeCategory(data.classification),
      String(data.imageDataUrl || ""),
      normalizeConfidenceLevel(data.confidenceLevel, finalScore),
      finalScore,
      normalizeOptionalConfidenceLevel(data.geminiConfidenceLevel, geminiScore),
      geminiScore,
      normalizeOptionalConfidenceLevel(data.edgeImpulseConfidenceLevel, edgeScore),
      edgeScore,
      normalizeRationale(data.rationale)
    );

  return getClassificationRecord(result.lastInsertRowid);
}

function updateClassificationRecord(id, body) {
  const entryId = normalizeEntryId(id);
  const current = getClassificationRecord(entryId);
  if (!current) {
    const error = new Error("Classification entry not found");
    error.code = "NOT_FOUND";
    throw error;
  }

  const nextClassification = body.classification === undefined
    ? current.classification
    : normalizeCategory(body.classification);
  const nextGeminiScore = body.geminiConfidenceScore === undefined
    ? current.geminiConfidenceScore
    : normalizeConfidenceScore(body.geminiConfidenceScore);
  const nextGeminiLevel = body.geminiConfidenceLevel === undefined
    ? current.geminiConfidenceLevel
    : normalizeOptionalConfidenceLevel(body.geminiConfidenceLevel, nextGeminiScore);
  const nextEdgeScore = body.edgeImpulseConfidenceScore === undefined
    ? current.edgeImpulseConfidenceScore
    : normalizeConfidenceScore(body.edgeImpulseConfidenceScore);
  const nextEdgeLevel = body.edgeImpulseConfidenceLevel === undefined
    ? current.edgeImpulseConfidenceLevel
    : normalizeOptionalConfidenceLevel(body.edgeImpulseConfidenceLevel, nextEdgeScore);
  const nextScore = body.confidenceScore === undefined
    ? nextGeminiScore
    : normalizeConfidenceScore(body.confidenceScore);
  const nextLevel = body.confidenceLevel === undefined
    ? (nextGeminiLevel || current.confidenceLevel)
    : normalizeConfidenceLevel(body.confidenceLevel, nextScore);
  const nextRationale = body.rationale === undefined
    ? current.rationale
    : normalizeRationale(body.rationale);

  classificationDb
    .prepare(`
      UPDATE classifications
      SET updated_at = ?, classification = ?, confidence_level = ?, confidence_score = ?, gemini_confidence_level = ?, gemini_confidence_score = ?, edge_impulse_confidence_level = ?, edge_impulse_confidence_score = ?, rationale = ?
      WHERE id = ?
    `)
    .run(
      new Date().toISOString(),
      nextClassification,
      nextLevel,
      nextScore,
      nextGeminiLevel,
      nextGeminiScore,
      nextEdgeLevel,
      nextEdgeScore,
      nextRationale,
      entryId
    );

  return getClassificationRecord(entryId);
}

function deleteClassificationRecord(id) {
  const entryId = normalizeEntryId(id);
  const result = classificationDb.prepare("DELETE FROM classifications WHERE id = ?").run(entryId);
  if (!result.changes) {
    const error = new Error("Classification entry not found");
    error.code = "NOT_FOUND";
    throw error;
  }
}

function classificationRowToJson(row) {
  const finalScore = nullableNumber(row.confidence_score);
  const geminiScore = nullableNumber(row.gemini_confidence_score) ?? finalScore;
  const edgeScore = nullableNumber(row.edge_impulse_confidence_score);
  return {
    id: Number(row.id),
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    aiModel: row.ai_model,
    classification: row.classification,
    imageDataUrl: row.image_data_url,
    confidenceLevel: row.confidence_level,
    confidenceScore: finalScore,
    geminiConfidenceLevel: row.gemini_confidence_level || row.confidence_level,
    geminiConfidenceScore: geminiScore,
    edgeImpulseConfidenceLevel: row.edge_impulse_confidence_level || null,
    edgeImpulseConfidenceScore: edgeScore,
    rationale: row.rationale || ""
  };
}

function nullableNumber(value) {
  return value === null || value === undefined ? null : Number(value);
}

function normalizeEntryId(value) {
  const id = Number.parseInt(value, 10);
  if (!Number.isFinite(id) || id < 1) {
    throw new Error("Invalid classification entry id");
  }
  return id;
}
function parseUploadedImage(body) {
  const dataUrl = String(body?.imageDataUrl || "");
  let mime = String(body?.mime || "").trim().toLowerCase();
  let base64 = String(body?.base64 || "").trim();

  if (dataUrl) {
    const match = dataUrl.match(/^data:(image\/(?:jpeg|jpg|png));base64,([A-Za-z0-9+/=\r\n]+)$/);
    if (!match) {
      throw new Error("Browser capture must be a JPEG or PNG image");
    }

    mime = match[1] === "image/jpg" ? "image/jpeg" : match[1];
    base64 = match[2].replace(/\s/g, "");
  }

  if (mime === "image/jpg") {
    mime = "image/jpeg";
  }

  if (!SUPPORTED_UPLOAD_TYPES.has(mime)) {
    throw new Error("Unsupported image type from browser camera");
  }

  if (!base64 || !/^[A-Za-z0-9+/]+={0,2}$/.test(base64) || base64.length % 4 !== 0) {
    throw new Error("Browser camera returned invalid image data");
  }

  const buffer = Buffer.from(base64, "base64");
  if (buffer.length < 256) {
    throw new Error("Browser camera image is too small");
  }

  if (!matchesImageSignature(buffer, mime)) {
    throw new Error("Browser camera image data does not match its file type");
  }

  return {
    mime,
    bytes: buffer.length,
    base64: buffer.toString("base64")
  };
}

function matchesImageSignature(buffer, mime) {
  if (mime === "image/jpeg") {
    return buffer.length >= 4 && buffer[0] === 0xff && buffer[1] === 0xd8 && buffer[buffer.length - 2] === 0xff && buffer[buffer.length - 1] === 0xd9;
  }

  if (mime === "image/png") {
    return (
      buffer.length >= 8 &&
      buffer[0] === 0x89 &&
      buffer[1] === 0x50 &&
      buffer[2] === 0x4e &&
      buffer[3] === 0x47 &&
      buffer[4] === 0x0d &&
      buffer[5] === 0x0a &&
      buffer[6] === 0x1a &&
      buffer[7] === 0x0a
    );
  }

  return false;
}
async function captureImage() {
  await setProgress("Capturing", "ESP32-CAM", "Requesting latest JPEG");
  await tryUpdateLcd("Capturing", "ESP32-CAM");

  const image = await getCameraJpeg(CAMERA_CAPTURE_URL);
  const mime = image.mime;
  const buffer = image.buffer;
  if (buffer.length < 4 || buffer[0] !== 0xff || buffer[1] !== 0xd8) {
    throw new Error("Camera did not return a JPEG image");
  }

  await setProgress("Captured", `${buffer.length} bytes`, "JPEG received from camera");
  return {
    mime,
    bytes: buffer.length,
    base64: buffer.toString("base64")
  };
}

async function getCameraJpeg(urlString) {
  if (process.platform === "win32") {
    return getCameraJpegWithCurl(urlString);
  }

  const url = new URL(urlString);
  if (url.protocol !== "http:") {
    const response = await fetch(urlString, {
      method: "GET",
      signal: AbortSignal.timeout(20000)
    });

    if (!response.ok) {
      throw new Error(`Camera capture failed: HTTP ${response.status}`);
    }

    return {
      mime: response.headers.get("content-type") || "image/jpeg",
      buffer: Buffer.from(await response.arrayBuffer())
    };
  }

  return new Promise((resolve, reject) => {
    const chunks = [];
    let settled = false;
    const socket = net.createConnection({
      host: url.hostname,
      port: Number(url.port || 80)
    });

    socket.setTimeout(20000);

    socket.on("connect", () => {
      const target = `${url.pathname || "/"}${url.search || ""}`;
      socket.write(
        `GET ${target} HTTP/1.1\r\n` +
        `Host: ${url.host}\r\n` +
        "Accept: image/jpeg\r\n" +
        "Connection: close\r\n\r\n"
      );
    });

    socket.on("data", chunk => {
      chunks.push(chunk);
      tryResolveJpeg();
    });

    socket.on("timeout", () => {
      socket.destroy(new Error("Camera capture timed out"));
    });

    socket.on("error", error => {
      if (!settled) {
        settled = true;
        reject(error);
      }
    });

    socket.on("end", () => {
      tryResolveJpeg(true);
    });

    function tryResolveJpeg(requireCompleteHttp = false) {
      if (settled) {
        return;
      }

      try {
        const raw = Buffer.concat(chunks);
        const splitAt = raw.indexOf(Buffer.from("\r\n\r\n"));
        if (splitAt < 0) {
          if (requireCompleteHttp) {
            throw new Error("Camera returned an invalid HTTP response");
          }
          return;
        }

        const bodyStart = splitAt + 4;
        const body = raw.slice(bodyStart);
        const jpegEndRelative = body.indexOf(Buffer.from([0xff, 0xd9]));
        if (jpegEndRelative < 0 && !requireCompleteHttp) {
          return;
        }

        if (jpegEndRelative < 0) {
          throw new Error("Camera returned an invalid HTTP response");
        }

        const headerText = raw.slice(0, splitAt).toString("latin1");
        const jpegBody = body.slice(0, jpegEndRelative + 2);
        const statusMatch = headerText.match(/^HTTP\/1\.[01]\s+(\d+)/i);
        const status = statusMatch ? Number(statusMatch[1]) : 0;
        if (status < 200 || status >= 300) {
          throw new Error(`Camera capture failed: HTTP ${status || "unknown"}`);
        }

        const contentTypeMatch = headerText.match(/^content-type:\s*([^\r\n]+)/im);
        settled = true;
        resolve({
          mime: contentTypeMatch?.[1]?.trim() || "image/jpeg",
          buffer: jpegBody
        });
        socket.destroy();
      } catch (error) {
        if (!settled) {
          settled = true;
          reject(error);
        }
      }
    }
  });
}

function getCameraJpegWithCurl(urlString) {
  return new Promise((resolve, reject) => {
    const child = spawn("curl.exe", [
      "--silent",
      "--show-error",
      "--ignore-content-length",
      "--max-time",
      "20",
      urlString
    ]);

    const stdout = [];
    const stderr = [];

    child.stdout.on("data", chunk => stdout.push(chunk));
    child.stderr.on("data", chunk => stderr.push(chunk));
    child.on("error", reject);
    child.on("close", code => {
      const errorText = Buffer.concat(stderr).toString("utf8").trim();
      if (code !== 0) {
        reject(new Error(errorText || `curl exited with code ${code}`));
        return;
      }

      const buffer = Buffer.concat(stdout);
      resolve({
        mime: "image/jpeg",
        buffer
      });
    });
  });
}

function createRunState(id, status = "running", message = "Starting") {
  return {
    id,
    status,
    category: null,
    confidenceLevel: null,
    confidenceScore: null,
    rationale: null,
    summaryEntry: null,
    imageDataUrl: null,
    gemini: null,
    edgeImpulse: null,
    aiDecision: null,
    steps: [],
    message
  };
}

function getEdgeImpulseConfigStatus() {
  return {
    mode: EDGE_IMPULSE_MODE,
    enabled: EDGE_IMPULSE_MODE !== "disabled",
    required: EDGE_IMPULSE_REQUIRED,
    decisionPolicy: EDGE_IMPULSE_DECISION_POLICY,
    hasApiKey: Boolean(EDGE_IMPULSE_API_KEY),
    projectConfigured: Boolean(EDGE_IMPULSE_PROJECT_ID),
    impulseConfigured: Boolean(EDGE_IMPULSE_IMPULSE_ID),
    offlineConfigured: Boolean(EDGE_IMPULSE_OFFLINE_URL),
    label: EDGE_IMPULSE_LABEL,
    confidenceThreshold: EDGE_IMPULSE_CONFIDENCE_THRESHOLD,
    reviewUploadEnabled: EDGE_IMPULSE_REVIEW_UPLOAD,
    reviewCategory: EDGE_IMPULSE_REVIEW_CATEGORY,
    reviewUrl: edgeImpulseStudioDataUrl()
  };
}

async function classifyWithAiReview(base64Image, mimeType = "image/jpeg") {
  const gemini = await classifyImage(base64Image, mimeType);
  await setProgress(
    "Gemini Result",
    `${gemini.category} ${formatConfidenceSummary(gemini)}`,
    gemini.rationale || `Gemini selected ${gemini.category}`
  );

  let edgeImpulse;
  if (shouldRunEdgeImpulseForGemini(gemini)) {
    await setProgress(
      "Low Confidence",
      "Edge Impulse",
      `Gemini confidence ${formatConfidenceValue(gemini.confidenceScore)} is at or below ${EDGE_IMPULSE_CONFIDENCE_THRESHOLD}%; checking Edge Impulse`
    );
    edgeImpulse = await classifyWithEdgeImpulseReview(base64Image, mimeType);
    if (edgeImpulse.status === "ok") {
      await setProgress(
        "Edge Impulse",
        `${edgeImpulse.category} ${formatConfidenceSummary(edgeImpulse)}`,
        edgeImpulse.rationale || `${EDGE_IMPULSE_LABEL} selected ${edgeImpulse.category}`
      );
    } else {
      await setProgress(
        "Edge Impulse",
        edgeImpulse.status === "disabled" ? "Disabled" : edgeImpulse.status === "skipped" ? "Skipped" : edgeImpulse.status === "needs-review" ? "Review needed" : "Unavailable",
        edgeImpulse.message
      );
    }
  } else {
    edgeImpulse = edgeImpulseStatus(
      "skipped",
      `Gemini confidence ${formatConfidenceValue(gemini.confidenceScore)} is above ${EDGE_IMPULSE_CONFIDENCE_THRESHOLD}%; using Gemini only`,
      "confidence-threshold"
    );
    await setProgress("Edge Impulse", "Not needed", edgeImpulse.message);
  }

  const decision = combineAiDecision(gemini, edgeImpulse);
  await setProgress("AI Decision", `${decision.category} ${formatConfidenceSummary(decision)}`, decision.rationale);

  return {
    category: decision.category,
    confidenceLevel: decision.confidenceLevel,
    confidenceScore: decision.confidenceScore,
    rationale: decision.rationale,
    aiModel: buildAiModelLabel(edgeImpulse, decision),
    gemini,
    edgeImpulse,
    decision
  };
}

async function classifyWithEdgeImpulseReview(base64Image, mimeType = "image/jpeg") {
  if (EDGE_IMPULSE_MODE === "disabled") {
    return edgeImpulseStatus("disabled", "Edge Impulse is disabled by EDGE_IMPULSE_MODE");
  }

  try {
    if (EDGE_IMPULSE_API_KEY && EDGE_IMPULSE_PROJECT_ID) {
      const manualReview = await findEdgeImpulseManualReviewLabel(base64Image, mimeType);
      if (manualReview?.category) {
        return {
          status: "ok",
          mode: "manual",
          ...manualReview
        };
      }
    }

    if ((EDGE_IMPULSE_MODE === "offline" || EDGE_IMPULSE_MODE === "auto") && EDGE_IMPULSE_OFFLINE_URL) {
      return {
        status: "ok",
        mode: "offline",
        ...(await classifyWithEdgeImpulseOffline(base64Image, mimeType))
      };
    }

    if (EDGE_IMPULSE_MODE === "offline") {
      return edgeImpulseStatus("skipped", "Set EDGE_IMPULSE_OFFLINE_URL to use the exported offline model endpoint");
    }

    if (!EDGE_IMPULSE_API_KEY || !EDGE_IMPULSE_PROJECT_ID) {
      return edgeImpulseStatus("skipped", "Set EDGE_IMPULSE_PROJECT_ID to enable Edge Impulse cloud verification");
    }

    return {
      status: "ok",
      mode: "cloud",
      ...(await classifyWithEdgeImpulseCloud(base64Image, mimeType))
    };
  } catch (error) {
    if (error?.isEdgeImpulseUnknownLabel) {
      const review = await uploadEdgeImpulseUnknownReviewSample(base64Image, mimeType, error).catch(uploadError => ({
        uploaded: false,
        message: `Review upload failed: ${uploadError.message || String(uploadError)}`
      }));
      return edgeImpulseStatus(
        "needs-review",
        review.manualLabel
          ? `Using Edge Impulse manual label ${review.manualLabel}`
          : `${error.message}. ${review.message}`,
        "edge-unknown",
        {
          confidenceLevel: null,
          confidenceScore: null,
          rawLabel: error.rawLabel,
          rawScore: error.score,
          review
        }
      );
    }

    if (EDGE_IMPULSE_REQUIRED) {
      throw new Error(`${EDGE_IMPULSE_LABEL} failed: ${error.message || String(error)}`);
    }
    return edgeImpulseStatus("error", error.message || String(error));
  }
}

async function classifyWithEdgeImpulseCloud(base64Image, mimeType = "image/jpeg") {
  const endpoint = new URL(`https://studio.edgeimpulse.com/v1/api/${encodeURIComponent(EDGE_IMPULSE_PROJECT_ID)}/classify/image`);
  if (EDGE_IMPULSE_IMPULSE_ID) {
    endpoint.searchParams.set("impulseId", EDGE_IMPULSE_IMPULSE_ID);
  }

  const imageBuffer = Buffer.from(base64Image, "base64");
  const imageName = mimeType === "image/png" ? "capture.png" : "capture.jpg";
  const form = new FormData();
  form.append("image", new Blob([imageBuffer], { type: mimeType }), imageName);

  const response = await fetch(endpoint, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "x-api-key": EDGE_IMPULSE_API_KEY
    },
    body: form,
    signal: AbortSignal.timeout(60000)
  });

  const bodyText = await response.text();
  if (!response.ok) {
    throw new Error(`HTTP ${response.status} ${bodyText.slice(0, 400)}`);
  }

  return parseEdgeImpulseClassificationBody(parseJsonResponse(bodyText, EDGE_IMPULSE_LABEL), "Edge Impulse cloud");
}

async function classifyWithEdgeImpulseOffline(base64Image, mimeType = "image/jpeg") {
  const imageDataUrl = `data:${mimeType};base64,${base64Image}`;
  const response = await fetch(EDGE_IMPULSE_OFFLINE_URL, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ imageDataUrl, base64: base64Image, mime: mimeType }),
    signal: AbortSignal.timeout(30000)
  });

  const bodyText = await response.text();
  if (!response.ok) {
    throw new Error(`offline HTTP ${response.status} ${bodyText.slice(0, 400)}`);
  }

  return parseEdgeImpulseClassificationBody(parseJsonResponse(bodyText, "Edge Impulse offline"), "Edge Impulse offline");
}

function parseJsonResponse(bodyText, sourceName) {
  try {
    return JSON.parse(bodyText);
  } catch (_error) {
    throw new Error(`${sourceName} returned invalid JSON: ${bodyText.slice(0, 300)}`);
  }
}

function parseEdgeImpulseClassificationBody(body, sourceName) {
  if (body?.success === false) {
    throw new Error(body.error || `${sourceName} returned success=false`);
  }

  const directValue = body?.category ?? body?.classification ?? body?.label ?? body?.prediction;
  if (typeof directValue === "string") {
    const directCategory = categoryFromEdgeImpulseLabel(directValue);
    if (!directCategory) {
      throw edgeImpulseUnknownLabelError(sourceName, directValue, body?.confidenceScore ?? body?.score ?? body?.confidence);
    }

    const direct = normalizeClassificationResult({
      category: directCategory,
      confidenceScore: body?.confidenceScore ?? body?.score ?? body?.confidence,
      rationale: `${sourceName} label ${directValue}`
    }, directCategory);
    direct.rawLabel = directValue;
    direct.source = sourceName;
    return direct;
  }

  const rawCandidates = collectEdgeImpulseCandidates(body)
    .sort((a, b) => Number(b.score ?? 0) - Number(a.score ?? 0));

  if (!rawCandidates.length) {
    throw new Error(`${sourceName} returned no labels`);
  }

  const top = rawCandidates[0];
  const topCategory = categoryFromEdgeImpulseLabel(top.label);
  if (!topCategory) {
    throw edgeImpulseUnknownLabelError(sourceName, top.label, top.score);
  }

  const result = normalizeClassificationResult({
    category: topCategory,
    confidenceScore: top.score,
    rationale: `${sourceName} label ${top.label}`
  }, topCategory);
  result.rawLabel = top.label;
  result.source = sourceName;
  return result;
}

function edgeImpulseUnknownLabelError(sourceName, label, score) {
  const normalizedScore = normalizeConfidenceScore(score);
  const scoreText = normalizedScore === null ? "no score" : `${normalizedScore}%`;
  const error = new Error(`${sourceName} top label "${label}" (${scoreText}) is not Garbage, Recycle, or Organic`);
  error.isEdgeImpulseUnknownLabel = true;
  error.rawLabel = String(label || "").trim();
  error.score = normalizedScore;
  error.sourceName = sourceName;
  return error;
}

async function findEdgeImpulseManualReviewLabel(base64Image, mimeType = "image/jpeg") {
  if (!EDGE_IMPULSE_API_KEY || !EDGE_IMPULSE_PROJECT_ID) {
    return null;
  }

  const fingerprint = edgeImpulseReviewFingerprint(base64Image);
  const endpoint = new URL(`https://studio.edgeimpulse.com/v1/api/${encodeURIComponent(EDGE_IMPULSE_PROJECT_ID)}/raw-data`);
  endpoint.searchParams.set("category", EDGE_IMPULSE_REVIEW_CATEGORY);
  endpoint.searchParams.set("filename", fingerprint);
  endpoint.searchParams.set("limit", "20");
  endpoint.searchParams.set("excludeSensors", "true");

  const response = await fetch(endpoint, {
    headers: {
      Accept: "application/json",
      "x-api-key": EDGE_IMPULSE_API_KEY
    },
    signal: AbortSignal.timeout(20000)
  });

  const bodyText = await response.text();
  if (!response.ok) {
    return null;
  }

  const body = parseJsonResponse(bodyText, "Edge Impulse sample lookup");
  const samples = Array.isArray(body?.samples) ? body.samples : [];
  for (const sample of samples) {
    const label = sample?.label;
    const category = categoryFromEdgeImpulseLabel(label);
    if (category) {
      return {
        category,
        confidenceLevel: "High",
        confidenceScore: 100,
        rationale: `${EDGE_IMPULSE_LABEL} manual label ${label}`,
        rawLabel: label,
        source: `${EDGE_IMPULSE_LABEL} manual label`,
        reviewSampleId: sample.id,
        reviewFilename: sample.filename,
        reviewUrl: edgeImpulseStudioDataUrl()
      };
    }
  }

  return null;
}

async function uploadEdgeImpulseUnknownReviewSample(base64Image, mimeType = "image/jpeg", unknownError = null) {
  if (!EDGE_IMPULSE_REVIEW_UPLOAD) {
    return { uploaded: false, message: "Review upload is disabled by EDGE_IMPULSE_REVIEW_UPLOAD" };
  }
  if (!EDGE_IMPULSE_API_KEY || !EDGE_IMPULSE_PROJECT_ID) {
    return { uploaded: false, message: "Set Edge Impulse API key and project ID to upload review samples" };
  }

  const manualReview = await findEdgeImpulseManualReviewLabel(base64Image, mimeType);
  if (manualReview?.category) {
    return { uploaded: false, manualLabel: manualReview.category, message: `Found existing manual label ${manualReview.category}` };
  }

  const imageBuffer = Buffer.from(base64Image, "base64");
  const fingerprint = edgeImpulseReviewFingerprint(base64Image);
  const imageName = edgeImpulseReviewFilename(fingerprint, mimeType);
  const form = new FormData();
  form.append("data", new Blob([imageBuffer], { type: mimeType }), imageName);

  const endpoint = `https://ingestion.edgeimpulse.com/api/${EDGE_IMPULSE_REVIEW_CATEGORY}/files`;
  const response = await fetch(endpoint, {
    method: "POST",
    headers: {
      "x-api-key": EDGE_IMPULSE_API_KEY,
      "x-no-label": "1",
      "x-disallow-duplicates": "1"
    },
    body: form,
    signal: AbortSignal.timeout(60000)
  });

  const bodyText = (await response.text()).trim();
  if (!response.ok && !/duplicate/i.test(bodyText)) {
    throw new Error(`HTTP ${response.status} ${bodyText.slice(0, 300)}`);
  }

  const rawLabel = unknownError?.rawLabel ? ` Edge Impulse returned "${unknownError.rawLabel}".` : "";
  return {
    uploaded: response.ok,
    duplicate: !response.ok,
    filename: imageName,
    fingerprint,
    response: bodyText,
    reviewUrl: edgeImpulseStudioDataUrl(),
    message: `${rawLabel} Uploaded review sample ${imageName}. Label it in Edge Impulse as Garbage, Recycle, or Organic, then run this same image again.`.trim()
  };
}

function edgeImpulseReviewFingerprint(base64Image) {
  return crypto.createHash("sha256").update(Buffer.from(base64Image, "base64")).digest("hex").slice(0, 16);
}

function edgeImpulseReviewFilename(fingerprint, mimeType = "image/jpeg") {
  const extension = mimeType === "image/png" ? "png" : "jpg";
  return `waste-sorter-review-${fingerprint}.${extension}`;
}

function edgeImpulseStudioDataUrl() {
  return EDGE_IMPULSE_PROJECT_ID
    ? `https://studio.edgeimpulse.com/studio/${encodeURIComponent(EDGE_IMPULSE_PROJECT_ID)}/acquisition/${EDGE_IMPULSE_REVIEW_CATEGORY}`
    : "";
}
function collectEdgeImpulseCandidates(value) {
  const candidates = [];

  function add(label, score) {
    const cleanLabel = String(label || "").trim();
    if (!cleanLabel) return;
    candidates.push({ label: cleanLabel, score: normalizeConfidenceScore(score) });
  }

  function scanMap(map) {
    if (!map || typeof map !== "object" || Array.isArray(map)) return;
    for (const [label, score] of Object.entries(map)) {
      if (typeof score === "number") {
        add(label, score);
      } else if (score && typeof score === "object") {
        add(label, score.value ?? score.score ?? score.confidence);
      }
    }
  }

  function scanArray(items) {
    if (!Array.isArray(items)) return;
    for (const item of items) {
      add(item?.label ?? item?.category ?? item?.classification ?? item?.name, item?.score ?? item?.value ?? item?.confidence);
    }
  }

  scanMap(value?.result);
  scanMap(value?.classification);
  scanMap(value?.classifications);
  scanArray(value?.results);
  scanArray(value?.boundingBoxes);
  scanArray(value?.predictions);
  scanArray(value?.outputs);
  return candidates;
}

function combineAiDecision(gemini, edgeImpulse) {
  const edgeOk = edgeImpulse?.status === "ok";
  if (!edgeOk) {
    if (shouldUseUnknownDecision(gemini, edgeImpulse)) {
      return {
        category: "Unknown",
        confidenceLevel: "Low",
        confidenceScore: normalizeConfidenceScore(gemini?.confidenceScore),
        agreement: "unknown",
        rationale: buildUnknownDecisionRationale(gemini, edgeImpulse)
      };
    }

    return {
      category: gemini.category,
      confidenceLevel: gemini.confidenceLevel,
      confidenceScore: gemini.confidenceScore,
      agreement: "gemini-only",
      rationale: `Gemini selected ${gemini.category}; Edge Impulse ${edgeImpulse?.message || "not configured"}.`
    };
  }

  if (gemini.category === "Unknown" && edgeImpulse.category !== "Unknown") {
    return {
      category: edgeImpulse.category,
      confidenceLevel: edgeImpulse.confidenceLevel,
      confidenceScore: edgeImpulse.confidenceScore,
      agreement: "edge-when-gemini-unknown",
      rationale: `Gemini returned Unknown; Edge Impulse selected ${edgeImpulse.category}.`
    };
  }

  if (edgeImpulse.category === gemini.category) {
    return {
      category: gemini.category,
      confidenceLevel: gemini.confidenceLevel,
      confidenceScore: gemini.confidenceScore,
      agreement: "agree",
      rationale: `Gemini and Edge Impulse agree on ${gemini.category}; scores shown separately.`
    };
  }

  if (EDGE_IMPULSE_DECISION_POLICY === "require-agreement") {
    throw new Error(`AI disagreement: Gemini=${gemini.category}, Edge Impulse=${edgeImpulse.category}`);
  }

  const selected = EDGE_IMPULSE_DECISION_POLICY === "edge-primary" ? edgeImpulse : gemini;
  const selectedName = selected === edgeImpulse ? "Edge Impulse" : "Gemini";
  const selectedScore = normalizeConfidenceScore(selected.confidenceScore ?? 45);
  return {
    category: selected.category,
    confidenceLevel: "Low",
    confidenceScore: selectedScore,
    agreement: "disagree",
    rationale: `Dual model validation mismatch: Gemini ${gemini.category}, Edge Impulse ${edgeImpulse.category}; using ${selectedName} primary.`
  };
}

function shouldUseUnknownDecision(gemini, edgeImpulse) {
  if (gemini?.category === "Unknown") {
    return true;
  }

  return edgeImpulse?.reason === "edge-unknown" && shouldRunEdgeImpulseForGemini(gemini);
}

function buildUnknownDecisionRationale(gemini, edgeImpulse) {
  if (edgeImpulse?.reason === "edge-unknown") {
    const rawLabel = edgeImpulse.rawLabel ? `Edge Impulse returned ${edgeImpulse.rawLabel}` : "Edge Impulse returned Unknown";
    return `Gemini confidence was low and ${rawLabel}; marked Unknown for manual review.`;
  }

  return `Gemini returned Unknown; ${edgeImpulse?.message || "Edge Impulse did not provide a sorter category"}.`;
}
function shouldRunEdgeImpulseForGemini(gemini) {
  const score = normalizeConfidenceScore(gemini?.confidenceScore);
  return gemini?.category === "Unknown" || score === null || score <= EDGE_IMPULSE_CONFIDENCE_THRESHOLD;
}

function formatConfidenceValue(value) {
  const score = normalizeConfidenceScore(value);
  return score === null ? "not reported" : `${score}%`;
}

function edgeImpulseStatus(status, message, reason = "", extra = {}) {
  return {
    status,
    reason,
    mode: EDGE_IMPULSE_MODE,
    category: null,
    confidenceLevel: null,
    confidenceScore: null,
    rationale: "",
    message,
    ...extra
  };
}

function buildAiModelLabel(edgeImpulse = null, decision = null) {
  const parts = [`Gemini ${GEMINI_MODEL}`];
  const edgeStatus = edgeImpulse?.status;
  const edgeMode = edgeImpulse?.mode || EDGE_IMPULSE_MODE;
  const usedGeminiOnly = edgeImpulse?.reason === "confidence-threshold";
  if (EDGE_IMPULSE_MODE !== "disabled" && !usedGeminiOnly) {
    const defaultEdgeLabel = edgeImpulse
      ? `${EDGE_IMPULSE_LABEL} ${edgeMode}${edgeStatus ? ` ${edgeStatus}` : ""}`
      : `${EDGE_IMPULSE_LABEL} if Gemini <= ${EDGE_IMPULSE_CONFIDENCE_THRESHOLD}%`;
    parts.push(defaultEdgeLabel);
  }
  if (decision?.agreement && !usedGeminiOnly) {
    parts.push(`decision:${decision.agreement}`);
  }
  return parts.join(" + ");
}

function normalizeEdgeImpulseReviewCategory(value) {
  const text = String(value || "").trim().toLowerCase();
  return ["training", "testing"].includes(text) ? text : "training";
}
function normalizeEdgeImpulseMode(value) {
  const text = String(value || "").trim().toLowerCase();
  if (["auto", "cloud", "offline", "disabled"].includes(text)) return text;
  return "auto";
}

function normalizeDecisionPolicy(value) {
  const text = String(value || "").trim().toLowerCase();
  if (["gemini-primary", "edge-primary", "require-agreement"].includes(text)) return text;
  return "gemini-primary";
}

function parseBool(value, fallback = false) {
  if (value === undefined || value === null || value === "") return fallback;
  return /^(1|true|yes|on)$/i.test(String(value).trim());
}
async function classifyImage(base64Image, mimeType = "image/jpeg") {
  if (GEMINI_API_KEY === "YOUR_GEMINI_API_KEY") {
    throw new Error("Set GEMINI_API_KEY before running classification");
  }

  const endpoint =
    `https://generativelanguage.googleapis.com/v1beta/models/${GEMINI_MODEL}:generateContent` +
    `?key=${encodeURIComponent(GEMINI_API_KEY)}`;

  const payload = {
    contents: [
      {
        role: "user",
        parts: [
          { text: GEMINI_PROMPT },
          {
            inline_data: {
              mime_type: mimeType,
              data: base64Image
            }
          }
        ]
      }
    ],
    generationConfig: {
      temperature: 0,
      maxOutputTokens: 128,
      response_mime_type: "application/json",
      response_schema: {
        type: "OBJECT",
        properties: {
          category: {
            type: "STRING",
            enum: ["Garbage", "Recycle", "Organic", "Unknown"]
          },
          confidenceLevel: {
            type: "STRING",
            enum: ["High", "Medium", "Low"]
          },
          confidenceScore: {
            type: "NUMBER"
          },
          rationale: {
            type: "STRING"
          }
        },
        required: ["category", "confidenceLevel", "confidenceScore", "rationale"]
      }
    }
  };

  const response = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(60000)
  });

  const bodyText = await response.text();
  if (!response.ok) {
    throw new Error(`Gemini failed: HTTP ${response.status} ${bodyText}`);
  }

  let body;
  try {
    body = JSON.parse(bodyText);
  } catch (_error) {
    throw new Error(`Gemini returned invalid JSON: ${bodyText.slice(0, 300)}`);
  }

  const candidateText = extractGeminiText(body);
  if (!candidateText) {
    throw new Error(`Gemini returned no classification: ${bodyText}`);
  }

  try {
    return parseGeminiClassificationText(candidateText);
  } catch (error) {
    console.warn(`[Gemini] Raw classification text: ${candidateText.slice(0, 500)}`);
    throw error;
  }
}

function normalizeSorterSettings(value = {}) {
  return {
    blueNudges: clampNumber(value?.blueNudges ?? value?.blueMs, WHEEL_BLUE_NUDGES, 1, 40),
    blueHomeNudges: clampNumber(value?.blueHomeNudges ?? value?.blueHomeMs, WHEEL_BLUE_HOME_NUDGES, 1, 40),
    greenNudges: clampNumber(value?.greenNudges ?? value?.greenMs, WHEEL_GREEN_NUDGES, 1, 40),
    greenHomeNudges: clampNumber(value?.greenHomeNudges ?? value?.greenHomeMs, WHEEL_GREEN_HOME_NUDGES, 1, 40),
    nudgeMs: clampInt(value?.nudgeMs, WHEEL_NUDGE_MS, 60, 1000),
    speed: clampInt(value?.speed, WHEEL_SPEED, 80, 255),
    leftDirection: normalizeWheelDirection(value?.leftDirection, WHEEL_LEFT_DIRECTION),
    rightDirection: normalizeWheelDirection(value?.rightDirection, WHEEL_RIGHT_DIRECTION)
  };
}

function normalizeWheelDirection(value, fallback) {
  const text = String(value || "").trim().toLowerCase();
  return text === "forward" || text === "reverse" ? text : fallback;
}
function sortTargetForCategory(category, sorterSettings = normalizeSorterSettings()) {
  if (category === "Unknown") {
    return {
      label: "Unknown",
      side: null,
      nudges: 0,
      returnNudges: 0,
      lcd: "Manual Review",
      skipSort: true
    };
  }

  if (category === "Recycle") {
    return {
      label: "Blue Left",
      side: "left",
      nudges: sorterSettings.blueNudges,
      returnNudges: sorterSettings.blueHomeNudges,
      lcd: "Recycle Blue"
    };
  }

  if (category === "Organic") {
    return {
      label: "Green Right",
      side: "right",
      nudges: sorterSettings.greenNudges,
      returnNudges: sorterSettings.greenHomeNudges,
      lcd: "Organic Green"
    };
  }

  return {
    label: "Garbage Home",
    side: null,
    nudges: 0,
    returnNudges: 0,
    lcd: "Garbage Home"
  };
}

async function sortAndOpenForCategory(category, sorterSettings = normalizeSorterSettings()) {
  const target = sortTargetForCategory(category, sorterSettings);

  if (target.skipSort) {
    await setProgress("Needs Review", "Unknown", "No bin movement or lid opening for Unknown classification");
    await tryUpdateLcd("AI RESULT", "Unknown");
    return { skipped: true, target };
  }

  if (target.side) {
    await setProgress("Aligning Bin", target.label, `${target.nudges} ${target.side} nudges from Garbage Home`);
    await tryUpdateLcd("Aligning Bin", target.lcd);
    await moveWheelNudges(target.side, target.nudges, target.label, sorterSettings);
  } else {
    await setProgress("Aligning Bin", target.label, "Garbage is already at the home position");
    await tryUpdateLcd("Bin Ready", target.lcd);
  }

  await setProgress("Opening Lid", target.label, `Opening lid for ${category}`);
  await tryUpdateLcd("Opening Lid", category);
  await openLid();

  if (target.side) {
    const returnSide = oppositeSide(target.side);
    await setProgress("Returning Home", "Garbage Home", `${target.returnNudges} ${returnSide} nudges back to home`);
    await tryUpdateLcd("Return Home", "Garbage");
    await moveWheelNudges(returnSide, target.returnNudges, "Return Home", sorterSettings);
  }

  await setProgress("Ready", "Garbage Home", "Wheel is ready at Garbage/Home for the next item");
  await tryUpdateLcd("Ready", "Garbage Home");
  return { skipped: false, target };
}

async function moveWheelNudges(side, count, label, sorterSettings = normalizeSorterSettings()) {
  const segments = wheelStepSegments(count, sorterSettings.nudgeMs);
  const totalText = formatNudgeCount(count);

  for (let index = 0; index < segments.length; index += 1) {
    const segment = segments[index];
    const progressText = `${formatNudgeCount(segment.completed)}/${totalText}`;
    const sideLabel = side === "left" ? "Left" : "Right";
    const detail = segment.fraction < 1
      ? `${sideLabel} half nudge ${progressText}`
      : `${sideLabel} nudge ${progressText}`;

    await setProgress(label, progressText, detail);
    await tryUpdateLcd(safeLcdText(label), progressText);
    await rotateMotorRaw({
      direction: wheelDirectionForSide(side, sorterSettings),
      ms: segment.ms
    });
    await sleep(WHEEL_PAUSE_MS);
  }
}

function wheelStepSegments(count, nudgeMs) {
  const normalized = clampNumber(count, 0, 0, 40);
  const fullSteps = Math.floor(normalized);
  const fraction = normalized - fullSteps;
  const segments = [];
  let completed = 0;

  for (let index = 0; index < fullSteps; index += 1) {
    completed += 1;
    segments.push({ ms: nudgeMs, fraction: 1, completed });
  }

  if (fraction >= 0.05) {
    completed = normalized;
    segments.push({
      ms: Math.max(WHEEL_PARTIAL_NUDGE_MIN_MS, Math.round(nudgeMs * fraction)),
      fraction,
      completed
    });
  }

  return segments;
}

function formatNudgeCount(value) {
  const numeric = Number.parseFloat(value);
  if (!Number.isFinite(numeric)) return "0";
  return Number.isInteger(numeric) ? String(numeric) : String(Number(numeric.toFixed(2)));
}

function wheelDirectionForSide(side, sorterSettings = normalizeSorterSettings()) {
  return side === "left" ? sorterSettings.leftDirection : sorterSettings.rightDirection;
}

function oppositeSide(side) {
  return side === "left" ? "right" : "left";
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function extractGeminiText(body) {
  const parts = body?.candidates?.[0]?.content?.parts;
  if (!Array.isArray(parts)) {
    return "";
  }

  return parts
    .map(part => typeof part?.text === "string" ? part.text : "")
    .filter(Boolean)
    .join("\n")
    .trim();
}

function parseGeminiClassificationText(text) {
  const rawText = String(text || "").trim();
  const parsed = tryParseJsonObject(rawText);
  if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
    return normalizeClassificationResult(parsed, rawText);
  }

  return normalizeClassificationResult({ category: rawText, rationale: rawText }, rawText);
}

function tryParseJsonObject(text) {
  let source = String(text || "").trim();
  const fenced = source.match(/```(?:json)?\s*([\s\S]*?)```/i);
  if (fenced) {
    source = fenced[1].trim();
  }

  try {
    return JSON.parse(source);
  } catch (_error) {
    const start = source.indexOf("{");
    const end = source.lastIndexOf("}");
    if (start >= 0 && end > start) {
      try {
        return JSON.parse(source.slice(start, end + 1));
      } catch (__error) {
        return null;
      }
    }
  }

  return null;
}

function normalizeClassificationResult(value, rawText = "") {
  const rawCategory = value?.category ?? value?.classification ?? value?.label ?? value?.type ?? value?.result ?? rawText;
  const category = normalizeCategory(rawCategory, rawText);
  const rawConfidence = value?.confidence;
  const rawScore = normalizeConfidenceScore(
    value?.confidenceScore ??
    value?.confidence_score ??
    value?.score ??
    (typeof rawConfidence === "number" ? rawConfidence : undefined)
  );
  const confidenceScore = category === "Unknown" ? 0 : rawScore;
  let confidenceLevel = normalizeConfidenceLevel(
    value?.confidenceLevel ??
    value?.confidence_level ??
    value?.certainty ??
    (typeof rawConfidence === "string" ? rawConfidence : undefined),
    confidenceScore
  );
  if (category === "Unknown" || !confidenceScore && /\b(unknown|unsure|uncertain|cannot determine)\b/i.test(String(rawCategory))) {
    confidenceLevel = "Low";
  }

  const rationale = normalizeRationale(
    value?.rationale ??
    value?.reason ??
    value?.explanation ??
    value?.details ??
    rawText ??
    `Gemini selected ${category}`
  );
  return { category, confidenceLevel, confidenceScore, rationale };
}

function normalizeConfidenceScore(value) {
  if (value === null || value === undefined || value === "") {
    return null;
  }

  let score = Number(value);
  if (!Number.isFinite(score)) {
    return null;
  }

  if (score > 0 && score <= 1) {
    score *= 100;
  }

  return Math.round(Math.min(100, Math.max(0, score)) * 10) / 10;
}

function normalizeOptionalConfidenceLevel(value, score = null) {
  const normalizedScore = normalizeConfidenceScore(score);
  if ((value === null || value === undefined || value === "") && normalizedScore === null) {
    return null;
  }
  return normalizeConfidenceLevel(value, normalizedScore);
}

function normalizeConfidenceLevel(value, score = null) {
  const text = String(value || "").trim().toLowerCase();
  if (text === "high") return "High";
  if (text === "medium") return "Medium";
  if (text === "low") return "Low";

  if (typeof score === "number") {
    if (score >= 80) return "High";
    if (score >= 55) return "Medium";
    return "Low";
  }

  return "Medium";
}

function normalizeRationale(value) {
  return String(value || "").replace(/[\r\n\t]+/g, " ").trim().slice(0, 500);
}

function formatConfidenceSummary(classification) {
  const parts = [];
  if (classification.confidenceLevel) {
    parts.push(classification.confidenceLevel);
  }
  if (classification.confidenceScore !== null && classification.confidenceScore !== undefined) {
    parts.push(`${classification.confidenceScore}%`);
  }
  return parts.length ? `(${parts.join(" ")})` : "";
}

function normalizeCategory(value, rawText = "") {
  const primary = categoryFromText(value);
  if (primary) {
    return primary;
  }

  const fallback = categoryFromText(rawText);
  if (fallback) {
    return fallback;
  }

  return "Unknown";
}

function categoryFromEdgeImpulseLabel(value) {
  const text = String(value || "").trim().toLowerCase().replace(/[_-]+/g, " ");
  if (!text || /\b(unknown|unsure|uncertain|background|noise|other)\b/.test(text)) {
    return null;
  }

  if (/\b(garbage|trash|landfill|general waste|non recyclable|nonrecyclable|not recyclable)\b/.test(text)) {
    return "Garbage";
  }
  if (/\b(organic|compost|compostable|food scrap|food waste|fruit|vegetable|plant matter|yard waste|green bin)\b/.test(text)) {
    return "Organic";
  }
  if (/\b(recycle|recyclable|plastic|paper|cardboard|carton|metal|aluminum|aluminium|can|bottle|glass|tin|container|jar|cup|blue bin)\b/.test(text)) {
    return "Recycle";
  }

  return null;
}
function categoryFromText(value) {
  const text = String(value || "").trim().toLowerCase().replace(/[_-]+/g, " ");
  if (!text) {
    return null;
  }

  if (/\b(unknown|unsure|uncertain|other|not sure|cannot classify)\b/.test(text) || text.includes("cannot determine")) {
    return "Unknown";
  }
  if (/\b(garbage|trash|landfill|general waste|non recyclable|nonrecyclable|not recyclable)\b/.test(text)) {
    return "Garbage";
  }
  if (/\b(organic|compost|compostable|food scrap|food waste|fruit|vegetable|plant matter|yard waste|green bin)\b/.test(text)) {
    return "Organic";
  }
  if (/\b(recycle|recyclable|plastic|paper|cardboard|carton|metal|aluminum|aluminium|can|bottle|glass|tin|container|jar|cup|blue bin)\b/.test(text)) {
    return "Recycle";
  }

  return null;
}

async function getEsp32Telemetry() {
  if (!ESP32_S3_CONTROL_URL) {
    throw new Error("ESP32-S3 control URL is not configured");
  }

  const url = `${ESP32_S3_CONTROL_URL.replace(/\/$/, "")}/voltage`;
  const response = await fetch(url, {
    method: "GET",
    signal: AbortSignal.timeout(3000)
  });

  const bodyText = await response.text();
  if (!response.ok) {
    throw new Error(`Telemetry failed: HTTP ${response.status} ${bodyText}`);
  }

  return JSON.parse(bodyText);
}
async function updateLcd(line1, line2) {
  if (!ESP32_S3_CONTROL_URL) return;

  const url = `${ESP32_S3_CONTROL_URL.replace(/\/$/, "")}/lcd`;
  const params = new URLSearchParams({
    line1: safeLcdText(line1),
    line2: safeLcdText(line2)
  });

  const response = await fetch(`${url}?${params}`, {
    method: "POST",
    signal: AbortSignal.timeout(5000)
  });

  if (!response.ok) {
    throw new Error(`LCD update failed: HTTP ${response.status}`);
  }
}

async function tryUpdateLcd(line1, line2) {
  try {
    await updateLcd(line1, line2);
    return true;
  } catch (error) {
    console.warn(`LCD update skipped: ${error.message || error}`);
    return false;
  }
}

async function rotateMotorRaw(options = {}) {
  if (!ESP32_S3_CONTROL_URL) {
    throw new Error("ESP32-S3 control URL is not configured");
  }

  const direction = ["forward", "reverse", "stop"].includes(String(options.direction || "").toLowerCase())
    ? String(options.direction).toLowerCase()
    : "forward";
  const ms = direction === "stop" ? 50 : clampInt(options.ms, 300, WHEEL_PARTIAL_NUDGE_MIN_MS, 1000);

  const url = `${ESP32_S3_CONTROL_URL.replace(/\/$/, "")}/motor-raw`;
  const params = new URLSearchParams({
    direction,
    ms: String(ms)
  });

  const response = await fetch(`${url}?${params}`, {
    method: "POST",
    signal: AbortSignal.timeout(Math.max(5000, ms + 3000))
  });

  const bodyText = await response.text();
  if (!response.ok) {
    throw new Error(`Raw wheel command failed: HTTP ${response.status} ${bodyText}`);
  }

  try {
    return JSON.parse(bodyText);
  } catch (_error) {
    return { raw: bodyText };
  }
}

async function rotateMotor(options = {}) {
  if (!ESP32_S3_CONTROL_URL) {
    throw new Error("ESP32-S3 control URL is not configured");
  }

  const direction = ["forward", "reverse", "stop"].includes(String(options.direction || "").toLowerCase())
    ? String(options.direction).toLowerCase()
    : "forward";
  const ms = direction === "stop" ? 100 : clampInt(options.ms, 800, 100, 5000);
  const speed = direction === "stop" ? 0 : clampInt(options.speed, 255, 0, 255);

  const url = `${ESP32_S3_CONTROL_URL.replace(/\/$/, "")}/motor`;
  const params = new URLSearchParams({
    direction,
    ms: String(ms),
    speed: String(speed)
  });

  const response = await fetch(`${url}?${params}`, {
    method: "POST",
    signal: AbortSignal.timeout(Math.max(5000, ms + 4000))
  });

  const bodyText = await response.text();
  if (!response.ok) {
    throw new Error(`Wheel command failed: HTTP ${response.status} ${bodyText}`);
  }

  try {
    return JSON.parse(bodyText);
  } catch (_error) {
    return { raw: bodyText };
  }
}

function clampInt(value, fallback, min, max) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.min(max, Math.max(min, parsed));
}

function clampNumber(value, fallback, min, max) {
  const parsed = Number.parseFloat(value);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.min(max, Math.max(min, parsed));
}
async function openLid() {
  if (!ESP32_S3_CONTROL_URL) {
    throw new Error("ESP32-S3 control URL is not configured");
  }

  const url = `${ESP32_S3_CONTROL_URL.replace(/\/$/, "")}/lid`;
  const response = await fetch(url, {
    method: "POST",
    signal: AbortSignal.timeout(8000)
  });

  if (!response.ok) {
    throw new Error(`Lid command failed: HTTP ${response.status}`);
  }
}

async function setProgress(title, value, detail) {
  latestRun.steps.push({
    at: new Date().toISOString(),
    title,
    value,
    detail
  });
  latestRun.message = detail;
  broadcast("snapshot", latestRun);
}

function safeLcdText(value) {
  return String(value || "").replace(/[^\x20-\x7e]/g, "").slice(0, 16);
}

function sendEvent(res, eventName, data) {
  res.write(`event: ${eventName}\n`);
  res.write(`data: ${JSON.stringify(data)}\n\n`);
}

function broadcast(eventName, data) {
  for (const client of progressClients) {
    sendEvent(client, eventName, data);
  }
}

app.listen(PORT, () => {
  console.log(`Waste Sorter Dashboard running at http://localhost:${PORT}`);
  console.log(`Camera source: ${CAMERA_LABEL}`);
  console.log(`Legacy ESP32-CAM URL: ${CAMERA_CAPTURE_URL}`);
  console.log(`ESP32-S3 control: ${ESP32_S3_CONTROL_URL}`);
  console.log(`Gemini model: ${GEMINI_MODEL}`);
  console.log(`Edge Impulse mode: ${EDGE_IMPULSE_MODE}; threshold: ${EDGE_IMPULSE_CONFIDENCE_THRESHOLD}%`);
});

function loadEnvFile(filePath) {
  if (!fs.existsSync(filePath)) {
    return;
  }

  const lines = fs.readFileSync(filePath, "utf8").split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) {
      continue;
    }

    const equalsAt = trimmed.indexOf("=");
    if (equalsAt <= 0) {
      continue;
    }

    const key = trimmed.slice(0, equalsAt).trim();
    const rawValue = trimmed.slice(equalsAt + 1).trim();
    const value = rawValue.replace(/^["']|["']$/g, "");

    if (!process.env[key]) {
      process.env[key] = value;
    }
  }
}
