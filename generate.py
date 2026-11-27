"""Builds Meta catalog ad images for wood-tech.gr.

Reads the public Skroutz feed, renders one 1:1 image per product
(cut-out photo + lifestyle photo, price, footer strip) and writes:
  site/img/<id>.jpg   the images
  site/feed.csv       supplementary feed for Meta Commerce Manager (id,image_link)
Unchanged products are skipped, so nightly runs only redo what changed.
"""
import csv, hashlib, html, io, json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

FEED_URL = "https://www.wood-tech.gr/skroutz.xml"
LOGO_URL = "https://www.wood-tech.gr/images/assets/logo-black.webp"
BASE_URL = os.environ.get("BASE_URL", "https://smartmarketingprogress-blip.github.io/woodtech-meta-images")
OUT = "site"
IMG_DIR = os.path.join(OUT, "img")
STATE = os.path.join(OUT, "state.json")
TEMPLATE_VERSION = "v3"  # bump to force a full re-render after design changes

S = 1080
BG = (188, 149, 101)       # wood tan from wood-tech.gr
DARK = (39, 39, 39)
INK = (17, 17, 17)
GREY = (85, 85, 85)
QUALITY = int(os.environ.get("JPEG_QUALITY", "74"))

FONT_DIRS = ["/usr/share/fonts/truetype/dejavu/"]
def font(bold, size):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for d in FONT_DIRS:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    raise SystemExit("DejaVu fonts missing")

F_PRICE = font(True, 92)
F_NAME = font(True, 28)
F_SMALL = font(False, 22)
F_BIG = font(True, 29)

session = requests.Session()
session.headers["User-Agent"] = "Mozilla/5.0 (WoodtechMetaImages)"

def get(url, tries=3):
    for i in range(tries):
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 200:
                return r.content
        except requests.RequestException:
            pass
        time.sleep(1 + i)
    return None

def tag(block, name):
    m = re.search(r"<%s>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?</%s>" % (name, name), block)
    return html.unescape(m.group(1).strip()) if m else ""

def parse_feed(xml):
    out = []
    for block in re.split(r"<product[\s>]", xml)[1:]:
        pid = tag(block, "id")
        if not pid:
            continue
        adds = [html.unescape(a.strip()) for a in re.findall(
            r"<additionalimage>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?</additionalimage>", block)]
        out.append({
            "id": pid,
            "name": tag(block, "name"),
            "price": tag(block, "price_with_vat"),
            "images": [u for u in [tag(block, "image")] + adds if u][:6],
        })
    return out

def load(url):
    url = url.replace("https://wood-tech.gr", "https://www.wood-tech.gr")
    data = get(url)
    if not data:
        return None
    try:
        im = Image.open(io.BytesIO(data))
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            return bg
        return im.convert("RGB")
    except Exception:
        return None

def white_ratio(im):
    small = im.resize((60, 60))
    px = small.getdata()
    return sum(1 for r, g, b in px if r > 235 and g > 235 and b > 235) / 3600

