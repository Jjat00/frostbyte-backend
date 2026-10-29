#!/usr/bin/env node
// frostbyte: CLI para que agentes (y personas) lean y editen Frostbyte por su API REST. Sin dependencias, Node 20+.
// Config: ~/.config/frostbyte/config.json (url) y sessions/ (puntero por url + archivo por sesión, 0600). Env: FROSTBYTE_URL, FROSTBYTE_USERNAME, FROSTBYTE_PASSWORD.
// Salida: texto en una TTY, JSON si stdout no es TTY o con --json. Diagnósticos por stderr.
// Códigos de salida: 0 ok, 1 error de API o red, 2 error de uso, 3 sin sesión, 4 no encontrado.

import { createHash, randomUUID } from "node:crypto";
import { appendFileSync, chmodSync, existsSync, mkdirSync, readdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { basename, extname, join, resolve } from "node:path";
import { parseArgs } from "node:util";

const VERSION = "0.1.0";
const DEFAULT_URL = "https://frostbyte-backend-production.up.railway.app/api/v1";
const LOCAL_URL = "http://localhost:18000/api/v1";
const CONFIG_DIR = join(process.env.XDG_CONFIG_HOME ?? join(homedir(), ".config"), "frostbyte");
const CONFIG = join(CONFIG_DIR, "config.json");
const AUDIT_DIR = join(process.env.XDG_STATE_HOME ?? join(homedir(), ".local", "state"), "frostbyte", "audit");
const DATE_FILTERS = ["today", "yesterday", "week", "month", "last_month", "year"];
// order ls solo entiende estos (apps/orders/views.py, OrderViewSet.get_queryset).
const ORDER_DATE_FILTERS = ["today", "yesterday", "week", "month"];
const MAX_PRICE = 99999999.99; // DecimalField(max_digits=10, decimal_places=2)
const ORDER_STATUS = ["pending", "preparing", "ready", "delivered", "cancelled"];

const SCHEMA = {
  name: "frostbyte",
  version: VERSION,
  description: "Lee y edita Frostbyte (bar Frostbyte y Frostbyte Food) por su API: carta, imágenes, ventas, pedidos, finanzas.",
  references: {
    product: "slug (p. ej. granizado-de-mora) o id numérico",
    category: "slug o id, como lo lista `frostbyte category ls`",
    business: "frostbyte | frostbyte-food",
    money: "COP, sin decimales en la práctica",
  },
  rules: [
    "Todo lo que escribe va a PRODUCCIÓN salvo que --url apunte a otro backend. Corre primero con --dry-run y muestra el resultado al humano.",
    "Desactivar no borra: el producto queda is_active=false y se reactiva con `product set <slug> --active true`.",
    "Las imágenes nuevas van como archivo local (se suben a R2) o como URL pública ya alojada.",
    "Nunca mencionar gramos en textos visibles al cliente (descripciones, historias, variantes).",
    "Cada escritura queda en el log de auditoría (`frostbyte audit`).",
  ],
  exit_codes: { 0: "ok", 1: "error de API o red", 2: "error de uso", 3: "sin sesión o sin permiso", 4: "no encontrado" },
  errors: "stderr: {error:{code, message, ...}}; una escritura que falló a medias trae partial y nextSteps",
  help: "--help imprime texto; --help --json imprime este schema",
  trust: {
    read: "business ls, category ls, product ls/show/image, sales *, finance *, order ls/show, store status, whoami, audit",
    write: "product create/set, variant add/set, image upload (aceptan --dry-run)",
    destructive: "product deactivate, variant deactivate (exigen --yes; aceptan --dry-run)",
  },
  commands: {
    login: { flags: { "--username": "usuario admin (o FROSTBYTE_USERNAME)" }, note: "la contraseña sale de FROSTBYTE_PASSWORD o se pide sin eco en una TTY; nunca se guarda, solo el refresh token" },
    logout: { output: "{revoked, local_cleared}; sale 1 si no pudo confirmar la revocación en el servidor" },
    whoami: {},
    config: { flags: { "--url": `URL base de la API (default ${DEFAULT_URL}; 'local' = ${LOCAL_URL})` } },
    "business ls": {},
    "category ls": { flags: { "-b, --business": "slug", "--all": "incluye inactivas" } },
    "product ls": {
      flags: {
        "-b, --business": "slug",
        "-c, --category": "slug",
        "-q, --query": "búsqueda (ignora tildes y mayúsculas)",
        "--all": "incluye inactivos",
        "--without-image": "solo los que no tienen imagen",
        "--coming-soon": "true|false",
      },
      output: "[{id, name, slug, description, image_url, is_active, is_coming_soon, category, category_slug, business_slug, variants:[{id,name,price,is_default,is_active}], is_configurable}]",
    },
    "product show": { args: ["<product>"], output: "producto con history, variants y modifier_groups" },
    "product image": {
      args: ["<product>..."],
      flags: { "--out": "carpeta destino (default .)", "--all": "todas las imágenes de los productos que filtre -b/-c/-q" },
      output: "[{slug, image_url, file, error?}]",
      note: "SIEMPRE descarga archivos al disco. Para solo leer URLs usa product ls / product show (campo image_url).",
    },
    "product create": {
      flags: {
        "--name": "obligatorio",
        "-c, --category": "obligatorio; el negocio se hereda de la categoría",
        "--description": "obligatorio",
        "--history": "historia opcional",
        "--image": "ruta local (se sube) o URL",
        "--variant": "repetible, 'Nombre=precio' (la primera queda por defecto); sin --variant usa --price",
        "precio": "entero o con punto decimal: 12000, 12000.50. Sin separador de miles (12.000 se rechaza)",
        "--price": "atajo: una sola variante 'Único' con ese precio",
        "--active": "true|false (default true)",
        "--coming-soon": "true|false (default false)",
        "--dry-run": "muestra lo que haría, no envía",
      },
    },
    "product set": {
      args: ["<product>"],
      flags: { "--name": "", "--description": "", "--history": "", "-c, --category": "", "--image": "ruta o URL ('' la quita)", "--active": "true|false", "--coming-soon": "true|false", "--dry-run": "" },
    },
    "product deactivate": { args: ["<product>"], flags: { "--yes": "obligatorio", "--dry-run": "" } },
    "variant add": { args: ["<product>"], flags: { "--name": "obligatorio", "--price": "obligatorio", "--default": "la marca por defecto", "--dry-run": "" } },
    "variant set": { args: ["<variant-id>"], flags: { "--name": "", "--price": "", "--default": "", "--active": "true|false", "--dry-run": "" } },
    "variant deactivate": { args: ["<variant-id>"], flags: { "--yes": "obligatorio", "--dry-run": "" } },
    "image upload": { args: ["<archivo>"], flags: { "--dry-run": "" }, output: "{url}" },
    "sales summary": {
      flags: { "--date": DATE_FILTERS.join("|") + " (default today)", "--from, --to": "YYYY-MM-DD (rango propio, fechas reales y from <= to)", "-b, --business": "slug" },
      output: "{total_orders, total_revenue, unpaid_total, by_status, by_payment_method, delivery}",
    },
    "sales products": {
      flags: { "--date": "igual que summary (default month)", "--from, --to": "", "-b, --business": "", "--limit": "default 20" },
      output: "{data:[{product_name, variant_name, quantity_sold, revenue}], start_date, end_date}, ordenado por ingresos",
    },
    "sales daily": { flags: { "--date": "default month", "--from, --to": "", "-b, --business": "" } },
    "sales hours": { flags: { "--date": "default month", "--from, --to": "" } },
    "sales weekdays": { flags: { "--date": "default month", "--from, --to": "" } },
    "finance summary": {
      flags: { "--year, --month": "mes ancla (default mes actual)", "-b, --business": "" },
      output: "KPIs del mes {revenue, total_expenses, inventory_expenses, operational_expenses, investment, net_profit, cash_after_investment, orders_count, avg_ticket} con {value, previous, change, year_ago, change_yoy}; profit_margin solo {value, previous, year_ago}; period",
    },
    "finance trend": { flags: { "--months": "1..36 (default 12)", "-b, --business": "" } },
    "order ls": { flags: { "--date": ORDER_DATE_FILTERS.join("|"), "--status": ORDER_STATUS.join("|"), "--open": "solo pendientes/preparando/listos", "-b, --business": "", "--limit": "default 50, recorre páginas" }, output: "{count, results:[pedido]}" },
    "order show": { args: ["<order-id>"] },
    "store status": { output: "{is_open, customer_ordering_enabled, opening_time, ...}" },
    audit: { flags: { "--limit": "default 20" }, note: "últimas escrituras hechas desde este equipo" },
    schema: {},
  },
};

const HELP = `frostbyte ${VERSION}: la carta, las ventas y los pedidos de Frostbyte desde la terminal

Sesión:
  frostbyte login --username jaime      (contraseña por FROSTBYTE_PASSWORD o sin eco en la TTY)
  frostbyte whoami | logout
  frostbyte config --url local          (o una URL; default producción)

Carta:
  frostbyte business ls
  frostbyte category ls [-b frostbyte-food]
  frostbyte product ls [-b …] [-c granizados] [-q mora] [--all] [--without-image]
  frostbyte product show granizado-de-mora
  frostbyte product image granizado-de-mora [--out ./imgs]     (--all baja todas las filtradas)
  frostbyte product create --name "…" -c granizados --description "…" --variant "Regular=12000" [--image foto.png] --dry-run
  frostbyte product set granizado-de-mora [--description …] [--image foto.png] [--active false] --dry-run
  frostbyte product deactivate granizado-de-mora --yes
  frostbyte variant add granizado-de-mora --name Grande --price 15000
  frostbyte variant set 160 --price 2500 | variant deactivate 160 --yes
  frostbyte image upload foto.png

Ventas y finanzas (admin):
  frostbyte sales summary [--date today|yesterday|week|month|last_month|year] [--from 2026-10-01 --to 2026-10-15] [-b …]
  frostbyte sales products [--date month] [--limit 20]
  frostbyte sales daily | hours | weekdays
  frostbyte finance summary [--year 2026 --month 9] [-b …]
  frostbyte finance trend [--months 6]

Pedidos y local:
  frostbyte order ls [--date today] [--status pending] [--open]
  frostbyte order show 1234
  frostbyte store status

  frostbyte audit          escrituras hechas desde este equipo
  frostbyte schema         descripción para agentes de todos los comandos

Global: --json (forzado si stdout no es TTY), --url <url|local>, --help, --version
Las escrituras van a PRODUCCIÓN: usa --dry-run primero.`;

// ---------- salida ----------

const isTTY = process.stdout.isTTY;
const color = isTTY && !process.env.NO_COLOR && process.env.TERM !== "dumb";
const paint = (code) => (s) => (color ? `\x1b[${code}m${s}\x1b[0m` : String(s));
const dim = paint(2), bold = paint(1), green = paint(32), red = paint(31), yellow = paint(33), cyan = paint(36);
const visible = (s) => String(s).replace(/\x1b\[[0-9;]*m/g, "");
let jsonMode = !isTTY;

const die = (code, msg, extra) => {
  if (jsonMode) process.stderr.write(JSON.stringify({ error: { code, message: msg, ...extra } }) + "\n");
  else process.stderr.write(`frostbyte: ${msg}\n`);
  process.exit(code);
};
class CliError extends Error {
  constructor(code, message, extra) {
    super(message);
    this.code = code;
    this.extra = extra;
  }
}
/** Error recuperable: lo atrapa quien escribe (auditoría, estado parcial) y al final main(). */
const fail = (code, msg, extra) => {
  throw new CliError(code, msg, extra);
};
const note = (msg) => process.stderr.write(dim(msg) + "\n");
const out = (data, human) => {
  if (jsonMode || !human) process.stdout.write(JSON.stringify(data, null, jsonMode ? 0 : 2) + "\n");
  else process.stdout.write(human(data) + "\n");
};
const cop = (v) => (v === null || v === undefined || v === "" ? "" : "$" + Math.round(Number(v)).toLocaleString("es-CO"));
const pct = (v) => (v === null || v === undefined ? "" : `${v > 0 ? "+" : ""}${Number(v).toFixed(1)}%`);

function table(rows, cols) {
  if (!rows.length) return dim("(nada)");
  const widths = cols.map((c) => Math.max(visible(c.label).length, ...rows.map((r) => visible(c.get(r)).length)));
  const line = (cells) =>
    cells.map((cell, i) => (cols[i].right ? " ".repeat(widths[i] - visible(cell).length) + cell : cell + " ".repeat(widths[i] - visible(cell).length))).join("  ").trimEnd();
  return [dim(line(cols.map((c) => c.label))), ...rows.map((r) => line(cols.map((c) => String(c.get(r)))))].join("\n");
}

// ---------- config y sesión ----------
//
// config.json solo guarda la URL elegida. Las sesiones viven en sessions/ (ver session()), así
// escribir una nunca pisa otra. No hay candado: la renovación es optimista (ver refreshAccess).

const SESSIONS_DIR = join(CONFIG_DIR, "sessions");

const readJson = (path) => {
  if (!existsSync(path)) return null;
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return die(3, `archivo inválido: ${path}`);
  }
};
/** Escritura atómica (temporal único + rename) con permisos 0600. */
function writeJson(path, data) {
  mkdirSync(CONFIG_DIR, { recursive: true, mode: 0o700 });
  mkdirSync(SESSIONS_DIR, { recursive: true, mode: 0o700 });
  const tmp = `${path}.${process.pid}.${randomUUID()}.tmp`;
  writeFileSync(tmp, JSON.stringify(data, null, 2), { mode: 0o600 });
  renameSync(tmp, path);
  chmodSync(path, 0o600);
}
const readConfig = () => readJson(CONFIG) ?? {};
const normUrl = (u) => (u === "local" ? LOCAL_URL : u === "prod" ? DEFAULT_URL : u).replace(/\/$/, "");
let baseUrl;
const apiUrl = () => (baseUrl ??= normUrl(globalThis.__urlFlag ?? process.env.FROSTBYTE_URL ?? readConfig().url ?? DEFAULT_URL));
// Cada login abre una sesión con id propio (sid) en su archivo <url>.<sid>.json; el puntero
// <url>.current dice cuál vale. Solo login y logout tocan el puntero, y una renovación solo
// reescribe el archivo de su sid: si llega tarde, tras un logout o un login nuevo, escribe en
// una sesión que ya nadie apunta y no revive ni pisa nada.
const urlKey = () => createHash("sha256").update(apiUrl()).digest("hex").slice(0, 16);
const pointerFile = () => join(SESSIONS_DIR, `${urlKey()}.current`);
const sidFile = (sid) => join(SESSIONS_DIR, `${urlKey()}.${sid}.json`);
const currentSid = () => readJson(pointerFile())?.sid ?? null;
function session() {
  const sid = currentSid();
  const data = sid && readJson(sidFile(sid));
  return data ? { ...data, sid } : null;
}
/** Sesión nueva (login): archivo propio y después el puntero. */
function openSession(data) {
  const sid = randomUUID();
  writeJson(sidFile(sid), { url: apiUrl(), ...data });
  writeJson(pointerFile(), { url: apiUrl(), sid });
  removeSessionFiles((f) => !f.endsWith(`.${sid}.json`));
}
/** Renovación: solo el archivo de su sid, y se descarta si la sesión ya no es la vigente. */
function renewSession(s, tokens) {
  const { sid, ...data } = s;
  writeJson(sidFile(sid), { ...data, ...tokens });
  if (currentSid() !== sid) removeSessionFiles((f) => f.endsWith(`.${sid}.json`));
}
function closeSession() {
  if (existsSync(pointerFile())) unlinkSync(pointerFile());
  removeSessionFiles(() => true);
}
function removeSessionFiles(which) {
  if (!existsSync(SESSIONS_DIR)) return;
  for (const f of readdirSync(SESSIONS_DIR))
    if (f.startsWith(urlKey() + ".") && f.endsWith(".json") && which(f)) {
      try {
        unlinkSync(join(SESSIONS_DIR, f));
      } catch {}
    }
}
const sleep = (ms) => new Promise((done) => setTimeout(done, ms));
const REQUEST_TIMEOUT_MS = 30000;

/** Lee el cuerpo; si la conexión se corta a mitad, una escritura queda en estado desconocido. */
async function readBody(res, method, path) {
  try {
    return await res.text();
  } catch (e) {
    return fail(1, `la respuesta de ${method} ${path} se cortó: ${e.cause?.code ?? e.name ?? e.message}`, { unknown: method !== "GET", status: res.status });
  }
}

async function rawFetch(method, path, { body, form, token } = {}) {
  const url = path.startsWith("http") ? path : apiUrl() + path;
  const headers = { accept: "application/json" };
  if (token) headers.authorization = `Bearer ${token}`;
  if (body) headers["content-type"] = "application/json";
  try {
    return await fetch(url, { method, headers, body: form ?? (body ? JSON.stringify(body) : undefined), signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS) });
  } catch (e) {
    // Sin respuesta no se sabe si el servidor aplicó una escritura: unknown lo marca para quien reporta.
    return fail(1, `no hay respuesta de ${apiUrl()}: ${e.cause?.code ?? e.name ?? e.message}`, { unknown: method !== "GET" });
  }
}

