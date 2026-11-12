"""Dev tool: sync product brand MD files (with copy front-matter) into app/brand_docs/.

The email engine reads these MD files at generate time ("Draft with Agent").
Front matter holds the copy fields the engine uses; the original brand doc text
is kept below the front matter as full reference context.

Run from sendwise/backend:  .venv\\Scripts\\python.exe sync_brand_docs.py
"""
import io
import os

APP = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(APP, "app", "brand_docs")
SRC = os.path.abspath(os.path.join(APP, "..", "..", "email-marketing", "brand-docs"))
os.makedirs(OUT, exist_ok=True)

from app.copy_engine import PRODUCTS  # noqa: E402

FILE_MAP = {
    "turbo_guard": "turbo-guard-brand.md",
    "turbo": "turbo-addons-brand.md",
    "travilo": "travilo-brand.md",
    "peopledesk": "peopledesk-brand.md",
    "managerium": "managerium-brand.md",
    "primevat": "primevat-brand.md",
    "trackforce": "trackforce-brand.md",
    "familyguard": "family-guard-brand.md",
}


def build_front_matter(key, cfg):
    lines = ["---", "# Brand copy source for the email engine (read at Draft with Agent time)", f"product: {key}", f"slug: {cfg['slug']}", f"purpose: {cfg['purpose']}", f"name: {cfg['name']}"]
    for i, s in enumerate(cfg["subjects"], 1):
        lines.append(f"subject_{i}: {s}")
    lines.append(f"preview: {cfg['preview']}")
    lines.append(f"hook: {cfg['hook']}")
    lines.append(f"problem: {cfg['problem']}")
    lines.append(f"solution: {cfg['solution']}")
    lines.append(f"cta: {cfg['cta']}")
    lines.append(f"cta_url: {cfg['cta_url']}")
    for i, p in enumerate(cfg["proof"], 1):
        lines.append(f"proof_{i}: {p}")
    lines.append("---")
    return "\n".join(lines)


def main():
    for key, fname in FILE_MAP.items():
        cfg = PRODUCTS.get(key)
        if not cfg:
            print("skip", key, "(no engine cfg)")
            continue
        fm = build_front_matter(key, cfg)
        src_path = os.path.join(SRC, fname)
        body = ""
        if os.path.exists(src_path):
            body = io.open(src_path, encoding="utf-8").read().strip()
        target = os.path.join(OUT, "turbo-guard-brand.md" if key == "turbo_guard" else key + "-brand.md")
        content = body + "\n" if key == "turbo_guard" else fm + "\n\n" + body + "\n"
        io.open(target, "w", encoding="utf-8").write(content)
        print("wrote", target, f"({len(fm + body)} chars, front matter ok)")
    skill_src = os.path.join(SRC, "email-writing-skill-v4.md")
    if os.path.exists(skill_src):
        skill_target = os.path.join(OUT, "email-writing-skill-v4.md")
        skill_body = io.open(skill_src, encoding="utf-8").read()
        io.open(skill_target, "w", encoding="utf-8").write(skill_body)
        print("wrote", skill_target, f"({len(skill_body)} chars, canonical rules)")


if __name__ == "__main__":
    main()
