import os
import csv
import re
import random
import requests

from bs4 import BeautifulSoup
from datetime import datetime, timezone
from openai import OpenAI
from difflib import SequenceMatcher

SITE_URL = os.environ["SITE_URL"].rstrip("/")
USERNAME = os.environ["WP_USERNAME"]
APP_PASSWORD = os.environ["WP_APP_PASSWORD"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

AUTH = (USERNAME, APP_PASSWORD)
client = OpenAI(api_key=OPENAI_API_KEY)

DAILY_SEQUENCE = [3, 5, 7, 9, 12, 15, 18]
RUNS_PER_DAY = 4

RUN_INDEX = int(os.environ.get("RUN_INDEX", "1"))
if RUN_INDEX < 1 or RUN_INDEX > RUNS_PER_DAY:
    RUN_INDEX = 1

REPORT_FILE = f"daily_report_run_{RUN_INDEX}.csv"


def get_daily_target_count():
    day_of_year = datetime.now(timezone.utc).timetuple().tm_yday
    index = (day_of_year - 1) % len(DAILY_SEQUENCE)
    return DAILY_SEQUENCE[index]


def split_daily_count(total, runs=4):
    base = total // runs
    remainder = total % runs
    parts = [base] * runs

    for i in range(remainder):
        parts[runs - 1 - i] += 1

    return parts


def get_current_run_target():
    daily_target = get_daily_target_count()
    distribution = split_daily_count(daily_target, RUNS_PER_DAY)
    run_target = distribution[RUN_INDEX - 1]
    return daily_target, run_target, distribution


def get_posts(per_page=50):
    url = f"{SITE_URL}/wp-json/wp/v2/posts"

    params = {
        "per_page": per_page,
        "status": "publish",
        "orderby": "modified",
        "order": "asc"
    }

    response = requests.get(url, params=params, auth=AUTH, timeout=30)
    response.raise_for_status()
    return response.json()


def update_post(post_id, new_html):
    url = f"{SITE_URL}/wp-json/wp/v2/posts/{post_id}"
    return requests.post(url, json={"content": new_html}, auth=AUTH, timeout=30)


def extract_valid_paragraphs(html):
    soup = BeautifulSoup(html, "lxml")
    valid = []

    for p in soup.find_all("p"):
        text = p.get_text(" ", strip=True)

        if len(text) < 120:
            continue
        if len(text) > 900:
            continue
        if p.find("a"):
            continue
        if "الأسئلة الشائعة" in text:
            continue
        if "FAQ" in text:
            continue

        valid.append(p)

    return soup, valid


def split_sentences(text):
    sentences = re.split(r'(?<=[.!؟])\s+', text.strip())
    return [s.strip() for s in sentences if len(s.strip()) >= 40]


def sentence_guard(old_sentence, new_sentence):
    old_sentence = old_sentence.strip()
    new_sentence = new_sentence.strip()

    if not new_sentence:
        return False, "Empty sentence"

    if old_sentence == new_sentence:
        return False, "No change"

    old_words = old_sentence.split()
    new_words = new_sentence.split()

    if len(new_words) < max(4, int(len(old_words) * 0.60)):
        return False, "Sentence removed too much"

    if len(new_words) > int(len(old_words) * 1.60):
        return False, "Sentence added too much"

    similarity = SequenceMatcher(None, old_sentence, new_sentence).ratio()

    if similarity < 0.30:
        return False, "Sentence change too large"

    return True, "Accepted"


def rewrite_sentence(sentence):
    prompt = f"""
عدّل الجملة التالية بشكل طبيعي وبشري.

الشروط:
- حافظ على المعنى الأساسي.
- لا تضف معلومات جديدة.
- لا تحذف معلومة مهمة.
- لا تغيّر الأرقام أو الأسعار أو أوقات العمل.
- لا تغيّر أسماء الأماكن أو الأشخاص أو المطاعم.
- لا تستخدم أسلوبًا تسويقيًا مبالغًا.
- اجعل التعديل واضحًا لكن غير مبالغ فيه.
- أعد الجملة فقط بدون شرح وبدون علامات اقتباس.

الجملة:
{sentence}
"""

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt,
        temperature=0.35
    )

    new_sentence = response.output_text.strip()
    new_sentence = new_sentence.strip('"').strip("'").strip()

    ok, reason = sentence_guard(sentence, new_sentence)

    if not ok:
        return sentence, reason

    return new_sentence, "AI sentence edit accepted"


