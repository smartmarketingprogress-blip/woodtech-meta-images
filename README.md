# woodtech-meta-images

Nightly GitHub Action that turns the wood-tech.gr Skroutz feed into designed 1:1 ad images for the Meta catalog.

- Images: `https://smartmarketingprogress-blip.github.io/woodtech-meta-images/img/<product id>.jpg`
- Supplementary feed for Commerce Manager: `https://smartmarketingprogress-blip.github.io/woodtech-meta-images/feed.csv` (`id,image_link`)
- Design changes: edit `render()` in `generate.py` and bump `TEMPLATE_VERSION` to re-render everything.
- Manual run: Actions → Build Meta images → Run workflow (set `limit` to test on a few products).
