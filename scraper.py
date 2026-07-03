from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import urljoin

import requests
from markdownify import markdownify as md
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ============================================================
# Configuration
# ============================================================

BASE_URL = "https://support.optisigns.com"
API_BASE = f"{BASE_URL}/api/v2/help_center"

# Where Markdown files will be stored
OUTPUT_DIR = Path("articles")

# Stores article metadata + content hashes + vector-store IDs
MANIFEST_PATH = Path("manifest.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; OptiSigns-Exporter/1.0)"
}


# ============================================================
# HTTP session with retries
# ============================================================

def build_session() -> requests.Session:
    """
    Build a requests session with retry behavior.

    Why:
    - daily jobs should not fail because of temporary 429 / 5xx responses
    - retries help make the scraper more robust in CI / cron / cloud jobs
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    return session


session = build_session()


# ============================================================
# Generic utilities
# ============================================================

def slugify(text: str) -> str:
    """
    Convert text into a filesystem-friendly slug.

    Example:
        "How to Add a YouTube Video?" -> "how-to-add-a-youtube-video"

    Notes:
    - this is intentionally simple and ASCII-oriented for stable file paths
    - article filenames still include article_id, so slug collisions are fine
    """
    text = (text or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "untitled"


def load_json(path: Path, default: Any) -> Any:
    """
    Load JSON if the file exists, otherwise return `default`.
    """
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def write_json(path: Path, data: Any) -> None:
    """
    Write JSON with UTF-8 and pretty indentation.
    """
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def sha256_text(text: str) -> str:
    """
    Compute a SHA-256 hash for a text string.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def remove_file_if_exists(path_str: str | None) -> None:
    """
    Best-effort delete of a file path.
    Used when:
    - an article was deleted upstream
    - an article moved to a new path because its title/category/section changed
    """
    if not path_str:
        return

    try:
        path = Path(path_str)
        if path.exists():
            path.unlink()
    except OSError:
        # We don't want the whole daily job to fail just because a local file
        # could not be deleted.
        pass


# ============================================================
# Zendesk API helpers
# ============================================================

def fetch_paginated(url: str, key: str) -> List[dict]:
    """
    Fetch all pages from a Zendesk Help Center endpoint.

    Zendesk pagination shape looks like:
        {
          "articles": [...],
          "next_page": "https://..."
        }

    Parameters
    ----------
    url:
        First page URL.
    key:
        JSON key to collect from each page, e.g. "articles", "sections", "categories".

    Returns
    -------
    List[dict]
        Flattened list across all pages.
    """
    items: List[dict] = []

    while url:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        items.extend(data.get(key, []))
        url = data.get("next_page")

    return items


def fetch_categories() -> List[dict]:
    """
    Fetch all help-center categories.
    """
    return fetch_paginated(f"{API_BASE}/categories.json?per_page=100", "categories")


def fetch_sections() -> List[dict]:
    """
    Fetch all help-center sections.
    """
    return fetch_paginated(f"{API_BASE}/sections.json?per_page=100", "sections")


def fetch_articles() -> List[dict]:
    """
    Fetch all help-center articles.

    We also filter out draft articles defensively.
    Public unauthenticated requests usually shouldn't return drafts,
    but this keeps the behavior safe if credentials are added later.
    """
    articles = fetch_paginated(f"{API_BASE}/articles.json?per_page=100", "articles")
    return [a for a in articles if not a.get("draft", False)]


# ============================================================
# Mapping helpers: category / section lookup
# ============================================================

def build_lookup_maps(
    categories: List[dict],
    sections: List[dict],
) -> Tuple[Dict[int, dict], Dict[int, dict]]:
    """
    Build two lookup dictionaries:
      1) category_map[category_id] -> {id, name, slug}
      2) section_map[section_id]   -> {id, name, slug, category_id}

    This allows us to map:
        article.section_id -> section -> category
    """
    category_map: Dict[int, dict] = {}
    for c in categories:
        category_id = c["id"]
        category_name = c.get("name", f"category-{category_id}")

        category_map[category_id] = {
            "id": category_id,
            "name": category_name,
            "slug": slugify(category_name),
        }

    section_map: Dict[int, dict] = {}
    for s in sections:
        section_id = s["id"]
        section_name = s.get("name", f"section-{section_id}")

        section_map[section_id] = {
            "id": section_id,
            "name": section_name,
            "slug": slugify(section_name),
            "category_id": s.get("category_id"),
        }

    return category_map, section_map


# ============================================================
# Content normalization
# ============================================================

