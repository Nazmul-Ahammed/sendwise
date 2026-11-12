from pathlib import Path


path = Path("/workspace/app/copy_engine.py")
source = path.read_text(encoding="utf-8")

key_marker = "    key = product_key(product)\n"
key_patch = '''    key = product_key(product)
    _campaign_offer = re.sub(r"\\s+", " ", str(offer or "")).lower()
    coffee_campaign = (
        key == "turbo_guard"
        and "5.55" in _campaign_offer
        and bool(re.search(r"\\bfirst[ -]year\\b|\\bfirst annual term\\b", _campaign_offer))
        and bool(re.search(r"\\bup to 2\\b.{0,24}\\b(?:site|wordpress)\\b", _campaign_offer))
        and bool(re.search(r"\\bone[ -]month\\b", _campaign_offer))
    )
'''

if "_campaign_offer = re.sub" not in source:
    if key_marker not in source:
        raise RuntimeError("Product-key marker not found")
    source = source.replace(key_marker, key_patch, 1)

blocks_marker = "    blocks = _journey_body_blocks(cfg, phase, offer=offer, bangla=bn)\n"
blocks_patch = '''    blocks = _journey_body_blocks(cfg, phase, offer=offer, bangla=bn)
    if coffee_campaign:
        blocks.insert(
            0,
            "For roughly the cost of one Starbucks coffee, Turbo Guard Pro gives up to 2 WordPress sites a more structured security routine for the first year.",
        )
        blocks.insert(
            1,
            "Malware and malicious PHP web-shell access can create serious WordPress security concerns. Turbo Guard makes its supported scan, review, monitoring, and response workflow easier to manage without promising complete protection.",
        )
        if n in (1, 2, 4, 7):
            blocks.insert(
                2,
                "PHP2Shell is one example of the broader PHP web-shell concern. Turbo Guard does not promise named PHP2Shell detection, so review remains essential.",
            )
'''

if "For roughly the cost of one Starbucks coffee" not in source:
    if blocks_marker not in source:
        raise RuntimeError("Body-block marker not found")
    source = source.replace(blocks_marker, blocks_patch, 1)

cta_marker = '''    if key == "turbo_guard":
        cta_label = cfg["cta"]
'''
cta_patch = '''    if key == "turbo_guard":
        cta_label = "Get Pro For US$5.55" if coffee_campaign else cfg["cta"]
'''

if cta_marker in source:
    source = source.replace(cta_marker, cta_patch, 1)

llm_marker = '''    if llm and key == "turbo_guard" and _unsafe_turbo_guard_copy(
'''
llm_patch = '''    if coffee_campaign:
        # Keep the reviewed campaign motifs when the agent omits required context.
        llm = None
'''

if "Keep the reviewed campaign motifs" not in source:
    if llm_marker not in source:
        raise RuntimeError("LLM marker not found")
    source = source.replace(llm_marker, llm_patch + llm_marker, 1)

path.with_suffix(".py.pre-campaign-hotfix-v2").write_text(
    path.read_text(encoding="utf-8"), encoding="utf-8"
)
path.write_text(source, encoding="utf-8")
