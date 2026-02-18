import os
import json
import requests
import feedparser
import time
import re
import random
import warnings 
import string
from datetime import datetime
from slugify import slugify
from io import BytesIO
from PIL import Image
from groq import Groq, APIError, RateLimitError

# --- SUPPRESS WARNINGS ---
warnings.filterwarnings("ignore", category=FutureWarning)

# --- GOOGLE INDEXING LIBS ---
try:
    from oauth2client.service_account import ServiceAccountCredentials
    from googleapiclient.discovery import build
    GOOGLE_LIBS_AVAILABLE = True
except ImportError:
    GOOGLE_LIBS_AVAILABLE = False

# ==========================================
# ⚙️ CONFIGURATION (FINANCE / WRITRACK)
# ==========================================

GROQ_KEYS_RAW = os.environ.get("GROQ_API_KEY", "") 
GROQ_API_KEYS = [k.strip() for k in GROQ_KEYS_RAW.split(",") if k.strip()]

WEBSITE_URL = "https://writrack.web.id" 
INDEXNOW_KEY = "e74819b68a0f40e98f6ec3dc24f610f0" 
GOOGLE_JSON_KEY = os.environ.get("GOOGLE_INDEXING_KEY", "") 

if not GROQ_API_KEYS:
    print("❌ FATAL ERROR: Groq API Key is missing!")
    exit(1)

# Penulis dengan Persona Spesifik (Wall Street Style)
AUTHOR_PROFILES = [
    "Michael Sterling (Senior Market Analyst)", 
    "Sarah Vanhouten (Certified Financial Planner - CFP)",
    "David Chen (Crypto & Tech Strategist)", 
    "Amanda Roy (Real Estate Investor)",
    "Robert K. Wilson (Global Economy Observer)"
]

VALID_CATEGORIES = [
    "Stock Market", "Personal Finance", "Crypto & Blockchain", 
    "Real Estate News", "Global Economy", "Retirement Planning", "ETF & Mutual Funds"
]

RSS_SOURCES = {
    "CNBC Investing": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15839069",
    "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
    "Investing.com": "https://www.investing.com/rss/news.rss",
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/"
}

CONTENT_DIR = "content/articles" 
IMAGE_DIR = "static/images"
DATA_DIR = "automation/data"
MEMORY_FILE = f"{DATA_DIR}/link_memory.json"

# 🔥 TARGET: 2 Artikel per sumber (Quality Focus)
TARGET_PER_SOURCE = 2

# ==========================================
# 🧠 HELPER FUNCTIONS
# ==========================================
def load_link_memory():
    if not os.path.exists(MEMORY_FILE): return {}
    try:
        with open(MEMORY_FILE, 'r') as f: return json.load(f)
    except: return {}

def save_link_to_memory(title, slug):
    os.makedirs(DATA_DIR, exist_ok=True)
    memory = load_link_memory()
    memory[title] = f"/articles/{slug}" 
    if len(memory) > 500: memory = dict(list(memory.items())[-500:])
    with open(MEMORY_FILE, 'w') as f: json.dump(memory, f, indent=2)

