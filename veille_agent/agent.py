#!/usr/bin/env python3
"""
Veille emploi autonome — agent IA pour Alexandre Le Clercq

Architecture :
  GitHub Actions (cron 8h UTC lun-ven)
      → agent.py
          → search_jobs()  via Tavily API  (16 requêtes)
          → LLM analyse + score            via Groq (gratuit)
          → send_report()  via Resend API  (email compact + rapport GitHub Pages)
"""

import json
import os
import requests
from datetime import datetime
from groq import Groq

# ─────────────────────────────────────────────────────────────────
#  PROFIL
# ─────────────────────────────────────────────────────────────────

PROFILE = """
Alexandre Le Clercq — Head of Account Management chez Sortlist (marketplace B2B SaaS, Bruxelles).
Expérience : gestion de comptes, ops commerciales, analytics, management d'équipes, scale B2B.

Cherche par ordre de priorité :
1. Associate / bras droit / co-fondateur pour lancer une activité
2. GTM Engineer / RevOps / Sales Ops
3. Head of Sales / Sales senior / Country Lead
4. Manager commercial / BizDev senior
5. Rôle opérationnel ou analytique dans le secteur de la santé (hôpitaux, cliniques, healthtech)

EXCLUS : grands groupes généralistes, banques, consultings classiques, postes nécessitant le néerlandais.
Localisation : Bruxelles, Wallonie, remote OK. Langues : français natif, anglais professionnel.
"""

# ─────────────────────────────────────────────────────────────────
#  REQUÊTES DE RECHERCHE (12 startup + 4 médical = 16)
# ─────────────────────────────────────────────────────────────────

SEARCH_QUERIES = [
    # ── Startup / GTM / Sales ──────────────────────────────────
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
    # ── Secteur Santé ──────────────────────────────────────────
    'site:linkedin.com/jobs "responsable opérations" OR "directeur opérationnel" hôpital OR clinique Belgique 2026',
    'site:linkedin.com/jobs "business analyst" OR "analyste performance" OR "chef de projet" santé OR healthcare Belgique',
    'site:linkedin.com/jobs "operations manager" OR "project manager" OR "chief of staff" hospital OR healthcare Belgium',
    'site:linkedin.com/jobs "digital health" OR "e-santé" OR "healthtech" operations OR commercial OR GTM Belgium 2026',
]

# ─────────────────────────────────────────────────────────────────
#  TOOLS — schémas JSON (format OpenAI/Groq)
# ─────────────────────────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_jobs",
            "description": "Recherche des offres d'emploi via Tavily. Appelle pour chaque requête.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_report",
            "description": "Génère le rapport et l'envoie par email. UNE SEULE FOIS à la fin.",
            "parameters": {
                "type": "object",
                "properties": {
                    "jobs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title":        {"type": "string"},
                                "company":      {"type": "string"},
                                "company_desc": {"type": ["string", "null"]},
                                "url":          {"type": "string"},
                                "stars":        {"type": "integer", "minimum": 1, "maximum": 5},
                                "type":         {"type": "string", "enum": ["mgr", "ae", "bd", "gtm", "ops", "ass", "med"]},
                                "sector":       {"type": "string"},
                                "loc":          {"type": "string", "enum": ["bxl", "be", "remote"]},
                                "fit":          {"type": "string"},
                                "is_new":       {"type": ["boolean", "null"]},
                                "posted_date":  {"type": ["string", "null"]}
                            },
                            "required": ["title", "company", "url", "stars", "type", "sector", "loc", "fit"]
                        }
                    },
                    "top_insight": {"type": "string"}
                },
                "required": ["jobs", "top_insight"]
            }
        }
    }
]

# ─────────────────────────────────────────────────────────────────
#  CSS & JS pour le rapport GitHub Pages (chaînes brutes, pas f-strings)
# ─────────────────────────────────────────────────────────────────