let refreshing = null;
let dryRun = false;

/**
 * Renueva el access. El backend rota el refresh y pone el viejo en lista negra, y varios procesos
 * pueden renovar a la vez. Sin candado, se cumple igual que la sesión no se pierde porque:
 *  - todo refresh que se guarda acaba de emitirse y nadie lo ha usado, así que es válido aunque
 *    gane el último en escribir;
 *  - quien recibe 401 al renovar es porque otro proceso ya rotó ese refresh: espera a ver en el
 *    disco el refresh nuevo y usa su access.
 * Dentro del proceso, las llamadas concurrentes comparten una sola renovación.
 */
function refreshAccess(staleAccess) {
  if (dryRun) fail(3, "la sesión necesita renovarse y --dry-run no toca la sesión: corre `frostbyte whoami` y repite");
  return (refreshing ??= (async () => {
    const s = session();
    if (!s?.refresh) return null;
    // Otro proceso ya renovó.
    if (s.access && s.access !== staleAccess) return s.access;
    const res = await rawFetch("POST", "/auth/refresh/", { body: { refresh: s.refresh } });
    const text = await readBody(res, "POST", "/auth/refresh/");
    if (res.ok) {
      const data = JSON.parse(text);
      renewSession(s, { access: data.access, refresh: data.refresh ?? s.refresh });
      return data.access;
    }
    if (res.status !== 401) return null;
    // Más que el timeout de una petición: el proceso que rotó ya tuvo tiempo de guardar.
    const deadline = Date.now() + REQUEST_TIMEOUT_MS + 5000;
    while (Date.now() < deadline) {
      const now = session();
      if (now?.sid !== s.sid) return null; // se cerró o se abrió otra sesión mientras tanto
      if (now.refresh && now.refresh !== s.refresh) return now.access;
      await sleep(200);
    }
    return null;
  })().finally(() => {
    refreshing = null;
  }));
}

