#!/usr/bin/env python3
"""
Veille emploi autonome — agent IA pour Alexandre Le Clercq

Architecture :
  GitHub Actions (cron 7h lun-ven)
      → agent.py
          → tool: search_jobs()   via Tavily API  (x10 requêtes)
          → LLM analyse + score les résultats    (Groq — gratuit)
          → tool: send_report()   via Resend API  (email HTML)

Pattern : agentic loop avec tool_use (format OpenAI/Groq).
"""

import json
import os
import requests
from datetime import datetime
from groq import Groq

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
    'site:linkedin.com/jobs "Head of Sales" OR "VP Sales" Belgium startup 2026',
    'site:linkedin.com/jobs "Sales Manager" OR "Sales Team Lead" Bruxelles startup scaleup',
    'site:linkedin.com/jobs "GTM Engineer" OR "Go-to-Market Engineer" Belgium startup',
    'site:linkedin.com/jobs "RevOps" OR "Revenue Operations" OR "Sales Operations" Belgium startup',
    'site:linkedin.com/jobs "Operational Lead" OR "Operations Lead" Belgium startup scaleup',
    'site:linkedin.com/jobs "Country Manager" OR "Country Lead" Belgium startup',
    'site:linkedin.com/jobs "associate" OR "founding sales" Belgium startup equity',
    'site:linkedin.com/jobs Leexi OR TechWolf OR Wooclap OR Nodalview sales Belgium',
    'site:welcometothejungle.com "GTM" OR "RevOps" OR "Sales Ops" belgique startup',
    'site:welcometothejungle.com sales manager OR "country manager" bruxelles startup',
    '"bras droit" fondateur commercial Belgique startup 2026',
    '"founding account executive" OR "founding sales" OR "GTM engineer" Belgium startup',
]

# ─────────────────────────────────────────────────────────────────
#  TOOLS — schémas JSON (format OpenAI/Groq)
# ─────────────────────────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_jobs",
            "description": (
                "Recherche des offres d'emploi sur le web via Tavily. "
                "À appeler pour chaque requête de recherche. "
                "Retourne titre, URL et extrait pour chaque résultat."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "La requête de recherche exacte"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_report",
            "description": (
                "Génère le rapport HTML final et l'envoie par email. "
                "À appeler UNE SEULE FOIS après avoir tout analysé."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "jobs": {
                        "type": "array",
                        "description": "Liste des offres retenues (★★★ minimum), triées par note décroissante",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title":        {"type": "string"},
                                "company":      {"type": "string"},
                                "company_desc": {"type": ["string", "null"]},
                                "url":          {"type": "string"},
                                "stars":        {"type": "integer", "minimum": 1, "maximum": 5},
                                "type":         {"type": "string", "enum": ["mgr", "ae", "bd", "gtm", "ops", "ass"]},
                                "sector":       {"type": "string"},
                                "loc":          {"type": "string", "enum": ["bxl", "be", "remote"]},
                                "fit":          {"type": "string"},
                                "is_new":       {"type": ["boolean", "null"]},
                                "posted_date":  {"type": ["string", "null"], "description": "Date de publication (ex: 'il y a 2 jours') ou null si inconnue"}
                            },
                            "required": ["title", "company", "url", "stars", "type", "sector", "loc", "fit"]
                        }
                    },
                    "top_insight": {
                        "type": "string",
                        "description": "Tendance ou signal fort détecté ce cycle"
                    }
                },
                "required": ["jobs", "top_insight"]
            }
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
                "max_results": 3,
                "search_depth": "basic",
                "include_answer": False,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "query": query,
            "results": [
                {
                    "title":   r.get("title", ""),
                    "url":     r.get("url", ""),
                    "snippet": r.get("content", "")[:200],
                }
                for r in data.get("results", [])
            ],
        }
    except Exception as e:
        return {"query": query, "error": str(e), "results": []}


