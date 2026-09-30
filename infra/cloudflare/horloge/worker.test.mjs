// Tests du Worker turf-horloge : node --test infra/cloudflare/horloge/worker.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import worker, { taches, declencher } from "./worker.js";

const HISTORIQUE = taches("17 1 * * *", "2026-10-01")[0];
const REPETITION = taches("5 5 * * *", "2026-10-01")[0];

const reponse = (status, body = "") => ({ status, text: async () => body });
const sansAttente = async () => {};

test("plan : chaque jour lance les bons workflows", () => {
  const noms = (cron, jour) => taches(cron, jour).map((t) => t.workflow);
  assert.deepEqual(noms("17 1 * * *", "2026-09-30"), ["history_export.yml"]);
  assert.deepEqual(noms("5 5 * * *", "2026-09-30"), []);
  assert.deepEqual(noms("5 5 * * *", "2026-10-01"), ["repetition_ombre.yml"]);
  assert.deepEqual(noms("50 5 * * *", "2026-10-06"), ["repetition_ombre.yml"]);
  assert.deepEqual(noms("5 5 * * *", "2026-10-07"), []);                     // jour du gel : rien
  assert.deepEqual(noms("5 5 * * *", "2026-10-08"), ["fondamental_nuit.yml"]);
  assert.deepEqual(noms("50 5 * * *", "2026-11-24"), ["fondamental_nuit.yml"]);
  assert.deepEqual(noms("5 5 * * *", "2026-11-25"), []);
  assert.deepEqual(taches("50 5 * * *", "2026-10-02")[0].inputs, { prevu: "05:50" });
});

test("lancement : 204 du premier coup, jeton jamais journalisé", async () => {
  const appels = [];
  const logs = [];
  const orig = console.log;
  console.log = (m) => logs.push(m);
  try {
    const ok = await declencher(REPETITION, { GITHUB_TOKEN: "jeton-secret" }, {
      fetchFn: async (url, init) => { appels.push({ url, init }); return reponse(204); }, attente: sansAttente });
    assert.equal(ok, true);
  } finally {
    console.log = orig;
  }
  assert.equal(appels.length, 1);
  assert.equal(appels[0].url, "https://api.github.com/repos/Moriah12783/turf-engine/actions/workflows/repetition_ombre.yml/dispatches");
  assert.deepEqual(JSON.parse(appels[0].init.body), { ref: "main", inputs: { prevu: "05:05" } });
  assert.equal(appels[0].init.headers.Authorization, "Bearer jeton-secret");
  assert.ok(logs.every((l) => !l.includes("jeton-secret")));
});

test("erreur passagère : réessaie, puis réussit", async () => {
  const statuts = [502, 429, 204];
  let n = 0;
  const ok = await declencher(HISTORIQUE, { GITHUB_TOKEN: "t" }, {
    fetchFn: async () => reponse(statuts[n++], "oups"), attente: sansAttente });
  assert.equal(ok, true);
  assert.equal(n, 3);
});

test("erreur de configuration (401/403/404) : abandon immédiat", async () => {
  for (const s of [401, 403, 404]) {
    let n = 0;
    const ok = await declencher(HISTORIQUE, { GITHUB_TOKEN: "t" }, {
      fetchFn: async () => { n++; return reponse(s, "Bad credentials"); }, attente: sansAttente });
    assert.equal(ok, false);
    assert.equal(n, 1);
  }
});

test("422 (entrée inconnue) : relance sans entrées", async () => {
  const corps = [];
  const ok = await declencher(REPETITION, { GITHUB_TOKEN: "t" }, {
    fetchFn: async (_u, init) => { corps.push(JSON.parse(init.body)); return reponse(corps.length === 1 ? 422 : 204); },
    attente: sansAttente });
  assert.equal(ok, true);
  assert.deepEqual(corps.map((c) => c.inputs), [{ prevu: "05:05" }, {}]);
});

test("sans jeton : rien n'est appelé", async () => {
  let n = 0;
  const ok = await declencher(HISTORIQUE, {}, { fetchFn: async () => { n++; return reponse(204); } });
  assert.equal(ok, false);
  assert.equal(n, 0);
});

test("page publique : 404", async () => {
  assert.equal((await worker.fetch()).status, 404);
});

test("événement programmé : lance le plan du jour via waitUntil", async () => {
  const orig = globalThis.fetch;
  const urls = [];
  globalThis.fetch = async (url) => { urls.push(url); return reponse(204); };
  try {
    const attentes = [];
    const ctx = { waitUntil: (p) => attentes.push(p) };
    await worker.scheduled({ cron: "50 5 * * *", scheduledTime: Date.parse("2026-10-09T05:50:00Z") }, { GITHUB_TOKEN: "t" }, ctx);
    await Promise.all(attentes);
    await worker.scheduled({ cron: "5 5 * * *", scheduledTime: Date.parse("2026-10-07T05:05:00Z") }, { GITHUB_TOKEN: "t" }, ctx);
  } finally {
    globalThis.fetch = orig;
  }
  assert.deepEqual(urls, ["https://api.github.com/repos/Moriah12783/turf-engine/actions/workflows/fondamental_nuit.yml/dispatches"]);
});
