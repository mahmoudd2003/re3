import random
import requests
import streamlit as st
from bs4 import BeautifulSoup

SITE_URL = st.secrets["SITE_URL"].rstrip("/")
USERNAME = st.secrets["WP_USERNAME"]
APP_PASSWORD = st.secrets["WP_APP_PASSWORD"]

auth = (USERNAME, APP_PASSWORD)

st.set_page_config(page_title="WordPress Article Updater", layout="wide")
st.title("WordPress Article Updater")
st.warning("هذه واجهة تجربة يدوية فقط. التشغيل اليومي يتم عبر GitHub Actions.")

def get_posts(per_page=10):
    url = f"{SITE_URL}/wp-json/wp/v2/posts"
    params = {
        "per_page": per_page,
        "status": "publish",
        "orderby": "modified",
        "order": "asc"
    }
    response = requests.get(url, params=params, auth=auth, timeout=30)
    if response.status_code != 200:
        st.error(f"فشل جلب المقالات: {response.status_code}")
        st.text(response.text)
        return []
    return response.json()

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
        if "الأسئلة الشائعة" in text or "FAQ" in text:
            continue

        valid.append(p)

    return soup, valid

posts = get_posts(per_page=10)

if not posts:
    st.stop()

post_options = {f'{post["id"]} - {post["title"]["rendered"]}': post for post in posts}
selected = st.selectbox("اختر مقالًا للفحص", list(post_options.keys()))
post = post_options[selected]

st.subheader("رابط المقال")
st.write(post["link"])

soup, valid_paragraphs = extract_valid_paragraphs(post["content"]["rendered"])
st.write(f"عدد الفقرات المناسبة للتعديل: {len(valid_paragraphs)}")

if valid_paragraphs:
    chosen_p = random.choice(valid_paragraphs)
    old_text = chosen_p.get_text(" ", strip=True)

    st.subheader("فقرة عشوائية مناسبة")
    st.write(old_text)
else:
    st.info("لا توجد فقرات مناسبة للتعديل.")
