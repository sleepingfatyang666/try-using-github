from __future__ import annotations

import datetime as dt
import email.utils
import html
import json
import os
import re
import textwrap
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests


ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs"
DATA_DIR = ROOT / "data"

TZ_NAME = os.getenv("TZ_NAME", "America/Chicago")
RUN_WINDOW_HOUR = int(os.getenv("RUN_WINDOW_HOUR", "10"))
FORCE_SEND = os.getenv("FORCE_SEND", "").lower() in {"1", "true", "yes"}
DRY_RUN = os.getenv("DRY_RUN", "").lower() in {"1", "true", "yes"}

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "").strip()
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()

WORLD_COUNT = int(os.getenv("WORLD_COUNT", "10"))
MATERIALS_COUNT = int(os.getenv("MATERIALS_COUNT", "8"))
GITHUB_TRENDING_COUNT = int(os.getenv("GITHUB_TRENDING_COUNT", "6"))
GITHUB_MATERIALS_COUNT = int(os.getenv("GITHUB_MATERIALS_COUNT", "6"))

HTTP_HEADERS = {
    "User-Agent": "daily-discord-digest/1.0 (+https://github.com)",
    "Accept": "application/json,text/html,application/xml,text/xml,*/*",
}


@dataclass
class Candidate:
    id: str
    section: str
    title: str
    url: str
    source: str
    summary: str = ""
    published: str = ""
    image_url: str = ""
    meta: str = ""


SECTION_META = {
    "world_news": {
        "zh_title": "每日世界新闻",
        "en_title": "Daily World News",
        "count": WORLD_COUNT,
    },
    "materials": {
        "zh_title": "材料学文章、突破与热点论文",
        "en_title": "Materials Breakthroughs and Papers",
        "count": MATERIALS_COUNT,
    },
    "github_trending": {
        "zh_title": "GitHub 流行项目",
        "en_title": "Popular GitHub Projects",
        "count": GITHUB_TRENDING_COUNT,
    },
    "github_materials": {
        "zh_title": "GitHub 材料学项目",
        "en_title": "Materials-related GitHub Projects",
        "count": GITHUB_MATERIALS_COUNT,
    },
}


def clean_text(value: str | None, limit: int = 700) -> str:
    if not value:
        return ""
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit].rstrip()


def parse_date(value: str | None) -> str:
    if not value:
        return ""
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        return parsed.date().isoformat()
    except Exception:
        return clean_text(value, 80)


def extract_image(element: ET.Element) -> str:
    for child in list(element):
        tag = child.tag.lower()
        if tag.endswith("content") or tag.endswith("thumbnail"):
            image_url = child.attrib.get("url", "")
            if image_url.startswith("http"):
                return image_url
        if tag.endswith("enclosure"):
            mime = child.attrib.get("type", "")
            image_url = child.attrib.get("url", "")
            if image_url.startswith("http") and mime.startswith("image/"):
                return image_url
    summary = ET.tostring(element, encoding="unicode", method="xml")
    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', summary)
    return html.unescape(match.group(1)) if match else ""


def http_get(url: str, headers: dict[str, str] | None = None) -> requests.Response:
    response = requests.get(url, headers={**HTTP_HEADERS, **(headers or {})}, timeout=30)
    response.raise_for_status()
    return response


def google_news_topic(topic: str) -> str:
    return f"https://news.google.com/rss/headlines/section/topic/{topic}?hl=en-US&gl=US&ceid=US:en"


def google_news_search(query: str) -> str:
    encoded = urllib.parse.quote(query)
    return f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"


def parse_rss(url: str, section: str, prefix: str, limit: int) -> list[Candidate]:
    try:
        root = ET.fromstring(http_get(url).content)
    except Exception as exc:
        print(f"RSS fetch failed: {url} ({exc})")
        return []

    candidates: list[Candidate] = []
    for idx, item in enumerate(root.findall(".//item"), start=1):
        title = clean_text(item.findtext("title"), 220)
        link = clean_text(item.findtext("link"), 500)
        if not title or not link:
            continue
        source = clean_text(item.findtext("source"), 100) or "RSS"
        summary = clean_text(item.findtext("description"), 550)
        published = parse_date(item.findtext("pubDate"))
        candidates.append(
            Candidate(
                id=f"{prefix}{idx:02d}",
                section=section,
                title=title,
                url=link,
                source=source,
                summary=summary,
                published=published,
                image_url=extract_image(item),
            )
        )
        if len(candidates) >= limit:
            break
    return candidates