def fetch_rss_feed(url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    try:
        response = requests.get(url, headers=headers, timeout=15)
        return feedparser.parse(response.content) if response.status_code == 200 else None
    except: return None

def clean_ai_content(text):
    if not text: return ""
    text = re.sub(r'^```[a-zA-Z]*\n', '', text)
    text = re.sub(r'\n```$', '', text)
    text = text.replace("```", "")
    
    # Hapus Header Basi & Disclaimer AI (Agar bersih)
    text = re.sub(r'^##\s*(Introduction|Conclusion|Summary|The Verdict|Final Thoughts|Disclaimer)\s*\n', '', text, flags=re.MULTILINE|re.IGNORECASE)
    
    text = text.replace("<h1>", "# ").replace("</h1>", "\n")
    text = text.replace("<h2>", "## ").replace("</h2>", "\n")
    text = text.replace("<h3>", "### ").replace("</h3>", "\n")
    text = text.replace("<h4>", "#### ").replace("</h4>", "\n")
    text = text.replace("<b>", "**").replace("</b>", "**")
    text = text.replace("<p>", "").replace("</p>", "\n\n")
    return text.strip()

# ==========================================
# 🧠 SMART SILO LINKING
# ==========================================
def get_contextual_links(current_title):
    memory = load_link_memory()
    items = list(memory.items())
    if not items: return []
    
    stop_words = ['the', 'a', 'an', 'in', 'on', 'at', 'for', 'to', 'of', 'and', 'with', 'is', 'stock', 'market', 'price'] 
    keywords = [w.lower() for w in current_title.split() if w.lower() not in stop_words and len(w) > 3]
    relevant_links = []
    
    for title, url in items:
        title_lower = title.lower()
        match_score = sum(1 for k in keywords if k in title_lower)
        if match_score > 0:
            relevant_links.append((title, url))
    
    if relevant_links:
        count = min(3, len(relevant_links))
        return random.sample(relevant_links, count)
    
    count = min(3, len(items))
    return random.sample(items, count)

def inject_links_into_body(content_body, current_title):
    links = get_contextual_links(current_title)
    if not links: return content_body

    link_box = "\n\n> **💰 Recommended Analysis:**\n"
    for title, url in links:
        link_box += f"> - [{title}]({url})\n"
    link_box += "\n"

    paragraphs = content_body.split('\n\n')
    if len(paragraphs) < 5: return content_body + link_box
    insert_pos = 3
    paragraphs.insert(insert_pos, link_box)
    return "\n\n".join(paragraphs)

# ==========================================
# 🚀 INDEXING FUNCTIONS
# ==========================================
def submit_to_indexnow(url):
    try:
        endpoint = "https://api.indexnow.org/indexnow"
        host = WEBSITE_URL.replace("https://", "").replace("http://", "")
        data = {
            "host": host, "key": INDEXNOW_KEY,
            "keyLocation": f"https://{host}/{INDEXNOW_KEY}.txt",
            "urlList": [url]
        }
        requests.post(endpoint, json=data, headers={'Content-Type': 'application/json; charset=utf-8'}, timeout=10)
        print(f"      🚀 IndexNow Submitted")
    except Exception as e: print(f"      ⚠️ IndexNow Failed: {e}")

def submit_to_google(url):
    if not GOOGLE_JSON_KEY or not GOOGLE_LIBS_AVAILABLE: return
    try:
        creds_dict = json.loads(GOOGLE_JSON_KEY)
        SCOPES = ["https://www.googleapis.com/auth/indexing"]
        credentials = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, SCOPES)
        service = build("indexing", "v3", credentials=credentials)
        body = {"url": url, "type": "URL_UPDATED"}
        service.urlNotifications().publish(body=body).execute()
        print(f"      🚀 Google Indexing Submitted")
    except Exception as e: print(f"      ⚠️ Google Indexing Error: {e}")

# ==========================================
# 🎨 IMAGE GENERATOR (NO POLLINATIONS - HYBRID ENGINE)
# ==========================================
def generate_robust_image(prompt, filename):
    output_path = f"{IMAGE_DIR}/{filename}"
    
    # 1. Bersihkan Prompt & Paksa Style Realistis
    # Kita hapus karakter aneh dan tambahkan keywords agar hasilnya seperti foto Bloomberg/Reuters
    clean_prompt = prompt.lower().replace('"', '').replace("'", "")
    
    forced_style = (
        "photorealistic, 8k, cinematic lighting, macro photography, "
        "stock market chart background, bokeh, professional financial news style, "
        "sharp focus, fujifilm color grading, highly detailed, no text"
    )
    
    final_prompt = f"{clean_prompt}, {forced_style}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://google.com"
    }

    print(f"      🎨 Generating Image: {clean_prompt[:30]}...")

    # --- STRATEGY 1: HERCAI (AI Generation - Free Alternative) ---
    # Menggantikan Pollinations dengan Hercai. 
    # Hercai menggunakan berbagai model (v3/lexica) yang gratis.
    try:
        # Encode prompt agar aman di URL
        encoded_prompt = requests.utils.quote(final_prompt)
        # Menggunakan endpoint v3 text2image
        hercai_url = f"https://hercai.onrender.com/v3/text2image?prompt={encoded_prompt}"
        
        # Timeout diset 60 detik karena free tier server kadang butuh waktu bangun (cold start)
        resp = requests.get(hercai_url, headers=headers, timeout=60)
        
        if resp.status_code == 200:
            data = resp.json()
            if "url" in data and data["url"]:
                img_url = data["url"]
                
                # Download gambar hasil generate
                img_resp = requests.get(img_url, headers=headers, timeout=30)
                if img_resp.status_code == 200:
                    img = Image.open(BytesIO(img_resp.content)).convert("RGB")
                    
                    # Resize standar HD (1280x720) agar loading web cepat & konsisten
                    img = img.resize((1280, 720), Image.LANCZOS)
                    
                    img.save(output_path, "WEBP", quality=90)
                    print("      ✅ Image Saved (Source: Hercai AI)")
                    return f"/images/{filename}"
    except Exception as e:
        print(f"      ⚠️ Hercai AI Failed, switching to Stock Photo... ({str(e)[:50]})")

    # --- STRATEGY 2: LOREMFLICKR (Real Stock Photos - Fallback) ---
    # Jika AI gagal/down, kita ambil FOTO ASLI dari LoremFlickr.
    # Ini sangat cocok untuk niche Finance karena hasilnya pasti relevan & profesional.
    try:
        # Rotasi keyword agar gambarnya tidak itu-itu saja
        fallback_keywords = ["finance", "stockmarket", "wallstreet", "bitcoin", "business", "office", "money"]
        chosen_keyword = random.choice(fallback_keywords)
        
        # URL ini akan mereturn gambar random sesuai keyword berukuran 1280x720
        flickr_url = f"https://loremflickr.com/1280/720/{chosen_keyword}"
        
        print(f"      🔄 Fetching Real Stock Photo ({chosen_keyword})...")
        resp = requests.get(flickr_url, headers=headers, timeout=30, allow_redirects=True)
        
        if resp.status_code == 200:
            img = Image.open(BytesIO(resp.content)).convert("RGB")
            img.save(output_path, "WEBP", quality=90)
            print(f"      ✅ Image Saved (Source: LoremFlickr - {chosen_keyword})")
            return f"/images/{filename}"
    except Exception as e:
        print(f"      ⚠️ Stock Photo Failed: {e}")

    # --- STRATEGY 3: DEFAULT LOCAL IMAGE (Last Resort) ---
    # Pastikan Anda punya gambar ini di folder static/images/
    print("      ⚠️ Using Default Static Image")
    return "/images/default-finance.webp"

