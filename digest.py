"""
Daily pain-point digest from Hacker News, Reddit, and Indie Hackers.
Filters for real user complaints / feature requests, summarizes with DeepSeek.
"""
import os
import json
import smtplib
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests
from anthropic import Anthropic

# 全局代理配置 (如果环境变量中配置了 PROXY)
GLOBAL_PROXIES = None
proxy_url = os.environ.get("PROXY")
if proxy_url:
    GLOBAL_PROXIES = {
        "http": proxy_url,
        "https": proxy_url
    }

# ---------- Config ----------
SUBREDDITS = ["webdev", "SaaS", "indiehackers", "Entrepreneur", "SideProject", "macapps"]
HN_STORIES_LIMIT = 100  # newest stories to scan
LOOKBACK_HOURS = 24

# Keywords that signal a real pain point / unmet need
PAIN_KEYWORDS = [
    "looking for a tool", "looking for an api", "is there a tool",
    "is there an api", "is there a service", "is there any tool",
    "wish there was", "wish there were", "wish someone would",
    "anyone know of", "anyone built", "anyone made",
    "how do you handle", "how do you solve", "how do you manage",
    "frustrated with", "tired of", "sick of",
    "best way to", "what's the best", "any alternative",
    "recommend a tool", "recommend an api",
    "i hate that", "annoying that", "pain point",
    "currently using", "switching from",
    # 中文关键词
    "有没有工具", "求推荐", "寻找一款", "寻找一个",
    "怎么解决", "如何处理", "有什么办法", "有没有什么",
    "太难用了", "受够了", "烦死了", "痛点",
    "最好的方式", "平替", "替代品",
    "希望有人能做", "真希望有个",
    # B2B 关键词
    "my team needs", "company is looking for", "we are paying for", "enterprise alternative",
    "预算", "公司在找", "团队需要", "付费求"
]

USER_AGENT = "PainPointDigest/1.0 (personal research tool)"


try:
    import psycopg2
except ImportError:
    psycopg2 = None

# ---------- Database ----------
def get_db_conn():
    """获取数据库连接，优先使用 PostgreSQL，否则降级使用 SQLite"""
    pg_url = os.environ.get("PGSQL_URL")
    if pg_url:
        if psycopg2 is None:
            raise ImportError("请安装 psycopg2 以使用 PostgreSQL: pip install psycopg2-binary")
        return psycopg2.connect(pg_url), "pg"
    return sqlite3.connect('pain_points.db'), "sqlite"

def init_db():
    conn, db_type = get_db_conn()
    c = conn.cursor()
    if db_type == "pg":
        c.execute('''CREATE TABLE IF NOT EXISTS pain_points
                     (id SERIAL PRIMARY KEY,
                      source TEXT, url TEXT UNIQUE, title TEXT,
                      pain TEXT, product_idea TEXT, score INTEGER,
                      is_b2b BOOLEAN, created_at TIMESTAMP)''')
    else:
        c.execute('''CREATE TABLE IF NOT EXISTS pain_points
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      source TEXT, url TEXT UNIQUE, title TEXT,
                      pain TEXT, product_idea TEXT, score INTEGER,
                      is_b2b BOOLEAN, created_at DATETIME)''')
    conn.commit()
    conn.close()