_PAGES_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f5f7;color:#1d1d1f;min-height:100vh}
.wrap{max-width:960px;margin:0 auto;padding:24px 16px}
.header{background:#fff;border-radius:18px;padding:24px 28px;margin-bottom:14px;box-shadow:0 2px 12px rgba(0,0,0,.07)}
.header h1{font-size:22px;font-weight:700;margin-bottom:3px}
.header .sub{color:#6e6e73;font-size:12.5px;margin-bottom:16px}
.stats{display:flex;gap:8px;flex-wrap:wrap}
.stat{background:#f5f5f7;border-radius:10px;padding:10px 16px;text-align:center;min-width:76px}
.stat-n{font-size:22px;font-weight:700}
.stat-l{font-size:10px;color:#6e6e73;margin-top:2px;text-transform:uppercase;letter-spacing:.4px}
.stat.hot{background:#FFF8E1;border:1px solid #FFD600}
.stat.hot .stat-n,.stat.hot .stat-l{color:#8B6000}
.insight{background:#EAF0FB;border-radius:12px;padding:13px 18px;margin-bottom:14px;font-size:13px;color:#1A4FBF;line-height:1.6}
.insight b{font-weight:700}
.filters{background:#fff;border-radius:14px;padding:14px 18px;margin-bottom:14px;box-shadow:0 2px 8px rgba(0,0,0,.06)}
.frow{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:8px}
.frow:last-child{margin-bottom:0}
.flabel{font-size:10px;font-weight:700;color:#8e8e93;text-transform:uppercase;letter-spacing:.6px;margin-right:2px;min-width:36px}
.fbtn{background:#f5f5f7;border:1.5px solid transparent;border-radius:18px;padding:4px 11px;font-size:11.5px;color:#3c3c43;cursor:pointer;transition:all .15s;font-family:inherit}
.fbtn:hover{background:#e5e5ea}
.fbtn.active{background:#1d1d1f;color:#fff;border-color:#1d1d1f}
.section-title{font-size:14px;font-weight:600;margin:16px 0 8px;display:flex;align-items:center;gap:7px}
.sc{font-size:11px;font-weight:500;color:#8e8e93;background:#e5e5ea;border-radius:9px;padding:1px 7px}
.new-wrap{background:#fff;border-radius:14px;margin-bottom:14px;box-shadow:0 2px 8px rgba(0,0,0,.06);overflow:hidden}
.new-hdr{display:flex;align-items:center;gap:8px;padding:13px 18px;cursor:pointer;user-select:none}
.new-hdr:hover{background:#fafafa}
.new-badge{font-size:11px;border-radius:18px;padding:2px 9px;background:#FFF0EE;color:#CC2200;font-weight:700}
.chevron{color:#aeaeb2;font-size:11px;margin-left:auto;transition:transform .2s}
.new-body{padding:0 14px 14px}
.med-wrap{background:#F0FBFC;border-radius:14px;margin-bottom:14px;padding:16px 18px;border:1.5px solid #0AA5B8}
.med-title{font-size:14px;font-weight:700;color:#0C5460;margin-bottom:10px}
.cards-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(400px,1fr));gap:10px}
.card{position:relative;background:#fff;border-radius:13px;padding:15px 17px;box-shadow:0 2px 8px rgba(0,0,0,.06);border-left:4px solid #e5e5e7;transition:box-shadow .15s,opacity .2s,transform .2s}
.card:hover{box-shadow:0 4px 16px rgba(0,0,0,.1)}
.card.s5{border-left-color:#FFB800}.card.s4{border-left-color:#30D158}.card.s3{border-left-color:#32ADE6}
.card.med{border-left-color:#0AA5B8;background:linear-gradient(135deg,#fff 0%,#f0fbfc 100%)}
.dismiss{position:absolute;top:8px;right:10px;width:22px;height:22px;background:none;border:none;color:#d1d1d6;font-size:15px;cursor:pointer;border-radius:50%;display:flex;align-items:center;justify-content:center;opacity:0;transition:opacity .15s,color .15s}
.card:hover .dismiss{opacity:1}.dismiss:hover{color:#FF3B30}
.ctitle{font-size:14.5px;font-weight:650;margin-bottom:2px;padding-right:24px;line-height:1.3}
.cco{font-size:13px;font-weight:600;color:#3c3c43;margin-bottom:1px}
.cdesc{font-size:11.5px;color:#8e8e93;font-style:italic;margin-bottom:8px;line-height:1.4}
.stars{font-size:11.5px;color:#FFB800;letter-spacing:1px}
.tags{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:8px}
.tag{border-radius:14px;padding:2px 8px;font-size:11px;font-weight:500}
.tag.tnew{background:#FF3B30;color:#fff;font-weight:700}
.tag.ttype{background:#FFF3CD;color:#85600A}
.tag.ttype.tmed{background:#D1ECF1;color:#0C5460}
.tag.tsect{background:#EAF0FB;color:#1A4FBF}
.tag.tloc{background:#E8FAF0;color:#1A7F45}
.tag.tloc.tremote{background:#F0EDFF;color:#5038D0}
.tag.tdate{background:#F5F5F7;color:#6e6e73}
.fit{background:#F0FFF4;border-radius:8px;padding:8px 10px;font-size:12.5px;color:#1a7f37;margin:7px 0 9px;line-height:1.5}
.fit.fitmed{background:#E8F8FB;color:#0C5460}
.cta{display:inline-block;background:#0071E3;color:#fff;text-decoration:none;padding:6px 14px;border-radius:18px;font-size:12px;font-weight:600}
.cta:hover{background:#005BBB}
@media(max-width:600px){.cards-grid{grid-template-columns:1fr}.wrap{padding:16px 10px}}
"""

_PAGES_JS = """
(function() {
  const dismissed = new Set(JSON.parse(sessionStorage.getItem('vd') || '[]'));
  dismissed.forEach(id => { const el = document.getElementById(id); if(el) el.style.display='none'; });

  let activeType = null, minStars = 0;
  function applyFilters() {
    document.querySelectorAll('.card').forEach(card => {
      if(dismissed.has(card.id)) return;
      const ok = (!activeType || card.dataset.type === activeType) && parseInt(card.dataset.stars) >= minStars;
      card.style.display = ok ? '' : 'none';
    });
  }
  document.querySelectorAll('.type-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      activeType = (activeType === btn.dataset.type) ? null : btn.dataset.type;
      document.querySelectorAll('.type-btn').forEach(b => b.classList.toggle('active', b.dataset.type === activeType));
      applyFilters();
    });
  });
  document.querySelectorAll('.stars-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const v = parseInt(btn.dataset.min);
      minStars = (minStars === v) ? 0 : v;
      document.querySelectorAll('.stars-btn').forEach(b => b.classList.toggle('active', parseInt(b.dataset.min) === minStars));
      applyFilters();
    });
  });
  document.querySelectorAll('.dismiss').forEach(btn => {
    btn.addEventListener('click', () => {
      const card = btn.closest('.card');
      card.style.opacity='0'; card.style.transform='scale(0.95)';
      setTimeout(() => card.style.display='none', 180);
      dismissed.add(card.id); sessionStorage.setItem('vd', JSON.stringify([...dismissed]));
    });
  });
  const nh = document.querySelector('.new-hdr');
  if(nh) {
    nh.addEventListener('click', () => {
      const nb = nh.nextElementSibling;
      const open = nb.style.display !== 'none';
      nb.style.display = open ? 'none' : 'block';
      nh.querySelector('.chevron').textContent = open ? '▶' : '▼';
    });
  }
})();
"""

# ─────────────────────────────────────────────────────────────────
#  HELPERS HTML
# ─────────────────────────────────────────────────────────────────

_TYPE_LABELS = {
    "mgr": "Manager", "ae": "Account Exec", "bd": "BizDev",
    "gtm": "GTM/RevOps", "ops": "Sales Ops", "ass": "Associate", "med": "🏥 Santé"
}
_STAR_COLORS = {5: "#FFB800", 4: "#30D158", 3: "#32ADE6", 2: "#98989D", 1: "#98989D"}


def _card_html(job: dict, idx: int) -> str:
    n = job["stars"]
    stars = "★" * n + "☆" * (5 - n)
    t = job.get("type", "mgr")
    is_med = t == "med"
    card_cls = f"card s{n}" + (" med" if is_med else "")
    new_tag = '<span class="tag tnew">🆕 NOUVEAU</span>' if job.get("is_new") else ""
    date_tag = f'<span class="tag tdate">📅 {job["posted_date"]}</span>' if job.get("posted_date") else ""
    loc = job.get("loc", "bxl")
    loc_cls = "tag tloc" + (" tremote" if loc == "remote" else "")
    type_cls = "tag ttype" + (" tmed" if is_med else "")
    fit_cls = "fit fitmed" if is_med else "fit"
    return f"""<div class="{card_cls}" id="j{idx}" data-type="{t}" data-stars="{n}">
  <button class="dismiss" title="Masquer">×</button>
  <div class="ctitle">{job['title']}</div>
  <div class="cco">{job['company']} <span class="stars">{stars}</span></div>
  <div class="cdesc">{job.get('company_desc') or ''}</div>
  <div class="tags">
    {new_tag}<span class="{type_cls}">{_TYPE_LABELS.get(t, t)}</span>
    <span class="tag tsect">{job.get('sector', '')}</span>
    <span class="{loc_cls}">{loc}</span>{date_tag}
  </div>
  <div class="{fit_cls}"><b>Pourquoi toi :</b> {job['fit']}</div>
  <a class="cta" href="{job['url']}" target="_blank" rel="noopener">Voir l'offre →</a>
</div>"""


def _build_pages_html(jobs: list, top_insight: str, date_str: str) -> str:
    """Rapport interactif complet pour GitHub Pages."""
    sorted_jobs = sorted(jobs, key=lambda x: (0 if x.get("is_new") else 1, -x["stars"]))
    new_jobs  = [j for j in sorted_jobs if j.get("is_new")]
    med_jobs  = [j for j in sorted_jobs if j.get("type") == "med"]
    top_picks = [j for j in jobs if j["stars"] >= 4]

    # Stats
    stats = (
        f'<div class="stat"><div class="stat-n">{len(jobs)}</div><div class="stat-l">Offres</div></div>'
        f'<div class="stat"><div class="stat-n">{len(top_picks)}</div><div class="stat-l">Top picks ★★★★+</div></div>'
        f'<div class="stat hot"><div class="stat-n">{len(new_jobs)}</div><div class="stat-l">🔥 Nouvelles 48h</div></div>'
    )

    # Nouveautés
    if new_jobs:
        nc = "".join(_card_html(j, 9000 + i) for i, j in enumerate(new_jobs))
        new_section = (
            f'<div class="new-wrap">'
            f'<div class="new-hdr"><span style="font-size:18px">🔥</span>'
            f'<span style="font-size:13.5px;font-weight:700;flex:1">Nouveautés du jour</span>'
            f'<span class="new-badge">{len(new_jobs)}</span><span class="chevron">▼</span></div>'
            f'<div class="new-body"><div class="cards-grid">{nc}</div></div></div>'
        )
    else:
        new_section = ""

    # Section médicale
    if med_jobs:
        mc = "".join(_card_html(j, 8000 + i) for i, j in enumerate(med_jobs))
        med_section = (
            f'<div class="med-wrap">'
            f'<div class="med-title">🏥 Secteur Santé <span class="sc">{len(med_jobs)}</span></div>'
            f'<div class="cards-grid">{mc}</div></div>'
        )
    else:
        med_section = ""

    # All cards
    all_cards = "".join(_card_html(j, i) for i, j in enumerate(sorted_jobs))

    # Filter buttons
    types_present = sorted(set(j.get("type", "mgr") for j in jobs))
    type_btns = '<button class="fbtn type-btn" data-type="">Tous</button>' + "".join(
        f'<button class="fbtn type-btn" data-type="{t}">{_TYPE_LABELS.get(t, t)}</button>'
        for t in types_present
    )

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Veille emploi — {date_str}</title>
<style>{_PAGES_CSS}</style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <h1>Veille emploi — {date_str}</h1>
    <p class="sub">Alexandre Le Clercq · Agent autonome Groq + Tavily</p>
    <div class="stats">{stats}</div>
  </div>
  <div class="insight"><b>💡 Insight du jour :</b> {top_insight}</div>
  <div class="filters">
    <div class="frow"><span class="flabel">Type</span>{type_btns}</div>
    <div class="frow"><span class="flabel">Stars</span>
      <button class="fbtn stars-btn" data-min="5">★★★★★ only</button>
      <button class="fbtn stars-btn" data-min="4">★★★★+</button>
      <button class="fbtn stars-btn" data-min="3">★★★+</button>
    </div>
  </div>
  {new_section}
  {med_section}
  <div class="section-title">💼 Toutes les offres <span class="sc">{len(jobs)}</span></div>
  <div class="cards-grid">{all_cards}</div>
  <div style="text-align:center;padding:24px 0;color:#aeaeb2;font-size:11px">
    Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')} · Groq + Tavily + Resend
  </div>
</div>
<script>{_PAGES_JS}</script>
</body></html>"""


def _build_email_html(jobs: list, top_insight: str, date_str: str) -> str:
    """Email compact : insight + nouveautés + top picks + lien vers rapport complet."""
    new_jobs  = [j for j in sorted(jobs, key=lambda x: -x["stars"]) if j.get("is_new")]
    top_picks = [j for j in sorted(jobs, key=lambda x: -x["stars"]) if x["stars"] >= 4]

    def _mini(job):
        n = job["stars"]; color = _STAR_COLORS.get(n, "#98989D")
        stars = "★" * n + "☆" * (5 - n)
        t = job.get("type", "mgr")
        is_med = t == "med"
        tbg  = "#D1ECF1" if is_med else "#FFF3CD"
        tcol = "#0C5460" if is_med else "#85600A"
        posted = f'<span style="color:#8e8e93;font-size:10px;margin-left:6px">📅 {job["posted_date"]}</span>' if job.get("posted_date") else ""
        return (
            f'<div style="background:#fff;border-radius:10px;padding:12px 14px;margin-bottom:8px;border-left:4px solid {color}">'
            f'<div style="font-size:13px;font-weight:700;margin-bottom:2px">{job["title"]} — {job["company"]}{posted}</div>'
            f'<div style="font-size:11px;color:#8e8e93;font-style:italic;margin-bottom:5px">{job.get("company_desc") or ""}</div>'
            f'<div style="margin-bottom:6px"><span style="background:{tbg};color:{tcol};border-radius:9px;padding:2px 7px;font-size:10.5px;font-weight:600;margin-right:5px">{_TYPE_LABELS.get(t, t)}</span>'
            f'<span style="color:#FFB800;font-size:11px">{stars}</span></div>'
            f'<div style="background:#F0FFF4;border-left:3px solid #30D158;border-radius:0 6px 6px 0;padding:7px 10px;font-size:12px;color:#1a7f37;margin-bottom:8px">{job["fit"]}</div>'
            f'<a href="{job["url"]}" style="background:#0071E3;color:#fff;text-decoration:none;padding:5px 12px;border-radius:13px;font-size:11.5px;font-weight:600">Voir →</a>'
            f'</div>'
        )

    new_section = ""
    if new_jobs:
        cards = "".join(_mini(j) for j in new_jobs[:5])
        new_section = (
            f'<div style="background:#FFF8E1;border-radius:12px;padding:14px 16px;margin-bottom:12px;border:1px solid #FFD600">'
            f'<div style="font-size:13.5px;font-weight:700;color:#8B6000;margin-bottom:10px">🔥 Nouveautés du jour ({len(new_jobs)})</div>'
            f'{cards}</div>'
        )

    other = [j for j in top_picks if not j.get("is_new")][:5]
    other_section = ""
    if other:
        cards = "".join(_mini(j) for j in other)
        other_section = (
            f'<div style="margin-bottom:12px">'
            f'<div style="font-size:13px;font-weight:700;margin-bottom:10px">⭐ Top picks</div>'
            f'{cards}</div>'
        )

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f5f5f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
<div style="max-width:600px;margin:0 auto;padding:20px 14px">
  <div style="background:#fff;border-radius:14px;padding:20px 24px;margin-bottom:12px;box-shadow:0 2px 10px rgba(0,0,0,.07)">
    <h1 style="font-size:20px;font-weight:700;margin:0 0 3px">Veille emploi — {date_str}</h1>
    <p style="color:#6e6e73;font-size:12px;margin:0 0 14px">Alexandre Le Clercq · Groq + Tavily</p>
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <div style="background:#f5f5f7;border-radius:9px;padding:9px 14px;text-align:center">
        <div style="font-size:20px;font-weight:700">{len(jobs)}</div>
        <div style="font-size:9.5px;color:#6e6e73;text-transform:uppercase;letter-spacing:.4px">offres</div>
      </div>
      <div style="background:#f5f5f7;border-radius:9px;padding:9px 14px;text-align:center">
        <div style="font-size:20px;font-weight:700">{len(top_picks)}</div>
        <div style="font-size:9.5px;color:#6e6e73;text-transform:uppercase;letter-spacing:.4px">top picks ★★★★+</div>
      </div>
      <div style="background:#FFF8E1;border:1px solid #FFD600;border-radius:9px;padding:9px 14px;text-align:center">
        <div style="font-size:20px;font-weight:700;color:#8B6000">{len(new_jobs)}</div>
        <div style="font-size:9.5px;color:#8B6000;text-transform:uppercase;letter-spacing:.4px">🔥 nouvelles 48h</div>
      </div>
    </div>
  </div>
  <div style="background:#EAF0FB;border-radius:10px;padding:12px 16px;margin-bottom:12px;font-size:12.5px;color:#1A4FBF;line-height:1.6">
    <b>💡 Insight du jour :</b> {top_insight}
  </div>
  {new_section}
  {other_section}
  <div style="text-align:center;margin:16px 0 10px">
    <a href="https://souslik37.github.io/veille-emploi/latest.html"
       style="display:inline-block;background:#1d1d1f;color:#fff;text-decoration:none;padding:12px 28px;border-radius:22px;font-size:13.5px;font-weight:600">
      📋 Voir le rapport complet →
    </a>
  </div>
  <div style="text-align:center;color:#aeaeb2;font-size:10.5px;padding-bottom:12px">
    Agent autonome · {datetime.now().strftime('%d/%m/%Y %H:%M')}
  </div>
</div></body></html>"""


# ─────────────────────────────────────────────────────────────────
#  IMPLÉMENTATIONS DES TOOLS
# ─────────────────────────────────────────────────────────────────

def search_jobs(query: str) -> dict:
    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": os.environ["TAVILY_API_KEY"],
                "query": query,
                "max_results": 3,
                "search_depth": "basic",
                "include_answer": False,
                "days": 30,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "query": query,
            "results": [
                {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")[:200]}
                for r in data.get("results", [])
            ],
        }
    except Exception as e:
        return {"query": query, "error": str(e), "results": []}


def send_report(jobs: list, top_insight: str) -> dict:
    date_str  = datetime.now().strftime("%d %B %Y")
    date_file = datetime.now().strftime("%Y%m%d")

    html_pages = _build_pages_html(jobs, top_insight, date_str)
    html_email = _build_email_html(jobs, top_insight, date_str)

    out_dir = os.environ.get("REPORT_DIR", "/tmp/veille_jobs")
    os.makedirs(out_dir, exist_ok=True)
    for fname in [f"rapport_{date_file}.html", "latest.html"]:
        with open(os.path.join(out_dir, fname), "w", encoding="utf-8") as f:
            f.write(html_pages)

    top3    = sorted(jobs, key=lambda x: -x["stars"])[:3]
    preview = " · ".join(f"{j['company']} ({j['stars']}★)" for j in top3)

    resp = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}", "Content-Type": "application/json"},
        json={
            "from":    os.environ.get("FROM_EMAIL", "Veille Emploi <onboarding@resend.dev>"),
            "to":      [os.environ.get("TO_EMAIL", "leclercq.alexandre@outlook.com")],
            "subject": f"Veille emploi {date_str} — {len(jobs)} offres · {preview}",
            "html":    html_email,
        },
        timeout=15,
    )
    return {"email_status": resp.status_code, "email_ok": resp.ok, "saved_to": out_dir}


# ─────────────────────────────────────────────────────────────────
#  SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = f"""Tu es un agent de veille emploi pour :

{PROFILE}

LANGUE OBLIGATOIRE : Tout le contenu généré doit être en FRANÇAIS sans exception.
Les champs `fit`, `top_insight`, `company_desc` et tous tes raisonnements doivent être en français,
même si l'offre originale est en anglais.

INSTRUCTIONS :
1. Appelle `search_jobs` pour CHACUNE des {len(SEARCH_QUERIES)} requêtes suivantes (dans l'ordre) :
{chr(10).join(f'   - "{q}"' for q in SEARCH_QUERIES)}

2. Après TOUTES les recherches, analyse et filtre :
   - EXCLURE : articles de blog, grandes entreprises (>500 pers.), banques, consultings classiques, néerlandais obligatoire, offres "no longer accepting applications"
   - EXCLURE les doublons
   - Ne retenir QUE les offres ★★★ minimum

3. FILTRE DE DATE — RÈGLE ABSOLUE :
   - Extrais la date si visible ("posted 2 days ago" → "il y a 2 jours", "Sep 26" → "26 sept.")
   - Offre > 30 jours → EXCLURE
   - Aucune date visible → EXCLURE (jamais inclure sans date confirmée)
   - `is_new` = true UNIQUEMENT si postée hier ou aujourd'hui (≤ 48h)

4. SCORING STARTUP :
   ★★★★★ Country Lead, associate/co-fondateur, Head of Sales, GTM Engineer chez startup IA/FinTech/SaaS belge
   ★★★★  Sales Manager, GTM Lead, RevOps, Sales Ops, Operational Lead chez scaleup B2B
   ★★★   BDM, AE senior, rôle intéressant mais secteur moins prioritaire

5. SCORING MÉDICAL (type="med") — pour les offres dans les hôpitaux, cliniques, healthtech :
   ★★★★★ Directeur des opérations, Chief of Staff, Head of Operations hôpital/clinique belge
   ★★★★  Business Analyst santé, Data Analyst healthcare, Chef de projet digital health
   ★★★   Coordinateur de projets, Analyste performance, rôle opérationnel healthtech
   → Le champ `fit` doit expliquer comment l'expérience SaaS B2B + AM + ops d'Alexandre se traduit dans ce contexte médical.
   → `sector` = "Santé" ou "HealthTech" ou "Digital Health"

6. Appelle `send_report` UNE SEULE FOIS avec la liste finale triée par note décroissante.
"""


# ─────────────────────────────────────────────────────────────────
#  AGENTIC LOOP (format OpenAI/Groq)
# ─────────────────────────────────────────────────────────────────

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

        msg_dict = {"role": "assistant", "content": message.content or ""}
        if message.tool_calls:
            msg_dict["tool_calls"] = [
                {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in message.tool_calls
            ]
        messages.append(msg_dict)

        if choice.finish_reason == "stop":
            print("\n✅  Agent terminé proprement.")
            break
        if choice.finish_reason != "tool_calls" or not message.tool_calls:
            print(f"⚠️  Stop inattendu : {choice.finish_reason}")
            break

        for tc in message.tool_calls:
            name = tc.function.name
            args = json.loads(tc.function.arguments)
            print(f"  🔧  {name}  args={list(args.keys())}")

            if name == "search_jobs":
                result = search_jobs(args["query"])
                print(f"       → {len(result.get('results', []))} résultats")
            elif name == "send_report":
                result = send_report(args["jobs"], args["top_insight"])
                print(f"  ✉️   Email envoyé — {result}")
            else:
                result = {"error": f"Outil inconnu : {name}"}

            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, ensure_ascii=False)})


if __name__ == "__main__":
    run_agent()
