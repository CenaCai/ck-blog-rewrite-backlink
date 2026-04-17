#!/usr/bin/env python3
"""
自动内容分发脚本：从 creditkaagapay.com 抓取文章 → AI 改写为故事风格 → 发布到 Hashnode + Dev.to
使用方法：
  1. 安装依赖：pip install requests
  2. 设置环境变量（或直接修改下方配置）
  3. 运行：python auto_publish.py

免费 AI 方案：使用 Groq（免费 API，每天 14,400 请求）
  获取 Groq API Key：https://console.groq.com/keys
"""

import os
import re
import json
import time
import hashlib
import requests
from datetime import datetime, timezone
from html import unescape

# ============ 配置区域 ============
# Hashnode 配置
HASHNODE_TOKEN = os.environ.get("HASHNODE_TOKEN", "在这里填你的Token")
HASHNODE_PUBLICATION_ID = os.environ.get("HASHNODE_PUBLICATION_ID", "")  # 自动获取

# Dev.to 配置
DEVTO_API_KEY = os.environ.get("DEVTO_API_KEY", "在这里填你的Dev.to Key")
DEVTO_API_URL = "https://dev.to/api/articles"

# Groq 配置（免费 AI API，用于改写文章）
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "在这里填你的Groq Key")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# WordPress REST API（用于获取完整文章内容）
WP_API_URL = "https://www.creditkaagapay.com/wp-json/wp/v2/posts"

# 已发布文章记录文件（防止重复发布）
PUBLISHED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "published.json")

# Hashnode GraphQL API
HASHNODE_API = "https://gql.hashnode.com"
# ============ 配置结束 ============


# ============ 故事风格提示词 ============
REWRITE_PROMPT = """Act as a real person in the Philippines who recently needed money urgently.

Rewrite the content into a STORY, not an article.

Make it feel like:
- A Reddit post
- Or a Facebook personal story

RULES:

1. Start with a very specific situation
   (example: "Last month, I suddenly needed ₱5,000 because...")

2. Show struggle:
   - loan rejected
   - high interest
   - confusion
   - stress

3. Include 1–2 emotional reactions:
   (worried, stressed, frustrated, relieved)

4. Add realism:
   - mention exact amounts (₱3,000 / ₱10,000)
   - mention time ("last week", "a few days ago")

5. DO NOT explain like a teacher
   → show, not explain

6. Naturally introduce this:
   "I found this while searching online"
   then insert:
   https://www.creditkaagapay.com/
   Keep the link INSIDE a sentence, NOT as a standalone line.

7. Also naturally link the original article URL somewhere in the story.

8. Avoid:
   - formal tone
   - generic phrases like "In conclusion"
   - AI-like structure

9. End casually:
   (example: "Just sharing in case this helps someone")

10. 500–800 words

11. Make sure the output does NOT look AI-generated and passes as a genuine human-written post.

Output format (use these exact labels):
TITLE: [a casual, personal title like a Reddit post title]
CONTENT: [the story in markdown format]

---
Original Title: {title}
Original URL: {url}
Original Content:
{content}"""
# ============ 提示词结束 ============


def load_published():
    """加载已发布文章列表"""
    if os.path.exists(PUBLISHED_FILE):
        with open(PUBLISHED_FILE, "r") as f:
            return json.load(f)
    return {}


def save_published(published):
    """保存已发布文章列表"""
    with open(PUBLISHED_FILE, "w") as f:
        json.dump(published, f, ensure_ascii=False, indent=2)


def get_article_id(url):
    """根据 URL 生成唯一 ID"""
    return hashlib.md5(url.encode()).hexdigest()


def strip_html(html_text):
    """去除 HTML 标签，保留纯文本"""
    text = re.sub(r'<[^>]+>', '', html_text)
    return unescape(text).strip()


def fetch_articles():
    """从 WordPress REST API 获取最新文章（含完整内容）"""
    print(f"[1/5] 正在抓取文章: {WP_API_URL}")
    params = {
        "per_page": 10,
        "_fields": "id,title,link,content,excerpt,date",
        "orderby": "date",
        "order": "desc",
    }
    resp = requests.get(WP_API_URL, params=params, timeout=30)
    resp.raise_for_status()
    posts = resp.json()

    articles = []
    for p in posts:
        articles.append({
            "title": unescape(p["title"]["rendered"]),
            "url": p["link"],
            "content": strip_html(p["content"]["rendered"]),
            "published": p.get("date", ""),
        })
    print(f"    找到 {len(articles)} 篇文章")
    return articles


def rewrite_article(title, url, content):
    """用 Groq (Llama 3.3 70B) 免费 API 改写文章为故事风格"""
    print(f"[2/5] 正在改写: {title[:50]}...")

    prompt = REWRITE_PROMPT.format(
        title=title,
        url=url,
        content=content[:8000],
    )

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.8,
        "max_tokens": 2500,
    }

    resp = requests.post(GROQ_API_URL, json=payload, headers=headers, timeout=60)

    # 处理速率限制：自动等待后重试
    if resp.status_code == 429:
        retry_after = int(resp.headers.get("retry-after", 30))
        print(f"    速率限制，等待 {retry_after} 秒后重试...")
        time.sleep(retry_after)
        resp = requests.post(GROQ_API_URL, json=payload, headers=headers, timeout=60)

    resp.raise_for_status()
    result = resp.json()["choices"][0]["message"]["content"].strip()

    # 解析返回结果
    new_title = ""
    new_content = ""

    if "TITLE:" in result and "CONTENT:" in result:
        parts = result.split("CONTENT:", 1)
        new_title = parts[0].replace("TITLE:", "").strip()
        new_content = parts[1].strip()
    else:
        new_title = f"My experience with: {title}"
        new_content = result

    print(f"    新标题: {new_title}")
    return new_title, new_content