def absolutize_relative_links(html_body: str) -> str:
    """
    Convert relative href/src links inside article HTML to absolute URLs.

    Why:
    - the assignment asks us to preserve links
    - relative links are awkward once the Markdown files live outside the website
    - absolute URLs are easier for the assistant to cite / for humans to open

    Examples:
      href="/hc/en-us/articles/123"  -> href="https://support.optisigns.com/hc/en-us/articles/123"
      src="/attachments/..."         -> src="https://support.optisigns.com/attachments/..."

    This is a lightweight regex-based normalization. It's good enough for this
    assignment because the HTML comes from Zendesk article bodies, not arbitrary pages.
    """
    if not html_body:
        return html_body

    # Replace href="/..." with absolute URL
    html_body = re.sub(
        r'href="/([^"]+)"',
        lambda m: f'href="{urljoin(BASE_URL, "/" + m.group(1))}"',
        html_body,
    )

    # Replace src="/..." with absolute URL
    html_body = re.sub(
        r'src="/([^"]+)"',
        lambda m: f'src="{urljoin(BASE_URL, "/" + m.group(1))}"',
        html_body,
    )

    return html_body


def html_to_markdown(html_body: str) -> str:
    """
    Convert article HTML -> Markdown.

    Goals:
    - keep headings
    - keep links
    - keep code blocks as well as markdownify allows
    - keep lists readable
    - avoid excessive blank lines

    Important note:
    Because we are using Zendesk's article body API, we are already avoiding
    site-wide nav/header/footer/ads. So we don't need a full DOM cleaner here.
    """
    html_body = absolutize_relative_links(html_body or "")

    markdown = md(
        html_body,
        heading_style="ATX",
        bullets="-",
    )

    # Normalize line endings
    markdown = markdown.replace("\r\n", "\n")

    # Collapse 3+ blank lines -> 2 blank lines
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)

    return markdown.strip()


def build_markdown(article: dict, category_name: str, section_name: str) -> str:
    """
    Build the final Markdown file content for one article.

    File structure:
      YAML front matter
      H1 title
      literal "Article URL: ..." line
      article body converted to Markdown

    We intentionally include BOTH:
      - YAML field: url: ...
      - visible body line: Article URL: ...

    because the assignment specifically wants the assistant to cite
    "Article URL:" lines in replies.
    """
    title = (article.get("title") or "").strip()
    article_id = article.get("id")
    article_url = article.get("html_url", "")
    updated_at = article.get("updated_at", "")
    body_md = html_to_markdown(article.get("body") or "")

    # Escape double quotes inside YAML string values
    safe_title = title.replace('"', '\\"')
    safe_category = category_name.replace('"', '\\"')
    safe_section = section_name.replace('"', '\\"')

    front_matter = f"""---
id: {article_id}
title: "{safe_title}"
url: {article_url}
category: "{safe_category}"
section: "{safe_section}"
updated_at: {updated_at}
---

# {title}

Article URL: {article_url}
"""

    if body_md:
        return f"{front_matter}\n{body_md}\n"

    return f"{front_matter}\n"


def document_hash(article: dict, category_name: str, section_name: str) -> str:
    """
    Compute a stable hash for the *meaningful document content*.

    Why not hash updated_at?
    ------------------------
    We do NOT want to mark an article as updated just because Zendesk changed
    the timestamp while the actual content stayed the same.

    What do we hash?
    ----------------
    We hash the parts that should trigger a rewrite/re-upload:
    - title
    - article URL
    - category name
    - section name
    - Markdown body

    This means we will detect:
    - article body edits
    - title changes
    - section/category moves
    - URL changes
    """
    title = (article.get("title") or "").strip()
    article_url = article.get("html_url", "")
    body_md = html_to_markdown(article.get("body") or "")

    stable_payload = {
        "title": title,
        "url": article_url,
        "category": category_name,
        "section": section_name,
        "body_md": body_md,
    }

    return sha256_text(
        json.dumps(stable_payload, ensure_ascii=False, sort_keys=True)
    )


# ============================================================
# Main scrape + sync logic
# ============================================================