def _build_email_html(jobs: list, top_insight: str, date_str: str) -> str:
    """Construit le HTML de l'email de rapport."""
    star_colors = {5: "#FFB800", 4: "#30D158", 3: "#32ADE6", 2: "#98989D", 1: "#98989D"}
    type_labels  = {"mgr": "Manager", "ae": "Account Executive", "bd": "BizDev", "gtm": "GTM/RevOps", "ops": "Sales Ops", "ass": "Associate"}

    cards = ""
    for job in sorted(jobs, key=lambda x: -x["stars"]):
        n     = job["stars"]
        stars = "★" * n + "☆" * (5 - n)
        color = star_colors.get(n, "#98989D")
        new_badge = (
            '<span style="background:#FF3B30;color:#fff;border-radius:12px;'
            'padding:2px 8px;font-size:11px;font-weight:700;margin-right:6px">🆕 NOUVEAU</span>'
            if job.get("is_new") else ""
        )
        posted = job.get("posted_date", "")
        posted_html = f'<span style="color:#8e8e93;font-size:11px;margin-left:8px">📅 {posted}</span>' if posted else ""
        cards += f"""
<div style="background:#fff;border-radius:12px;padding:16px 18px;margin-bottom:12px;
     border-left:4px solid {color};box-shadow:0 1px 6px rgba(0,0,0,.06)">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:6px">
    <div>
      <div style="font-size:15px;font-weight:700;margin-bottom:2px">{new_badge}{job['title']}</div>
      <div style="font-size:13px;font-weight:600;color:#3c3c43">{job['company']}{posted_html}</div>
      <div style="font-size:11.5px;color:#8e8e93;font-style:italic;margin-top:2px">{job.get('company_desc','')}</div>
    </div>
    <span style="font-size:16px;color:#FFB800;letter-spacing:2px;padding-left:12px;white-space:nowrap">{stars}</span>
  </div>
  <div style="margin:10px 0 8px">
    <span style="background:#FFF3CD;color:#85600A;border-radius:12px;padding:3px 10px;font-size:11px;font-weight:600;margin-right:4px">{type_labels.get(job['type'], job['type'])}</span>
    <span style="background:#EAF0FB;color:#1A4FBF;border-radius:12px;padding:3px 10px;font-size:11px;margin-right:4px">{job['sector']}</span>
    <span style="background:#E8FAF0;color:#1A7F45;border-radius:12px;padding:3px 10px;font-size:11px">{job['loc']}</span>
  </div>
  <div style="background:#F0FFF4;border-left:3px solid #30D158;border-radius:0 8px 8px 0;padding:10px 12px;font-size:12.5px;color:#1a7f37;margin-bottom:12px;line-height:1.5">
    <b>Pourquoi toi :</b> {job['fit']}
  </div>
  <a href="{job['url']}" style="display:inline-block;background:#0071E3;color:#fff;text-decoration:none;
     padding:8px 18px;border-radius:20px;font-size:12px;font-weight:600">Voir l'offre →</a>
</div>"""

    top_picks  = [j for j in jobs if j["stars"] >= 4]
    new_jobs   = [j for j in sorted(jobs, key=lambda x: -x["stars"]) if j.get("is_new")]
    new_count  = len(new_jobs)

    new_section = ""
    if new_jobs:
        new_cards = ""
        for job in new_jobs:
            n = job["stars"]; stars = "★"*n+"☆"*(5-n); color = star_colors.get(n,"#98989D")
            posted = job.get("posted_date","")
            posted_html = f'<span style="color:#8e8e93;font-size:11px;margin-left:8px">📅 {posted}</span>' if posted else ""
            new_cards += f"""<div style="background:#fff;border-radius:10px;padding:14px 16px;margin-bottom:8px;border-left:4px solid {color}">
  <div style="font-size:14px;font-weight:700">{job['title']} — {job['company']}{posted_html}</div>
  <div style="font-size:11.5px;color:#8e8e93;margin:2px 0 6px">{job.get('company_desc','')}</div>
  <div style="font-size:12px;color:#1a7f37;margin-bottom:8px">{job['fit']}</div>
  <a href="{job['url']}" style="background:#0071E3;color:#fff;text-decoration:none;padding:5px 12px;border-radius:14px;font-size:11px;font-weight:600">Voir →</a>
</div>"""
        new_section = f"""<div style="background:#FFF8E1;border-radius:14px;padding:16px 18px;margin-bottom:20px;border:1px solid #FFD600">
  <div style="font-size:15px;font-weight:700;color:#8B6000;margin-bottom:12px">🔥 Nouveautés du jour ({new_count})</div>
  {new_cards}
</div>"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f5f5f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
<div style="max-width:640px;margin:0 auto;padding:24px 16px">

  <div style="background:#fff;border-radius:16px;padding:24px 28px;margin-bottom:16px;
       box-shadow:0 2px 12px rgba(0,0,0,.07)">
    <h1 style="font-size:22px;font-weight:700;margin:0 0 4px">Veille emploi — {date_str}</h1>
    <p style="color:#6e6e73;font-size:13px;margin:0 0 16px">Alexandre Le Clercq · Agent autonome Groq + Tavily</p>
    <div style="display:flex;gap:10px;flex-wrap:wrap">
      <div style="background:#f5f5f7;border-radius:10px;padding:10px 16px;text-align:center">
        <div style="font-size:22px;font-weight:700">{len(jobs)}</div>
        <div style="font-size:10px;color:#6e6e73;text-transform:uppercase;letter-spacing:.4px">offres</div>
      </div>
      <div style="background:#f5f5f7;border-radius:10px;padding:10px 16px;text-align:center">
        <div style="font-size:22px;font-weight:700">{len(top_picks)}</div>
        <div style="font-size:10px;color:#6e6e73;text-transform:uppercase;letter-spacing:.4px">top picks ★★★★+</div>
      </div>
      <div style="background:#FFF8E1;border-radius:10px;padding:10px 16px;text-align:center;border:1px solid #FFD600">
        <div style="font-size:22px;font-weight:700;color:#8B6000">{new_count}</div>
        <div style="font-size:10px;color:#8B6000;text-transform:uppercase;letter-spacing:.4px">🔥 nouvelles 48h</div>
      </div>
    </div>
  </div>

  <div style="background:#EAF0FB;border-radius:12px;padding:14px 18px;margin-bottom:16px;
       font-size:13px;color:#1A4FBF;line-height:1.6">
    <b>💡 Insight du jour :</b> {top_insight}
  </div>

  {new_section}

  {cards}

  <div style="text-align:center;color:#aeaeb2;font-size:11px;margin-top:24px;padding-bottom:16px">
    Agent autonome · Groq + Tavily + Resend · {datetime.now().strftime('%d/%m/%Y %H:%M UTC')}
  </div>
</div>
</body></html>"""