# ============ Hashnode 发布 ============
def get_hashnode_publication_id():
    """获取 Hashnode Publication ID"""
    global HASHNODE_PUBLICATION_ID
    if HASHNODE_PUBLICATION_ID:
        return HASHNODE_PUBLICATION_ID

    query = """
    query Me {
        me {
            publications(first: 1) {
                edges {
                    node { id title url }
                }
            }
        }
    }
    """
    headers = {"Authorization": HASHNODE_TOKEN, "Content-Type": "application/json"}
    resp = requests.post(HASHNODE_API, json={"query": query}, headers=headers)
    data = resp.json()

    if "errors" in data:
        print(f"    Hashnode 错误: {data['errors']}")
        return None

    edges = data.get("data", {}).get("me", {}).get("publications", {}).get("edges", [])
    if edges:
        pub = edges[0]["node"]
        HASHNODE_PUBLICATION_ID = pub["id"]
        print(f"    Hashnode: {pub['title']} ({pub['url']})")
        return HASHNODE_PUBLICATION_ID
    return None


def publish_to_hashnode(title, content):
    """发布文章到 Hashnode"""
    pub_id = get_hashnode_publication_id()
    if not pub_id:
        print("    Hashnode 发布失败：无 Publication ID")
        return False

    mutation = """
    mutation PublishPost($input: PublishPostInput!) {
        publishPost(input: $input) {
            post { id title url }
        }
    }
    """
    variables = {
        "input": {
            "title": title,
            "contentMarkdown": content,
            "publicationId": pub_id,
            "tags": [
                {"slug": "finance", "name": "Finance"},
                {"slug": "philippines", "name": "Philippines"},
            ],
        }
    }
    headers = {"Authorization": HASHNODE_TOKEN, "Content-Type": "application/json"}
    resp = requests.post(HASHNODE_API, json={"query": mutation, "variables": variables}, headers=headers)
    data = resp.json()

    if "errors" in data:
        print(f"    Hashnode 错误: {data['errors']}")
        return False

    post = data.get("data", {}).get("publishPost", {}).get("post", {})
    if post:
        print(f"    Hashnode 发布成功: {post.get('url', 'N/A')}")
        return True
    return False


# ============ Dev.to 发布 ============
def publish_to_devto(title, content):
    """发布文章到 Dev.to"""
    headers = {
        "api-key": DEVTO_API_KEY,
        "Content-Type": "application/json",
    }

    payload = {
        "article": {
            "title": title,
            "body_markdown": content,
            "published": True,
            "tags": ["finance", "philippines", "personalfinance", "money"],
        }
    }

    resp = requests.post(DEVTO_API_URL, json=payload, headers=headers, timeout=30)

    if resp.status_code == 201:
        data = resp.json()
        print(f"    Dev.to 发布成功: {data.get('url', 'N/A')}")
        return True
    else:
        print(f"    Dev.to 发布失败 ({resp.status_code}): {resp.text[:200]}")
        return False


# ============ 主流程 ============
def main():
    print("=" * 60)
    print("自动内容分发系统 (Hashnode + Dev.to)")
    print(f"时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    # 检查配置
    if GROQ_API_KEY == "在这里填你的Groq Key":
        print("\n❌ 请先设置 GROQ_API_KEY！")
        print("   免费获取：https://console.groq.com/keys")
        return

    hashnode_ok = HASHNODE_TOKEN != "在这里填你的Token"
    devto_ok = DEVTO_API_KEY != "在这里填你的Dev.to Key"

    if not hashnode_ok and not devto_ok:
        print("\n❌ 请至少设置一个发布平台（HASHNODE_TOKEN 或 DEVTO_API_KEY）")
        return

    platforms = []
    if hashnode_ok:
        pub_id = get_hashnode_publication_id()
        if pub_id:
            platforms.append("Hashnode")
    if devto_ok:
        platforms.append("Dev.to")

    print(f"    发布平台: {', '.join(platforms)}")

    # 抓取文章
    articles = fetch_articles()
    if not articles:
        print("没有找到文章")
        return

    # 加载已发布记录
    published = load_published()

    # 处理每篇文章
    new_count = 0
    for article in articles:
        article_id = get_article_id(article["url"])

        if article_id in published:
            print(f"\n[跳过] 已发布: {article['title'][:50]}...")
            continue

        print(f"\n{'─' * 40}")
        print(f"处理文章: {article['title']}")

        # AI 改写为故事风格
        try:
            new_title, new_content = rewrite_article(
                article["title"], article["url"], article["content"]
            )
        except Exception as e:
            print(f"    改写失败: {e}")
            continue

        # 发布到各平台
        success_any = False

        if hashnode_ok:
            print(f"[3/5] 发布到 Hashnode...")
            if publish_to_hashnode(new_title, new_content):
                success_any = True

        if devto_ok:
            print(f"[4/5] 发布到 Dev.to...")
            if publish_to_devto(new_title, new_content):
                success_any = True

        if success_any:
            published[article_id] = {
                "original_title": article["title"],
                "original_url": article["url"],
                "new_title": new_title,
                "published_at": datetime.now(timezone.utc).isoformat(),
                "platforms": platforms,
            }
            save_published(published)
            new_count += 1

        # 每篇文章间隔 15 秒，避免触发 Groq 速率限制
        time.sleep(15)

    print(f"\n{'=' * 60}")
    print(f"[5/5] 完成！新发布 {new_count} 篇文章到 {', '.join(platforms)}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
