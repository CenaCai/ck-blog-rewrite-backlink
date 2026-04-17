#!/usr/bin/env python3
"""
自动内容分发脚本：从 creditkaagapay.com 抓取文章 → AI 改写 → 发布到 Hashnode
使用方法：
  1. 安装依赖：pip install requests groq
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

# Groq 配置（免费 AI API，用于改写文章）
# 获取免费 Key：https://console.groq.com/keys
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
    print(f"[1/4] 正在抓取文章: {WP_API_URL}")
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
    """用 Groq (Llama 3.3 70B) 免费 API 改写文章"""
    print(f"[2/4] 正在改写: {title[:50]}...")

    prompt = f"""You are a content rewriter for a finance blog. Given the following article, please do the following:
1. Create a new, catchy title (different from the original). The title should be in the same language as the original article.
2. Rewrite and summarize the article to about 40% of its original length, in your own words. Keep the same language as the original.
3. Keep the same topic and key information.
4. At the end, add this line exactly:

---

*Originally published at [Credit Kaagapay]({url})*

Output format (use these exact labels):
TITLE: [new title]
CONTENT: [rewritten article in markdown format with backlink at the end]

---
Original Title: {title}
Original URL: {url}
Original Content:
{content[:8000]}"""

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 2000,
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
        new_title = f"Summary: {title}"
        new_content = result

    print(f"    新标题: {new_title}")
    return new_title, new_content


def get_hashnode_publication_id():
    """获取 Hashnode Publication ID"""
    global HASHNODE_PUBLICATION_ID
    if HASHNODE_PUBLICATION_ID:
        return HASHNODE_PUBLICATION_ID

    print("[*] 正在获取 Hashnode Publication ID...")

    query = """
    query Me {
        me {
            publications(first: 1) {
                edges {
                    node {
                        id
                        title
                        url
                    }
                }
            }
        }
    }
    """

    headers = {
        "Authorization": HASHNODE_TOKEN,
        "Content-Type": "application/json",
    }

    resp = requests.post(HASHNODE_API, json={"query": query}, headers=headers)
    data = resp.json()

    if "errors" in data:
        print(f"    错误: {data['errors']}")
        return None

    edges = data.get("data", {}).get("me", {}).get("publications", {}).get("edges", [])
    if edges:
        pub = edges[0]["node"]
        HASHNODE_PUBLICATION_ID = pub["id"]
        print(f"    Publication: {pub['title']} ({pub['url']})")
        print(f"    ID: {HASHNODE_PUBLICATION_ID}")
        return HASHNODE_PUBLICATION_ID
    else:
        print("    未找到 Publication。请先在 Hashnode 创建一个博客。")
        return None


def publish_to_hashnode(title, content):
    """发布文章到 Hashnode"""
    print(f"[3/4] 正在发布到 Hashnode: {title[:50]}...")

    pub_id = get_hashnode_publication_id()
    if not pub_id:
        print("    发布失败：无法获取 Publication ID")
        return False

    mutation = """
    mutation PublishPost($input: PublishPostInput!) {
        publishPost(input: $input) {
            post {
                id
                title
                url
            }
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
                {"slug": "credit", "name": "Credit"},
            ],
        }
    }

    headers = {
        "Authorization": HASHNODE_TOKEN,
        "Content-Type": "application/json",
    }

    resp = requests.post(
        HASHNODE_API,
        json={"query": mutation, "variables": variables},
        headers=headers,
    )
    data = resp.json()

    if "errors" in data:
        print(f"    发布错误: {data['errors']}")
        return False

    post = data.get("data", {}).get("publishPost", {}).get("post", {})
    if post:
        print(f"    发布成功！URL: {post.get('url', 'N/A')}")
        return True
    else:
        print(f"    发布失败，返回: {data}")
        return False


def main():
    print("=" * 60)
    print("自动内容分发系统")
    print(f"时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    # 检查配置
    if HASHNODE_TOKEN == "在这里填你的Token":
        print("\n❌ 请先设置 HASHNODE_TOKEN！")
        print("   方法1：设置环境变量 export HASHNODE_TOKEN=xxx")
        print("   方法2：直接修改脚本中的 HASHNODE_TOKEN")
        return

    if GROQ_API_KEY == "在这里填你的Groq Key":
        print("\n❌ 请先设置 GROQ_API_KEY！")
        print("   免费获取：https://console.groq.com/keys")
        print("   方法1：设置环境变量 export GROQ_API_KEY=xxx")
        print("   方法2：直接修改脚本中的 GROQ_API_KEY")
        return

    # 获取 Publication ID
    pub_id = get_hashnode_publication_id()
    if not pub_id:
        return

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

        # AI 改写
        try:
            new_title, new_content = rewrite_article(
                article["title"], article["url"], article["content"]
            )
        except Exception as e:
            print(f"    改写失败: {e}")
            continue

        # 发布到 Hashnode
        success = publish_to_hashnode(new_title, new_content)

        if success:
            published[article_id] = {
                "original_title": article["title"],
                "original_url": article["url"],
                "new_title": new_title,
                "published_at": datetime.now(timezone.utc).isoformat(),
            }
            save_published(published)
            new_count += 1

        # 每篇文章间隔 15 秒，避免触发 Groq 速率限制
        time.sleep(15)

    print(f"\n{'=' * 60}")
    print(f"[4/4] 完成！新发布 {new_count} 篇文章")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