def send_report(jobs: list, top_insight: str) -> dict:
    """Sauvegarde le rapport HTML localement et l'envoie par email via Resend."""
    date_str  = datetime.now().strftime("%d %B %Y")
    date_file = datetime.now().strftime("%Y%m%d")
    html      = _build_email_html(jobs, top_insight, date_str)

    out_dir = os.environ.get("REPORT_DIR", "/tmp/veille_jobs")
    os.makedirs(out_dir, exist_ok=True)
    for fname in [f"rapport_{date_file}.html", "latest.html"]:
        with open(os.path.join(out_dir, fname), "w", encoding="utf-8") as f:
            f.write(html)

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
#  AGENTIC LOOP (format OpenAI/Groq)
# ─────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = f"""Tu es un agent de veille emploi pour :

{PROFILE}

LANGUE : Réponds toujours en français dans tes analyses et dans le champ `top_insight`.

INSTRUCTIONS :
1. Appelle `search_jobs` pour CHACUNE des {len(SEARCH_QUERIES)} requêtes suivantes (dans l'ordre) :
{chr(10).join(f'   - "{q}"' for q in SEARCH_QUERIES)}

2. Après TOUTES les recherches, analyse l'ensemble des résultats :
   - ÉLIMINE immédiatement : articles de blog, grandes entreprises (>500 pers.), banques, consulting, admins, postes nécessitant le néerlandais, offres "no longer accepting applications", offres postées il y a plus de 60 jours
   - ÉLIMINE les doublons (même poste vu plusieurs fois)
   - Score chaque offre pertinente de 1 à 5 étoiles
   - Ne retiens QUE les ★★★ minimum

3. Pour chaque offre, extrais la date de publication si visible dans l'extrait ("posted 2 days ago" → "il y a 2 jours", "Sep 26" → "26 sept."). Si l'offre date de plus de 30 jours ou est clôturée : EXCLURE.
   - `is_new` = true UNIQUEMENT si postée hier ou aujourd'hui (≤ 48h)

4. Appelle `send_report` UNE SEULE FOIS avec la liste finale.

Critères de scoring :
★★★★★ Country Lead, associate co-fondateur, Head of Sales, GTM Engineer chez startup IA/FinTech/SaaS belge
★★★★  Sales Manager, GTM Lead, RevOps, Sales Ops, Operational Lead chez scaleup B2B — bonne autonomie
★★★   BDM, AE senior, rôle intéressant mais secteur moins prioritaire
★★    Trop junior, trop corporate, néerlandais requis, grande entreprise → NE PAS INCLURE
"""


def run_agent():
    client   = Groq(api_key=os.environ["GROQ_API_KEY"])
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": "Lance la veille emploi."},
    ]

    print(f"🤖  Agent démarré — {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")
    iteration = 0

    while True:
        iteration += 1
        print(f"\n── Iteration {iteration} ──")

        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            max_tokens=4096,
            tools=TOOLS,
            tool_choice="auto",
            messages=messages,
        )

        choice  = response.choices[0]
        message = choice.message

        if message.content:
            print(f"  LLM: {message.content[:120]}{'…' if len(message.content or '') > 120 else ''}")

        # Ajouter la réponse dans l'historique
        msg_dict = {"role": "assistant", "content": message.content or ""}
        if message.tool_calls:
            msg_dict["tool_calls"] = [
                {
                    "id":       tc.id,
                    "type":     "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in message.tool_calls
            ]
        messages.append(msg_dict)

        # Condition de sortie
        if choice.finish_reason == "stop":
            print("\n✅  Agent terminé proprement.")
            break

        if choice.finish_reason != "tool_calls" or not message.tool_calls:
            print(f"⚠️  Stop inattendu : {choice.finish_reason}")
            break

        # Exécuter les tool calls
        for tc in message.tool_calls:
            name = tc.function.name
            args = json.loads(tc.function.arguments)

            print(f"  🔧  Tool call : {name}  args={list(args.keys())}")

            if name == "search_jobs":
                result = search_jobs(args["query"])
                n = len(result.get("results", []))
                print(f"       → {n} résultats pour : {args['query'][:60]}")

            elif name == "send_report":
                result = send_report(args["jobs"], args["top_insight"])
                print(f"  ✉️   Email envoyé — {result}")

            else:
                result = {"error": f"Outil inconnu : {name}"}

            messages.append({
                "role":         "tool",
                "tool_call_id": tc.id,
                "content":      json.dumps(result, ensure_ascii=False),
            })


if __name__ == "__main__":
    run_agent()