# ==========================================
# 🧠 CONTENT ENGINE (1500 WORDS + NO AI DISCLAIMER)
# ==========================================

def get_groq_article_json(title, summary, link, author_name):
    current_date = datetime.now().strftime("%Y-%m-%d")
    
    structures = [
        "COMPREHENSIVE_ANALYSIS (Cover: Current Event, Historical Context, Market Impact, Technical Analysis, Expert Opinions)",
        "INVESTOR_DEEP_DIVE (Cover: Fundamentals, Valuation, Risk Factors, Competitive Landscape, Future Outlook)",
        "MACRO_ECONOMIC_REPORT (Cover: Data Release, Fed Implications, Sector Rotations, Global Ripple Effects)"
    ]
    chosen_structure = random.choice(structures)

    system_prompt = f"""
    You are {author_name}, a seasoned senior financial analyst for the US Market (Wall Street).
    Current Date: {current_date}.
    
    OBJECTIVE: Write a **DEEP DIVE, LONG-FORM (1500+ Words)** financial analysis.
    TARGET AUDIENCE: Institutional Investors, Sophisticated Traders, and Business Professionals.
    STRUCTURE STYLE: {chosen_structure}.
    
    🚫 NEGATIVE CONSTRAINTS (CRITICAL):
    1. **NO DISCLAIMERS**: DO NOT write a disclaimer at the end. I will add the legal text programmatically.
    2. **NO GENERIC HEADERS**: Do NOT use "Introduction", "Conclusion", "Summary". Start straight with the thesis.
    3. **NO FLUFF**: Do not repeat the same point. Expand by adding historical data, competitor analysis, or technical levels.
    
    ✅ MANDATORY REQUIREMENTS:
    1. **LENGTH**: The article MUST be comprehensive (aim for 1200-1500 words). Use multiple sub-sections.
    2. **DATA TABLE**: You MUST include a detailed Markdown Table (e.g., Financial Metrics, Peer Comparison).
    3. **HIERARCHY**: Use H2 (##) for major sections, H3 (###) for deeper analysis, and H4 (####) for specific data points.
    4. **FAQ**: Add a "Frequently Asked Questions" section at the very end with 3 complex questions.
    5. **VISUAL KEYWORD**: Describe a specific financial scene for the image generator.
    
    OUTPUT FORMAT (JSON):
    {{
        "title": "Professional, Click-Worthy Headline",
        "description": "SEO Meta description (150 chars)",
        "category": "One of: {', '.join(VALID_CATEGORIES)}",
        "main_keyword": "Visual prompt...",
        "tags": ["tag1", "tag2", "tag3", "tag4"],
        "content_body": "The full long-form markdown content..."
    }}
    """
    
    user_prompt = f"""
    SOURCE MATERIAL:
    - Headline: {title}
    - Summary: {summary}
    - Link: {link}
    
    Write the 1500-word analysis now.
    """
    
    for api_key in GROQ_API_KEYS:
        client = Groq(api_key=api_key)
        try:
            print(f"      🤖 AI Writing ({chosen_structure.split()[0]} - Long Form)...")
            completion = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.6,
                max_tokens=7500,
                response_format={"type": "json_object"}
            )
            return completion.choices[0].message.content
        except RateLimitError:
            print("      ⚠️ Rate Limit Hit, switching key...")
            time.sleep(2)
        except Exception: pass
    return None

