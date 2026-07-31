import urllib.request
import re

def search_chunk(js_path):
    js_url = 'https://chatjimmy.ai' + js_path
    content = urllib.request.urlopen(urllib.request.Request(js_url, headers={'User-Agent': 'Mozilla/5.0'})).read().decode('utf-8', errors='ignore')
    print(f"\n================ CHUNK: {js_path} ================")
    # Print occurrences of fetch, api, chat, body, messages
    for match in re.finditer(r'useChat|\/api\/chat|body:|messages:|systemPrompt|topK|top_k', content):
        start = max(0, match.start() - 200)
        end = min(len(content), match.start() + 300)
        print(f"--- Match '{match.group(0)}' at {match.start()} ---")
        print(content[start:end])

search_chunk('/_next/static/chunks/8642-5fe380e15c27e6e8.js')
search_chunk('/_next/static/chunks/app/page-fa2c10f6f6dff069.js')