def parse_arxiv(section: str = "materials", prefix: str = "P", limit: int = 24) -> list[Candidate]:
    query = '(cat:cond-mat.mtrl-sci OR cat:cond-mat.soft OR cat:physics.chem-ph OR all:"materials informatics")'
    params = urllib.parse.urlencode(
        {
            "search_query": query,
            "start": 0,
            "max_results": limit,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
    )
    url = f"https://export.arxiv.org/api/query?{params}"
    try:
        root = ET.fromstring(http_get(url).content)
    except Exception as exc:
        print(f"arXiv fetch failed: {exc}")
        return []

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    candidates: list[Candidate] = []
    for idx, entry in enumerate(root.findall("atom:entry", ns), start=1):
        title = clean_text(entry.findtext("atom:title", default="", namespaces=ns), 240)
        summary = clean_text(entry.findtext("atom:summary", default="", namespaces=ns), 700)
        published = clean_text(entry.findtext("atom:published", default="", namespaces=ns), 40)[:10]
        link = ""
        for link_element in entry.findall("atom:link", ns):
            if link_element.attrib.get("rel") == "alternate":
                link = link_element.attrib.get("href", "")
                break
        if not link:
            link = entry.findtext("atom:id", default="", namespaces=ns)
        if title and link:
            candidates.append(
                Candidate(
                    id=f"{prefix}{idx:02d}",
                    section=section,
                    title=title,
                    url=link,
                    source="arXiv",
                    summary=summary,
                    published=published,
                    meta="paper",
                )
            )
    return candidates


def github_search(query: str, section: str, prefix: str, limit: int, sort: str = "stars") -> list[Candidate]:
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    params = urllib.parse.urlencode({"q": query, "sort": sort, "order": "desc", "per_page": limit})
    url = f"https://api.github.com/search/repositories?{params}"
    try:
        payload = http_get(url, headers=headers).json()
    except Exception as exc:
        print(f"GitHub search failed: {query} ({exc})")
        return []

    candidates: list[Candidate] = []
    for idx, repo in enumerate(payload.get("items", [])[:limit], start=1):
        full_name = repo.get("full_name", "")
        html_url = repo.get("html_url", "")
        if not full_name or not html_url:
            continue
        stars = repo.get("stargazers_count", 0)
        language = repo.get("language") or "mixed"
        description = clean_text(repo.get("description"), 450)
        pushed = clean_text(repo.get("pushed_at"), 40)[:10]
        candidates.append(
            Candidate(
                id=f"{prefix}{idx:02d}",
                section=section,
                title=full_name,
                url=html_url,
                source="GitHub",
                summary=description,
                published=pushed,
                meta=f"{stars:,} stars, {language}",
            )
        )
    return candidates


def dedupe(candidates: list[Candidate], limit: int) -> list[Candidate]:
    seen_titles: set[str] = set()
    seen_urls: set[str] = set()
    result: list[Candidate] = []
    for candidate in candidates:
        title_key = re.sub(r"\W+", "", candidate.title.lower())[:90]
        url_key = candidate.url.split("?")[0]
        if title_key in seen_titles or url_key in seen_urls:
            continue
        seen_titles.add(title_key)
        seen_urls.add(url_key)
        result.append(candidate)
        if len(result) >= limit:
            break
    return result


def collect_candidates(today: dt.date) -> dict[str, list[Candidate]]:
    since = (today - dt.timedelta(days=2)).isoformat()
    github_since = (today - dt.timedelta(days=14)).isoformat()

    world = []
    world.extend(parse_rss(google_news_topic("WORLD"), "world_news", "W", 10))
    world.extend(parse_rss(google_news_topic("BUSINESS"), "world_news", "B", 8))
    world.extend(parse_rss(google_news_search("global politics economy election diplomacy when:2d"), "world_news", "P", 8))
    world.extend(parse_rss(google_news_search("art culture museum film literature music when:3d"), "world_news", "A", 8))
    world.extend(parse_rss(google_news_topic("SCIENCE"), "world_news", "S", 6))

    materials = []
    materials.extend(
        parse_rss(
            google_news_search(
                '"materials science" OR "new material" OR "battery materials" OR "semiconductor materials" when:7d'
            ),
            "materials",
            "M",
            14,
        )
    )
    materials.extend(parse_arxiv(limit=24))

    trending = github_search(f"stars:>1000 pushed:>{since}", "github_trending", "G", 18)

    github_materials = []
    material_queries = [
        f"topic:materials-science pushed:>{github_since}",
        f"topic:materials-informatics pushed:>{github_since}",
        f"topic:density-functional-theory pushed:>{github_since}",
        f"topic:molecular-dynamics pushed:>{github_since}",
        f"pymatgen OR matminer pushed:>{github_since}",
    ]
    for idx, query in enumerate(material_queries, start=1):
        github_materials.extend(github_search(query, "github_materials", f"R{idx}", 8, sort="updated"))
        time.sleep(0.4)

    return {
        "world_news": dedupe(world, 40),
        "materials": dedupe(materials, 36),
        "github_trending": dedupe(trending, 18),
        "github_materials": dedupe(github_materials, 24),
    }


def prompt_payload(candidates_by_section: dict[str, list[Candidate]], today: dt.date) -> str:
    compact = {
        key: [
            {
                "id": c.id,
                "title": c.title,
                "source": c.source,
                "summary": c.summary,
                "published": c.published,
                "meta": c.meta,
            }
            for c in values
        ]
        for key, values in candidates_by_section.items()
    }
    requested_counts = {key: value["count"] for key, value in SECTION_META.items()}
    return json.dumps(
        {
            "date": today.isoformat(),
            "requested_counts": requested_counts,
            "sections": compact,
            "output_schema": {
                "date": "YYYY-MM-DD",
                "headline_zh": "one sentence",
                "headline_en": "one sentence",
                "sections": [
                    {
                        "key": "world_news | materials | github_trending | github_materials",
                        "zh_title": "section title",
                        "en_title": "section title",
                        "items": [
                            {
                                "source_id": "must match an input id",
                                "zh_title": "Chinese title",
                                "en_title": "English title",
                                "zh_summary": "1-2 sentence Chinese summary",
                                "en_summary": "1-2 sentence English summary",
                            }
                        ],
                    }
                ],
            },
        },
        ensure_ascii=False,
    )


def call_openai_json(prompt: str) -> dict[str, Any] | None:
    if not OPENAI_API_KEY:
        print("OPENAI_API_KEY is not set; using fallback digest.")
        return None
    if not OPENAI_MODEL:
        print("OPENAI_MODEL is not set; using fallback digest.")
        return None
    body = {
        "model": OPENAI_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You create concise bilingual daily digests. Use only the supplied source ids. "
                    "Do not invent facts, URLs, sources, or paper claims. Prefer diverse geography and fields. "
                    "Return strict JSON only."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Select the best items for each requested section and write Chinese and English titles "
                    "plus summaries. For materials papers, explain the material system and why it may matter "
                    "without overstating the result. For GitHub projects, mention the likely use case. "
                    f"Input:\n{prompt}"
                ),
            },
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    response = requests.post(
        f"{OPENAI_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        json=body,
        timeout=120,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}") + 1
        return json.loads(content[start:end])