# ==========================================
# 🏁 MAIN WORKFLOW
# ==========================================
def main():
    os.makedirs(CONTENT_DIR, exist_ok=True)
    os.makedirs(IMAGE_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)

    print("🔥 ENGINE STARTED: WRITRACK PRO (1500 WORDS + CLEAN DISCLAIMER)")

    for source_name, rss_url in RSS_SOURCES.items():
        print(f"\n📡 Reading: {source_name}")
        feed = fetch_rss_feed(rss_url)
        if not feed: continue

        processed_count = 0
        
        for entry in feed.entries:
            if processed_count >= TARGET_PER_SOURCE:
                print(f"   🛑 Target reached for {source_name}")
                break
            
            clean_title = entry.title.split(" - ")[0]
            slug = slugify(clean_title, max_length=60, word_boundary=True)
            filename = f"{slug}.md"
            
            if os.path.exists(f"{CONTENT_DIR}/{filename}"): 
                continue
            
            # 🛡️ Anti-Crash: Handle Missing Summary
            entry_summary = ""
            if hasattr(entry, 'summary'): entry_summary = entry.summary
            elif hasattr(entry, 'description'): entry_summary = entry.description
            else: entry_summary = clean_title 

            print(f"   ⚡ Processing: {clean_title[:40]}...")
            
            author = random.choice(AUTHOR_PROFILES)
            raw_json = get_groq_article_json(clean_title, entry_summary, entry.link, author)
            
            if not raw_json: continue
            try:
                data = json.loads(raw_json)
            except:
                print("      ❌ JSON Parse Error")
                continue

            # 1. Generate Image (Dengan Metode Pollinations Flux)
            image_prompt = data.get('main_keyword', clean_title)
            final_img_path = generate_robust_image(image_prompt, f"{slug}.webp")
            
            # 2. Clean Content (Hapus Disclaimer buatan AI)
            clean_body = clean_ai_content(data['content_body'])
            
            # 3. Inject Links (Silo)
            final_body_with_links = inject_links_into_body(clean_body, data['title'])
            
            # 4. Fallback Category
            if data.get('category') not in VALID_CATEGORIES:
                data['category'] = "Stock Market"

            # 5. HARDCODED DISCLAIMER (Satu-satunya yang muncul)
            footer_disclaimer = """
---
### **Disclaimer**
*The content provided on **WriTrack.web.id** is for **informational and educational purposes only**. It should not be construed as professional financial advice, investment recommendation, or a solicitation to buy or sell any securities. Trading stocks, cryptocurrencies, and other financial assets involves high risk. **Always consult with a licensed financial advisor before making any investment decisions.** The authors may hold positions in the securities mentioned.*
"""
            
            md_content = f"""---
title: "{data['title'].replace('"', "'")}"
date: {datetime.now().strftime("%Y-%m-%dT%H:%M:%S+00:00")}
author: "{author}"
categories: ["{data['category']}"]
tags: {json.dumps(data.get('tags', []))}
featured_image: "{final_img_path}"
description: "{data['description'].replace('"', "'")}"
slug: "{slug}"
url: "/{slug}/"
draft: false
weight: {random.randint(1, 10)}
---

{final_body_with_links}

{footer_disclaimer}

---
*Source Reference: Analysis by {author} based on reports from [{source_name}]({entry.link}).*
"""
            with open(f"{CONTENT_DIR}/{filename}", "w", encoding="utf-8") as f:
                f.write(md_content)
            
            # 6. Save & Index
            save_link_to_memory(data['title'], slug)
            
            full_url = f"{WEBSITE_URL}/articles/{slug}/"
            submit_to_indexnow(full_url)
            submit_to_google(full_url)

            print(f"      ✅ Published: {slug}")
            processed_count += 1
            
            # Delay natural
            print("      💤 Sleeping for 60s (Deep Dive Processing)...")
            time.sleep(60)

if __name__ == "__main__":
    main()
