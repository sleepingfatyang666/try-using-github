import json
import re
import time

import requests

import daily_digest


_call_openai_json = daily_digest.call_openai_json


def clean_title(title, source=""):
    title = daily_digest.clean_text(title, 220)
    if source:
        title = re.sub(rf"\s+-\s+{re.escape(source)}$", "", title, flags=re.IGNORECASE).strip()
    return title


def short_summary(value, limit=220):
    value = daily_digest.clean_text(value, 500)
    if not value:
        return ""
    value = re.sub(r"\bSee more headlines.*$", "", value, flags=re.IGNORECASE).strip()
    value = re.sub(r"\bComprehensive up-to-date news coverage.*$", "", value, flags=re.IGNORECASE).strip()
    sentences = re.split(r"(?<=[.!?。！？])\s+", value)
    return daily_digest.clean_text(" ".join(sentences[:2]) if sentences else value, limit)


def fallback_summaries(candidate):
    source = candidate.source or "Source"
    if candidate.source == "GitHub":
        summary = short_summary(candidate.summary or candidate.meta, 240)
        return f"项目简介：{summary or candidate.meta}", summary or candidate.meta or "Open the repository for details."
    if candidate.source == "arXiv":
        summary = short_summary(candidate.summary, 240)
        return f"论文条目，来源：arXiv。{summary or '点击链接查看摘要和论文详情。'}", summary or "Open the paper for its abstract and details."
    summary = short_summary(candidate.summary, 200)
    return f"来源：{source}。{summary or '点击链接阅读完整报道。'}", summary or f"Source: {source}. Open the source link for the full report."


def prompt_payload(candidates_by_section, today):
    compact = {
        key: [
            {
                "id": candidate.id,
                "title": clean_title(candidate.title, candidate.source),
                "source": candidate.source,
                "summary": short_summary(candidate.summary, 260),
                "published": candidate.published,
                "meta": candidate.meta,
            }
            for candidate in values[: max(daily_digest.SECTION_META.get(key, {}).get("count", 6) * 2, 8)]
        ]
        for key, values in candidates_by_section.items()
    }
    return json.dumps(
        {
            "date": today.isoformat(),
            "requested_counts": {key: value["count"] for key, value in daily_digest.SECTION_META.items()},
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


def fallback_digest(candidates_by_section, today):
    sections = []
    for key, meta in daily_digest.SECTION_META.items():
        items = []
        for candidate in candidates_by_section.get(key, [])[: meta["count"]]:
            zh_summary, en_summary = fallback_summaries(candidate)
            title = clean_title(candidate.title, candidate.source)
            items.append(
                {
                    "source_id": candidate.id,
                    "zh_title": title,
                    "en_title": title,
                    "zh_summary": zh_summary,
                    "en_summary": en_summary,
                }
            )
        sections.append({"key": key, "zh_title": meta["zh_title"], "en_title": meta["en_title"], "items": items})
    return {
        "date": today.isoformat(),
        "headline_zh": "今日综合简报已生成。",
        "headline_en": "Today's digest has been generated.",
        "sections": sections,
    }


def discord_blocks(digest, page_url):
    header = [
        f"**每日综合简报 / Daily Digest — {digest['date']}**",
        digest.get("headline_zh", ""),
        digest.get("headline_en", ""),
        f"完整页面 / Full page: {page_url}" if page_url else "完整页面会保存到 GitHub Pages。",
    ]
    blocks = ["\n".join(part for part in header if part)]
    for section in digest["sections"]:
        lines = [f"\n**{section['zh_title']} / {section['en_title']}**"]
        for item in section["items"][:3]:
            item_lines = [f"- [{daily_digest.clean_text(item['zh_title'], 180)}]({item['url']})"]
            if daily_digest.clean_text(item.get("en_title"), 180) != daily_digest.clean_text(item.get("zh_title"), 180):
                item_lines.append(f"  EN: {daily_digest.clean_text(item['en_title'], 180)}")
            item_lines.append(f"  简介: {daily_digest.clean_text(item['zh_summary'], 220)}")
            if daily_digest.clean_text(item.get("en_summary"), 220) != daily_digest.clean_text(item.get("zh_summary"), 220):
                item_lines.append(f"  Summary: {daily_digest.clean_text(item['en_summary'], 220)}")
            meta_parts = [item.get("source", ""), item.get("published", ""), item.get("meta", "")]
            meta = " · ".join(part for part in meta_parts if part)
            if meta:
                item_lines.append(f"  Source: {daily_digest.clean_text(meta, 160)}")
            lines.append("\n".join(item_lines))
        if len(section["items"]) > 3:
            lines.append(f"  还有 {len(section['items']) - 3} 条在完整页面中 / More in the full page.")
        blocks.append("\n".join(lines))
    return blocks


def call_openai_json_with_fallback(prompt):
    for attempt in range(1, 3):
        try:
            return _call_openai_json(prompt)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            body = daily_digest.clean_text(exc.response.text if exc.response is not None else "", 500)
            print(f"OpenAI request failed with HTTP {status}: {body}")
            if status == 429 and attempt == 1:
                retry_after = exc.response.headers.get("retry-after") if exc.response is not None else ""
                wait_seconds = int(retry_after) if retry_after and retry_after.isdigit() else 20
                print(f"Retrying OpenAI request after {wait_seconds} seconds.")
                time.sleep(wait_seconds)
                continue
            print("Using fallback digest because OpenAI did not return a usable response.")
            return None
        except requests.RequestException as exc:
            print(f"OpenAI request failed: {exc}")
            print("Using fallback digest because OpenAI did not return a usable response.")
            return None


daily_digest.prompt_payload = prompt_payload
daily_digest.fallback_digest = fallback_digest
daily_digest.discord_blocks = discord_blocks
daily_digest.call_openai_json = call_openai_json_with_fallback
daily_digest.main()