def scrape_articles() -> dict:
    """
    Run the full scrape + local sync process.

    Returns a summary dict that main.py / uploader.py can use later.

    Return shape
    ------------
    {
      "total_articles_seen": int,
      "saved_manifest_entries": int,
      "added": ["123", ...],
      "updated": ["456", ...],
      "skipped": ["789", ...],
      "deleted": ["111", ...],
      "deleted_entries": {article_id: old_manifest_entry, ...},
      "errors": [{"article_id": ..., "reason": ...}, ...]
    }

    Notes
    -----
    - added / updated / skipped are based on content hash comparison
    - deleted means article existed in old manifest but no longer exists upstream
    - deleted_entries are kept so the uploader can remove old vector-store files
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Load previous run manifest if it exists.
    # This is what lets us detect deltas.
    old_manifest: Dict[str, dict] = load_json(MANIFEST_PATH, default={})

    # Fetch everything we need from Zendesk
    categories = fetch_categories()
    sections = fetch_sections()
    articles = fetch_articles()

    # Build lookup maps so article.section_id can be resolved to
    # section + category information
    category_map, section_map = build_lookup_maps(categories, sections)

    new_manifest: Dict[str, dict] = {}

    added: List[str] = []
    updated: List[str] = []
    skipped: List[str] = []
    errors: List[dict] = []

    for art in articles:
        article_id = art["id"]
        article_id_str = str(article_id)

        title = art.get("title", f"article-{article_id}")
        article_slug = slugify(title)

        section_id = art.get("section_id")
        section_info = section_map.get(section_id)

        # If we cannot resolve the article's section, keep going and log it
        # instead of crashing the whole job.
        if not section_info:
            errors.append({
                "article_id": article_id,
                "reason": f"Missing section mapping for section_id={section_id}",
            })
            continue

        category_info = category_map.get(section_info["category_id"])
        if not category_info:
            errors.append({
                "article_id": article_id,
                "reason": f"Missing category mapping for category_id={section_info['category_id']}",
            })
            continue

        category_name = category_info["name"]
        category_slug = category_info["slug"]
        section_name = section_info["name"]
        section_slug = section_info["slug"]

        # Build final file path:
        # articles/<category_slug>/<section_slug>/<article_id>-<article_slug>.md
        article_dir = OUTPUT_DIR / category_slug / section_slug
        article_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{article_id}-{article_slug}.md"
        filepath = article_dir / filename

        # Generate file content + content hash
        content = build_markdown(art, category_name, section_name)
        content_hash = document_hash(art, category_name, section_name)

        # Look up old manifest entry for delta detection
        old_entry = old_manifest.get(article_id_str)
        old_file = old_entry.get("file") if old_entry else None

        # Determine change status
        if not old_entry:
            status = "added"
        elif old_entry.get("hash") != content_hash:
            status = "updated"
        else:
            status = "skipped"

        # If article is new or changed, write the new Markdown file.
        # Also delete the old file if the path changed
        # (e.g. title changed -> slug changed, or section/category changed).
        if status in ("added", "updated"):
            if old_file and old_file != str(filepath):
                remove_file_if_exists(old_file)

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)

        # Build the new manifest entry.
        #
        # Important:
        # We carry over old openai_file_id if present so that later uploader.py
        # can decide whether it needs to delete/re-upload vector-store files.
        new_manifest[article_id_str] = {
            "id": article_id,
            "title": title,
            "slug": article_slug,
            "url": art.get("html_url"),
            "updated_at": art.get("updated_at"),
            "hash": content_hash,
            "file": str(filepath),
            "category": category_name,
            "category_slug": category_slug,
            "section": section_name,
            "section_slug": section_slug,
            "openai_file_id": old_entry.get("openai_file_id") if old_entry else None,
        }

        if status == "added":
            added.append(article_id_str)
        elif status == "updated":
            updated.append(article_id_str)
        else:
            skipped.append(article_id_str)

    # --------------------------------------------------------
    # Detect deleted articles
    # --------------------------------------------------------
    #
    # If an article existed in the old manifest but is no longer returned
    # by the API, treat it as deleted.
    #
    # We keep deleted_entries so the uploader can remove the old file from
    # the vector store even after the article disappears from the new manifest.
    deleted: List[str] = []
    deleted_entries: Dict[str, dict] = {}

    old_ids = set(old_manifest.keys())
    new_ids = set(new_manifest.keys())

    for removed_id in sorted(old_ids - new_ids):
        deleted.append(removed_id)
        deleted_entries[removed_id] = old_manifest[removed_id]

        old_file = old_manifest[removed_id].get("file")
        remove_file_if_exists(old_file)

    # Save the new manifest after all processing is done
    write_json(MANIFEST_PATH, new_manifest)

    return {
        "total_articles_seen": len(articles),
        "saved_manifest_entries": len(new_manifest),
        "added": added,
        "updated": updated,
        "skipped": skipped,
        "deleted": deleted,
        "deleted_entries": deleted_entries,
        "errors": errors,
    }


# ============================================================
# CLI entrypoint
# ============================================================

if __name__ == "__main__":
    result = scrape_articles()

    # Print a compact summary for local runs / cron logs / Docker logs
    print(json.dumps({
        "total_articles_seen": result["total_articles_seen"],
        "saved_manifest_entries": result["saved_manifest_entries"],
        "added": len(result["added"]),
        "updated": len(result["updated"]),
        "skipped": len(result["skipped"]),
        "deleted": len(result["deleted"]),
        "errors": len(result["errors"]),
    }, indent=2))