/**
 * Llama a la API. auth: "none" (carta pública: nunca manda ni renueva la sesión) | "required".
 * body puede ser una función, para recalcularlo tras renovar la sesión.
 */
async function api(method, path, { body, form, auth = "required" } = {}) {
  const payload = () => (typeof body === "function" ? body() : body);
  let token = auth === "none" ? null : session()?.access;
  if (auth === "required" && !session()) fail(3, `sin sesión en ${apiUrl()}: corre \`frostbyte login --username <usuario>\``);
  let res = await rawFetch(method, path, { body: payload(), form: form?.(), token });
  if (res.status === 401 && token) {
    token = await refreshAccess(token);
    if (!token) fail(3, "la sesión expiró: corre `frostbyte login` de nuevo");
    res = await rawFetch(method, path, { body: payload(), form: form?.(), token });
  }
  const text = await readBody(res, method, path);
  let data;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { raw: text.slice(0, 300) };
  }
  if (res.status === 404) fail(4, `no encontrado: ${method} ${path}`);
  if (res.status === 401 || res.status === 403) fail(3, `sin permiso (${res.status}): ${data?.detail ?? data?.error ?? "hace falta un usuario admin"}`);
  // Un 502/504 lo da el proxy de Railway: el backend pudo aplicar la escritura antes de que se cortara.
  if (!res.ok) fail(1, `${method} ${path} → ${res.status}`, { status: res.status, details: data, ...(method !== "GET" && [502, 504].includes(res.status) ? { unknown: true } : {}) });
  return data;
}

/** Lecturas de la carta pública: sin sesión, así un --dry-run nunca renueva ni reescribe la config. */
async function allPages(path) {
  const sep = path.includes("?") ? "&" : "?";
  let data = await api("GET", `${path}${sep}page_size=1000`, { auth: "none" });
  if (Array.isArray(data)) return data;
  const rows = [...data.results];
  while (data.next) {
    data = await api("GET", data.next, { auth: "none" });
    rows.push(...data.results);
  }
  return rows;
}

// ---------- auditoría ----------

function audit(entry) {
  mkdirSync(AUDIT_DIR, { recursive: true, mode: 0o700 });
  const file = join(AUDIT_DIR, new Date().toISOString().slice(0, 10) + ".jsonl");
  appendFileSync(file, JSON.stringify({ ts: new Date().toISOString(), url: apiUrl(), user: session()?.username, ...entry }) + "\n", { mode: 0o600 });
}

/** Escritura con auditoría en dos fases: registro pendiente antes de la llamada y resultado después. */
async function tracked(method, path, opts) {
  const id = randomUUID();
  audit({ id, phase: "pending", method, path, body: opts?.body ?? opts?.describe });
  try {
    const data = await api(method, path, opts);
    audit({ id, phase: "done", method, path, result: data ? { id: data.id, slug: data.slug, url: data.url } : null });
    return data;
  } catch (e) {
    audit({ id, phase: e.extra?.unknown ? "unknown" : "error", method, path, error: e.message, status: e.extra?.status });
    // Sin respuesta completa la escritura pudo aplicarse: se dice explícitamente para que nadie la repita a ciegas.
    if (e.extra?.unknown && !e.message.includes("no se sabe")) e.message += "; no se sabe si se aplicó: verifica antes de repetir";
    throw e;
  }
}

// ---------- utilidades de dominio ----------

