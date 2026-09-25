#!/usr/bin/env python3
"""
Veille emploi autonome — agent IA pour Alexandre Le Clercq

Architecture :
  GitHub Actions (cron 7h lun-ven)
      → agent.py
          → tool: search_jobs()   via Tavily API  (x10 requêtes)
          → Claude analyse + score les résultats
          → tool: send_report()   via Resend API  (email HTML)

Pattern pédagogique : agentic loop avec tool_use Anthropic SDK.
Claude décide lui-même quels outils appeler et dans quel ordre.
"""

import anthropic
import json
import os
import requests
from datetime import datetime

# ─────────────────────────────────────────────────────────────────
#  PROFIL & REQUÊTES
# ─────────────────────────────────────────────────────────────────

PROFILE = """
Alexandre Le Clercq — Head of Account Management chez Sortlist (marketplace B2B SaaS, Bruxelles)
Cherche par ordre de priorité :
1. Associate / bras droit / co-fondateur pour lancer une activité
2. GTM Engineer / RevOps
3. Head of Sales / Sales senior
4. Manager commercial
5. Business Development
UNIQUEMENT startups et scaleups. PAS : grands groupes, banques, consultings, admins.
Localisation : Bruxelles, Wallonie, remote OK.
Langues : français natif, anglais professionnel.
"""

SEARCH_QUERIES = [
    'site:linkedin.com/jobs "Head of Sales" OR "Co-Head of Sales" Belgium startup 2026',
    'site:linkedin.com/jobs "Sales Manager" OR "Sales Team Lead" Bruxelles startup scaleup',
    'site:linkedin.com/jobs "Go-to-Market" OR "GTM" OR "RevOps" Belgium startup',
    'site:linkedin.com/jobs "Country Manager" OR "Country Lead" Belgium startup',
    'site:linkedin.com/jobs "associate" OR "founding sales" Belgium startup equity',
    'site:linkedin.com/jobs Leexi OR TechWolf OR Wooclap OR Nodalview sales Belgium',
    'site:welcometothejungle.com sales manager bruxelles startup',
    'site:welcometothejungle.com "country manager" belgique startup',
    '"bras droit" fondateur commercial Belgique startup 2026',
    '"founding account executive" OR "founding sales" Belgium startup',
]

# ─────────────────────────────────────────────────────────────────
#  TOOLS — schémas JSON pour Claude
# ─────────────────────────────────────────────────────────────────
#
#  Deux outils seulement. Claude appellera search_jobs() autant de
#  fois qu'il le juge utile, puis send_report() une seule fois.

TOOLS = [
    {
        "name": "search_jobs",
        "description": (
            "Recherche des offres d'emploi sur le web via Tavily. "
            "À appeler pour chaque requête de recherche. "
            "Retourne titre, URL et extrait pour chaque résultat."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "La requête de recherche exacte"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "send_report",
        "description": (
            "Génère le rapport HTML final et l'envoie par email. "
            "À appeler UNE SEULE FOIS après avoir tout analysé."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "jobs": {
                    "type": "array",
                    "description": "Liste des offres retenues (★★★ minimum), triées par note décroissante",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title":        {"type": "string", "description": "Intitulé du poste"},
                            "company":      {"type": "string", "description": "Nom de l'entreprise"},
                            "company_desc": {"type": "string", "description": "Secteur · stade · taille"},
                            "url":          {"type": "string", "description": "URL directe de l'offre"},
                            "stars":        {"type": "integer", "minimum": 1, "maximum": 5},
                            "type":         {"type": "string", "enum": ["mgr", "ae", "bd", "gtm", "ass"]},
                            "sector":       {"type": "string", "description": "ai | fintech | cleantech | saas-rh | marketplace | saas-b2b | sport | gaming | early"},
                            "loc":          {"type": "string", "enum": ["bxl", "be", "remote"]},
                            "fit":          {"type": "string", "description": "Pourquoi c'est pertinent pour Alexandre — 1 phrase"},
                            "is_new":       {"type": "boolean", "description": "true si l'offre a été publiée aujourd'hui ou hier"}
                        },
                        "required": ["title", "company", "url", "stars", "type", "sector", "loc", "fit"]
                    }
                },
                "top_insight": {
                    "type": "string",
                    "description": "Tendance ou signal fort détecté ce cycle (ex: 'Fort signal GTM en FinTech cette semaine')"
                }
            },
            "required": ["jobs", "top_insight"]
        }
    }
]

# ─────────────────────────────────────────────────────────────────
#  IMPLÉMENTATIONS DES TOOLS
# ─────────────────────────────────────────────────────────────────

def search_jobs(query: str) -> dict:
    """Appelle Tavily Search API et retourne les résultats bruts."""
    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": os.environ["TAVILY_API_KEY"],
                "query": query,
                "max_results": 5,
                "search_depth": "basic",
                "include_answer": False,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        # Tronquer les contenus pour ne pas exploser le contexte de Claude
        return {
            "query": query,
            "results": [
                {
                    "title":   r.get("title", ""),
                    "url":     r.get("url", ""),
                    "snippet": r.get("content", "")[:400],
                }
                for r in data.get("results", [])
            ],
        }
    except Exception as e:
        return {"query": query, "error": str(e), "results": []}


