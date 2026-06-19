"""Reddit Public .json endpoint — Both modes. No API key required."""
import httpx

HEADERS = {"User-Agent": "elixir-research/1.0 (medical research tool)"}

def fetch_reddit(query: str, max_threads: int = 3) -> list[dict]:
    snippets = []
    search_url = f"https://www.reddit.com/search.json?q={query}&sort=relevance&t=year&limit={max_threads}"

    try:
        with httpx.Client(headers=HEADERS, timeout=10) as client:
            resp = client.get(search_url)

            # bot trap: reddit sometimes returns html instead of json
            content_type = resp.headers.get("content-type", "")
            if "text/html" in content_type:
                return []

            data = resp.json()
            threads = data.get("data", {}).get("children", [])

            for thread in threads[:max_threads]:
                post = thread["data"]
                thread_url = f"https://www.reddit.com{post['permalink']}.json"

                try:
                    t_resp = client.get(thread_url)
                    if "text/html" in t_resp.headers.get("content-type", ""):
                        continue

                    t_data = t_resp.json()
                    comments = t_data[1]["data"]["children"]
                    top_comments = [
                        c["data"].get("body", "")
                        for c in comments[:5]
                        if c["kind"] == "t1"
                    ]
                    combined = post.get("selftext", "") + " " + " ".join(top_comments)
                    if combined.strip():
                        snippets.append({
                            "source_id": f"reddit:{post['id']}",
                            "source_name": "Reddit",
                            "url": f"https://reddit.com{post['permalink']}",
                            "text": combined[:1500],
                            "entity_fingerprint": set(),
                            "source_tier": "contextual",
                        })
                except Exception:
                    continue
    except Exception:
        pass
    return snippets