def fit(canvas, im, box, cover, radius=0):
    x, y, w, h = box
    s = max(w / im.width, h / im.height) if cover else min(w / im.width, h / im.height)
    nw, nh = max(1, round(im.width * s)), max(1, round(im.height * s))
    r = im.resize((nw, nh), Image.LANCZOS)
    if cover:
        left, top = (nw - w) // 2, (nh - h) // 2
        r = r.crop((left, top, left + w, top + h))
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, w, h), radius, fill=255)
        canvas.paste(r, (x, y), mask)
    else:
        canvas.paste(r, (x + (w - nw) // 2, y + (h - nh) // 2))

def fmt_price(p):
    v = float(p)
    if abs(v - round(v)) < 0.005:
        return f"{int(round(v))}€"
    return f"{v:.2f}".replace(".", ",") + "€"

def clean_name(n):
    n = re.sub(r"\s+WT\d+(\.\d+)?", "", n)
    return re.sub(r"[“”\"]", "", n).strip()

def ellipsize(draw, text, fnt, maxw):
    if draw.textlength(text, font=fnt) <= maxw:
        return text
    while text and draw.textlength(text + "…", font=fnt) > maxw:
        text = text[:-1]
    return text.rstrip() + "…"

LOGO = None

# Colour charts, spec sheets and dimension drawings must never be the main or lifestyle photo
SKIP_NAME = re.compile(r"chrom|color|colour|chart|xrom|diastas|dimens|spec|sxedio|schedio|odigies", re.I)

def render(p):
    loaded = [(u, load(u)) for u in p["images"]]
    imgs = [i for u, i in loaded if i is not None and not SKIP_NAME.search(u.rsplit("/", 1)[-1])]
    if not imgs:
        imgs = [i for u, i in loaded if i is not None][:1]
    if not imgs:
        return None
    scored = [(white_ratio(i), i) for i in imgs]
    cut = max(scored, key=lambda t: t[0])[1]
    # lifestyle: not the cut-out, not portrait (charts/infographics), clearly not a white-background shot
    life_c = [(w, i) for w, i in scored if i is not cut and i.width >= i.height * 0.95 and w < 0.5]
    life = min(life_c, key=lambda t: t[0])[1] if life_c else None

    c = Image.new("RGB", (S, S), BG)
    card = Image.new("RGB", (1000, 1000), (255, 255, 255))
    d = ImageDraw.Draw(card)

    if LOGO is not None:
        lh = 90
        lw = round(LOGO.width * lh / LOGO.height)
        lg = LOGO.resize((lw, lh), Image.LANCZOS)
        card.paste(lg, (1000 - lw - 40, 36), lg if lg.mode == "RGBA" else None)

    if life is not None:
        fit(card, cut, (30, 150, 400, 500), False)
        fit(card, life, (450, 150, 520, 500), True, 18)
    else:
        fit(card, cut, (60, 150, 880, 500), False)

    d.text((50, 685), ellipsize(d, clean_name(p["name"]), F_NAME, 900), font=F_NAME, fill=GREY)
    d.text((48, 725), fmt_price(p["price"]), font=F_PRICE, fill=INK)

    fy = 850
    d.rectangle((0, fy, 1000, 1000), fill=DARK)
    # truck icon
    d.rounded_rectangle((50, fy + 45, 114, fy + 91), 6, outline=BG, width=4)
    for cx in (68, 100):
        d.ellipse((cx - 9, fy + 91, cx + 9, fy + 109), outline=BG, width=4)
    d.text((140, fy + 44), "ΑΠΟΣΤΟΛΗ", font=F_SMALL, fill=(255, 255, 255))
    d.text((140, fy + 76), "ΣΕ ΟΛΗ ΤΗΝ ΕΛΛΑΔΑ", font=F_BIG, fill=(255, 255, 255))
    # gift icon
    d.rounded_rectangle((540, fy + 42, 600, fy + 106), 8, outline=BG, width=4)
    d.line((540, fy + 62, 600, fy + 62), fill=BG, width=4)
    d.line((570, fy + 42, 570, fy + 106), fill=BG, width=4)
    d.text((625, fy + 44), "ΔΩΡΕΑΝ ΜΕΤΑΦΟΡΙΚΑ", font=F_SMALL, fill=(255, 255, 255))
    d.text((625, fy + 76), "ΑΠΟ 159€", font=F_BIG, fill=(255, 255, 255))

    mask = Image.new("L", (1000, 1000), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, 1000, 1000), 36, fill=255)
    c.paste(card, (40, 40), mask)
    return c

def key_of(p):
    raw = "|".join([TEMPLATE_VERSION, p["name"], p["price"]] + p["images"])
    return hashlib.sha1(raw.encode()).hexdigest()[:10]

def main():
    global LOGO
    os.makedirs(IMG_DIR, exist_ok=True)
    xml = get(FEED_URL)
    if not xml:
        sys.exit("Could not download feed")
    products = parse_feed(xml.decode("utf-8", "replace"))
    print(f"products in feed: {len(products)}")
    logo = get(LOGO_URL)
    if logo:
        LOGO = Image.open(io.BytesIO(logo)).convert("RGBA")

    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    limit = int(os.environ.get("LIMIT", "0"))
    todo = [p for p in products
            if state.get(p["id"]) != key_of(p) or not os.path.exists(os.path.join(IMG_DIR, p["id"] + ".jpg"))]
    if limit:
        todo = todo[:limit]
    print(f"to render: {len(todo)}")

    def work(p):
        try:
            im = render(p)
        except Exception as e:
            print("fail", p["id"], e)
            return p["id"], None
        if im is None:
            return p["id"], None
        im.save(os.path.join(IMG_DIR, p["id"] + ".jpg"), "JPEG", quality=QUALITY, optimize=True, progressive=True)
        return p["id"], key_of(p)

    done = 0
    with ThreadPoolExecutor(int(os.environ.get("WORKERS", "12"))) as ex:
        for pid, k in ex.map(work, todo):
            done += 1
            if k:
                state[pid] = k
            if done % 500 == 0:
                print(f"  {done}/{len(todo)}")
                json.dump(state, open(STATE, "w"))

    live = {p["id"] for p in products}
    for f in os.listdir(IMG_DIR):
        if f.endswith(".jpg") and f[:-4] not in live:
            os.remove(os.path.join(IMG_DIR, f))
            state.pop(f[:-4], None)
    json.dump(state, open(STATE, "w"))

    rows = 0
    with open(os.path.join(OUT, "feed.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "image_link"])
        for p in products:
            if p["id"] in state and os.path.exists(os.path.join(IMG_DIR, p["id"] + ".jpg")):
                w.writerow([p["id"], f"{BASE_URL}/img/{p['id']}.jpg?v={state[p['id']]}"])
                rows += 1
    total = sum(os.path.getsize(os.path.join(IMG_DIR, f)) for f in os.listdir(IMG_DIR))
    with open(os.path.join(OUT, "index.html"), "w") as fh:
        fh.write(f"<!doctype html><meta charset=utf-8><title>Woodtech Meta images</title>"
                 f"<p>{rows} images · updated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}</p>"
                 f"<p><a href=feed.csv>feed.csv</a></p>")
    print(f"feed rows: {rows}, images size: {total/1e6:.0f} MB")
    if total > 950e6:
        print("::warning::site is close to the 1 GB GitHub Pages limit; lower JPEG_QUALITY")

if __name__ == "__main__":
    main()
