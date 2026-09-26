#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
《冬至日》小说爬虫脚本
针对 5165.org 网站的段落乱序与目录混淆（通过 data-id 客户端重排）反爬机制进行专门解析还原。
无需安装第三方库，直接使用 Python 3 标准库即可运行。
"""

import os
import re
import sys
import time
import html
import urllib.request
import urllib.error
from urllib.parse import urljoin, urlparse

# 请求头模拟浏览器访问，避免被 Cloudflare 拦截
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

def fetch_html(url, retries=3, delay=1.0):
    """抓取指定 URL 的 HTML 内容，包含重试机制"""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=15) as response:
                content_bytes = response.read()
                # 尝试 UTF-8 解码，失败则回退到 gbk
                try:
                    return content_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    return content_bytes.decode("gbk", errors="ignore")
        except Exception as e:
            if attempt < retries - 1:
                wait_time = delay * (attempt + 1)
                print(f"  [重试中] 获取 {url} 失败: {e}，等待 {wait_time:.1f} 秒后重试...")
                time.sleep(wait_time)
            else:
                print(f"  [错误] 最终获取失败 {url}: {e}")
                return None

def get_catalog(catalog_url):
    """
    抓取目录页面，并根据 data-id 进行升序排序（还原网站前端 JS 重排逻辑）
    """
    print(f"[*] 正在获取小说目录: {catalog_url}")
    page_html = fetch_html(catalog_url)
    if not page_html:
        raise RuntimeError("无法加载小说目录页，请检查网络或 URL 是否正确。")

    # 匹配 <li data-id="123"><a href="...">章节名</a></li>
    pattern = re.compile(
        r'<li\s+data-id="(\d+)"[^>]*>\s*<a\s+href="([^"]+)"[^>]*>(.*?)</a>\s*</li>',
        re.DOTALL
    )
    matches = pattern.findall(page_html)

    if not matches:
        raise RuntimeError("未能从目录页面提取到任何章节链接，页面结构可能发生变化。")

    # 关键点：按 data-id 数值升序排序，还原真实的章节先后顺序
    sorted_matches = sorted(matches, key=lambda x: int(x[0]))
    
    chapter_list = []
    for did, href, raw_title in sorted_matches:
        full_url = urljoin(catalog_url, href)
        title = html.unescape(raw_title).strip()
        chapter_list.append({
            "id": int(did),
            "title": title,
            "url": full_url
        })

    print(f"[✓] 成功获取并重排目录，共识别到 {len(chapter_list)} 章。\n")
    return chapter_list

def parse_chapter_content(chapter_url):
    """
    解析单章内容，根据 <div data-id="N"> 进行升序重排，解密打乱的段落文本
    """
    page_html = fetch_html(chapter_url)
    if not page_html:
        return None, []

    # 提取章节标题
    title_match = re.search(r'<h1 class="entry-title"[^>]*>(.*?)</h1>', page_html, re.DOTALL)
    title = html.unescape(title_match.group(1)).strip() if title_match else "未命名章节"

    # 提取正文容器 <div class="entry-content"...>...</div>
    content_match = re.search(
        r'<div class="entry-content"[^>]*>(.*?)</div>\s*<!-- \.entry-content -->',
        page_html,
        re.DOTALL
    )
    if not content_match:
        # 兼容没有注释标记的情况
        content_match = re.search(r'<div class="entry-content"[^>]*>(.*?)</div>', page_html, re.DOTALL)

    if not content_match:
        return title, []

    content_html = content_match.group(1)

    # 提取所有 <div data-id="N">...</div> 块
    div_matches = re.findall(r'<div\s+data-id="(\d+)"[^>]*>(.*?)</div>', content_html, re.DOTALL)

    paragraphs = []
    if div_matches:
        # 核心防反爬：按照 data-id 升序重排段落
        sorted_divs = sorted(div_matches, key=lambda x: int(x[0]))
        for did, block in sorted_divs:
            # 去除 HTML 标签，清理空白符
            clean_text = re.sub(r'<[^>]+>', '', block).strip()
            clean_text = html.unescape(clean_text)
            if clean_text:
                paragraphs.append(clean_text)
    else:
        # 后备方案：如果没有 data-id，直接顺序提取 <p>
        p_matches = re.findall(r'<p[^>]*>(.*?)</p>', content_html, re.DOTALL)
        for p in p_matches:
            clean_text = re.sub(r'<[^>]+>', '', p).strip()
            clean_text = html.unescape(clean_text)
            if clean_text:
                paragraphs.append(clean_text)

    return title, paragraphs

def crawl_novel(target_url, output_file="冬至日_穆成.txt", save_split_chapters=False, crawl_delay=0.6):
    """
    爬取全书并写入文件
    """
    # 如果用户输入的是某具体章节网址，自动解析出小说目录地址
    # 例如 https://5165.org/wangluo/dongzhiri/4374349356.html -> https://5165.org/wangluo/dongzhiri/
    parsed = urlparse(target_url)
    path_parts = [p for p in parsed.path.split('/') if p]
    if len(path_parts) >= 2 and path_parts[-1].endswith('.html'):
        catalog_path = '/' + '/'.join(path_parts[:-1]) + '/'
        catalog_url = f"{parsed.scheme}://{parsed.netloc}{catalog_path}"
    else:
        catalog_url = target_url

    chapters = get_catalog(catalog_url)

    chapters_dir = "chapters"
    if save_split_chapters:
        os.makedirs(chapters_dir, exist_ok=True)

    print(f"[*] 开始下载小说正文，结果将保存至: {output_file}")
    total = len(chapters)

    with open(output_file, "w", encoding="utf-8") as f_out:
        # 写入小说头部信息
        f_out.write("《冬至日》\n")
        f_out.write("作者：穆成\n")
        f_out.write("说明：文本由爬虫自动重排还原（解决网页段落反爬乱序）\n\n")
        f_out.write("=" * 50 + "\n\n")

        for idx, ch in enumerate(chapters, 1):
            ch_url = ch["url"]
            print(f"[{idx:02d}/{total:02d}] 正在抓取: {ch['title']} ({ch_url}) ...", end="", flush=True)

            title, paragraphs = parse_chapter_content(ch_url)

            if not paragraphs:
                print(" [警告: 内容为空或抓取失败]")
            else:
                print(f" [成功: {len(paragraphs)} 段]")

            # 写入汇总 TXT
            f_out.write(f"\n\n{'='*20} {title} {'='*20}\n\n")
            for p in paragraphs:
                # 按照中文排版规范，每段首行缩进两个全角空格
                f_out.write(f"　　{p}\n\n")

            # 可选：保存分章文件
            if save_split_chapters:
                safe_title = re.sub(r'[\\/*?:"<>|]', '_', title)
                split_filename = os.path.join(chapters_dir, f"{idx:02d}_{safe_title}.txt")
                with open(split_filename, "w", encoding="utf-8") as f_split:
                    f_split.write(f"{title}\n\n")
                    for p in paragraphs:
                        f_split.write(f"　　{p}\n\n")

            # 礼貌爬取，避免触发 Cloudflare 封禁
            time.sleep(crawl_delay)

    print(f"\n[🎉] 全部完成！小说已完整下载并保存至: {os.path.abspath(output_file)}")
    if save_split_chapters:
        print(f"[📁] 分章节文件已保存在: {os.path.abspath(chapters_dir)} 目录下")

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="《冬至日》小说爬虫（5165.org 专用段落反乱序还原版）")
    parser.add_argument(
        "--url",
        default="https://5165.org/wangluo/dongzhiri/4374349356.html",
        help="目标小说章节或目录 URL"
    )
    parser.add_argument(
        "--output",
        "-o",
        default="冬至日_穆成.txt",
        help="保存的合成 txt 文件名 (默认: 冬至日_穆成.txt)"
    )
    parser.add_argument(
        "--split",
        action="store_true",
        help="是否同时在 chapters/ 文件夹下保存每章独立 txt"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.6,
        help="每章抓取间隔秒数 (默认 0.6 秒)"
    )

    args = parser.parse_args()
    crawl_novel(
        target_url=args.url,
        output_file=args.output,
        save_split_chapters=args.split,
        crawl_delay=args.delay
    )