def fallback_digest(candidates_by_section: dict[str, list[Candidate]], today: dt.date) -> dict[str, Any]:
    sections = []
    for key, meta in SECTION_META.items():
        items = []
        for candidate in candidates_by_section.get(key, [])[: meta["count"]]:
            summary = candidate.summary or candidate.meta or "Source item collected for today's digest."
            items.append(
                {
                    "source_id": candidate.id,
                    "zh_title": candidate.title,
                    "en_title": candidate.title,
                    "zh_summary": summary,
                    "en_summary": summary,
                }
            )
        sections.append(
            {
                "key": key,
                "zh_title": meta["zh_title"],
                "en_title": meta["en_title"],
                "items": items,
            }
        )
    return {
        "date": today.isoformat(),
        "headline_zh": "今日综合简报已生成。",
        "headline_en": "Today's digest has been generated.",
        "sections": sections,
    }


def hydrate_digest(raw_digest: dict[str, Any], candidates_by_section: dict[str, list[Candidate]], today: dt.date) -> dict[str, Any]:
    by_id = {candidate.id: candidate for values in candidates_by_section.values() for candidate in values}
    hydrated_sections = []
    for section in raw_digest.get("sections", []):
        key = section.get("key")
        meta = SECTION_META.get(key, {})
        hydrated_items = []
        for item in section.get("items", []):
            candidate = by_id.get(item.get("source_id", ""))
            if not candidate:
                continue
            hydrated_items.append(
                {
                    "source_id": candidate.id,
                    "zh_title": clean_text(item.get("zh_title"), 220) or candidate.title,
                    "en_title": clean_text(item.get("en_title"), 220) or candidate.title,
                    "zh_summary": clean_text(item.get("zh_summary"), 550) or candidate.summary,
                    "en_summary": clean_text(item.get("en_summary"), 550) or candidate.summary,
                    "source": candidate.source,
                    "url": candidate.url,
                    "published": candidate.published,
                    "image_url": candidate.image_url,
                    "meta": candidate.meta,
                }
            )
        hydrated_sections.append(
            {
                "key": key,
                "zh_title": clean_text(section.get("zh_title"), 120) or meta.get("zh_title", key),
                "en_title": clean_text(section.get("en_title"), 120) or meta.get("en_title", key),
                "items": hydrated_items,
            }
        )
    return {
        "date": raw_digest.get("date") or today.isoformat(),
        "headline_zh": clean_text(raw_digest.get("headline_zh"), 220),
        "headline_en": clean_text(raw_digest.get("headline_en"), 220),
        "sections": hydrated_sections,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def page_url_for(date_slug: str) -> str:
    if not PUBLIC_BASE_URL:
        return ""
    return f"{PUBLIC_BASE_URL}/{date_slug}/"


def render_item(item: dict[str, Any]) -> str:
    image = ""
    if item.get("image_url"):
        image = f'<img src="{html.escape(item["image_url"])}" alt="" loading="lazy">'
    meta_parts = [item.get("source", ""), item.get("published", ""), item.get("meta", "")]
    meta = " · ".join(html.escape(part) for part in meta_parts if part)
    return f"""
      <article class="item">
        {image}
        <div>
          <h3><a href="{html.escape(item["url"])}">{html.escape(item["zh_title"])}</a></h3>
          <h4>{html.escape(item["en_title"])}</h4>
          <p>{html.escape(item["zh_summary"])}</p>
          <p class="en">{html.escape(item["en_summary"])}</p>
          <p class="meta">{meta}</p>
        </div>
      </article>
    """


def render_html_page(digest: dict[str, Any]) -> str:
    sections = []
    for section in digest["sections"]:
        items = "\n".join(render_item(item) for item in section["items"])
        sections.append(
            f"""
            <section>
              <h2>{html.escape(section["zh_title"])}</h2>
              <p class="section-en">{html.escape(section["en_title"])}</p>
              <div class="items">{items}</div>
            </section>
            """
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Daily Digest {html.escape(digest["date"])}</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #f7f7f4;
      --fg: #202124;
      --muted: #666b73;
      --line: #dadce0;
      --accent: #0b6bcb;
      --panel: #ffffff;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #101214;
        --fg: #f1f3f4;
        --muted: #a6adb7;
        --line: #30363d;
        --accent: #8ab4f8;
        --panel: #171a1f;
      }}
    }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--fg);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.6;
    }}
    main {{
      max-width: 1040px;
      margin: 0 auto;
      padding: 32px 18px 56px;
    }}
    header {{
      border-bottom: 1px solid var(--line);
      padding-bottom: 24px;
      margin-bottom: 28px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: clamp(2rem, 5vw, 4rem);
      line-height: 1.05;
      letter-spacing: 0;
    }}
    .lede {{
      max-width: 780px;
      color: var(--muted);
      font-size: 1.05rem;
      margin: 8px 0 0;
    }}
    section {{
      margin: 36px 0 0;
    }}
    h2 {{
      font-size: 1.45rem;
      margin: 0;
    }}
    .section-en {{
      margin: 2px 0 16px;
      color: var(--muted);
    }}
    .items {{
      display: grid;
      gap: 14px;
    }}
    .item {{
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      gap: 14px;
      padding: 16px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
    }}
    .item img {{
      width: 100%;
      max-height: 260px;
      object-fit: cover;
      border-radius: 6px;
      border: 1px solid var(--line);
    }}
    .item h3 {{
      margin: 0 0 4px;
      font-size: 1.08rem;
      line-height: 1.35;
    }}
    .item h4 {{
      margin: 0 0 10px;
      color: var(--muted);
      font-weight: 600;
      line-height: 1.4;
    }}
    .item p {{
      margin: 8px 0;
    }}
    .item .en {{
      color: var(--muted);
    }}
    .meta {{
      font-size: 0.88rem;
      color: var(--muted);
    }}
    a {{
      color: var(--accent);
      text-decoration: none;
    }}
    a:hover {{
      text-decoration: underline;
    }}
    @media (min-width: 760px) {{
      .item:has(img) {{
        grid-template-columns: 260px minmax(0, 1fr);
      }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <p class="meta">Daily Digest · {html.escape(digest["date"])}</p>
      <h1>每日综合简报</h1>
      <p class="lede">{html.escape(digest.get("headline_zh") or "")}</p>
      <p class="lede">{html.escape(digest.get("headline_en") or "")}</p>
      <p><a href="../">Archive</a> · <a href="../latest.html">Latest</a></p>
    </header>
    {''.join(sections)}
  </main>
</body>
</html>
"""


def render_archive_index(dates: list[str]) -> str:
    links = "\n".join(f'<li><a href="{date}/">{date}</a></li>' for date in sorted(dates, reverse=True))
    latest = sorted(dates, reverse=True)[0] if dates else ""
    latest_link = f'<p><a href="{latest}/">Open latest digest</a></p>' if latest else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Daily Digest Archive</title>
  <style>
    body {{
      max-width: 760px;
      margin: 0 auto;
      padding: 32px 18px 56px;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.6;
    }}
    a {{ color: #0b6bcb; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <h1>每日综合简报归档</h1>
  <p>Daily bilingual digest archive.</p>
  {latest_link}
  <ul>{links}</ul>
</body>
</html>
"""


def write_outputs(digest: dict[str, Any]) -> str:
    date_slug = digest["date"]
    page_dir = DOCS_DIR / date_slug
    page_dir.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (page_dir / "index.html").write_text(render_html_page(digest), encoding="utf-8")
    (DATA_DIR / f"{date_slug}.json").write_text(json.dumps(digest, ensure_ascii=False, indent=2), encoding="utf-8")

    dates = sorted(path.name for path in DOCS_DIR.iterdir() if path.is_dir() and re.match(r"\d{4}-\d{2}-\d{2}", path.name))
    (DOCS_DIR / "index.html").write_text(render_archive_index(dates), encoding="utf-8")
    (DOCS_DIR / "latest.html").write_text(
        f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url={date_slug}/"><a href="{date_slug}/">Latest digest</a>',
        encoding="utf-8",
    )
    return page_url_for(date_slug)


def discord_blocks(digest: dict[str, Any], page_url: str) -> list[str]:
    header = [
        f"**每日综合简报 / Daily Digest — {digest['date']}**",
        digest.get("headline_zh", ""),
        digest.get("headline_en", ""),
    ]
    if page_url:
        header.append(f"完整页面 / Full page: {page_url}")
    else:
        header.append("完整页面会保存到 GitHub Pages；请设置 PUBLIC_BASE_URL 后在 Discord 中显示公开链接。")

    blocks = ["\n".join(part for part in header if part)]
    for section in digest["sections"]:
        lines = [f"\n**{section['zh_title']} / {section['en_title']}**"]
        for item in section["items"][:3]:
            lines.append(
                "\n".join(
                    [
                        f"- [{item['zh_title']}]({item['url']})",
                        f"  {item['en_title']}",
                        f"  {item['zh_summary']}",
                        f"  {item['en_summary']}",
                    ]
                )
            )
        if len(section["items"]) > 3:
            lines.append(f"  还有 {len(section['items']) - 3} 条在完整页面中 / More in the full page.")
        blocks.append("\n".join(lines))
    return blocks


def split_discord_messages(blocks: list[str], limit: int = 1850) -> list[str]:
    messages: list[str] = []
    current = ""
    for block in blocks:
        if len(current) + len(block) + 2 <= limit:
            current = f"{current}\n\n{block}".strip()
        else:
            if current:
                messages.append(current)
            if len(block) <= limit:
                current = block
            else:
                wrapped = textwrap.wrap(block, width=limit, replace_whitespace=False, drop_whitespace=False)
                messages.extend(wrapped[:-1])
                current = wrapped[-1]
    if current:
        messages.append(current)
    return messages


def first_image_embeds(digest: dict[str, Any]) -> list[dict[str, Any]]:
    embeds = []
    for section in digest["sections"]:
        for item in section["items"]:
            image_url = item.get("image_url")
            if image_url:
                embeds.append(
                    {
                        "title": item["en_title"][:250],
                        "url": item["url"],
                        "description": item["zh_title"][:220],
                        "image": {"url": image_url},
                    }
                )
            if len(embeds) >= 3:
                return embeds
    return embeds


def send_discord(digest: dict[str, Any], page_url: str) -> None:
    messages = split_discord_messages(discord_blocks(digest, page_url))
    embeds = first_image_embeds(digest)
    if DRY_RUN:
        print("\n\n--- DRY RUN DISCORD MESSAGE ---\n")
        print("\n\n--- NEXT MESSAGE ---\n\n".join(messages))
        return
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError("DISCORD_WEBHOOK_URL is not set.")
    for idx, content in enumerate(messages, start=1):
        payload: dict[str, Any] = {
            "username": "每日综合简报 / Daily Digest",
            "content": content,
            "allowed_mentions": {"parse": []},
        }
        if idx == 1 and embeds:
            payload["embeds"] = embeds
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=30)
        response.raise_for_status()
        print(f"Sent Discord message {idx}/{len(messages)}")
        time.sleep(1)


def should_run(now: dt.datetime) -> bool:
    today = now.date().isoformat()
    if FORCE_SEND:
        return True
    if now.hour != RUN_WINDOW_HOUR:
        print(f"Skipping: local hour is {now.hour}, run window is {RUN_WINDOW_HOUR}.")
        return False
    if (DOCS_DIR / today / "index.html").exists():
        print(f"Skipping: digest already exists for {today}.")
        return False
    return True


def main() -> None:
    tz = ZoneInfo(TZ_NAME)
    now = dt.datetime.now(tz)
    today = now.date()

    if not should_run(now):
        return

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    candidates_by_section = collect_candidates(today)
    for section, candidates in candidates_by_section.items():
        print(f"Collected {len(candidates)} candidates for {section}.")

    prompt = prompt_payload(candidates_by_section, today)
    raw_digest = call_openai_json(prompt) or fallback_digest(candidates_by_section, today)
    digest = hydrate_digest(raw_digest, candidates_by_section, today)
    page_url = write_outputs(digest)
    send_discord(digest, page_url)
    print(f"Generated digest for {today}.")


if __name__ == "__main__":
    main()