const bool = (name, v) => {
  if (v === undefined) return undefined;
  if (["true", "1", "si", "sí", "yes"].includes(v.toLowerCase())) return true;
  if (["false", "0", "no"].includes(v.toLowerCase())) return false;
  return die(2, `--${name} debe ser true o false`);
};
/** Precio sin ambigüedad: 12000 o 12000.50 (punto decimal). "12.000" o "12,000" se rechazan. */
const money = (name, v) => {
  const raw = String(v).trim().replace(/^\$/, "");
  if (!/^\d+(\.\d{1,2})?$/.test(raw)) die(2, `--${name} no es un precio válido: ${v} (usa 12000 o 12000.50, sin separador de miles)`);
  const n = Number(raw);
  if (n > MAX_PRICE) die(2, `--${name} supera el máximo de la API: ${v}`);
  return n.toFixed(2);
};
const posInt = (name, v, fallback, max = Infinity) => {
  if (v === undefined) return fallback;
  if (!/^\d+$/.test(v) || Number(v) < 1 || Number(v) > max) die(2, `--${name} debe ser un entero ${max === Infinity ? "positivo" : `entre 1 y ${max}`}`);
  return Number(v);
};
const realDate = (d) => {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(d ?? "")) return false;
  const t = new Date(d + "T00:00:00Z");
  return !Number.isNaN(t.getTime()) && t.toISOString().slice(0, 10) === d;
};
const variantName = (n) => {
  const name = String(n ?? "").trim();
  if (!name) die(2, "el nombre de la variante no puede ir vacío");
  if (name.length > 100) die(2, `nombre de variante de más de 100 caracteres: ${name.slice(0, 30)}…`);
  return name;
};
const need = (v, flag) => (v === undefined || v === "" ? die(2, `falta ${flag}`) : v);

async function findProduct(ref) {
  need(ref, "<product>");
  if (/^\d+$/.test(ref)) {
    const rows = await allPages("/products/?active_only=false");
    const p = rows.find((r) => String(r.id) === ref);
    if (!p) fail(4, `no hay producto con id ${ref}`);
    return api("GET", `/products/${p.slug}/`, { auth: "none" });
  }
  return api("GET", `/products/${encodeURIComponent(ref)}/`, { auth: "none" });
}

async function findCategory(ref) {
  const cats = await allPages("/categories/?active_only=false");
  const c = cats.find((x) => x.slug === ref || String(x.id) === String(ref));
  if (!c) fail(4, `no existe la categoría ${ref}; mira \`frostbyte category ls\``);
  return c;
}

function dateQuery(v, fallback) {
  const q = new URLSearchParams();
  if (v.from || v.to) {
    if (!v.from || !v.to) die(2, "--from y --to van juntos (YYYY-MM-DD)");
    for (const d of [v.from, v.to]) if (!realDate(d)) die(2, `fecha inválida: ${d} (YYYY-MM-DD real)`);
    if (v.from > v.to) die(2, "--from va antes que --to");
    q.set("date", "custom");
    q.set("start_date", v.from);
    q.set("end_date", v.to);
  } else {
    const d = v.date ?? fallback;
    if (!DATE_FILTERS.includes(d)) die(2, `--date debe ser ${DATE_FILTERS.join("|")}`);
    q.set("date", d);
  }
  if (v.business) q.set("business", v.business);
  return q;
}

