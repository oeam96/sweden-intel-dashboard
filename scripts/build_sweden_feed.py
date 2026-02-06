import os
import json
import hashlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import feedparser
import dateparser
from deep_translator import GoogleTranslator
from bs4 import BeautifulSoup

# -------------------------
# CONFIG
# -------------------------
NEWS_FEEDS = [
    {"url": "https://www.dn.se/rss/", "source": "Dagens Nyheter (DN)"},
    {"url": "https://www.svd.se/feed/articles.rss", "source": "Svenska Dagbladet (SvD)"},
    {"url": "https://www.hd.se/feeds/feed.xml", "source": "Helsingborgs Dagblad (HD)"},
    {"url": "https://www.sydsvenskan.se/feeds/feed.xml", "source": "Sydsvenskan"},
    {"url": "https://www.dagen.se/arc/outboundfeeds/rss/", "source": "Dagen"},
    {"url": "https://www.tv4.se/rss", "source": "TV4 Nyheterna"},
    {"url": "https://www.abcnyheter.se/feed/", "source": "ABC Nyheter"},
]

GOV_MASTER_FEED = "https://www.government.se/Filter/RssFeed?filterType=Taxonomy&filterByType=FilterablePageBase&preFilteredCategories=2069%2C2070%2C2071%2C2072%2C2073%2C2074%2C2075%2C2076%2C2077%2C2078%2C2079%2C2082%2C2083%2C2426&rootPageReference=0&filteredContentCategories=2253%2C2218%2C2210%2C2026%2C2027%2C2029%2C2030%2C2234%2C2016%2C2319%2C2025%2C2033%2C2035%2C2034%2C2219%2C2743%2C2036&filteredPoliticalLevelCategories=2039%2C2040&filteredPoliticalAreaCategories=2716%2C2658%2C2659%2C2217%2C2370%2C2155%2C2368%2C2369%2C2169%2C2171%2C2156%2C2166%2C2371%2C2157%2C2714%2C2463%2C2170%2C2158%2C2177%2C2176%2C2159%2C2160%2C2188%2C2298%2C2167%2C2187%2C2153%2C2301%2C2152%2C2165%2C2392%2C2180%2C2151%2C2150%2C2715%2C2173%2C2161%2C2748%2C2174%2C2660%2C2168%2C2436%2C2657%2C2216%2C2179%2C2172%2C2183%2C2184%2C2154%2C2462%2C2181%2C2162%2C2186%2C2251&filteredPublisherCategories=2630%2C2631%2C2757%2C2632%2C2633%2C2634%2C2796%2C2635%2C2636%2C2637%2C2638%2C2755%2C2794%2C2640%2C2642%2C2643%2C2645%2C2785%2C2646%2C2648%2C2649%2C2650%2C2793%2C2652%2C2069%2C2070%2C2071%2C2072%2C2073%2C2074%2C2076%2C2077%2C2078%2C2426%2C2079%2C2083%2C2082"

AGENCY_FEEDS = [
    {"url": "https://www.riksdagen.se/sv/rss/nyheter-fran-riksdagen", "source": "Riksdagen", "ministry": "Parliament"},
    {"url": "https://www.riksbank.se/sv/rss/pressmeddelanden", "source": "Riksbanken", "ministry": "Central Bank"},
    {"url": "https://www.scb.se/rss/", "source": "SCB", "ministry": "Statistics Sweden"},
]

MAX_ITEMS_PER_FEED = 30          # raise a bit; we'll filter by window anyway
WINDOW_DAYS = 14                 # your brief window
TARGET_LANG = "en"

DAILY_NEWS_SOURCES = {
    "Dagens Nyheter (DN)",
    "Svenska Dagbladet (SvD)",
    "TV4 Nyheterna",
    "ABC Nyheter",
    "Dagen",
}
DAILY_NEWS_MAX_ITEMS = 5

OUT_DIR = "public"
API_PATH = os.path.join(OUT_DIR, "api", "latest.json")
DAILY_NEWS_PATH = os.path.join(OUT_DIR, "api", "daily_news.json")
MD_PATH = os.path.join(OUT_DIR, "sweden_intelligence.md")
HTML_PATH = os.path.join(OUT_DIR, "index.html")

