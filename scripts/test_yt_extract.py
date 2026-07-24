"""Standalone test: extract first YouTube video ID via HTTP+regex."""
import re
import requests
from urllib.parse import quote

q = "arijit singh song"
url = f"https://www.youtube.com/results?search_query={quote(q)}"

resp = requests.get(
    url,
    params={"hl": "en", "gl": "IN"},
    headers={
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
    },
    timeout=10,
)
html = resp.text
print(f"Status: {resp.status_code} | HTML length: {len(html)}")

ids = re.findall(r'"videoId"\s*:\s*"([a-zA-Z0-9_-]{11})"', html)
seen = set()
unique = []
for v in ids:
    if v not in seen:
        seen.add(v)
        unique.append(v)

print(f"Found {len(unique)} unique video IDs")
if unique:
    vid = unique[0]
    print(f"First video ID: {vid}")
    # Try title extraction
    tm = re.search(
        r'"videoId"\s*:\s*"' + re.escape(vid) + r'".*?"title"\s*:\s*\{\s*"runs"\s*:\s*\[\s*\{\s*"text"\s*:\s*"([^"]+)"',
        html,
    )
    title = tm.group(1) if tm else "N/A"
    print(f"Title: {title}")
    print(f"URL: https://www.youtube.com/watch?v={vid}&autoplay=1")
else:
    print("NO VIDEO IDS FOUND - regex may need updating")
    # Debug: show a snippet around 'videoId' if present
    idx = html.find("videoId")
    if idx >= 0:
        print(f"'videoId' found at index {idx}, snippet: {html[idx:idx+100]}")
    else:
        print("'videoId' NOT found in HTML at all")