def _build_email_html(jobs: list, top_insight: str, date_str: str) -> str:
    """Construit le HTML de l'email de rapport."""
    star_colors = {5: "#FFB800", 4: "#30D158", 3: "#32ADE6", 2: "#98989D", 1: "#98989D"}
    type_labels  = {"mgr": "Manager", "ae": "Account Executive", "bd": "BizDev", "gtm": "GTM/RevOps", "ass": "Associate"}

    cards = ""
    for job in sorted(jobs, key=lambda x: -x["stars"]):
        n     = job["stars"]
        stars = "★" * n + "☆" * (5 - n)
        color = star_colors.get(n, "#98989D")
        new   = (
            '<span style="background:#FF3B30;color:#fff;border-radius:12px;'
            'padding:2px 8px;font-size:11px;font-weight:700;margin-right:6px">NOUVEAU</span>'
            if job.get("is_new") else ""
        )
        cards += f"""
<div style="background:#fff;border-radius:12px;padding:16px 18px;margin-bottom:12px;
     border-left:4px solid {color};box-shadow:0 1px 6px rgba(0,0,0,.06)">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:4px">
    <span style="font-size:15px;font-weight:700">{new}{job['title']}</span>
    <span style="font-size:13px;color:#FFB800;letter-spacing:2px;padding-left:8px;white-space:nowrap">{stars}</span>
  </div>
  <div style="font-size:13px;font-weight:600;color:#3c3c43;margin-bottom:2px">{job['company']}</div>
  <div style="font-size:11.5px;color:#8e8e93;font-style:italic;margin-bottom:8px">{job.get('company_desc','')}</div>
  <div style="margin-bottom:8px">
    <span style="background:#FFF3CD;color:#85600A;border-radius:12px;padding:2px 8px;font-size:11px;margin-right:4px">{type_labels.get(job['type'], job['type'])}</span>
    <span style="background:#EAF0FB;color:#1A4FBF;border-radius:12px;padding:2px 8px;font-size:11px;margin-right:4px">{job['sector']}</span>
    <span style="background:#E8FAF0;color:#1A7F45;border-radius:12px;padding:2px 8px;font-size:11px">{job['loc']}</span>
  </div>
  <div style="background:#F0FFF4;border-radius:8px;padding:8px 10px;font-size:12px;color:#1a7f37;margin-bottom:10px">{job['fit']}</div>
  <a href="{job['url']}" style="display:inline-block;background:#0071E3;color:#fff;text-decoration:none;
     padding:6px 14px;border-radius:16px;font-size:12px;font-weight:600">Voir l'offre →</a>
</div>"""

    top_picks = [j for j in jobs if j["stars"] >= 4]
    new_count = len([j for j in jobs if j.get("is_new")])

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f5f5f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
<div style="max-width:640px;margin:0 auto;padding:24px 16px">

  <div style="background:#fff;border-radius:16px;padding:24px 28px;margin-bottom:16px;
       box-shadow:0 2px 12px rgba(0,0,0,.07)">
    <h1 style="font-size:22px;font-weight:700;margin:0 0 4px">Veille emploi — {date_str}</h1>
    <p style="color:#6e6e73;font-size:13px;margin:0 0 16px">Alexandre Le Clercq · Agent autonome Anthropic + Tavily</p>
    <div style="display:flex;gap:10px;flex-wrap:wrap">
      <div style="background:#f5f5f7;border-radius:10px;padding:10px 16px;text-align:center">
        <div style="font-size:22px;font-weight:700">{len(jobs)}</div>
        <div style="font-size:10px;color:#6e6e73;text-transform:uppercase;letter-spacing:.4px">offres</div>
      </div>
      <div style="background:#f5f5f7;border-radius:10px;padding:10px 16px;text-align:center">
        <div style="font-size:22px;font-weight:700">{len(top_picks)}</div>
        <div style="font-size:10px;color:#6e6e73;text-transform:uppercase;letter-spacing:.4px">top picks ★★★★+</div>
      </div>
      <div style="background:#f5f5f7;border-radius:10px;padding:10px 16px;text-align:center">
        <div style="font-size:22px;font-weight:700">{new_count}</div>
        <div style="font-size:10px;color:#6e6e73;text-transform:uppercase;letter-spacing:.4px">nouvelles 48h</div>
      </div>
    </div>
  </div>

  <div style="background:#EAF0FB;border-radius:12px;padding:14px 18px;margin-bottom:20px;
       font-size:13px;color:#1A4FBF;line-height:1.6">
    <b>💡 Insight du jour :</b> {top_insight}
  </div>

  {cards}

  <div style="text-align:center;color:#aeaeb2;font-size:11px;margin-top:24px;padding-bottom:16px">
    Agent autonome · Anthropic API (claude-sonnet-4-5) + Tavily + Resend<br>
    Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M UTC')}
  </div>