def rewrite_one_sentence_in_paragraph(paragraph_text):
    sentences = split_sentences(paragraph_text)

    if not sentences:
        return paragraph_text, "", "", "No valid sentence found"

    random.shuffle(sentences)

    for old_sentence in sentences:
        new_sentence, message = rewrite_sentence(old_sentence)

        if old_sentence != new_sentence:
            new_paragraph = paragraph_text.replace(old_sentence, new_sentence, 1)
            return new_paragraph, old_sentence, new_sentence, message

    return paragraph_text, "", "", "No sentence accepted"


def refresh_sitemap(post_url):
    urls = [
        post_url,
        f"{SITE_URL}/sitemap_index.xml",
        f"{SITE_URL}/post-sitemap.xml",
    ]

    for url in urls:
        try:
            requests.get(url, timeout=15)
        except Exception:
            pass


def write_report(rows):
    fieldnames = [
        "run_time_utc",
        "run_index",
        "daily_target",
        "run_target",
        "daily_distribution",
        "post_id",
        "title",
        "link",
        "status",
        "old_sentence",
        "new_sentence",
        "old_paragraph",
        "new_paragraph",
        "message"
    ]

    with open(REPORT_FILE, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def add_row(
    rows,
    now,
    daily_target,
    run_target,
    distribution,
    post,
    status,
    old_sentence="",
    new_sentence="",
    old_paragraph="",
    new_paragraph="",
    message=""
):
    rows.append({
        "run_time_utc": now,
        "run_index": RUN_INDEX,
        "daily_target": daily_target,
        "run_target": run_target,
        "daily_distribution": str(distribution),
        "post_id": post.get("id", ""),
        "title": post.get("title", {}).get("rendered", ""),
        "link": post.get("link", ""),
        "status": status,
        "old_sentence": old_sentence,
        "new_sentence": new_sentence,
        "old_paragraph": old_paragraph,
        "new_paragraph": new_paragraph,
        "message": message
    })


def main():
    now = datetime.now(timezone.utc).isoformat()
    daily_target, run_target, distribution = get_current_run_target()

    posts = get_posts(per_page=50)
    random.shuffle(posts)

    rows = []
    updated_count = 0

    for post in posts:
        if updated_count >= run_target:
            break

        try:
            soup, valid_paragraphs = extract_valid_paragraphs(
                post["content"]["rendered"]
            )

            if not valid_paragraphs:
                add_row(
                    rows,
                    now,
                    daily_target,
                    run_target,
                    distribution,
                    post,
                    "skipped",
                    message="No valid paragraph found"
                )
                continue

            random.shuffle(valid_paragraphs)

            updated_this_post = False

            for chosen_p in valid_paragraphs:
                old_paragraph = chosen_p.get_text(" ", strip=True)

                new_paragraph, old_sentence, new_sentence, rewrite_message = (
                    rewrite_one_sentence_in_paragraph(old_paragraph)
                )

                if old_paragraph == new_paragraph:
                    continue

                chosen_p.string = new_paragraph

                response = update_post(post["id"], str(soup))

                if response.status_code in [200, 201]:
                    refresh_sitemap(post["link"])
                    updated_count += 1
                    updated_this_post = True

                    add_row(
                        rows,
                        now,
                        daily_target,
                        run_target,
                        distribution,
                        post,
                        "updated",
                        old_sentence,
                        new_sentence,
                        old_paragraph,
                        new_paragraph,
                        rewrite_message
                    )
                else:
                    add_row(
                        rows,
                        now,
                        daily_target,
                        run_target,
                        distribution,
                        post,
                        "failed",
                        old_sentence,
                        new_sentence,
                        old_paragraph,
                        new_paragraph,
                        f"{response.status_code}: {response.text[:300]}"
                    )

                break

            if not updated_this_post:
                add_row(
                    rows,
                    now,
                    daily_target,
                    run_target,
                    distribution,
                    post,
                    "skipped",
                    message="No acceptable sentence edit"
                )

        except Exception as e:
            add_row(
                rows,
                now,
                daily_target,
                run_target,
                distribution,
                post,
                "failed",
                message=str(e)
            )

    write_report(rows)

    print(f"Run index: {RUN_INDEX}")
    print(f"Daily target: {daily_target}")
    print(f"Distribution: {distribution}")
    print(f"Current run target: {run_target}")
    print(f"Updated count: {updated_count}")
    print(f"Report saved to {REPORT_FILE}")


if __name__ == "__main__":
    main()
