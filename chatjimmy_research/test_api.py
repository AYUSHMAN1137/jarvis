import urllib.request
import json
import time

url = 'https://chatjimmy.ai/api/chat'
payload = {
    'messages': [{'role': 'user', 'content': 'Write a 150-word story about a fast AI.'}],
    'chatOptions': {
        'selectedModel': 'llama3.1-8B',
        'systemPrompt': 'You are a helpful assistant.',
        'topK': 8
    },
    'attachment': None
}

data = json.dumps(payload).encode('utf-8')
req = urllib.request.Request(
    url,
    data=data,
    headers={
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    },
    method='POST'
)

start_time = time.time()
ttft = None
full_text = ""

try:
    with urllib.request.urlopen(req) as resp:
        for chunk in resp:
            text_chunk = chunk.decode('utf-8')
            if not ttft and text_chunk:
                ttft = time.time() - start_time
            full_text += text_chunk

    total_time = time.time() - start_time
    print(f"Time to First Token (TTFT): {ttft:.4f}s")
    print(f"Total Time: {total_time:.4f}s")
    print(f"Full Text Length: {len(full_text)} chars")
    
    # Check for stats sentinel
    if "<|stats|>" in full_text:
        parts = full_text.split("<|stats|>")
        content = parts[0]
        stats = parts[1].replace("<|/stats|>", "").strip()
        print("\n--- Parsed Stats Sentinel ---")
        print(stats)
        print("\n--- Output Text (first 200 chars) ---")
        print(content[:200] + "...")
    else:
        print("\n--- Raw Output ---")
        print(full_text[:300])

except Exception as e:
    print('Error:', e)