</div>
</body></html>"""


def send_report(jobs: list, top_insight: str) -> dict:
    """Sauvegarde le rapport HTML localement et l'envoie par email via Resend."""
    date_str  = datetime.now().strftime("%d %B %Y")
    date_file = datetime.now().strftime("%Y%m%d")
    html      = _build_email_html(jobs, top_insight, date_str)

    # — Sauvegarde locale (pour compatibilité avec CLAUDE.md) —
    out_dir = os.environ.get("REPORT_DIR", "/tmp/veille_jobs")
    os.makedirs(out_dir, exist_ok=True)
    for fname in [f"rapport_{date_file}.html", "latest.html"]:
        with open(os.path.join(out_dir, fname), "w", encoding="utf-8") as f:
            f.write(html)

    # — Email via Resend —
    top3    = sorted(jobs, key=lambda x: -x["stars"])[:3]
    preview = " · ".join(f"{j['company']} ({j['stars']}★)" for j in top3)

    resp = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {os.environ['RESEND_API_KEY']}",
            "Content-Type": "application/json",
        },
        json={
            "from":    os.environ.get("FROM_EMAIL", "Veille Emploi <onboarding@resend.dev>"),
            "to":      [os.environ.get("TO_EMAIL", "leclercq.alexandre@outlook.com")],
            "subject": f"Veille emploi {date_str} — {len(jobs)} offres · {preview}",
            "html":    html,
        },
        timeout=15,
    )

    return {
        "email_status": resp.status_code,
        "email_ok":     resp.ok,
        "saved_to":     out_dir,
    }


# ─────────────────────────────────────────────────────────────────
#  AGENTIC LOOP
# ─────────────────────────────────────────────────────────────────
#
#  Pattern : boucle while qui tourne jusqu'à stop_reason == "end_turn".
#  À chaque itération :
#    1. Claude répond (texte + éventuels tool_use)
#    2. On exécute les tools demandés
#    3. On renvoie les tool_result à Claude
#    4. Claude décide si besoin d'autres tools, ou s'il a fini
#
#  C'est exactement ce que font LangChain, CrewAI, etc. — mais ici
#  directement avec le SDK Anthropic, sans abstraction.

SYSTEM_PROMPT = f"""Tu es un agent de veille emploi pour :

{PROFILE}

INSTRUCTIONS :
1. Appelle `search_jobs` pour CHACUNE des requêtes suivantes (dans l'ordre) :
{chr(10).join(f'   - "{q}"' for q in SEARCH_QUERIES)}

2. Après TOUTES les recherches, analyse l'ensemble des résultats :
   - Filtre : élimine articles, grandes entreprises, admins, postes nécessitant néerlandais
   - Score chaque offre pertinente de 1 à 5 étoiles
   - Ne retiens que les ★★★ minimum dans le rapport final

3. Appelle `send_report` UNE SEULE FOIS avec la liste finale.

Critères de scoring :
★★★★★ Country Lead, associate co-fondateur, Head of Sales chez startup IA/FinTech/SaaS belge
★★★★  Sales Manager, GTM Lead, RevOps chez scaleup B2B — bonne autonomie marché
★★★   BDM, AE senior, rôle intéressant mais secteur moins prioritaire
★★    Trop junior, trop corporate, ou localisation difficile (ne pas inclure)
"""


def run_agent():
    client   = anthropic.Anthropic()
    messages = [{"role": "user", "content": "Lance la veille emploi."}]

    print(f"🤖  Agent démarré — {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")
    iteration = 0

    while True:
        iteration += 1
        print(f"\n── Iteration {iteration} ──")

        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=8192,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        # Ajouter la réponse dans l'historique des messages
        messages.append({"role": "assistant", "content": response.content})

        # Afficher les blocs de texte (réflexion interne de Claude)
        for block in response.content:
            if hasattr(block, "text") and block.text:
                print(f"  Claude: {block.text[:120]}{'…' if len(block.text) > 120 else ''}")

        # Condition de sortie : plus aucun tool_use demandé
        if response.stop_reason == "end_turn":
            print("\n✅  Agent terminé proprement.")
            break

        if response.stop_reason != "tool_use":
            print(f"⚠️  Stop inattendu : {response.stop_reason}")
            break

        # Exécuter tous les tool_use de cette itération
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            print(f"  🔧  Tool call : {block.name}  args={list(block.input.keys())}")

            if block.name == "search_jobs":
                result = search_jobs(block.input["query"])
                n = len(result.get("results", []))
                print(f"       → {n} résultats pour : {block.input['query'][:60]}")

            elif block.name == "send_report":
                result = send_report(block.input["jobs"], block.input["top_insight"])
                print(f"  ✉️   Email envoyé — {result}")

            else:
                result = {"error": f"Outil inconnu : {block.name}"}

            tool_results.append({
                "type":        "tool_result",
                "tool_use_id": block.id,
                "content":     json.dumps(result, ensure_ascii=False),
            })

        # Renvoyer les résultats à Claude pour qu'il continue
        messages.append({"role": "user", "content": tool_results})


if __name__ == "__main__":
    run_agent()
