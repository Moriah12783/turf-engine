// turf-horloge : horloge fiable des tâches de nuit de turf-engine.
//
// Les horaires GitHub ne sont pas garantis la nuit (28 et 29/09 : le passage
// de 01h17 n'a jamais démarré, le filet de 03h47 est parti avec plus de 3 h
// de retard). Les horaires Cloudflare (Cron Triggers, UTC) sont tenus à la
// minute : ce Worker lance à heure fixe les workflows GitHub par
// workflow_dispatch, qui démarre en quelques secondes. Les horaires GitHub
// restent en place comme filet.
//
// Il ne fait rien d'autre : aucune donnée lue ni écrite, aucune page publique
// (fetch répond 404). Les garde-fous restent dans les workflows eux-mêmes
// (créneau 00h-05h du Radar, limites de 06h20 et 06h28, OMBRE_PAS_OUVERTE).
// Seul secret : GITHUB_TOKEN, jeton GitHub limité au lancement des workflows
// de ce dépôt (Actions : lecture et écriture). Il n'est jamais journalisé.
// Guide : docs/HORLOGE_CLOUDFLARE.md.

const DEPOT = "Moriah12783/turf-engine";
const BRANCHE = "main";

// cron (UTC) -> workflow et entrées ; du/au bornent les jours (inclus).
const PLAN = [
  // Export nocturne de l'historique Radar (créneau 00h-05h vérifié par le workflow).
  { cron: "17 1 * * *", workflow: "history_export.yml", inputs: { action: "run" } },
  // Répétition générale de l'ombre (GO de Steph du 28/09).
  { cron: "5 5 * * *", workflow: "repetition_ombre.yml", inputs: { prevu: "05:05" }, du: "2026-10-01", au: "2026-10-06" },
  { cron: "50 5 * * *", workflow: "repetition_ombre.yml", inputs: { prevu: "05:50" }, du: "2026-10-01", au: "2026-10-06" },
  // Ombre : calcul de nuit du fondamental, du premier jour d'ombre (08/10) au
  // dernier jour possible avec une remise à zéro (version corrigée au 14e jour,
  // puis 35 jours : 24/11). Avant le gel, le calcul refuse (OMBRE_PAS_OUVERTE).
  { cron: "5 5 * * *", workflow: "fondamental_nuit.yml", inputs: { action: "nuit" }, du: "2026-10-08", au: "2026-11-24" },
  { cron: "50 5 * * *", workflow: "fondamental_nuit.yml", inputs: { action: "nuit" }, du: "2026-10-08", au: "2026-11-24" },
];

export function taches(cron, jour) {
  return PLAN.filter((t) => t.cron === cron && (!t.du || jour >= t.du) && (!t.au || jour <= t.au));
}

function journal(payload) {
  console.log("HORLOGE " + JSON.stringify(payload));
}

const pause = (ms) => new Promise((r) => setTimeout(r, ms));

async function appel(tache, token, inputs, fetchFn) {
  return fetchFn(`https://api.github.com/repos/${DEPOT}/actions/workflows/${tache.workflow}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "turf-horloge",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: BRANCHE, inputs }),
  });
}

// Lance un workflow. Réessaie sur erreur GitHub passagère (5xx, 429, réseau) ;
// abandonne sur erreur de configuration (401, 403, 404). Sur 422 (entrée
// inconnue du workflow), relance une fois sans entrées : le workflow garde
// alors ses valeurs par défaut plutôt que de ne pas tourner.
export async function declencher(tache, env, { fetchFn = fetch, attente = pause } = {}) {
  if (!env.GITHUB_TOKEN) {
    journal({ etat: "SANS_JETON", workflow: tache.workflow });
    return false;
  }
  let inputs = tache.inputs || {};
  for (let essai = 1; essai <= 4; essai++) {
    let statut = 0;
    let detail = "";
    try {
      const r = await appel(tache, env.GITHUB_TOKEN, inputs, fetchFn);
      statut = r.status;
      if (statut === 204) {
        journal({ etat: "LANCE", workflow: tache.workflow, inputs, essai });
        return true;
      }
      detail = (await r.text()).slice(0, 200);
    } catch (e) {
      detail = String(e && e.message ? e.message : e).slice(0, 200);
    }
    journal({ etat: "ECHEC", workflow: tache.workflow, statut, essai, detail });
    if (statut === 422 && Object.keys(inputs).length) {
      inputs = {};
      continue;
    }
    if (statut && statut < 500 && statut !== 429) return false;
    await attente(2000 * 2 ** (essai - 1));
  }
  return false;
}

export default {
  async scheduled(event, env, ctx) {
    const jour = new Date(event.scheduledTime).toISOString().slice(0, 10);
    const liste = taches(event.cron, jour);
    if (!liste.length) {
      journal({ etat: "RIEN_A_LANCER", cron: event.cron, jour });
      return;
    }
    ctx.waitUntil(Promise.all(liste.map((t) => declencher(t, env))));
  },
  async fetch() {
    return new Response("Not found", { status: 404 });
  },
};
