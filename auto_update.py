import os
import csv
import random
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from openai import OpenAI

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

    response = requests.get(
        url,
        params=params,
        auth=AUTH,
        timeout=30
    )

    response.raise_for_status()
    return response.json()


def update_post(post_id, new_html):
    url = f"{SITE_URL}/wp-json/wp/v2/posts/{post_id}"

    response = requests.post(
        url,
        json={"content": new_html},
        auth=AUTH,
        timeout=30
    )

    return response


def extract_valid_paragraphs(html):
    soup = BeautifulSoup(html, "lxml")
    valid = []

    for p in soup.find_all("p"):
        text = p.get_text(" ", strip=True)

        if len(text) < 80:
            continue

        if len(text) > 700:
            continue

        if p.find("a"):
            continue

        if "الأسئلة الشائعة" in text:
            continue

        if "FAQ" in text:
            continue

        valid.append(p)

    return soup, valid


def similarity_guard(old_text, new_text):
    old_words = old_text.split()
    new_words = new_text.split()

    if len(new_text.strip()) < 50:
        return False, "New text too short"

    if len(new_words) < max(5, int(len(old_words) * 0.85)):
        return False, "Removed too much content"

    if len(new_words) > int(len(old_words) * 1.20):
        return False, "Added too much content"

    old_set = set(old_words)
    new_set = set(new_words)

    if not old_set:
        return False, "Old text empty"

    overlap = len(old_set.intersection(new_set)) / max(1, len(old_set))

    if overlap < 0.85:
        return False, "Change too large"

    if old_text.strip() == new_text.strip():
        return False, "No change"

    return True, "Accepted"


def micro_rewrite_paragraph(text):
    prompt = f"""
قم بتعديل خفيف وطبيعي جدًا على الفقرة التالية.

الشروط الصارمة:

- لا تغيّر المعنى إطلاقًا.
- لا تضف أي معلومة جديدة.
- لا تحذف أي معلومة مهمة.
- لا تغيّر أسماء الأماكن أو الأشخاص أو المطاعم.
- لا تغيّر الأرقام أو الأسعار أو أوقات العمل.
- لا تغيّر الكلمات المفتاحية المهمة.
- التعديل يجب أن يكون خفيفًا وطبيعيًا.
- نسبة التعديل يجب أن تكون تقريبًا 5% إلى 7% فقط.
- يمكن تحسين جملة قصيرة أو استبدال بعض الكلمات البسيطة.
- لا تستخدم أسلوبًا تسويقيًا.
- لا تجعل النص يبدو مكتوبًا بالذكاء الاصطناعي.
- أعد كتابة الفقرة فقط بدون شرح.
- بدون علامات اقتباس.

الفقرة:
{text}
"""

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt,
        temperature=0.2
    )

    new_text = response.output_text.strip()
    new_text = new_text.strip('"').strip("'").strip()

    ok, reason = similarity_guard(text, new_text)

    if not ok:
        return text, reason

    return new_text, "AI micro edit accepted"


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
        "old_paragraph",
        "new_paragraph",
        "message"
    ]

    with open(
        REPORT_FILE,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as f:
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

            chosen_p = random.choice(valid_paragraphs)
            old_text = chosen_p.get_text(" ", strip=True)

            new_text, rewrite_message = micro_rewrite_paragraph(old_text)

            if old_text == new_text:
                add_row(
                    rows,
                    now,
                    daily_target,
                    run_target,
                    distribution,
                    post,
                    "skipped",
                    old_text,
                    new_text,
                    rewrite_message
                )
                continue

            chosen_p.string = new_text

            response = update_post(
                post["id"],
                str(soup)
            )

            if response.status_code in [200, 201]:
                refresh_sitemap(post["link"])
                updated_count += 1

                add_row(
                    rows,
                    now,
                    daily_target,
                    run_target,
                    distribution,
                    post,
                    "updated",
                    old_text,
                    new_text,
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
                    old_text,
                    new_text,
                    f"{response.status_code}: {response.text[:300]}"
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