CACHE_DIR = ".cache"
TRANSLATION_CACHE_PATH = os.path.join(CACHE_DIR, "translation_cache.json")

# -------------------------
# HELPERS
# -------------------------
def ensure_dirs():
    os.makedirs(os.path.join(OUT_DIR, "api"), exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)

def clean_html(html_content: str) -> str:
    if not html_content:
        return ""
    return BeautifulSoup(html_content, "html.parser").get_text(separator=" ").strip()

def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()

def normalize_text(s: str) -> str:
    s = (s or "").strip().lower()
    s = " ".join(s.split())
    return s

def parse_date(date_str: str) -> datetime:
    """
    Try parse date into timezone-aware UTC; fallback to now (UTC).
    """
    now = datetime.now(timezone.utc)
    if not date_str:
        return now
    d = dateparser.parse(date_str)
    if not d:
        return now
    if d.tzinfo is None:
        # Assume UTC if timezone missing
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)

def age_tag(date_obj: datetime, now_utc: datetime) -> str:
    delta = now_utc - date_obj
    days = int(delta.total_seconds() // 86400)
    if days <= 0:
        return "today"
    if days == 1:
        return "1 day old"
    return f"{days} days old"

def load_translation_cache() -> dict:
    if not os.path.exists(TRANSLATION_CACHE_PATH):
        return {}
    try:
        with open(TRANSLATION_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_translation_cache(cache: dict):
    try:
        with open(TRANSLATION_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def translate_text(text: str, cache: dict) -> str:
    """
    Cached translation to reduce flaky runs. Keeps original on failure.
    """
    if not text or len(text) < 3:
        return text
    t = text.strip()
    key = sha1(t[:2000])
    if key in cache:
        return cache[key]
    try:
        translated = GoogleTranslator(source="auto", target=TARGET_LANG).translate(t[:2000])
        cache[key] = translated
        return translated
    except Exception:
        return t

# -------------------------
# FEED PROCESSING
# -------------------------
def process_feed(url: str, default_source: str, default_ministry: str, is_gov_master: bool, now_utc: datetime, cutoff_utc: datetime):
    items = []
    feed = feedparser.parse(url)

    for i, entry in enumerate(feed.entries):
        if i >= MAX_ITEMS_PER_FEED:
            break

        title = entry.get("title", "No Title")
        link = getattr(entry, "link", "") or entry.get("link", "")
        pub_date = parse_date(entry.get("published", "") or entry.get("updated", ""))

        # Filter by window early
        if pub_date < cutoff_utc:
            continue

        raw_summary = entry.get("summary", "") or entry.get("description", "")
        summary = clean_html(raw_summary)

        ministry = default_ministry
        tags_list = []
        if "tags" in entry:
            for tag in entry.tags:
                term = getattr(tag, "term", None)
                if term:
                    tags_list.append(term)

        if is_gov_master and tags_list:
            for term in tags_list:
                if "Ministry" in term or "Prime Minister" in term:
                    ministry = term
                    break

        type_label = "News"
        if is_gov_master or ("Parliament" in default_ministry) or ("Bank" in default_ministry):
            type_label = "Official Information"

        items.append({
            "title_original": title,
            "summary_original": summary,
            "link": link,
            "date_utc": pub_date,
            "source": "Government.se" if is_gov_master else default_source,
            "ministry": ministry,
            "type": type_label,
            "tags": tags_list,
        })

    return items

def dedupe_items(items):
    """
    Dedupe by normalized title + link. Keep newest.
    """
    best = {}
    for it in items:
        key = sha1(normalize_text(it.get("title_original", "")) + "|" + normalize_text(it.get("link", "")))
        if key not in best:
            best[key] = it
        else:
            if it["date_utc"] > best[key]["date_utc"]:
                best[key] = it
    return list(best.values())

# -------------------------
# OUTPUT GENERATION
# -------------------------
def build_markdown(items, generated_at_local: str, window_days: int) -> str:
    md = []
    md.append("# Sweden Intelligence Report")
    md.append(f"Generated: {generated_at_local}")
    md.append(f"Window: last {window_days} days")
    md.append("")
    for it in items:
        md.append(f"## {it['title']}")
        md.append(f"**Outlet:** {it['source']}")
        md.append(f"**Category:** {it['ministry']} | **Date:** {it['date_display']} ({it['age_tag']})")
        md.append("")
        md.append(it["summary"] if it["summary"] else "_No summary text available from RSS._")
        md.append("")
        md.append(f"[Read Full Article]({it['link']})")
        md.append("\n---\n")
    return "\n".join(md)

def build_dashboard_html(json_str: str, item_count: int) -> str:
    # Same CSS/UI as you have, slight header change and uses embedded json_str.
    css = """
body { font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background: #f0f2f5; padding: 20px; color: #333; }
header { background: white; padding: 25px; border-radius: 12px; margin-bottom: 30px; text-align: center; box-shadow: 0 4px 12px rgba(0,0,0,0.05); }
h1 { margin: 0 0 10px 0; color: #1a1a1a; }

/* CONTROLS */
.controls { display: flex; flex-wrap: wrap; gap: 10px; justify-content: center; margin-top: 20px; }
button, select { padding: 10px 18px; border-radius: 8px; border: 1px solid #ddd; cursor: pointer; font-size: 0.95rem; background: white; transition: all 0.2s; }
button:hover, select:hover { background: #f8f9fa; border-color: #bbb; }
button.active { background: #007bff; color: white; border-color: #007bff; }
button.download { background: #28a745; color: white; border-color: #28a745; font-weight: 600; }
select { padding-right: 30px; }

/* GRID */
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 25px; }

/* CARD */
.card {
    background: white; border-radius: 12px; overflow: hidden;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06);
    display: flex; flex-direction: column;
    transition: transform 0.2s;
    border-top: 6px solid #ccc;
    height: auto;
}
.card:hover { transform: translateY(-3px); box-shadow: 0 8px 16px rgba(0,0,0,0.1); }
.card.official { border-color: #007bff; }
.card.news { border-color: #ffc107; }

.card-body { padding: 20px; flex: 1; display: flex; flex-direction: column; }

/* META */
.tags { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
.tag { font-size: 0.75rem; padding: 4px 10px; border-radius: 20px; font-weight: 600; letter-spacing: 0.3px; }
.tag-type { background: #e9ecef; color: #495057; }
.tag-ministry { background: #e3f2fd; color: #0d47a1; }
.tag-old { background: #ffebee; color: #c62828; }

.meta-line { font-size: 0.85rem; color: #6c757d; margin-bottom: 12px; font-weight: 500; }
h3 { margin: 0 0 12px 0; font-size: 1.2rem; line-height: 1.4; color: #212529; }

/* SUMMARY (Expandable) */
.summary-container { position: relative; font-size: 0.95rem; color: #444; line-height: 1.6; flex: 1; }
.summary-text {
    display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden;
    transition: max-height 0.3s ease;
}
.summary-text.expanded { -webkit-line-clamp: unset; overflow: visible; }

/* FOOTER */
.card-footer {
    padding: 15px 20px; background: #f8f9fa; border-top: 1px solid #eee;
    display: flex; justify-content: space-between; align-items: center;
}
.btn-text { background: none; border: none; color: #007bff; font-weight: 600; padding: 0; font-size: 0.9rem; }
.btn-text:hover { text-decoration: underline; background: none; }
.btn-link { text-decoration: none; color: #495057; font-weight: 600; font-size: 0.9rem; display: flex; align-items: center; gap: 5px; }
.btn-link:hover { color: #212529; }
"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Swedish Intelligence Dashboard</title>
  <style>{css}</style>
</head>
<body>
<header>
  <h1>🇸🇪 Swedish Intelligence Dashboard</h1>
  <p>Monitoring <strong>{item_count}</strong> items (last 14 days)</p>

  <div class="controls">
    <button class="active" onclick="filterType('all', this)">All Types</button>
    <button onclick="filterType('Official Information', this)">Official Only</button>
    <button onclick="filterType('News', this)">News Only</button>

    <select id="ministrySelect" onchange="filterMinistry()">
      <option value="all">All Sources & Ministries</option>
    </select>

    <button class="download" onclick="downloadMD()">⬇ Download Markdown Report</button>
  </div>
</header>

<div class="grid" id="container"></div>

<script>
  const data = {json_str};
  let currentType = 'all';
  let currentMinistry = 'all';

  const ministries = [...new Set(data.map(i => i.ministry))].sort();
  const select = document.getElementById('ministrySelect');
  ministries.forEach(m => {{
    let opt = document.createElement('option');
    opt.value = m;
    opt.textContent = m;
    select.appendChild(opt);
  }});

  function render() {{
    const container = document.getElementById('container');
    container.innerHTML = '';

    const filtered = data.filter(item => {{
      const typeMatch = (currentType === 'all') || (item.type === currentType);
      const minMatch = (currentMinistry === 'all') || (item.ministry === currentMinistry);
      return typeMatch && minMatch;
    }});

    filtered.forEach(item => {{
      const typeClass = item.type === 'Official Information' ? 'official' : 'news';
      const ageHtml = item.age_tag ? `<span class="tag tag-old">⏱ ${{item.age_tag}}</span>` : '';
      const ministryTag = `<span class="tag tag-ministry">${{item.ministry}}</span>`;

      const card = `
        <div class="card ${{typeClass}}">
          <div class="card-body">
            <div class="tags">
              <span class="tag tag-type">${{item.type}}</span>
              ${{ministryTag}}
              ${{ageHtml}}
            </div>
            <div class="meta-line">
              <strong>${{item.source}}</strong> | ${{item.date_display}}
            </div>
            <h3>${{item.title}}</h3>
            <div class="summary-container">
              <div class="summary-text" id="sum-${{item.id}}">
                ${{item.summary || 'No summary text available.'}}
              </div>
            </div>
          </div>
          <div class="card-footer">
            <button class="btn-text" onclick="toggleText('${{item.id}}', this)">Read More</button>
            <a href="${{item.link}}" target="_blank" class="btn-link">🔗 Open Article</a>
          </div>
        </div>
      `;
      container.innerHTML += card;
    }});

    if (filtered.length === 0) {{
      container.innerHTML = '<p style="text-align:center; width:100%; color:#666;">No results.</p>';
    }}
  }}

  function toggleText(id, btn) {{
    const el = document.getElementById('sum-' + id);
    el.classList.toggle('expanded');
    btn.textContent = el.classList.contains('expanded') ? "Show Less" : "Read More";
  }}

  function filterType(type, btn) {{
    currentType = type;
    document.querySelectorAll('button').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    const dlBtn = document.querySelector('.download');
    if (btn !== dlBtn) dlBtn.classList.remove('active');
    render();
  }}

  function filterMinistry() {{
    currentMinistry = document.getElementById('ministrySelect').value;
    render();
  }}

  function downloadMD() {{
    let md = "# Sweden Intelligence Report\\n";
    md += "Generated: " + new Date().toLocaleString() + "\\n\\n";

    data.forEach(item => {{
      md += `## ${{item.title}}\\n`;
      md += `**Outlet:** ${{item.source}}\\n`;
      md += `**Category:** ${{item.ministry}} | **Date:** ${{item.date_display}} (${{item.age_tag}})\\n\\n`;
      md += `${{item.summary || 'No summary text available.'}}\\n\\n`;
      md += `[Read Full Article](${{item.link}})\\n`;
      md += "---\\n\\n";
    }});

    const blob = new Blob([md], {{type: 'text/markdown'}});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'sweden_intelligence.md';
    a.click();
  }}

  render();
</script>
</body>
</html>
"""
    return html

def build_daily_news_items(final_items, today_stockholm, stockholm_tz):
    today_news = [
        item for item in final_items
        if item["type"] == "News"
        and item["source"] in DAILY_NEWS_SOURCES
        and datetime.fromisoformat(item["date_iso"]).astimezone(stockholm_tz).date() == today_stockholm
    ]
    official_last_two_weeks = [
        item for item in final_items
        if item["type"] == "Official Information"
    ]
    return today_news[:DAILY_NEWS_MAX_ITEMS] + official_last_two_weeks

def main():
    ensure_dirs()

    now_utc = datetime.now(timezone.utc)
    stockholm_tz = ZoneInfo("Europe/Stockholm")
    today_stockholm = now_utc.astimezone(stockholm_tz).date()
    cutoff_utc = now_utc - timedelta(days=WINDOW_DAYS)

    # Translation cache
    tcache = load_translation_cache()

    all_items = []

    print("\n--- Government Master Feed ---")
    all_items.extend(process_feed(GOV_MASTER_FEED, "Government.se", "General Government", True, now_utc, cutoff_utc))

    print("\n--- Agencies ---")
    for a in AGENCY_FEEDS:
        all_items.extend(process_feed(a["url"], a["source"], a["ministry"], False, now_utc, cutoff_utc))

    print("\n--- News ---")
    for n in NEWS_FEEDS:
        all_items.extend(process_feed(n["url"], n["source"], "General News", False, now_utc, cutoff_utc))

    # Dedupe + sort
    all_items = dedupe_items(all_items)
    all_items.sort(key=lambda x: x["date_utc"], reverse=True)

    # Guard against publishing empty artifacts when feeds are temporarily unavailable.
    if not all_items:
        raise RuntimeError(
            "No feed items were fetched. Aborting build to avoid overwriting published artifacts with empty data."
        )

    # Translate + shape final items
    final_items = []
    for idx, it in enumerate(all_items):
        title_en = translate_text(it["title_original"], tcache)
        summary_en = translate_text(it["summary_original"], tcache)

        final_items.append({
            "id": idx,
            "title": title_en,
            "summary": summary_en,
            "title_original": it["title_original"],
            "summary_original": it["summary_original"],
            "link": it["link"],
            "date_display": it["date_utc"].strftime("%Y-%m-%d"),
            "date_iso": it["date_utc"].isoformat(),
            "source": it["source"],
            "ministry": it["ministry"],
            "type": it["type"],
            "age_tag": age_tag(it["date_utc"], now_utc),
            "tags": it["tags"],
        })

    # Save cache
    save_translation_cache(tcache)

    # Write API JSON (object wrapper)
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "window_days": WINDOW_DAYS,
        "item_count": len(final_items),
        "items": final_items,
    }
    with open(API_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # Write daily API JSON:
    # - up to 5 today-news items from selected outlets
    # - all official information items from the last 2 weeks window
    daily_news_items = build_daily_news_items(final_items, today_stockholm, stockholm_tz)
    # Write daily API JSON (all types + today's Stockholm date)
    daily_news_items = [
        item for item in final_items
        if datetime.fromisoformat(item["date_iso"]).astimezone(stockholm_tz).date() == today_stockholm
    ]
    daily_news_payload = {
        "generated_at": payload["generated_at"],
        "date": today_stockholm.isoformat(),
        "item_count": len(daily_news_items),
        "items": daily_news_items,
    }
    with open(DAILY_NEWS_PATH, "w", encoding="utf-8") as f:
        json.dump(daily_news_payload, f, ensure_ascii=False, indent=2)

    # Write Markdown
    md = build_markdown(final_items, payload["generated_at"], WINDOW_DAYS)
    with open(MD_PATH, "w", encoding="utf-8") as f:
        f.write(md)

    # Write Dashboard HTML (embed only the items array for client-side filtering)
    json_str = json.dumps(final_items, ensure_ascii=False)
    html = build_dashboard_html(json_str=json_str, item_count=len(final_items))
    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print("\n-----------------------------------------------------------")
    print(f"SUCCESS! Wrote:\n- {HTML_PATH}\n- {MD_PATH}\n- {API_PATH}\n- {DAILY_NEWS_PATH}")
    print("-----------------------------------------------------------")

if __name__ == "__main__":
    main()
