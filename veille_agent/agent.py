#!/usr/bin/env python3
"""
Veille emploi autonome — agent IA pour Alexandre Le Clercq

Architecture :
  GitHub Actions (cron 8h UTC lun-ven)
      → agent.py
          → search_jobs()  via Tavily API  (30 requêtes)
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
Formation : Master universitaire (5 ans d'études supérieures).
Expérience clé : pilotage de comptes stratégiques, analytics & reporting (KPIs, dashboards), management d'équipes (8+ pers.), ops commerciales à l'échelle, coordination de projets complexes multi-parties prenantes, construction de processus.

Cherche par ordre de priorité :
1. Associate / bras droit / co-fondateur pour lancer une activité
2. GTM Engineer / RevOps / Sales Ops
3. Head of Sales / Sales senior / Country Lead
4. Manager commercial / BizDev senior
5. Rôle dans le secteur de la santé — PAS SEULEMENT SALES : opérations, data/analytics, coordination de projets, management intermédiaire, chargé de mission

PROFIL SANTÉ ÉLARGI — Alexandre n'est pas que "commercial". Son Master + expérience en pilotage de performance + management + coordination projets le qualifient pour :
- Chef de projet / coordinateur de projets dans un hôpital ou réseau de santé
- Business Analyst / Analyste de performance dans la santé
- Responsable opérations ou qualité dans une clinique / réseau
- Chargé de mission dans une fédération de santé, mutualité, INAMI, IRISCARE
- Rôle GTM / ops / commercial dans une healthtech, medtech, pharma
- Business Development ou Account Management dans le pharma / dispositifs médicaux

EXCLUS : grands groupes généralistes, banques, consultings classiques, postes nécessitant le néerlandais.
Localisation : Bruxelles, Wallonie, remote OK. Langues : français natif, anglais professionnel.
"""

# ─────────────────────────────────────────────────────────────────
#  REQUÊTES DE RECHERCHE (30 requêtes — sources diversifiées)
# ─────────────────────────────────────────────────────────────────