def save_to_db(results):
    conn, db_type = get_db_conn()
    c = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    for r in results:
        try:
            if db_type == "pg":
                c.execute('''INSERT INTO pain_points 
                             (source, url, title, pain, product_idea, score, is_b2b, created_at)
                             VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                             ON CONFLICT (url) DO NOTHING''',
                          (r['source'], r['url'], r['title'], r.get('pain', ''), 
                           r.get('product_idea', ''), r.get('score', 0), 
                           bool(r.get('is_b2b', False)), now))
            else:
                c.execute('''INSERT OR IGNORE INTO pain_points 
                             (source, url, title, pain, product_idea, score, is_b2b, created_at)
                             VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                          (r['source'], r['url'], r['title'], r.get('pain', ''), 
                           r.get('product_idea', ''), r.get('score', 0), 
                           bool(r.get('is_b2b', False)), now))
        except Exception as e:
            print(f"[DB] Insert error: {e}")
    conn.commit()
    conn.close()

def get_recent_pain_points(days=7):
    conn, db_type = get_db_conn()
    c = conn.cursor()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    
    if db_type == "pg":
        c.execute("SELECT pain, product_idea FROM pain_points WHERE created_at >= %s", (cutoff,))
    else:
        c.execute("SELECT pain, product_idea FROM pain_points WHERE created_at >= ?", (cutoff,))
        
    rows = c.fetchall()
    conn.close()
    return [{"pain": r[0], "product_idea": r[1]} for r in rows]

def get_monthly_stats():
    conn, db_type = get_db_conn()
    c = conn.cursor()
    # Get the start of the current month
    first_day_of_month = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    
    if db_type == "pg":
        c.execute("SELECT COUNT(*) FROM pain_points WHERE created_at >= %s", (first_day_of_month,))
        total_points = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM pain_points WHERE created_at >= %s AND is_b2b = TRUE", (first_day_of_month,))
        b2b_points = c.fetchone()[0]
    else:
        c.execute("SELECT COUNT(*) FROM pain_points WHERE created_at >= ?", (first_day_of_month,))
        total_points = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM pain_points WHERE created_at >= ? AND is_b2b = 1", (first_day_of_month,))
        b2b_points = c.fetchone()[0]
    
    conn.close()
    return total_points, b2b_points

# ---------- Fetchers ----------
def fetch_hn():
    """Hacker News: newest stories + Ask HN posts."""
    items = []
    try:
        # Ask HN posts are usually the gold mine
        r = requests.get(
            "https://hn.algolia.com/api/v1/search_by_date",
            params={"tags": "ask_hn", "hitsPerPage": 50},
            timeout=15,
            proxies=GLOBAL_PROXIES
        )
        for hit in r.json().get("hits", []):
            text = (hit.get("title") or "") + " " + (hit.get("story_text") or "")
            items.append({
                "source": "HN Ask",
                "title": hit.get("title", ""),
                "text": hit.get("story_text") or "",
                "url": f"https://news.ycombinator.com/item?id={hit['objectID']}",
                "score": hit.get("points", 0),
                "comments": hit.get("num_comments", 0),
                "combined": text,
            })
    except Exception as e:
        print(f"[HN] error: {e}")
    return items


def fetch_reddit(subreddit):
    """Reddit public JSON endpoint, no auth needed."""
    items = []
    try:
        r = requests.get(
            f"https://www.reddit.com/r/{subreddit}/new.json",
            params={"limit": 100},
            headers={"User-Agent": USER_AGENT},
            timeout=15,
            proxies=GLOBAL_PROXIES
        )
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)).timestamp()
        for post in r.json().get("data", {}).get("children", []):
            d = post["data"]
            if d.get("created_utc", 0) < cutoff:
                continue
            text = (d.get("title") or "") + " " + (d.get("selftext") or "")
            items.append({
                "source": f"r/{subreddit}",
                "title": d.get("title", ""),
                "text": d.get("selftext") or "",
                "url": f"https://reddit.com{d.get('permalink', '')}",
                "score": d.get("score", 0),
                "comments": d.get("num_comments", 0),
                "combined": text,
            })
    except Exception as e:
        print(f"[Reddit r/{subreddit}] error: {e}")
    return items


def fetch_indiehackers():
    """Indie Hackers via their RSS feed."""
    items = []
    try:
        import xml.etree.ElementTree as ET
        r = requests.get(
            "https://www.indiehackers.com/feed.xml",
            headers={"User-Agent": USER_AGENT},
            timeout=15,
            proxies=GLOBAL_PROXIES
        )
        root = ET.fromstring(r.content)
        # RSS items live under channel/item
        for item in root.iter("item"):
            # Check date if available to apply LOOKBACK_HOURS
            pub_date_str = item.findtext("pubDate")
            if pub_date_str:
                try:
                    from email.utils import parsedate_to_datetime
                    pub_date = parsedate_to_datetime(pub_date_str)
                    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
                    if pub_date < cutoff:
                        continue
                except Exception:
                    pass # If date parsing fails, just include it

            title = (item.findtext("title") or "").strip()
            desc = (item.findtext("description") or "").strip()
            link = (item.findtext("link") or "").strip()
            items.append({
                "source": "IndieHackers",
                "title": title,
                "text": desc,
                "url": link,
                "score": 0,
                "comments": 0,
                "combined": title + " " + desc,
            })
    except Exception as e:
        print(f"[IndieHackers] error: {e}")
    return items


def fetch_v2ex():
    """V2EX API - fetch recent topics from 'create' (Share/Create) and 'qna' (Q&A) nodes."""
    items = []
    nodes = ["create", "qna"]
    try:
        for node in nodes:
            r = requests.get(
                f"https://www.v2ex.com/api/topics/show.json?node_name={node}",
                headers={"User-Agent": USER_AGENT},
                timeout=15,
                proxies=GLOBAL_PROXIES
            )
            # V2EX API returns a list of topics
            topics = r.json()
            if not isinstance(topics, list):
                continue
                
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)).timestamp()
            for t in topics:
                if t.get("created", 0) < cutoff:
                    continue
                
                title = t.get("title", "")
                content = t.get("content", "")
                text = title + " " + content
                
                items.append({
                    "source": f"V2EX ({node})",
                    "title": title,
                    "text": content,
                    "url": t.get("url", ""),
                    "score": t.get("replies", 0),  # V2EX doesn't have score in this endpoint, use replies as proxy
                    "comments": t.get("replies", 0),
                    "combined": text,
                })
            time.sleep(2) # Be polite
    except Exception as e:
        print(f"[V2EX] error: {e}")
    return items


def fetch_producthunt():
    """Product Hunt discussion discussions via RSS (requires no auth)."""
    items = []
    try:
        import xml.etree.ElementTree as ET
        r = requests.get(
            "https://www.producthunt.com/feed?category=discussions",
            headers={"User-Agent": USER_AGENT},
            timeout=15,
            proxies=GLOBAL_PROXIES
        )
        root = ET.fromstring(r.content)
        for item in root.iter("item"):
            # Check date
            pub_date_str = item.findtext("pubDate")
            if pub_date_str:
                try:
                    from email.utils import parsedate_to_datetime
                    pub_date = parsedate_to_datetime(pub_date_str)
                    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
                    if pub_date < cutoff:
                        continue
                except Exception:
                    pass

            title = (item.findtext("title") or "").strip()
            desc = (item.findtext("description") or "").strip()
            link = (item.findtext("link") or "").strip()
            items.append({
                "source": "ProductHunt",
                "title": title,
                "text": desc,
                "url": link,
                "score": 0,
                "comments": 0,
                "combined": title + " " + desc,
            })
    except Exception as e:
        print(f"[ProductHunt] error: {e}")
    return items


def fetch_devto():
    """Dev.to articles API (focuses on 'discuss' and 'help' tags)."""
    items = []
    tags = ["discuss", "help"]
    try:
        for tag in tags:
            r = requests.get(
                "https://dev.to/api/articles",
                params={"tag": tag, "top": 1},  # Get recent popular/relevant posts in these tags
                headers={"User-Agent": USER_AGENT},
                timeout=15,
                proxies=GLOBAL_PROXIES
            )
            articles = r.json()
            if not isinstance(articles, list):
                continue
                
            cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
            for a in articles:
                # parse 'published_at' (e.g., '2023-10-15T12:00:00Z')
                pub_date_str = a.get("published_at")
                if not pub_date_str:
                    continue
                    
                try:
                    pub_date = datetime.fromisoformat(pub_date_str.replace('Z', '+00:00'))
                    if pub_date < cutoff:
                        continue
                except Exception:
                    pass
                
                title = a.get("title", "")
                desc = a.get("description", "")
                text = title + " " + desc
                
                items.append({
                    "source": f"Dev.to ({tag})",
                    "title": title,
                    "text": desc,
                    "url": a.get("url", ""),
                    "score": a.get("public_reactions_count", 0),
                    "comments": a.get("comments_count", 0),
                    "combined": text,
                })
            time.sleep(1) # Be polite
    except Exception as e:
        print(f"[Dev.to] error: {e}")
    return items


def fetch_github_discussions():
    """GitHub Discussions via RSS (Example: vercel/next.js)."""
    # Note: GitHub doesn't have a global discussion feed, so we track a few huge repos
    # where developers often ask for tools/features.
    items = []
    repos = ["vercel/next.js", "facebook/react", "tailwindlabs/tailwindcss"]
    try:
        import xml.etree.ElementTree as ET
        for repo in repos:
            r = requests.get(
                f"https://github.com/{repo}/discussions.atom",
                headers={"User-Agent": USER_AGENT},
                timeout=15,
                proxies=GLOBAL_PROXIES
            )
            if r.status_code != 200:
                continue
                
            root = ET.fromstring(r.content)
            # Atom feed uses a namespace
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            for entry in root.findall('atom:entry', ns):
                # Check date
                updated_str = entry.findtext('atom:updated', namespaces=ns)
                if updated_str:
                    try:
                        updated_date = datetime.fromisoformat(updated_str.replace('Z', '+00:00'))
                        cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
                        if updated_date < cutoff:
                            continue
                    except Exception:
                        pass
                
                title = (entry.findtext('atom:title', namespaces=ns) or "").strip()
                link_el = entry.find('atom:link', namespaces=ns)
                link = link_el.attrib['href'] if link_el is not None else ""
                
                # GitHub RSS doesn't give full text easily in a clean way, we just use title
                items.append({
                    "source": f"GitHub ({repo})",
                    "title": title,
                    "text": "",
                    "url": link,
                    "score": 0,
                    "comments": 0,
                    "combined": title,
                })
            time.sleep(1)
    except Exception as e:
        print(f"[GitHub] error: {e}")
    return items


# ---------- Filtering ----------
def keyword_prefilter(items):
    """Cheap keyword filter to cut down what we send to DeepSeek."""
    kept = []
    for it in items:
        lower = it["combined"].lower()
        # must contain at least one pain keyword
        if not any(kw in lower for kw in PAIN_KEYWORDS):
            continue
        # too short usually = noise
        if len(it["combined"]) < 80:
            continue
        kept.append(it)
    return kept


def deepseek_classify(items, client):
    """Ask DeepSeek to score each item: is this a real, actionable pain point?"""
    if not items:
        return [], ""

    # Build a compact batch prompt
    numbered = []
    for i, it in enumerate(items):
        snippet = it["combined"][:800]
        numbered.append(f"[{i}] SOURCE: {it['source']}\nTITLE: {it['title']}\nTEXT: {snippet}")

    prompt = f"""You are helping an indie developer find real pain points worth building products for.

For each post below, decide:
1. Is this a REAL, SPECIFIC pain point or unmet need? (not generic advice-seeking, not vague)
2. Could a small SaaS/API/tool solve it?
3. Rate from 1-5 how promising it is as a product opportunity. **IMPORTANT: If it is a B2B need (teams/companies willing to pay), give it a higher score.**

For each item output a JSON line: {{"id": <number>, "score": <1-5>, "is_b2b": <true/false>, "pain": "<one-line summary IN CHINESE>", "product_idea": "<what could be built IN CHINESE>", "target_users": "<where to find first users IN CHINESE>", "competitors": "<existing alternatives & differentiation IN CHINESE>", "tech_stack": "<recommended MVP tech stack IN CHINESE>"}}

Only include items with score >= 3. Output ONLY JSON lines, one per item. DO NOT output any other text before or after the JSON lines.

Posts:
{chr(10).join(numbered)}
"""

    try:
        resp = client.messages.create(
            model="deepseek-chat",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text
    except Exception as e:
        print(f"[DeepSeek] Classification API 调用失败: {e}")
        return [], ""

    results = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
            idx = obj["id"]
            if 0 <= idx < len(items):
                merged = {**items[idx], **obj}
                results.append(merged)
        except (json.JSONDecodeError, KeyError):
            continue

    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    
    # 增加第二步：让 AI 基于筛选出的高分痛点，给出一个总体的商业分析和开发建议
    conclusion = ""
    if results:
        conclusion_prompt = """你是一个资深的独立开发者和商业顾问。
请基于以下我为你提供的“今日高分痛点列表”，写一段简短的总结结论（300字左右）。

你的结论必须包括：
1. 【趋势洞察】：今天大家主要在抱怨什么？有没有集中的痛点方向？
2. 【最佳切入点】：在这些痛点中，挑出 1-2 个最适合“单人独立开发者”在 1-2 周内做成 MVP（最小可行性产品）的商业点子，并简单说明为什么适合。

今日高分痛点列表：
"""
        for i, r in enumerate(results[:10]):  # 只拿前10个给AI分析，防止太长
            conclusion_prompt += f"\n痛点 {i+1}: {r.get('pain', '')}\n可能的产品: {r.get('product_idea', '')}\n"

        try:
            conclusion_resp = client.messages.create(
                model="deepseek-chat",
                max_tokens=1000,
                messages=[{"role": "user", "content": conclusion_prompt}],
            )
            conclusion = conclusion_resp.content[0].text
        except Exception as e:
            print(f"[DeepSeek] Conclusion API 调用失败: {e}")
            conclusion = "无法生成今日总结（API调用失败）。"

    return results, conclusion


def analyze_trends(client, recent_points):
    if not recent_points or len(recent_points) < 5:
        return "过去7天数据不足，暂无趋势聚类（需至少累积5个痛点）。"
        
    prompt = """你是一个资深商业分析师。这里是过去7天内收集到的痛点数据。
请帮我把这些散乱的痛点进行【聚类分析】（Clustering）。
找出被反复提及的 2-3 个核心需求大类。
【输出要求】：必须极其简短！每个大类只用一句话（不多于30字）概括其核心痛点和潜在方向。使用无序列表输出。

痛点数据：
"""
    for p in recent_points:
        prompt += f"- 痛点: {p['pain']} (潜在产品: {p['product_idea']})\n"
        
    try:
        resp = client.messages.create(
            model="deepseek-chat",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text
    except Exception as e:
        print(f"[DeepSeek] Trends API 调用失败: {e}")
        return "聚类分析生成失败。"


# ---------- Output ----------
def build_email(results, conclusion, trends, monthly_total, monthly_b2b):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    current_month_name = datetime.now(timezone.utc).strftime("%Y年%m月")
    if not results:
        return f"Pain-Point Digest (痛点挖掘日报) — {today}", "<p>过去 24 小时内没有发现有价值的痛点信号。</p>"

    subject = f"Pain-Point Digest (痛点挖掘日报) — {today} ({len(results)} 个信号)"
    
    # 将 AI 的结论放在邮件最显眼的位置
    html_parts = [
        f"<h2>痛点挖掘日报 — {today}</h2>",
        
        f"<div style='background-color:#f8f9fa; border-left:4px solid #0d6efd; padding:15px; margin-bottom:15px; border-radius:4px;'>",
        f"<h3 style='margin-top:0; color:#0d6efd;'>💡 今日洞察 (Daily Insight)</h3>",
        f"<div style='line-height:1.6; color:#333;'>{conclusion.replace(chr(10), '<br>')}</div>",
        f"</div>",

        f"<p>过去 24 小时内发现了 {len(results)} 个有价值的痛点信号，已按潜力评分排序。</p>"
    ]

    for r in results:
        stars = "★" * r.get("score", 0) + "☆" * (5 - r.get("score", 0))
        b2b_tag = "<span style='background:#0dcaf0; color:#000; padding:2px 6px; border-radius:12px; margin-right:8px; font-weight:bold; font-size:11px;'>B2B / 付费潜力</span>" if r.get("is_b2b") else ""
        
        html_parts.append(f"""
        <div style="border-left:3px solid #888;padding:8px 14px;margin:18px 0;">
            <div style="color:#666;font-size:12px; margin-bottom:6px;">
                {b2b_tag}
                <span style="display:inline-block; padding:2px 6px; background:#eee; border-radius:12px; margin-right:8px;">{r['source']}</span> 
                {stars} ({r.get('score', 0)}/5) · 👍 {r.get('score_x', r.get('score', 0))} 💬 {r.get('comments', 0)}
            </div>
            <div style="font-weight:bold;margin-top:8px;font-size:16px;"><a href="{r['url']}" style="color:#0969da;text-decoration:none;">{r['title']}</a></div>
            
            <div style="margin-top:12px; background:#fefefe; padding:10px; border-radius:4px; border:1px solid #eee;">
                <div style="margin-bottom:8px;">
                    <span style="font-size:14px;">😫</span> <b>痛点核心:</b> 
                    <span style="color:#b02a37; font-weight:500;">{r.get('pain', '')}</span>
                </div>
                <div style="margin-bottom:8px;">
                    <span style="font-size:14px;">🚀</span> <b>产品点子:</b> 
                    <span style="color:#198754;">{r.get('product_idea', '')}</span>
                </div>
                <div style="margin-bottom:8px;">
                    <span style="font-size:14px;">🎯</span> <b>获客渠道:</b> 
                    <span style="color:#666;">{r.get('target_users', '')}</span>
                </div>
                <div style="margin-bottom:8px;">
                    <span style="font-size:14px;">⚔️</span> <b>竞品分析:</b> 
                    <span style="color:#666;">{r.get('competitors', '')}</span>
                </div>
                <div>
                    <span style="font-size:14px;">💻</span> <b>MVP 技术栈:</b> 
                    <span style="color:#666;">{r.get('tech_stack', '')}</span>
                </div>
            </div>
            
            <div style="margin-top:10px; font-size:13px; color:#555; border-top:1px dashed #eee; padding-top:8px;">
                <b>💬 原始片段:</b> <i>"{r.get('text', '')[:150]}..."</i>
            </div>
        </div>
        """)

    # 把聚类分析和统计数据移到最下面
    html_parts.append(f"""
        <hr style="margin-top: 30px; border: 0; border-top: 1px solid #eee;">
        <div style='background-color:#fff3cd; border-left:4px solid #ffc107; padding:15px; margin-bottom:15px; margin-top:20px; border-radius:4px;'>
            <h3 style='margin-top:0; color:#b38100; font-size:15px;'>🔥 7天痛点聚类 (Weekly Trends)</h3>
            <div style='line-height:1.5; color:#333; font-size:14px;'>{trends.replace(chr(10), '<br>')}</div>
        </div>
        
        <div style='background-color:#e2e3e5; border-left:4px solid #6c757d; padding:15px; margin-bottom:20px; border-radius:4px;'>
            <h3 style='margin-top:0; color:#495057; font-size:15px;'>📊 本月统计 ({current_month_name})</h3>
            <div style='line-height:1.5; color:#333; font-size:14px;'>
                本月已累计为你挖掘 <b>{monthly_total}</b> 个高分商业痛点，其中 <b>{monthly_b2b}</b> 个具备 B2B 付费潜力。
            </div>
        </div>
    """)

    html_parts.append("<p style='color:#999;font-size:11px; text-align:center;'>自动生成。请在开发前阅读原文确认。</p>")
    return subject, "\n".join(html_parts)


def send_email(subject, html_body):
    smtp_host = os.environ["SMTP_HOST"]
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ["SMTP_USER"]  
    smtp_pass = os.environ["SMTP_PASS"] # 
    to_addr = os.environ["EMAIL_TO"] # 131

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = to_addr
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(smtp_host, smtp_port) as s:
        s.starttls()
        s.login(smtp_user, smtp_pass)
        s.send_message(msg)


# ---------- Main ----------
def main():
    init_db()
    print("Fetching sources...")
    items = []
    items += fetch_hn()
    for sub in SUBREDDITS:
        items += fetch_reddit(sub)
        time.sleep(2)  # be polite to Reddit
    items += fetch_indiehackers()
    items += fetch_v2ex()
    items += fetch_producthunt()
    items += fetch_devto()
    items += fetch_github_discussions()
    print(f"Fetched {len(items)} raw items.")

    filtered = keyword_prefilter(items)
    print(f"After keyword prefilter: {len(filtered)} items.")

    if not filtered:
        send_email("Pain-Point Digest (痛点挖掘日报) — 今日无信号", "<p>今天没有帖子匹配预设的痛点关键词。</p>")
        return

    # cap at 50 to control cost
    filtered = filtered[:50]

    client = Anthropic(api_key=os.environ["DEEPSEEK_API_KEY"], base_url="https://api.deepseek.com/anthropic")
    print("Classifying with DeepSeek (Anthropic API Compatibility)...")
    results, conclusion = deepseek_classify(filtered, client)
    
    if results:
        save_to_db(results)
        
    recent_points = get_recent_pain_points(days=7)
    print(f"Analyzing trends for {len(recent_points)} recent pain points...")
    trends = analyze_trends(client, recent_points)
    
    monthly_total, monthly_b2b = get_monthly_stats()
    
    print(f"Got {len(results)} scored signals.")

    subject, html = build_email(results, conclusion, trends, monthly_total, monthly_b2b)
    send_email(subject, html)
    print("Email sent.")

if __name__ == "__main__":
    main()
