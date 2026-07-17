import express from "express";
import { spawn } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

loadEnvFile(path.join(__dirname, ".env"));

const app = express();
const PORT = Number(process.env.PORT || 3400);
const GEMINI_API_KEY = process.env.GEMINI_API_KEY || "YOUR_GEMINI_API_KEY";
const GEMINI_MODEL = process.env.GEMINI_MODEL || "gemini-3.1-flash-lite";
const CAMERA_CAPTURE_URL = process.env.CAMERA_CAPTURE_URL || "http://192.168.137.156/capture";
const ESP32_S3_CONTROL_URL = process.env.ESP32_S3_CONTROL_URL || "http://192.168.137.108";

const GEMINI_PROMPT =
  "Classify the waste item in this image for a smart trash sorter. " +
  "Choose exactly one category: Garbage, Recycle, Organic. " +
  "Return only JSON with a category field.";

const progressClients = new Set();
let latestRun = {
  id: null,
  status: "idle",
  category: null,
  imageDataUrl: null,
  steps: [],
  message: "Ready"
};

app.use(express.json({ limit: "1mb" }));
app.use(express.static(path.join(__dirname, "public")));

app.get("/api/config", (_req, res) => {
  res.json({
    cameraCaptureUrl: CAMERA_CAPTURE_URL,
    esp32S3ControlUrl: ESP32_S3_CONTROL_URL,
    geminiModel: GEMINI_MODEL,
    hasGeminiKey: GEMINI_API_KEY !== "YOUR_GEMINI_API_KEY"
  });
});

app.get("/api/latest", (_req, res) => {
  res.json(latestRun);
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

app.post("/api/run-classification", async (_req, res) => {
  const runId = `run-${Date.now()}`;
  latestRun = {
    id: runId,
    status: "running",
    category: null,
    imageDataUrl: null,
    steps: [],
    message: "Starting"
  };

  broadcast("snapshot", latestRun);

  try {
    await setProgress("Preparing", "Motor disabled", "Starting test mode");
    await tryUpdateLcd("Preparing", "Motor disabled");

    const image = await captureImage();
    latestRun.imageDataUrl = `data:${image.mime};base64,${image.base64}`;
    broadcast("snapshot", latestRun);

    await setProgress("Classifying", "Gemini AI", "Sending image to Gemini");
    await tryUpdateLcd("Classifying", "Gemini AI");

    const category = await classifyImage(image.base64);
    latestRun.category = category;
    latestRun.status = "complete";
    latestRun.message = `Classified as ${category}`;

    await setProgress("Complete", category, `Gemini classified the item as ${category}`);
    await tryUpdateLcd("AI RESULT", category);
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

app.post("/api/future/motor", (_req, res) => {
  res.status(423).json({
    ok: false,
    message: "Motor control is intentionally locked in the web dashboard until mechanical calibration is complete."
  });
});

app.post("/api/lid", async (_req, res) => {
  try {
    await openLid();
    res.json({ ok: true, message: "Lid opened and closed" });
  } catch (error) {
    res.status(500).json({ ok: false, error: error.message || String(error) });
  }
});

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

async function classifyImage(base64Image) {
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
              mime_type: "image/jpeg",
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
            enum: ["Garbage", "Recycle", "Organic"]
          }
        },
        required: ["category"]
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

  const body = JSON.parse(bodyText);
  const candidateText = body?.candidates?.[0]?.content?.parts?.[0]?.text;
  if (!candidateText) {
    throw new Error(`Gemini returned no classification: ${bodyText}`);
  }

  const parsed = JSON.parse(candidateText);
  return normalizeCategory(parsed.category);
}

function normalizeCategory(value) {
  const text = String(value || "").trim().toLowerCase();
  if (text.includes("recycle")) return "Recycle";
  if (text.includes("organic") || text.includes("compost")) return "Organic";
  if (text.includes("garbage") || text.includes("trash") || text.includes("landfill")) return "Garbage";
  throw new Error(`Unknown category from Gemini: ${value}`);
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
  console.log(`Camera: ${CAMERA_CAPTURE_URL}`);
  console.log(`ESP32-S3 control: ${ESP32_S3_CONTROL_URL}`);
  console.log(`Gemini model: ${GEMINI_MODEL}`);
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