SEARCH_QUERIES = [
    # ── Startup / GTM / Sales — Indeed.be ─────────────────────
    'site:indeed.com "Head of Sales" OR "VP Sales" startup Belgium Bruxelles',
    'site:indeed.com "GTM Engineer" OR "Revenue Operations" OR "Sales Operations" startup Belgium',
    'site:indeed.com "Sales Manager" OR "Country Manager" startup scaleup Belgium',
    'site:indeed.com "Operations Lead" OR "Business Operations" OR "founding sales" Belgium startup',
    # ── Startup — References.be ────────────────────────────────
    'site:references.be "Head of Sales" OR "Sales Director" OR "GTM" startup Belgique',
    'site:references.be "RevOps" OR "Sales Operations" OR "Country Manager" startup Belgique',
    # ── Startup — Stepstone.be ─────────────────────────────────
    'site:stepstone.be "Head of Sales" OR "Sales Manager" OR "GTM Engineer" startup Belgique',
    'site:stepstone.be "Country Manager" OR "RevOps" OR "Operations Lead" startup Belgique',
    # ── Startup — Welcome to the Jungle ────────────────────────
    'site:welcometothejungle.com "GTM" OR "RevOps" OR "Sales Ops" belgique startup',
    'site:welcometothejungle.com "Sales Manager" OR "Country Manager" OR "associate" startup bruxelles',
    # ── Startup — LinkedIn (complémentaire) ─────────────────────
    'site:linkedin.com/jobs "GTM Engineer" OR "Head of Sales" OR "RevOps" Belgium startup 2026',
    'site:linkedin.com/jobs "founding sales" OR "associate" OR "bras droit" Belgium startup',
    # ── Startup — Requêtes larges ────────────────────────────────
    '"bras droit" fondateur commercial Belgique startup emploi',
    '"founding account executive" OR "founding sales" OR "GTM engineer" Belgium startup emploi',
    # ── Santé ── Indeed.be ──────────────────────────────────────
    'site:indeed.com "chef de projet" OR "coordinateur" hôpital OR clinique OR santé Belgique Bruxelles Wallonie',
    'site:indeed.com "business analyst" OR "analyste" OR "chargé de mission" santé OR healthcare OR mutualité Belgique',
    'site:indeed.com "responsable opérations" OR "directeur opérationnel" OR manager santé OR hôpital Belgique',
    'site:indeed.com healthtech OR medtech OR pharma "project manager" OR GTM OR "business development" Belgique Belgium',
    # ── Santé ── References.be ──────────────────────────────────
    'site:references.be "chef de projet" OR "coordinateur" OR "responsable" hôpital OR clinique OR santé Belgique',
    'site:references.be "business analyst" OR "analyste" OR "chargé de mission" santé OR healthcare Belgique',
    # ── Santé ── Stepstone.be ───────────────────────────────────
    'site:stepstone.be "chef de projet" OR "coordinateur" OR manager hôpital OR clinique OR santé Belgique',
    'site:stepstone.be "business analyst" OR "analyste" OR "responsable opérations" santé Belgique',
    # ── Santé ── LinkedIn ────────────────────────────────────────
    'site:linkedin.com/jobs "chef de projet" OR "coordinateur" hôpital CHIREC OR "Saint-Luc" OR Erasme OR CHU Bruxelles',
    'site:linkedin.com/jobs INAMI OR IRISCARE OR mutualité "chargé de mission" OR coordinateur OR analyste',
    'site:linkedin.com/jobs pharma OR healthtech OR medtech "project manager" OR "business development" Belgium 2026',
    # ── Santé ── Medination ──────────────────────────────────────
    'site:medination.com manager OR coordinateur OR "chef de projet" OR analyste OR directeur',
    'site:medination.com responsable OR "chargé de mission" OR "business developer" OR "account manager"',
    # ── Santé ── Requêtes larges ─────────────────────────────────
    '"chef de projet santé" OR "coordinateur santé" OR "analyste santé" Belgique Bruxelles Wallonie emploi',
    '"chargé de mission" OR "responsable opérations" hôpital OR mutualité OR INAMI Bruxelles Wallonie emploi',
    '"business analyst" OR "analyste performance" santé OR healthcare OR soins Belgique emploi 2026',
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
                                "salary":       {"type": ["string", "null"]},
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
#  CSS pour le rapport GitHub Pages — design 4 sections + shortlist
# ─────────────────────────────────────────────────────────────────

_PAGES_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f5f7;color:#1d1d1f;min-height:100vh}
.wrap{max-width:960px;margin:0 auto;padding:24px 16px 80px}
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
/* Filtres */
.filters{background:#fff;border-radius:14px;padding:14px 18px;margin-bottom:14px;box-shadow:0 2px 8px rgba(0,0,0,.06)}
.frow{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:8px}
.frow:last-child{margin-bottom:0}
.flabel{font-size:10px;font-weight:700;color:#8e8e93;text-transform:uppercase;letter-spacing:.6px;min-width:44px}
.fbtn{background:#f5f5f7;border:1.5px solid transparent;border-radius:18px;padding:4px 11px;font-size:11.5px;color:#3c3c43;cursor:pointer;transition:all .15s;font-family:inherit}
.fbtn:hover{background:#e5e5ea}
.fbtn.active{background:#1d1d1f;color:#fff;border-color:#1d1d1f}
.filter-footer{display:flex;align-items:center;margin-top:6px;gap:10px}
.filter-count{font-size:12px;color:#8e8e93;flex:1}
.reset-btn{background:none;border:1.5px solid #e5e5ea;border-radius:18px;padding:4px 11px;font-size:11px;color:#8e8e93;cursor:pointer;font-family:inherit}
.reset-btn:hover{border-color:#1d1d1f;color:#1d1d1f}
/* Sections */
.section-hdr{display:flex;align-items:center;gap:8px;margin:20px 0 10px;padding:0 2px}
.section-hdr h2{font-size:15px;font-weight:700;margin:0}
.sec-badge{background:#e5e5ea;color:#8e8e93;border-radius:9px;padding:2px 8px;font-size:11px;font-weight:600;min-width:24px;text-align:center}
.sec-badge.gold{background:#FFF3CD;color:#85600A}
.sec-badge.green{background:#D4EDDA;color:#1A7F45}
.sec-badge.blue{background:#D1ECF1;color:#0C5460}
/* Nouveautés collapsible */
.new-wrap{background:#fff;border-radius:14px;margin-bottom:14px;box-shadow:0 2px 8px rgba(0,0,0,.06);overflow:hidden}
.new-hdr{display:flex;align-items:center;gap:8px;padding:13px 18px;cursor:pointer;user-select:none}
.new-hdr:hover{background:#fafafa}
.new-badge{font-size:11px;border-radius:18px;padding:2px 9px;background:#FFF0EE;color:#CC2200;font-weight:700}
.chevron{color:#aeaeb2;font-size:11px;margin-left:auto;transition:transform .2s}
.new-body{padding:0 14px 14px}
.new-body.collapsed{display:none}
/* Cards */
.cards-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(400px,1fr));gap:10px}
.card{position:relative;background:#fff;border-radius:13px;padding:15px 17px;box-shadow:0 2px 8px rgba(0,0,0,.06);border-left:4px solid #e5e5e7;transition:box-shadow .15s,opacity .2s,transform .2s}
.card:hover{box-shadow:0 4px 16px rgba(0,0,0,.1)}
.card.s5{border-left-color:#FFB800}
.card.s4{border-left-color:#30D158}
.card.s3{border-left-color:#32ADE6}
.card.s2,.card.s1{border-left-color:#aeaeb2}
.card.hidden,.card.dismissed{display:none!important}
@keyframes dismissOut{from{opacity:1;transform:scale(1)}to{opacity:0;transform:scale(.95)}}
.card.dismissing{animation:dismissOut .2s ease forwards}
.card.saved .save-btn{color:#FF3B30!important}
/* Card internals */
.dismiss{position:absolute;top:8px;right:10px;width:22px;height:22px;background:none;border:none;color:#d1d1d6;font-size:15px;cursor:pointer;border-radius:50%;display:flex;align-items:center;justify-content:center;opacity:0;transition:opacity .15s,color .15s}
.card:hover .dismiss{opacity:1}.dismiss:hover{color:#FF3B30}
.ctitle{font-size:14.5px;font-weight:650;margin-bottom:2px;padding-right:24px;line-height:1.3}
.cco{font-size:13px;font-weight:600;color:#3c3c43;margin-bottom:1px}
.cdesc{font-size:11.5px;color:#8e8e93;font-style:italic;margin-bottom:8px;line-height:1.4}
.tags{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:8px}
.tag{border-radius:14px;padding:2px 8px;font-size:11px;font-weight:500}
.tag.tnew{background:#FF3B30;color:#fff;font-weight:700}
.tag.ttype{background:#FFF3CD;color:#85600A}
.tag.ttype.tmed{background:#D1ECF1;color:#0C5460}
.tag.tsect{background:#EAF0FB;color:#1A4FBF}
.tag.tloc{background:#E8FAF0;color:#1A7F45}
.tag.tloc.tremote{background:#F0EDFF;color:#5038D0}
.tag.tdate{background:#F5F5F7;color:#6e6e73}
.stars{font-size:11.5px;color:#FFB800;letter-spacing:1px}
.fit{background:#F0FFF4;border-radius:8px;padding:8px 10px;font-size:12.5px;color:#1a7f37;margin:7px 0 0;line-height:1.5}
.fit.fitmed{background:#E8F8FB;color:#0C5460}
.card-footer{display:flex;align-items:center;justify-content:space-between;margin-top:10px}
.cta{display:inline-block;background:#0071E3;color:#fff;text-decoration:none;padding:6px 14px;border-radius:18px;font-size:12px;font-weight:600}
.cta:hover{background:#005BBB}
.save-btn{background:none;border:none;font-size:18px;color:#d1d1d6;cursor:pointer;padding:4px 8px;line-height:1;transition:color .15s}
.save-btn:hover{color:#FF3B30}
/* Restore bar */
#restore-bar{display:none;position:fixed;bottom:0;left:0;right:0;background:#1d1d1f;color:#fff;padding:12px 20px;z-index:100;align-items:center;gap:12px;justify-content:center}
.restore-count{font-size:13px}
#restore-all{background:#fff;color:#1d1d1f;border:none;border-radius:18px;padding:5px 14px;font-size:12px;font-weight:700;cursor:pointer}
/* Shortlist FAB */
#shortlist-fab{position:fixed;bottom:28px;right:24px;background:#1d1d1f;color:#fff;border:none;border-radius:50px;padding:12px 20px;font-size:13px;font-weight:700;cursor:pointer;display:flex;align-items:center;gap:8px;box-shadow:0 4px 20px rgba(0,0,0,.3);z-index:200;transition:background .15s,transform .1s}
#shortlist-fab:hover{transform:scale(1.05)}
#shortlist-fab.fab-active{background:#FF3B30}
.fab-count{background:rgba(255,255,255,.25);border-radius:20px;padding:1px 7px;font-size:11px}
/* Shortlist drawer */
#shortlist-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:300}
#shortlist-drawer{position:fixed;bottom:0;right:0;width:380px;max-width:100vw;background:#fff;border-radius:18px 18px 0 0;box-shadow:0 -4px 30px rgba(0,0,0,.2);z-index:400;transform:translateY(100%);transition:transform .3s ease;max-height:70vh;display:flex;flex-direction:column}
#shortlist-drawer.open{transform:translateY(0)}
.drawer-header{padding:16px 20px;border-bottom:1px solid #e5e5ea;display:flex;align-items:center;justify-content:space-between}
.drawer-title{font-size:15px;font-weight:700}
.drawer-close{background:none;border:none;font-size:18px;cursor:pointer;color:#8e8e93;padding:4px 8px;line-height:1}
#shortlist-list{overflow-y:auto;padding:12px 16px;flex:1}
.sl-empty{color:#8e8e93;font-size:13px;text-align:center;padding:20px 0}
.sl-item{display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid #f5f5f7}
.sl-info{flex:1;min-width:0}
.sl-title{font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sl-co{font-size:11.5px;color:#8e8e93}
.sl-link{color:#0071E3;font-size:12px;font-weight:600;text-decoration:none;white-space:nowrap;margin-right:4px}
.sl-rm{background:none;border:none;color:#d1d1d6;cursor:pointer;font-size:14px;padding:4px;line-height:1}
.sl-rm:hover{color:#FF3B30}
@media(max-width:600px){.cards-grid{grid-template-columns:1fr}.wrap{padding:16px 10px 80px}#shortlist-fab{bottom:70px}}
"""

_PAGES_JS = """
(function() {
  const active = {type: null, sector: null, loc: null};
  let minStars = 0;
  const dismissed = [];
  const shortlistArr = [];

  function applyFilters() {
    document.querySelectorAll('.card:not(.dismissed)').forEach(card => {
      const ok = (!active.type   || card.dataset.type   === active.type)
              && (!active.sector || card.dataset.sector === active.sector)
              && (!active.loc    || card.dataset.loc    === active.loc)
              && parseInt(card.dataset.stars||'0') >= minStars;
      card.classList.toggle('hidden', !ok);
    });
    ['top','good','explore','watch'].forEach(s => {
      const grid = document.getElementById('sec-' + s);
      const hdr  = document.getElementById('hdr-' + s);
      if (!grid || !hdr) return;
      const vis = grid.querySelectorAll('.card:not(.dismissed):not(.hidden)').length;
      const tot = grid.querySelectorAll('.card').length;
      const badge = document.getElementById('c-' + s);
      if (badge) badge.textContent = vis + '/' + tot;
      const anyVis = vis > 0;
      hdr.style.display  = anyVis ? '' : 'none';
      grid.style.display = anyVis ? '' : 'none';
    });
    const allVis = document.querySelectorAll('.card:not(.dismissed):not(.hidden)').length;
    const el = document.getElementById('stat-total');
    if (el) el.textContent = allVis;
    const fc = document.querySelector('.filter-count');
    if (fc) fc.textContent = allVis + ' offres affichées';
  }

  // Filter buttons use data-g / data-v
  document.querySelectorAll('[data-g]').forEach(btn => {
    btn.addEventListener('click', () => {
      const g = btn.dataset.g, v = btn.dataset.v;
      if (active[g] === v) {
        active[g] = null;
        btn.classList.remove('active');
      } else {
        active[g] = v;
        document.querySelectorAll('[data-g="'+g+'"]').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
      }
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
  const resetBtn = document.querySelector('.reset-btn');
  if (resetBtn) resetBtn.addEventListener('click', () => {
    Object.keys(active).forEach(k => active[k] = null);
    minStars = 0;
    document.querySelectorAll('[data-g],.stars-btn').forEach(b => b.classList.remove('active'));
    applyFilters();
  });

  // Dismiss
  function dismissCard(card) {
    card.classList.add('dismissing');
    setTimeout(() => {
      card.classList.remove('dismissing');
      card.classList.add('dismissed');
      dismissed.push(card.id);
      renderRestoreBar();
      applyFilters();
    }, 200);
  }
  document.querySelectorAll('.dismiss').forEach(btn => {
    btn.addEventListener('click', () => dismissCard(btn.closest('.card')));
  });
  function renderRestoreBar() {
    const bar = document.getElementById('restore-bar');
    if (!bar) return;
    if (dismissed.length === 0) { bar.style.display = 'none'; return; }
    bar.style.display = 'flex';
    bar.querySelector('.restore-count').textContent = dismissed.length + ' offre(s) masquée(s)';
  }
  const restoreAllBtn = document.getElementById('restore-all');
  if (restoreAllBtn) restoreAllBtn.addEventListener('click', () => {
    dismissed.length = 0;
    document.querySelectorAll('.card.dismissed').forEach(c => c.classList.remove('dismissed'));
    renderRestoreBar();
    applyFilters();
  });

  // Shortlist
  function toggleSave(card) {
    const id = card.id;
    const idx = shortlistArr.indexOf(id);
    if (idx >= 0) { shortlistArr.splice(idx,1); card.classList.remove('saved'); }
    else { shortlistArr.push(id); card.classList.add('saved'); }
    updateFab(); renderDrawer();
  }
  document.querySelectorAll('.save-btn').forEach(btn => {
    btn.addEventListener('click', () => toggleSave(btn.closest('.card')));
  });
  function updateFab() {
    const fab = document.getElementById('shortlist-fab');
    if (!fab) return;
    fab.querySelector('.fab-count').textContent = shortlistArr.length;
    fab.classList.toggle('fab-active', shortlistArr.length > 0);
  }
  function renderDrawer() {
    const list = document.getElementById('shortlist-list');
    if (!list) return;
    if (shortlistArr.length === 0) {
      list.innerHTML = '<p class="sl-empty">Aucun poste dans la shortlist</p>'; return;
    }
    list.innerHTML = shortlistArr.map(id => {
      const c = document.getElementById(id); if (!c) return '';
      const title = c.querySelector('.ctitle')?.textContent || '';
      const co    = c.querySelector('.cco')?.textContent?.trim() || '';
      const url   = c.querySelector('.cta')?.href || '#';
      return '<div class="sl-item" data-cid="'+id+'"><div class="sl-info"><div class="sl-title">'+title+'</div><div class="sl-co">'+co+'</div></div>'
           + '<a class="sl-link" href="'+url+'" target="_blank">Voir →</a>'
           + '<button class="sl-rm">✕</button></div>';
    }).join('');
    list.querySelectorAll('.sl-rm').forEach(btn => {
      btn.addEventListener('click', () => {
        const id = btn.closest('.sl-item').dataset.cid;
        const card = document.getElementById(id);
        if (card) card.querySelector('.save-btn').click();
      });
    });
  }
  const fab = document.getElementById('shortlist-fab');
  const overlay = document.getElementById('shortlist-overlay');
  const drawer  = document.getElementById('shortlist-drawer');
  if (fab) fab.addEventListener('click', () => {
    if (overlay) overlay.style.display = 'block';
    if (drawer) drawer.classList.add('open');
    renderDrawer();
  });
  function closeDrawer() {
    if (overlay) overlay.style.display = 'none';
    if (drawer) drawer.classList.remove('open');
  }
  if (overlay) overlay.addEventListener('click', closeDrawer);
  document.querySelector('.drawer-close')?.addEventListener('click', closeDrawer);

  // Nouveautés collapse
  const nh = document.querySelector('.new-hdr');
  if (nh) nh.addEventListener('click', () => {
    const nb = document.getElementById('new-body');
    const open = !nb.classList.contains('collapsed');
    nb.classList.toggle('collapsed', open);
    nh.querySelector('.chevron').textContent = open ? '▶' : '▼';
  });

  applyFilters();
})();
"""

_SANTE_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#EAF7FA;color:#1d1d1f;min-height:100vh}
.wrap{max-width:980px;margin:0 auto;padding:24px 16px}
.header{background:#fff;border-radius:18px;padding:24px 28px;margin-bottom:14px;box-shadow:0 2px 12px rgba(0,0,0,.07);border-top:4px solid #0AA5B8}
.header h1{font-size:22px;font-weight:700;margin-bottom:4px}
.header .sub{color:#6e6e73;font-size:12.5px;margin-bottom:16px}
.profile-box{background:#EAF7FA;border-radius:10px;padding:12px 16px;margin-bottom:16px;font-size:12.5px;color:#0C5460;line-height:1.6;border-left:3px solid #0AA5B8}
.stats{display:flex;gap:8px;flex-wrap:wrap}
.stat{background:#f5f5f7;border-radius:10px;padding:10px 16px;text-align:center;min-width:76px}
.stat-n{font-size:22px;font-weight:700}
.stat-l{font-size:10px;color:#6e6e73;margin-top:2px;text-transform:uppercase;letter-spacing:.4px}
.stat.teal{background:#D1ECF1;border:1px solid #0AA5B8}
.stat.teal .stat-n,.stat.teal .stat-l{color:#0C5460}
.back-link{display:inline-flex;align-items:center;gap:6px;color:#0AA5B8;font-size:13px;font-weight:600;text-decoration:none;margin-bottom:14px}
.back-link:hover{color:#0C5460}
.filters{background:#fff;border-radius:14px;padding:14px 18px;margin-bottom:14px;box-shadow:0 2px 8px rgba(0,0,0,.06)}
.frow{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:8px}
.frow:last-child{margin-bottom:0}
.flabel{font-size:10px;font-weight:700;color:#8e8e93;text-transform:uppercase;letter-spacing:.6px;margin-right:2px;min-width:36px}
.fbtn{background:#f5f5f7;border:1.5px solid transparent;border-radius:18px;padding:4px 11px;font-size:11.5px;color:#3c3c43;cursor:pointer;transition:all .15s;font-family:inherit}
.fbtn:hover{background:#e5e5ea}
.fbtn.active{background:#0AA5B8;color:#fff;border-color:#0AA5B8}
.section-title{font-size:14px;font-weight:600;margin:16px 0 8px;display:flex;align-items:center;gap:7px}
.sc{font-size:11px;font-weight:500;color:#8e8e93;background:#e5e5ea;border-radius:9px;padding:1px 7px}
.cards-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(430px,1fr));gap:12px}
.card{position:relative;background:#fff;border-radius:14px;padding:16px 18px;box-shadow:0 2px 8px rgba(0,0,0,.06);border-left:4px solid #0AA5B8;transition:box-shadow .15s,opacity .2s,transform .2s}
.card:hover{box-shadow:0 4px 18px rgba(0,0,0,.11)}
.card.s5{border-left-color:#FFB800}.card.s4{border-left-color:#30D158}.card.s3{border-left-color:#32ADE6}
.dismiss{position:absolute;top:8px;right:10px;width:22px;height:22px;background:none;border:none;color:#d1d1d6;font-size:15px;cursor:pointer;border-radius:50%;display:flex;align-items:center;justify-content:center;opacity:0;transition:opacity .15s,color .15s}
.card:hover .dismiss{opacity:1}.dismiss:hover{color:#FF3B30}
.ctitle{font-size:15px;font-weight:700;margin-bottom:3px;padding-right:24px;line-height:1.3}
.cco{font-size:13px;font-weight:600;color:#3c3c43;margin-bottom:1px}
.cdesc{font-size:11.5px;color:#8e8e93;font-style:italic;margin-bottom:8px;line-height:1.4}
.stars{font-size:11.5px;color:#FFB800;letter-spacing:1px}
.salary-row{display:flex;align-items:center;gap:6px;margin-bottom:8px}
.salary-badge{background:#E8F8FB;color:#0C5460;border:1px solid #0AA5B8;border-radius:8px;padding:4px 10px;font-size:12px;font-weight:600}
.salary-badge.estimated{background:#F5F5F7;color:#6e6e73;border-color:#e5e5e7;font-weight:500}
.tags{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:8px}
.tag{border-radius:14px;padding:2px 8px;font-size:11px;font-weight:500}
.tag.tnew{background:#FF3B30;color:#fff;font-weight:700}
.tag.tsect{background:#D1ECF1;color:#0C5460}
.tag.tloc{background:#E8FAF0;color:#1A7F45}
.tag.tloc.tremote{background:#F0EDFF;color:#5038D0}
.tag.tdate{background:#F5F5F7;color:#6e6e73}
.fit{background:#E8F8FB;border-left:3px solid #0AA5B8;border-radius:0 8px 8px 0;padding:9px 12px;font-size:12.5px;color:#0C5460;margin:7px 0 10px;line-height:1.5}
.fit b{font-weight:700}
.cta{display:inline-block;background:#0AA5B8;color:#fff;text-decoration:none;padding:7px 16px;border-radius:18px;font-size:12px;font-weight:600}
.cta:hover{background:#0C5460}
.card.hidden,.card.dismissed{display:none!important}
@media(max-width:600px){.cards-grid{grid-template-columns:1fr}.wrap{padding:16px 10px}}
"""

_SANTE_JS = """
(function() {
  const dismissed = new Set(JSON.parse(sessionStorage.getItem('vs') || '[]'));
  dismissed.forEach(id => { const el = document.getElementById(id); if(el) el.style.display='none'; });

  let activeSect = null, minStars = 0;
  function applyFilters() {
    document.querySelectorAll('.card').forEach(card => {
      if(dismissed.has(card.id)) return;
      const ok = (!activeSect || card.dataset.sect === activeSect) && parseInt(card.dataset.stars) >= minStars;
      card.classList.toggle('hidden', !ok);
    });
  }
  document.querySelectorAll('.sect-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      activeSect = (activeSect === btn.dataset.sect) ? null : btn.dataset.sect;
      document.querySelectorAll('.sect-btn').forEach(b => b.classList.toggle('active', b.dataset.sect === activeSect));
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
      dismissed.add(card.id); sessionStorage.setItem('vs', JSON.stringify([...dismissed]));
    });
  });
})();
"""

# ─────────────────────────────────────────────────────────────────
#  HELPERS HTML
# ─────────────────────────────────────────────────────────────────

_TYPE_LABELS = {
    "mgr": "Manager", "ae": "Account Exec", "bd": "BizDev",
    "gtm": "GTM/RevOps", "ops": "Ops", "ass": "Associate", "med": "🏥 Santé"
}
_STAR_COLORS = {5: "#FFB800", 4: "#30D158", 3: "#32ADE6", 2: "#98989D", 1: "#98989D"}
_LOC_LABELS  = {"bxl": "📍 Bruxelles", "be": "🇧🇪 Belgique", "remote": "🌐 Remote"}


def _card_html(job: dict, idx: int) -> str:
    n    = job["stars"]
    stars = "★" * n + "☆" * (5 - n)
    t    = job.get("type", "mgr")
    loc  = job.get("loc", "bxl")
    sect = job.get("sector", "")

    card_cls  = f"card s{n}"
    new_tag   = '<span class="tag tnew">🆕 NOUVEAU</span>' if job.get("is_new") else ""
    date_tag  = f'<span class="tag tdate">📅 {job["posted_date"]}</span>' if job.get("posted_date") else ""
    fd = job.get("found_date")
    found_tag = f'<span class="tag tdate">🗂️ {fd}</span>' if fd and not job.get("is_new") else ""
    loc_cls   = "tag tloc" + (" tremote" if loc == "remote" else "")
    type_cls  = "tag ttype" + (" tmed" if t == "med" else "")
    fit_cls   = "fit fitmed" if t == "med" else "fit"

    return f"""<div class="{card_cls}" id="j{idx}" data-type="{t}" data-stars="{n}" data-loc="{loc}" data-sector="{sect}">
  <button class="dismiss" title="Masquer">×</button>
  <div class="ctitle">{job['title']}</div>
  <div class="cco">{job['company']}</div>
  <div class="cdesc">{job.get('company_desc') or ''}</div>
  <div class="tags">
    {new_tag}<span class="{type_cls}">{_TYPE_LABELS.get(t, t)}</span>
    <span class="tag tsect">{sect}</span>
    <span class="{loc_cls}">{_LOC_LABELS.get(loc, loc)}</span>{date_tag}{found_tag}
    <span class="stars">{stars}</span>
  </div>
  <div class="{fit_cls}"><b>Pourquoi toi :</b> {job['fit']}</div>
  <div class="card-footer">
    <a class="cta" href="{job['url']}" target="_blank" rel="noopener">Voir l'offre →</a>
    <button class="save-btn" title="Shortlist">♡</button>
  </div>
</div>"""


def _build_pages_html(jobs: list, top_insight: str, date_str: str) -> str:
    """Rapport interactif complet — 4 sections par tier + shortlist + dismiss."""
    sorted_jobs = sorted(jobs, key=lambda x: (0 if x.get("is_new") else 1, -x["stars"]))
    new_jobs  = [j for j in sorted_jobs if j.get("is_new")]
    tier5  = [j for j in sorted_jobs if j["stars"] == 5]
    tier4  = [j for j in sorted_jobs if j["stars"] == 4]
    tier3  = [j for j in sorted_jobs if j["stars"] == 3]
    tier12 = [j for j in sorted_jobs if j["stars"] <= 2]
    top_picks = [j for j in sorted_jobs if j["stars"] >= 4]

    # Stats
    stats = (
        f'<div class="stat"><div class="stat-n" id="stat-total">{len(jobs)}</div>'
        f'<div class="stat-l">Total accumulées</div></div>'
        f'<div class="stat"><div class="stat-n">{len(top_picks)}</div>'
        f'<div class="stat-l">Top picks ★★★★+</div></div>'
        f'<div class="stat hot"><div class="stat-n">{len(new_jobs)}</div>'
        f'<div class="stat-l">🔥 Nouvelles aujourd\'hui</div></div>'
    )

    # Nouveautés collapsible
    new_section = ""
    if new_jobs:
        nc = "".join(_card_html(j, 9000 + i) for i, j in enumerate(new_jobs))
        new_section = (
            f'<div class="new-wrap">'
            f'<div class="new-hdr"><span style="font-size:18px">🔥</span>'
            f'<span style="font-size:13.5px;font-weight:700;flex:1">Nouveautés du jour</span>'
            f'<span class="new-badge">{len(new_jobs)}</span>'
            f'<span class="chevron">▼</span></div>'
            f'<div class="new-body" id="new-body"><div class="cards-grid">{nc}</div></div>'
            f'</div>'
        )

    # Filtres — utilise data-g / data-v
    types_present = sorted(set(j.get("type", "mgr") for j in jobs))
    type_btns = "".join(
        f'<button class="fbtn" data-g="type" data-v="{t}">{_TYPE_LABELS.get(t, t)}</button>'
        for t in types_present
    )
    locs_present = sorted(set(j.get("loc", "bxl") for j in jobs))
    loc_btns = "".join(
        f'<button class="fbtn" data-g="loc" data-v="{l}">{_LOC_LABELS.get(l, l)}</button>'
        for l in locs_present
    )
    sects_present = sorted(set(j.get("sector", "") for j in jobs if j.get("sector")))
    sect_btns = "".join(
        f'<button class="fbtn" data-g="sector" data-v="{s}">{s}</button>'
        for s in sects_present
    )

    # Helper pour les sections
    def section_html(title: str, icon: str, tier_jobs: list, sec_id: str,
                     badge_cls: str, idx_offset: int) -> str:
        if not tier_jobs:
            return ""
        cards = "".join(_card_html(j, idx_offset + i) for i, j in enumerate(tier_jobs))
        return (
            f'<div class="section-hdr" id="hdr-{sec_id}">'
            f'<span style="font-size:20px">{icon}</span>'
            f'<h2>{title}</h2>'
            f'<span class="sec-badge {badge_cls}" id="c-{sec_id}">{len(tier_jobs)}/{len(tier_jobs)}</span>'
            f'</div>'
            f'<div class="cards-grid" id="sec-{sec_id}">{cards}</div>'
        )

    top_section     = section_html("Top picks",         "⭐", tier5,  "top",     "gold",  0)
    good_section    = section_html("Très intéressantes","👍", tier4,  "good",    "green", 100)
    explore_section = section_html("À explorer",        "📋", tier3,  "explore", "blue",  200)
    watch_section   = section_html("À surveiller",      "👀", tier12, "watch",   "",      300)

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
    <p class="sub">Alexandre Le Clercq · Agent autonome Groq + Tavily · <a href="./sante.html" style="color:#0AA5B8;font-weight:600">🏥 Page Santé →</a></p>
    <div class="stats">{stats}</div>
  </div>
  <div class="insight"><b>💡 Insight du jour :</b> {top_insight}</div>
  <div class="filters">
    <div class="frow"><span class="flabel">Type</span>{type_btns}</div>
    <div class="frow"><span class="flabel">Lieu</span>{loc_btns}</div>
    <div class="frow"><span class="flabel">Secteur</span>{sect_btns}</div>
    <div class="frow"><span class="flabel">Stars</span>
      <button class="fbtn stars-btn" data-min="5">★★★★★</button>
      <button class="fbtn stars-btn" data-min="4">★★★★+</button>
      <button class="fbtn stars-btn" data-min="3">★★★+</button>
    </div>
    <div class="filter-footer">
      <span class="filter-count">{len(jobs)} offres affichées</span>
      <button class="reset-btn">Réinitialiser</button>
    </div>
  </div>
  {new_section}
  {top_section}
  {good_section}
  {explore_section}
  {watch_section}
  <div style="text-align:center;padding:32px 0 16px;color:#aeaeb2;font-size:11px">
    Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')} · Groq + Tavily + Resend
  </div>
</div>

<!-- Restore bar -->
<div id="restore-bar">
  <span class="restore-count"></span>
  <button id="restore-all">Restaurer tout</button>
</div>

<!-- Shortlist FAB -->
<button id="shortlist-fab">♡ Shortlist <span class="fab-count">0</span></button>

<!-- Shortlist overlay + drawer -->
<div id="shortlist-overlay"></div>
<div id="shortlist-drawer">
  <div class="drawer-header">
    <span class="drawer-title">♡ Ma shortlist</span>
    <button class="drawer-close">✕</button>
  </div>
  <div id="shortlist-list"><p class="sl-empty">Aucun poste dans la shortlist</p></div>
</div>

<script>{_PAGES_JS}</script>
</body></html>"""


def _build_email_html(jobs: list, top_insight: str, date_str: str) -> str:
    """Email compact : insight + nouveautés + top picks + lien vers rapport complet."""
    new_jobs  = [j for j in sorted(jobs, key=lambda x: -x["stars"]) if j.get("is_new")]
    top_picks = [j for j in sorted(jobs, key=lambda x: -x["stars"]) if j["stars"] >= 4]

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
        <div style="font-size:9.5px;color:#8B6000;text-transform:uppercase;letter-spacing:.4px">🔥 nouvelles aujourd'hui</div>
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


def _build_sante_html(jobs: list, date_str: str) -> str:
    """Page dédiée secteur santé — large, avec salaire et matching profil complet."""
    sorted_jobs = sorted(jobs, key=lambda x: (0 if x.get("is_new") else 1, -x["stars"]))
    top_picks = [j for j in sorted_jobs if j["stars"] >= 4]

    stats = (
        f'<div class="stat teal"><div class="stat-n">{len(jobs)}</div><div class="stat-l">Opportunités</div></div>'
        f'<div class="stat teal"><div class="stat-n">{len(top_picks)}</div><div class="stat-l">Top picks ★★★★+</div></div>'
        f'<div class="stat"><div class="stat-n">{len([j for j in jobs if j.get("salary")])}</div><div class="stat-l">Avec salaire</div></div>'
    )

    sectors = sorted(set(j.get("sector", "Santé") for j in jobs))
    sect_btns = "".join(
        f'<button class="fbtn sect-btn" data-sect="{s}">{s}</button>' for s in sectors
    )

    def _scard(job: dict, idx: int) -> str:
        n = job["stars"]
        stars = "★" * n + "☆" * (5 - n)
        loc = job.get("loc", "bxl")
        loc_cls = "tag tloc" + (" tremote" if loc == "remote" else "")
        new_tag = '<span class="tag tnew">🆕 NOUVEAU</span>' if job.get("is_new") else ""
        date_tag = f'<span class="tag tdate">📅 {job["posted_date"]}</span>' if job.get("posted_date") else ""
        sect = job.get("sector", "Santé")

        sal = job.get("salary")
        if sal:
            is_est = "estimation" in sal.lower() or "ific" in sal.lower()
            sal_cls = "salary-badge estimated" if is_est else "salary-badge"
            salary_row = f'<div class="salary-row"><span class="{sal_cls}">💶 {sal}</span></div>'
        else:
            salary_row = '<div class="salary-row"><span class="salary-badge estimated">💶 Salaire non communiqué</span></div>'

        return f"""<div class="card s{n}" id="s{idx}" data-stars="{n}" data-sect="{sect}">
  <button class="dismiss" title="Masquer">×</button>
  <div class="ctitle">{job['title']}</div>
  <div class="cco">{job['company']} <span class="stars">{stars}</span></div>
  <div class="cdesc">{job.get('company_desc') or ''}</div>
  {salary_row}
  <div class="tags">
    {new_tag}<span class="tag tsect">{sect}</span>
    <span class="{loc_cls}">{_LOC_LABELS.get(loc, loc)}</span>{date_tag}
  </div>
  <div class="fit"><b>Pourquoi ton profil matche :</b> {job['fit']}</div>
  <a class="cta" href="{job['url']}" target="_blank" rel="noopener">Voir l'offre →</a>
</div>"""

    all_cards = "".join(_scard(j, i) for i, j in enumerate(sorted_jobs))

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Veille Santé — {date_str}</title>
<style>{_SANTE_CSS}</style>
</head>
<body>
<div class="wrap">
  <a class="back-link" href="./index.html">← Rapport principal</a>
  <div class="header">
    <h1>🏥 Veille Santé — {date_str}</h1>
    <p class="sub">Alexandre Le Clercq · Ciblage large secteur santé belge</p>
    <div class="profile-box">
      <b>Profil :</b> Master universitaire (5 ans) · Head of AM (management 8+ pers.) · Analytics & KPIs · Coordination de projets · Ops B2B SaaS<br>
      <b>Secteurs couverts :</b> Hôpitaux &amp; cliniques · Mutualités · INAMI/IRISCARE · HealthTech · Pharma · Dispositifs médicaux · Fédérations de santé
    </div>
    <div class="stats">{stats}</div>
  </div>
  <div class="filters">
    <div class="frow"><span class="flabel">Secteur</span>{sect_btns}</div>
    <div class="frow"><span class="flabel">Stars</span>
      <button class="fbtn stars-btn" data-min="5">★★★★★</button>
      <button class="fbtn stars-btn" data-min="4">★★★★+</button>
      <button class="fbtn stars-btn" data-min="3">★★★+</button>
    </div>
  </div>
  <div class="section-title">💼 Toutes les opportunités santé <span class="sc">{len(jobs)}</span></div>
  <div class="cards-grid">{all_cards}</div>
  <div style="text-align:center;padding:28px 0 16px;color:#aeaeb2;font-size:11px">
    Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')} · Groq + Tavily · <a href="./index.html" style="color:#0AA5B8">Rapport principal →</a>
  </div>
</div>
<script>{_SANTE_JS}</script>
</body></html>"""


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
                "max_results": 5,
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
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("content", "")[:300],
                    "published_date": r.get("published_date", None),
                }
                for r in data.get("results", [])
            ],
        }
    except Exception as e:
        return {"query": query, "error": str(e), "results": []}


_STALE_SIGNALS = ["no longer accepting", "plus disponible", "closed", "fermé", "expired", "expiré"]


def _is_stale(job: dict) -> bool:
    pd = (job.get("posted_date") or "").lower()
    if not pd:
        return False
    return any(s in pd for s in _STALE_SIGNALS)


_MOIS_FR = ["janvier","février","mars","avril","mai","juin","juillet","août","septembre","octobre","novembre","décembre"]


def _load_prev_jobs(jobs_db_path: str) -> dict:
    """Charge les jobs précédents depuis jobs.json, retourne un dict {url: job}."""
    if not jobs_db_path or not os.path.exists(jobs_db_path):
        return {}
    try:
        with open(jobs_db_path, encoding="utf-8") as f:
            return {j["url"]: j for j in json.load(f)}
    except Exception:
        return {}


def send_report(jobs: list, top_insight: str) -> dict:
    now = datetime.now()
    date_str  = f"{now.day} {_MOIS_FR[now.month - 1]} {now.year}"
    date_file = now.strftime("%Y%m%d")
    today_str = now.strftime("%Y-%m-%d")

    # ── Filtre anti-offres explicitement fermées ─────────────────
    before = len(jobs)
    jobs = [j for j in jobs if not _is_stale(j)]
    if before - len(jobs):
        print(f"  🗑️   {before - len(jobs)} offre(s) périmée(s) supprimée(s)")

    # ── Accumulation : fusion avec les jobs précédents ───────────
    jobs_db_path = os.environ.get("JOBS_DB")
    prev = _load_prev_jobs(jobs_db_path)
    print(f"  📂  {len(prev)} offre(s) précédente(s) chargée(s)")

    new_urls = set()
    for j in jobs:
        if j["url"] not in prev:
            j["found_date"] = today_str
            j["is_new"] = True
            new_urls.add(j["url"])
        else:
            j["found_date"] = prev[j["url"]].get("found_date", today_str)
            j.setdefault("is_new", False)
        prev[j["url"]] = j

    # Garde les anciens jobs (≤ 60 jours) non recrawlés aujourd'hui
    cutoff = now.toordinal() - 60
    for url, old_job in prev.items():
        if url in new_urls or any(j["url"] == url for j in jobs):
            continue
        try:
            fd = datetime.strptime(old_job.get("found_date", today_str), "%Y-%m-%d")
            if fd.toordinal() >= cutoff:
                old_job["is_new"] = False
                jobs.append(old_job)
        except Exception:
            pass

    print(f"  📊  {len(jobs)} offres au total ({len(new_urls)} nouvelles aujourd'hui)")

    # ── Sauvegarde jobs.json ─────────────────────────────────────
    out_dir = os.environ.get("REPORT_DIR", "/tmp/veille_jobs")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "jobs.json"), "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)

    med_jobs   = [j for j in jobs if j.get("type") == "med"]
    html_pages = _build_pages_html(jobs, top_insight, date_str)
    html_email = _build_email_html(jobs, top_insight, date_str)
    html_sante = _build_sante_html(med_jobs, date_str) if med_jobs else None

    for fname in [f"rapport_{date_file}.html", "latest.html"]:
        with open(os.path.join(out_dir, fname), "w", encoding="utf-8") as f:
            f.write(html_pages)
    if html_sante:
        with open(os.path.join(out_dir, "sante.html"), "w", encoding="utf-8") as f:
            f.write(html_sante)
        print(f"  🏥  Page santé sauvegardée — {len(med_jobs)} offres")

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

DATE D'AUJOURD'HUI : {datetime.now().strftime('%d %B %Y')}

LANGUE OBLIGATOIRE : Tout le contenu généré doit être en FRANÇAIS sans exception.

INSTRUCTIONS :
1. Appelle `search_jobs` pour CHACUNE des {len(SEARCH_QUERIES)} requêtes suivantes (dans l'ordre, SANS EN SAUTER AUCUNE) :
{chr(10).join(f'   - "{q}"' for q in SEARCH_QUERIES)}

2. Après TOUTES les recherches, analyse et filtre :
   - EXCLURE : articles de blog, grands groupes (>1000 pers.), banques, consultings classiques, postes nécessitant le néerlandais
   - EXCLURE les doublons (même poste/même entreprise)
   - EXCLURE UNIQUEMENT les offres explicitement fermées : "no longer accepting applications", "closed", "expiré"
   - INCLURE les offres même si la date de publication n'est pas clairement indiquée — NE PAS exclure sur l'absence de date

3. FILTRE DE DATE — règle unique :
   - Si le snippet dit explicitement "X months ago" (avec un chiffre ≥ 2) → exclure
   - Si "no longer accepting applications" → exclure
   - SINON → conserver l'offre, mettre `posted_date` = ce que tu trouves ("il y a 3 jours", "28 sept.", null si inconnu)
   - `is_new` = true si postée il y a ≤ 7 jours (ou si date inconnue mais l'offre semble récente)

4. VOLUME MINIMUM — OBLIGATOIRE : inclure AU MINIMUM 15 offres dans `send_report`.
   Tu dois être inclusif : mets ★★ pour les offres moins parfaites mais potentiellement intéressantes.
   Mieux vaut 20 offres avec quelques ★★ que 6 offres parfaites.

5. SCORING STARTUP :
   ★★★★★ Country Lead, associate/co-fondateur, Head of Sales, GTM Engineer chez startup IA/FinTech/SaaS belge
   ★★★★  Sales Manager, GTM Lead, RevOps, Sales Ops, Operational Lead chez scaleup B2B
   ★★★   BDM, AE senior, rôle intéressant mais secteur moins prioritaire
   ★★    Offre possible, moins parfaite (secteur différent, niveau différent) — inclure quand même

6. SCORING MÉDICAL (type="med") — pour les offres dans les hôpitaux, cliniques, mutualités, pharma, healthtech :
   ★★★★★ Directeur des opérations, Head of Operations, Chief of Staff hôpital belge
   ★★★★  Business Analyst santé, Chef de projet digital health, BizDev pharma/medtech, Data Analyst healthcare
   ★★★   Coordinateur de projets, Analyste performance, chargé de mission, ops healthtech
   ★★    Tout rôle dans la santé potentiellement intéressant
   → PROFIL ÉLARGI : Master universitaire (5 ans), management d'équipes, analytics, coordination projets
   → `sector` = "Santé" ou "HealthTech" ou "Pharma" ou "Mutualité" ou "Hôpital" selon le cas

7. SALAIRE pour offres type="med" :
   → Si mentionné → copie verbatim
   → Hôpital/clinique belge sans salaire → IFIC : coordinateur/analyste "IFIC 14-16 (~2 800-3 500€/mois)", manager "IFIC 17-19 (~3 500-5 000€/mois)"
   → Healthtech/startup → "marché ~X 000-X 000€/mois (estimation)"
   → Si vraiment inconnu → null

8. Appelle `send_report` UNE SEULE FOIS avec la liste finale triée par note décroissante.
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

        report_sent = False
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
                report_sent = True
            else:
                result = {"error": f"Outil inconnu : {name}"}

            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, ensure_ascii=False)})

        if report_sent:
            print("\n✅  Rapport envoyé — arrêt de la boucle.")
            break


if __name__ == "__main__":
    run_agent()