const MIME = { ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif" };

/** --image: URL tal cual, ruta local se sube a R2. En dry-run no sube nada. */
async function resolveImage(value, dryRun) {
  if (value === undefined) return undefined;
  if (value === "") return "";
  if (/^https?:\/\//.test(value)) return value;
  const path = resolve(value);
  if (!existsSync(path)) die(2, `no existe la imagen ${value}`);
  if (!MIME[extname(path).toLowerCase()]) die(2, `formato de imagen no soportado: ${extname(path)} (png, jpg, webp, gif)`);
  if (dryRun) return `<se subiría ${basename(path)}>`;
  const data = await uploadImage(path);
  return data.url;
}

async function uploadImage(path) {
  const buf = readFileSync(path);
  return tracked("POST", "/upload/image/", {
    describe: { file: basename(path), bytes: buf.length },
    form: () => {
      const fd = new FormData();
      fd.append("image", new Blob([buf], { type: MIME[extname(path).toLowerCase()] }), basename(path));
      return fd;
    },
  });
}

function parseVariants(v) {
  if (v.variant?.length) {
    const rows = v.variant.map((spec, i) => {
      const m = /^(.+?)\s*=\s*(.+)$/.exec(spec);
      if (!m) die(2, `--variant debe ser 'Nombre=precio': ${spec}`);
      return { name: variantName(m[1]), price: money("variant", m[2]), is_default: i === 0, is_active: true };
    });
    const names = rows.map((r) => r.name.toLowerCase());
    if (new Set(names).size !== names.length) die(2, "hay variantes con el mismo nombre");
    return rows;
  }
  if (v.price !== undefined) return [{ name: "Único", price: money("price", v.price), is_default: true, is_active: true }];
  return die(2, "falta --variant 'Nombre=precio' (repetible) o --price");
}

const productLine = (p) =>
  [`${bold(p.name)} ${dim(`(${p.slug}, id ${p.id})`)}${p.is_active ? "" : " " + red("inactivo")}${p.is_coming_soon ? " " + yellow("próximamente") : ""}`,
    `${dim("negocio")} ${p.business_name ?? p.business_slug}  ${dim("categoría")} ${p.category_name}`,
    `${dim("imagen")} ${p.image_url || yellow("sin imagen")}`,
    p.description && `${dim("descripción")} ${p.description}`,
    p.history && `${dim("historia")} ${p.history}`,
    p.variants?.length && `${dim("variantes")}\n` + table(p.variants, [
      { label: "id", get: (x) => x.id, right: true },
      { label: "nombre", get: (x) => x.name + (x.is_default ? dim(" ·defecto") : "") },
      { label: "precio", get: (x) => cop(x.price), right: true },
      { label: "", get: (x) => (x.is_active ? "" : red("inactiva")) },
    ]).replace(/^/gm, "  "),
    p.modifier_groups?.length && `${dim("opciones")} ${p.modifier_groups.map((g) => g.name ?? g.group_name ?? g.group?.name).join(", ")}`,
  ].filter(Boolean).join("\n");

/** Comando listo para pegar en un shell POSIX: cada argumento va entre comillas simples si hace falta. */
const shq = (a) => (/^[\w@%+=:,./-]+$/.test(a) ? a : `'${a.replace(/'/g, `'\\''`)}'`);
const cmd = (...args) => ["frostbyte", ...args.map(String)].map(shq).join(" ");

const dryRunOut = (what, payload) => out({ dry_run: true, would: what, payload }, (d) => `${yellow("dry-run")} ${d.would}\n${JSON.stringify(d.payload, null, 2)}`);

// ---------- comandos ----------

const commands = {
  async login(v) {
    const username = v.username ?? process.env.FROSTBYTE_USERNAME ?? die(2, "falta --username");
    let password = process.env.FROSTBYTE_PASSWORD;
    if (!password) password = await promptSecret(`Contraseña de ${username} en ${apiUrl()}: `);
    if (!password) die(2, "sin contraseña: usa FROSTBYTE_PASSWORD o corre login en una terminal");
    const res = await rawFetch("POST", "/auth/login/", { body: { username, password } });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) die(3, data.error ?? `login falló (${res.status})`);
    if (!data.user?.is_admin && data.user?.role !== "admin") note("aviso: este usuario no es admin; las escrituras y la analítica van a fallar con 403");
    openSession({ username, role: data.user?.role, access: data.tokens.access, refresh: data.tokens.refresh, at: new Date().toISOString() });
    out({ ok: true, url: apiUrl(), user: { username, role: data.user?.role } }, (d) => `${green("✓")} sesión guardada para ${bold(username)} en ${d.url}`);
  },

  async logout() {
    if (!session()) return out({ revoked: false, local_cleared: false, had_session: false }, () => "no había sesión");
    let revoked = false;
    let reason;
    try {
      // api() renueva si el access venció; el body se recalcula para mandar el refresh vigente.
      const data = await api("POST", "/auth/logout/", { body: () => ({ refresh: session()?.refresh }) });
      // LogoutView responde 200 aunque no logre invalidar el token: solo este mensaje confirma la revocación.
      revoked = data?.message === "Sesión cerrada exitosamente";
      if (!revoked) reason = "el servidor no confirmó la revocación";
    } catch (e) {
      reason = e.message;
    }
    closeSession();
    out({ revoked, local_cleared: true, reason }, () => (revoked ? `${green("✓")} sesión cerrada y revocada` : `${yellow("!")} sesión borrada de este equipo, pero sin revocar en el servidor: ${reason}`));
    if (!revoked) process.exitCode = 1;
  },

  async whoami() {
    const me = await api("GET", "/auth/me/");
    out({ url: apiUrl(), username: me.username, role: me.role, is_admin: me.is_admin ?? me.role === "admin" }, (d) => `${bold(d.username)} (${d.role}) en ${d.url}`);
  },

  async config(v) {
    if (v.url) writeJson(CONFIG, { ...readConfig(), url: normUrl(v.url) });
    const cfg = readConfig();
    const sessions = existsSync(SESSIONS_DIR) ? readdirSync(SESSIONS_DIR).filter((f) => f.endsWith(".current")).map((f) => readJson(join(SESSIONS_DIR, f))?.url).filter(Boolean) : [];
    out({ url: normUrl(process.env.FROSTBYTE_URL ?? cfg.url ?? DEFAULT_URL), config: CONFIG, sessions }, (d) => `url: ${d.url}\nconfig: ${d.config}\nsesiones: ${d.sessions.join(", ") || "ninguna"}`);
  },

  async "business ls"() {
    const rows = await allPages("/businesses/");
    out(rows, (r) => table(r, [{ label: "slug", get: (x) => x.slug }, { label: "nombre", get: (x) => x.name }, { label: "piso", get: (x) => x.floor, right: true }, { label: "descripción", get: (x) => x.description }]));
  },

  async "category ls"(v) {
    const q = new URLSearchParams({ active_only: v.all ? "false" : "true" });
    if (v.business) q.set("business", v.business);
    const rows = await allPages(`/categories/?${q}`);
    out(rows, (r) => table(r, [
      { label: "slug", get: (x) => x.slug }, { label: "nombre", get: (x) => x.name }, { label: "negocio", get: (x) => x.business_name },
      { label: "productos", get: (x) => x.products_count, right: true }, { label: "", get: (x) => (x.is_active ? "" : red("inactiva")) },
    ]));
  },

  async "product ls"(v) {
    const q = new URLSearchParams({ active_only: v.all ? "false" : "true" });
    if (v.business) q.set("business", v.business);
    if (v.category) q.set("category", v.category);
    if (v.query) q.set("search", v.query);
    if (v["coming-soon"] !== undefined) q.set("coming_soon", String(bool("coming-soon", v["coming-soon"])));
    let rows = await allPages(`/products/?${q}`);
    if (v["without-image"]) rows = rows.filter((p) => !p.image_url);
    out(rows, (r) => table(r, [
      { label: "slug", get: (x) => x.slug },
      { label: "nombre", get: (x) => x.name },
      { label: "categoría", get: (x) => x.category_name },
      { label: "precios", get: (x) => x.variants.filter((y) => y.is_active).sort((a, b) => a.price - b.price).map((y) => (x.variants.length > 1 ? `${y.name} ` : "") + cop(y.price)).join(" · ") },
      { label: "img", get: (x) => (x.image_url ? green("●") : yellow("○")) },
      { label: "", get: (x) => [!x.is_active && red("inactivo"), x.is_coming_soon && yellow("pronto")].filter(Boolean).join(" ") },
    ]) + `\n${dim(`${r.length} productos`)}`);
  },

  async "product show"(v, [ref]) {
    out(await findProduct(ref), productLine);
  },

  async "product image"(v, refs) {
    let products;
    if (v.all) {
      const q = new URLSearchParams({ active_only: "true" });
      if (v.business) q.set("business", v.business);
      if (v.category) q.set("category", v.category);
      if (v.query) q.set("search", v.query);
      products = (await allPages(`/products/?${q}`)).filter((p) => p.image_url);
    } else {
      if (!refs.length) die(2, "falta <product> (o --all con -b/-c/-q)");
      products = await Promise.all(refs.map(findProduct));
    }
    const dir = resolve(v.out ?? ".");
    mkdirSync(dir, { recursive: true });
    const results = [];
    for (const p of products) {
      if (!p.image_url) {
        results.push({ slug: p.slug, image_url: null, file: null, error: "sin imagen" });
        continue;
      }
      const res = await fetch(p.image_url).catch((e) => ({ ok: false, statusText: e.message }));
      if (!res.ok) {
        results.push({ slug: p.slug, image_url: p.image_url, file: null, error: `descarga falló: ${res.status ?? res.statusText}` });
        continue;
      }
      const type = res.headers.get("content-type") ?? "";
      const ext = extname(new URL(p.image_url).pathname) || ({ "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp" }[type.split(";")[0]] ?? ".img");
      const file = join(dir, p.slug + ext);
      writeFileSync(file, Buffer.from(await res.arrayBuffer()));
      results.push({ slug: p.slug, image_url: p.image_url, file });
      if (!jsonMode && products.length > 3) note(`${results.length}/${products.length} ${p.slug}`);
    }
    out(results, (r) => r.map((x) => (x.file ? `${green("✓")} ${x.slug} → ${x.file}` : `${yellow("·")} ${x.slug}: ${x.error}`)).join("\n"));
    if (results.some((x) => x.error && x.image_url)) process.exitCode = 1;
  },

  async "product create"(v) {
    const variants = parseVariants(v);
    const wantActive = bool("active", v.active) ?? true;
    const name = need(v.name, "--name").trim();
    if (name.length > 200) die(2, "--name admite hasta 200 caracteres");
    const category = await findCategory(need(v.category, "--category"));
    const body = {
      name,
      description: need(v.description, "--description"),
      history: v.history ?? "",
      category: category.id,
      is_active: wantActive,
      is_coming_soon: bool("coming-soon", v["coming-soon"]) ?? false,
    };
    const existing = await allPages(`/products/?active_only=false&search=${encodeURIComponent(body.name)}`);
    const twin = existing.find((p) => p.name.toLowerCase() === body.name.toLowerCase());
    if (twin) die(2, `ya existe "${twin.name}" (${twin.slug}${twin.is_active ? "" : ", inactivo"}); edítalo con product set`);
    if (v["dry-run"]) {
      body.image_url = await resolveImage(v.image, true);
      return dryRunOut(`crear "${body.name}" en ${category.name} (${category.business_name}) con ${variants.length} variante(s)`, { product: body, variants });
    }
    body.image_url = (await resolveImage(v.image, false)) ?? "";
    // La API no crea producto y variantes de forma atómica: nace inactivo y se activa al final,
    // así un fallo a medias nunca deja en la carta un producto sin precio.
    let product;
    try {
      product = await tracked("POST", "/products/", { body: { ...body, is_active: false } });
    } catch (e) {
      if (e.extra?.unknown) fail(1, `sin respuesta al crear "${name}": no se sabe si quedó creado (inactivo)`, { ...e.extra, nextSteps: [cmd("product", "ls", "--all", "-q", name)] });
      throw e;
    }
    const created = [];
    let step = "variants";
    try {
      for (const variant of variants) created.push((await tracked("POST", "/variants/", { body: { ...variant, product: product.id } })).id);
      step = "activate";
      if (wantActive) await tracked("PATCH", `/products/${product.slug}/`, { body: { is_active: true } });
    } catch (e) {
      const missing = variants.slice(created.length);
      if (e.extra?.unknown) {
        // Sin respuesta, la escritura pudo aplicarse: no se sugiere repetirla a ciegas.
        fail(1, `sin respuesta a mitad de la creación de ${product.slug}: ${step === "variants" ? `no se sabe si la variante "${missing[0].name}" quedó creada` : "no se sabe si quedó activo"}; revisa con product show`, {
          ...e.extra,
          partial: { product: { id: product.id, slug: product.slug, is_active: step === "variants" ? false : "desconocido" }, variants_created: created, variants_unconfirmed: step === "variants" ? [missing[0]] : [], variants_missing: step === "variants" ? missing.slice(1) : [] },
          nextSteps: [cmd("product", "show", product.slug)],
        });
      }
      fail(e.code ?? 1, `creación a medias: el producto ${product.slug} quedó INACTIVO con ${created.length} de ${variants.length} variantes (${e.message})`, {
        ...e.extra,
        partial: { product: { id: product.id, slug: product.slug, is_active: false }, variants_created: created, variants_missing: missing },
        nextSteps: [
          ...missing.map((m) => cmd("variant", "add", product.slug, "--name", m.name, "--price", m.price, ...(m.is_default ? ["--default"] : []))),
          ...(wantActive ? [cmd("product", "set", product.slug, "--active", "true")] : []),
        ],
      });
    }
    const nextSteps = [cmd("product", "show", product.slug), cmd("product", "set", product.slug, "--image", "<archivo>")];
    let full;
    try {
      full = await api("GET", `/products/${product.slug}/`, { auth: "none" });
    } catch (e) {
      // Lo escrito ya está confirmado: un fallo al releer no lo convierte en error.
      full = { ...product, is_active: wantActive, variant_ids: created, warning: `creado, pero no se pudo releer: ${e.message}` };
    }
    out({ ...full, nextSteps }, (p) => `${green("✓ creado")}${p.warning ? " " + yellow(p.warning) : ""}\n${p.variants ? productLine(p) : `${p.name} (${p.slug})`}`);
  },

  async "product set"(v, [ref]) {
    const p = await findProduct(ref);
    const body = {};
    for (const k of ["name", "description", "history"]) if (v[k] !== undefined) body[k] = v[k];
    if (body.name !== undefined && (!body.name.trim() || body.name.length > 200)) die(2, "--name no puede ir vacío ni pasar de 200 caracteres");
    if (body.description !== undefined && !body.description.trim()) die(2, "--description no puede ir vacía");
    if (v.category !== undefined) body.category = (await findCategory(v.category)).id;
    if (v.active !== undefined) body.is_active = bool("active", v.active);
    if (v["coming-soon"] !== undefined) body.is_coming_soon = bool("coming-soon", v["coming-soon"]);
    if (v.image !== undefined) body.image_url = await resolveImage(v.image, v["dry-run"]);
    if (!Object.keys(body).length) die(2, "nada que cambiar: pasa --name, --description, --history, --category, --image, --active o --coming-soon");
    const before = Object.fromEntries(Object.keys(body).map((k) => [k, k === "category" ? p.category : p[k]]));
    if (v["dry-run"]) return dryRunOut(`editar ${p.slug}`, { before, after: body });
    const patched = await tracked("PATCH", `/products/${p.slug}/`, { body });
    const slug = patched?.slug ?? p.slug;
    let full;
    try {
      full = await api("GET", `/products/${slug}/`, { auth: "none" });
    } catch (e) {
      full = { ...p, ...patched, warning: `actualizado, pero no se pudo releer: ${e.message}` };
    }
    out({ ...full, changed: Object.keys(body) }, (x) => `${green("✓ actualizado")} ${dim(x.changed.join(", "))}${x.warning ? " " + yellow(x.warning) : ""}\n${productLine(x)}`);
  },

  async "product deactivate"(v, [ref]) {
    const p = await findProduct(ref);
    if (!p.is_active) return out({ slug: p.slug, is_active: false, unchanged: true }, () => `${p.slug} ya estaba inactivo`);
    if (v["dry-run"]) return dryRunOut(`desactivar ${p.slug} (sale de la carta; se reactiva con product set --active true)`, { slug: p.slug });
    if (!v.yes) die(2, `desactivar ${p.slug} lo saca de la carta: confirma con --yes`);
    await tracked("DELETE", `/products/${p.slug}/`);
    out({ slug: p.slug, is_active: false }, () => `${green("✓")} ${p.slug} desactivado`);
  },

  async "variant add"(v, [ref]) {
    const p = await findProduct(ref);
    const body = { product: p.id, name: variantName(need(v.name, "--name")), price: money("price", need(v.price, "--price")), is_default: !!v.default, is_active: true };
    if (body.is_default && p.variants.some((x) => x.is_default)) die(2, "ya hay una variante por defecto: créala sin --default y luego `variant set <id> --default`");
    if (v["dry-run"]) return dryRunOut(`agregar variante a ${p.slug}`, body);
    const created = await tracked("POST", "/variants/", { body });
    out(created, (x) => `${green("✓")} variante ${x.id} ${x.name} ${cop(x.price)} en ${p.slug}`);
  },

  async "variant set"(v, [id]) {
    if (!/^\d+$/.test(id ?? "")) die(2, "falta <variant-id> (lo muestra product show)");
    // La API no dice de qué producto es una variante: se busca en la carta completa.
    const owner = (await allPages("/products/?active_only=false")).find((p) => p.variants.some((x) => String(x.id) === id));
    if (!owner) die(4, `no hay variante con id ${id}`);
    const current = owner.variants.find((x) => String(x.id) === id);
    // Todo se valida antes de la primera escritura.
    const fields = {};
    if (v.name !== undefined) fields.name = variantName(v.name);
    if (v.price !== undefined) fields.price = money("price", v.price);
    if (v.active !== undefined) fields.is_active = bool("active", v.active);
    const makeDefault = !!v.default && !current.is_default;
    if (!Object.keys(fields).length && !makeDefault) die(2, v.default ? "esa variante ya es la de por defecto" : "nada que cambiar: --name, --price, --default o --active");
    const oldDefault = makeDefault && owner.variants.find((x) => x.is_default && String(x.id) !== id);
    const after = { ...fields, ...(makeDefault ? { is_default: true } : {}) };
    if (v["dry-run"]) return dryRunOut(`editar la variante ${id} de ${owner.slug}${oldDefault ? ` (y quitar el defecto a ${oldDefault.name})` : ""}`, { before: current, after });
    let updated = current;
    const applied = [];
    let oldUnset = false;
    let step = "fields";
    try {
      if (Object.keys(fields).length) {
        updated = await tracked("PATCH", `/variants/${id}/`, { body: { ...fields, product: owner.id } });
        applied.push(...Object.keys(fields));
      }
      if (makeDefault) {
        // Solo puede haber una por defecto: se le quita a la anterior y luego se marca la nueva.
        step = "unset";
        if (oldDefault) {
          await tracked("PATCH", `/variants/${oldDefault.id}/`, { body: { product: owner.id, is_default: false } });
          oldUnset = true;
        }
        step = "set";
        updated = await tracked("PATCH", `/variants/${id}/`, { body: { product: owner.id, is_default: true } });
      }
    } catch (e) {
      let unknown = !!e.extra?.unknown;
      const notes = [applied.length ? `ya quedaron: ${applied.join(", ")}` : "ningún campo quedó aplicado"];
      const nextSteps = [];
      let restored = null;
      if (unknown) {
        // Sin respuesta la escritura pudo aplicarse: no se toca nada más, se manda a revisar.
        notes.push(step === "fields" ? "no se sabe si los campos se aplicaron" : step === "unset" ? `no se sabe si ${oldDefault.name} sigue por defecto` : `no se sabe si ${id} quedó por defecto`);
        nextSteps.push(cmd("product", "show", owner.slug));
      } else if (oldUnset) {
        try {
          await tracked("PATCH", `/variants/${oldDefault.id}/`, { body: { product: owner.id, is_default: true } });
          restored = true;
          notes.push(`${oldDefault.name} sigue por defecto`);
        } catch (re) {
          if (re.extra?.unknown) {
            // La restauración pudo aplicarse: no se afirma nada ni se proponen más escrituras.
            restored = "desconocido";
            unknown = true;
            notes.push(`no se sabe si ${oldDefault.name} volvió a quedar por defecto`);
            nextSteps.push(cmd("product", "show", owner.slug));
          } else {
            restored = false;
            notes.push(`${owner.slug} quedó SIN variante por defecto`);
            nextSteps.push(cmd("variant", "set", oldDefault.id, "--default"));
          }
        }
      }
      if (makeDefault && !unknown) nextSteps.push(cmd("variant", "set", id, "--default"));
      fail(e.code ?? 1, `variante ${id} a medias (${e.message}): ${notes.join("; ")}`, {
        ...e.extra,
        unknown,
        partial: { failed_step: step, fields_applied: applied, previous_default_unset: oldUnset, previous_default_restored: restored, state_unknown: unknown },
        nextSteps,
      });
    }
    out(updated, (x) => `${green("✓")} variante ${x.id} ${x.name} ${cop(x.price)}${x.is_active ? "" : red(" inactiva")} en ${owner.slug}`);
  },

  async "variant deactivate"(v, [id]) {
    if (!/^\d+$/.test(id ?? "")) die(2, "falta <variant-id>");
    if (v["dry-run"]) return dryRunOut(`desactivar la variante ${id}`, { id });
    if (!v.yes) die(2, "confirma con --yes");
    await tracked("DELETE", `/variants/${id}/`);
    out({ id: Number(id), is_active: false }, () => `${green("✓")} variante ${id} desactivada`);
  },

  async "image upload"(v, [file]) {
    need(file, "<archivo>");
    if (v["dry-run"]) return dryRunOut(`subir ${file} a R2`, { file: await resolveImage(file, true) });
    if (!existsSync(file)) die(2, `no existe ${file}`);
    out(await uploadImage(resolve(file)), (d) => d.url);
  },

  async "sales summary"(v) {
    const d = await api("GET", `/orders/stats/?${dateQuery(v, "today")}`);
    out(d, (s) => [
      `${bold("Ventas")} ${dim(v.from ? `${v.from} a ${v.to}` : s.period)}${v.business ? dim(` · ${v.business}`) : ""}`,
      `${dim("ingresos")}   ${bold(cop(s.total_revenue))}`,
      `${dim("pedidos")}    ${s.total_orders}   ${dim("por cobrar")} ${cop(s.unpaid_total)}`,
      `${dim("domicilios")} ${s.delivery?.orders_count ?? 0} ${dim("(envíos")} ${cop(s.delivery?.fees_total)}${dim(")")}`,
      `${dim("estados")}    ${Object.entries(s.by_status ?? {}).filter(([, n]) => n).map(([k, n]) => `${k} ${n}`).join(" · ")}`,
      `${dim("por método")} ${Object.values(s.by_payment_method ?? {}).filter((m) => m.count).map((m) => `${m.name} ${cop(m.total)}`).join(" · ")}`,
    ].join("\n"));
  },

  async "sales products"(v) {
    const limit = posInt("limit", v.limit, 20);
    const d = await api("GET", `/orders/product_stats/?${dateQuery(v, "month")}`);
    d.data = [...d.data].sort((a, b) => b.revenue - a.revenue).slice(0, limit);
    out(d, (r) => table(r.data, [
      { label: "#", get: (x) => r.data.indexOf(x) + 1, right: true },
      { label: "producto", get: (x) => x.display_name },
      { label: "unidades", get: (x) => x.quantity_sold, right: true },
      { label: "ingresos", get: (x) => cop(x.revenue), right: true },
    ]) + `\n${dim(`${r.start_date.slice(0, 10)} a ${r.end_date.slice(0, 10)}, top ${limit} por ingresos`)}`);
  },

  async "sales daily"(v) {
    const d = await api("GET", `/orders/revenue_by_day/?${dateQuery(v, "month")}`);
    const rows = Array.isArray(d) ? d : d.data ?? d;
    out(d, () => (Array.isArray(rows) ? table(rows, Object.keys(rows[0] ?? {}).map((k) => ({ label: k, get: (x) => (typeof x[k] === "number" && /rev|total|amount/.test(k) ? cop(x[k]) : x[k]), right: typeof rows[0][k] === "number" }))) : JSON.stringify(d, null, 2)));
  },

  async "sales hours"(v) {
    const d = await api("GET", `/orders/sales_by_hour/?${dateQuery(v, "month")}`);
    out(d, null);
  },

  async "sales weekdays"(v) {
    const d = await api("GET", `/orders/sales_by_weekday/?${dateQuery(v, "month")}`);
    out(d, null);
  },

  async "finance summary"(v) {
    const q = new URLSearchParams();
    if (v.year || v.month) {
      if (!/^\d{4}$/.test(need(v.year, "--year")) || +v.year < 2000 || +v.year > 2100) die(2, "--year debe ser un año como 2026");
      posInt("month", need(v.month, "--month"), null, 12);
      q.set("year", v.year);
      q.set("month", String(Number(v.month)));
    }
    if (v.business) q.set("business", v.business);
    const d = await api("GET", `/analytics/financial/summary/?${q}`);
    const row = (label, k, fmt = cop) => {
      const b = d[k];
      const ch = b.change === undefined ? "" : (b.change >= 0 ? green : red)(pct(b.change));
      return `${dim(label.padEnd(16))}${fmt(b.value).padStart(14)}  ${dim("vs mes ant.")} ${ch}  ${dim("interanual")} ${pct(b.change_yoy)}`;
    };
    out(d, () => [
      `${bold("Finanzas")} ${dim(d.period.current_month)}${v.business ? dim(` · ${v.business}`) : ""}`,
      row("ingresos", "revenue"), row("gastos", "total_expenses"), row("ganancia neta", "net_profit"),
      row("inversión", "investment"), row("caja tras inv.", "cash_after_investment"),
      row("pedidos", "orders_count", (x) => String(x)), row("ticket promedio", "avg_ticket"),
      `${dim("margen".padEnd(16))}${(d.profit_margin.value + "%").padStart(14)}  ${dim("mes ant.")} ${d.profit_margin.previous}%`,
    ].join("\n"));
  },

  async "finance trend"(v) {
    const q = new URLSearchParams({ months: String(posInt("months", v.months, 12, 36)) });
    if (v.business) q.set("business", v.business);
    const d = await api("GET", `/analytics/financial/monthly_trend/?${q}`);
    const rows = Array.isArray(d) ? d : d.data ?? [];
    out(d, () => table(rows, Object.keys(rows[0] ?? {}).map((k) => ({ label: k, get: (x) => (typeof x[k] === "number" && k !== "orders" && !/count|margin/.test(k) ? cop(x[k]) : x[k]), right: typeof rows[0][k] === "number" }))));
  },

  async "order ls"(v) {
    const limit = posInt("limit", v.limit, 50);
    const q = new URLSearchParams({ ordering: "-created_at", page_size: String(Math.min(limit, 1000)) });
    if (v.date) {
      if (!ORDER_DATE_FILTERS.includes(v.date)) die(2, `--date en order ls debe ser ${ORDER_DATE_FILTERS.join("|")}`);
      q.set("date", v.date);
    }
    if (v.status) {
      if (!ORDER_STATUS.includes(v.status)) die(2, `--status debe ser ${ORDER_STATUS.join("|")}`);
      q.set("status", v.status);
    }
    if (v.open) q.set("active", "true");
    if (v.business) q.set("business", v.business);
    let page = await api("GET", `/orders/?${q}`);
    const rows = [...page.results];
    // El servidor topa cada página en 1000: se recorren páginas hasta el límite pedido.
    while (rows.length < limit && page.next) {
      page = await api("GET", page.next);
      rows.push(...page.results);
    }
    const result = { count: page.count, results: rows.slice(0, limit) };
    out(result, ({ results: r, count }) => table(r, [
      { label: "id", get: (x) => x.id, right: true },
      { label: "número", get: (x) => x.order_number },
      { label: "hora", get: (x) => new Date(x.created_at).toLocaleString("es-CO", { timeZone: "America/Bogota", dateStyle: "short", timeStyle: "short" }) },
      { label: "cliente", get: (x) => x.customer_name },
      { label: "tipo", get: (x) => x.order_type_display ?? x.order_type ?? "" },
      { label: "estado", get: (x) => x.status_display ?? x.status },
      { label: "total", get: (x) => cop(x.total), right: true },
      { label: "pago", get: (x) => (x.is_paid ? green("pagado") : yellow("por cobrar")) },
    ]) + `\n${dim(`${r.length} pedidos${count > r.length ? ` de ${count} (usa --limit)` : ""}`)}`);
  },

  async "order show"(v, [id]) {
    if (!/^\d+$/.test(id ?? "")) die(2, "falta <order-id> (el id numérico de order ls)");
    out(await api("GET", `/orders/${id}/`), null);
  },

  async "store status"() {
    const d = await api("GET", "/orders/store-settings/");
    out(d, (s) => `${s.is_open ? green("● abierto") : red("● cerrado")}  ${dim("pedidos en línea")} ${s.customer_ordering_enabled ? "sí" : "no"}  ${dim("apertura")} ${s.opening_time ?? "-"}`);
  },

  async audit(v) {
    if (!existsSync(AUDIT_DIR)) return out([], () => dim("(sin escrituras)"));
    const lines = readdirSync(AUDIT_DIR).sort().reverse().flatMap((f) => readFileSync(join(AUDIT_DIR, f), "utf8").trim().split("\n").reverse());
    const rows = lines.filter(Boolean).slice(0, posInt("limit", v.limit, 20)).map((l) => JSON.parse(l));
    out(rows, (r) => table(r, [
      { label: "cuándo", get: (x) => x.ts.replace("T", " ").slice(0, 19) },
      { label: "fase", get: (x) => (x.phase === "done" ? green(x.phase) : x.phase === "pending" ? dim(x.phase) : x.phase === "error" ? red(x.phase) : yellow(x.phase)) },
      { label: "llamada", get: (x) => `${x.method ?? ""} ${x.path ?? ""}` },
      { label: "usuario", get: (x) => x.user ?? "" },
    ]));
  },

  async schema() {
    jsonMode = true;
    out(SCHEMA);
  },
};

async function promptSecret(question) {
  if (!process.stdin.isTTY || !process.stderr.isTTY) return null;
  process.stderr.write(question);
  process.stdin.setRawMode(true);
  process.stdin.resume();
  return new Promise((done) => {
    let value = "";
    const onData = (chunk) => {
      for (const ch of chunk.toString("utf8")) {
        if (ch === "\r" || ch === "\n") {
          process.stdin.setRawMode(false);
          process.stdin.pause();
          process.stdin.off("data", onData);
          process.stderr.write("\n");
          return done(value);
        }
        if (ch === "\u0003") process.exit(130);
        if (ch === "\u007f") value = value.slice(0, -1);
        else value += ch;
      }
    };
    process.stdin.on("data", onData);
  });
}

// ---------- entrada ----------

const OPTIONS = {
  json: { type: "boolean" }, help: { type: "boolean", short: "h" }, version: { type: "boolean" }, url: { type: "string" },
  username: { type: "string" }, business: { type: "string", short: "b" }, category: { type: "string", short: "c" },
  query: { type: "string", short: "q" }, all: { type: "boolean" }, "without-image": { type: "boolean" }, "coming-soon": { type: "string" },
  out: { type: "string" }, name: { type: "string" }, description: { type: "string" }, history: { type: "string" }, image: { type: "string" },
  variant: { type: "string", multiple: true }, price: { type: "string" }, active: { type: "string" }, default: { type: "boolean" },
  "dry-run": { type: "boolean" }, yes: { type: "boolean" }, date: { type: "string" }, from: { type: "string" }, to: { type: "string" },
  limit: { type: "string" }, status: { type: "string" }, open: { type: "boolean" }, year: { type: "string" }, month: { type: "string" }, months: { type: "string" },
};
// Plurales y sinónimos que un agente prueba primero.
const NOUNS = { products: "product", categories: "category", businesses: "business", orders: "order", variants: "variant" };
const VERBS = { list: "ls", get: "show", update: "set", edit: "set", add: "create", new: "create" };

async function main() {
  // --json se mira antes de parsear para que también los errores de parseo salgan en JSON.
  if (process.argv.includes("--json")) jsonMode = true;
  let parsed;
  try {
    parsed = parseArgs({ args: process.argv.slice(2), options: OPTIONS, allowPositionals: true, strict: true });
  } catch (e) {
    die(2, e.message.split("\n")[0]);
  }
  const { values: v, positionals } = parsed;
  if (v.json) jsonMode = true;
  if (v.url) globalThis.__urlFlag = v.url;
  dryRun = !!v["dry-run"];
  if (v.version) return out({ version: VERSION }, (d) => d.version);
  const words = positionals.slice(0, 2);
  words[0] = NOUNS[words[0]] ?? words[0];
  if (v.help) return v.json ? out(SCHEMA) : process.stdout.write(HELP + "\n");
  if (!words.length) {
    if (jsonMode) die(2, "falta el comando: corre `frostbyte schema`");
    process.stderr.write(HELP + "\n");
    process.exit(2);
  }
  // Primero el verbo tal cual ("variant add", "sales products"); después su sinónimo.
  const exact = `${words[0]} ${words[1]}`;
  const synonym = `${words[0]} ${VERBS[words[1]] ?? words[1]}`;
  const key = commands[exact] ? exact : commands[synonym] ? synonym : commands[words[0]] ? words[0] : null;
  if (!key) die(2, `comando desconocido: ${positionals.slice(0, 2).join(" ")}. Mira \`frostbyte --help\` o \`frostbyte schema\``);
  const args = positionals.slice(key.split(" ").length);
  await commands[key](v, args);
}

main().catch((e) => (e instanceof CliError ? die(e.code, e.message, e.extra) : die(1, e.stack ?? String(e))));
