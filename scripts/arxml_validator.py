import os
import json
import pandas as pd
from lxml import etree
import re

# ============== CONFIG ==================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ARXML_DIR = os.path.join(BASE_DIR, "..")   # Scan everything under project
RULE_FILE = os.path.join(BASE_DIR, "..", "rules", "rules.json")
OUTPUT_FILE = os.path.join(BASE_DIR, "output", "report.xlsx")
# ========================================


def load_rules():
    with open(RULE_FILE, "r", encoding="utf-8") as f:
        return json.load(f).get("rules", [])


def evaluate_condition(actual, expected, condition):
    if condition == "EQUALS":
        return actual == expected
    if condition == "NOT_EQUALS":
        return actual != expected
    if condition == "EXISTS":
        return actual not in (None, "")
    if condition == "IN":
        return actual in [x.strip() for x in expected.split(",")]
    if condition == "REGEX":
        return re.match(expected, actual or "") is not None
    return False


def normalize_xpath(xpath):
    """
    Force namespace-safe XPath
    """
    if "local-name()" in xpath:
        return xpath
    return xpath.replace("//", "//*[local-name()='") \
                .replace("/", "'][local-name()='") + "']"


# ============== MAIN ==================
results = []
rules = load_rules()

print(f"Loaded rules: {len(rules)}")

if not rules:
    raise SystemExit("ERROR: No rules loaded")

for root_dir, _, files in os.walk(ARXML_DIR):
    for file in files:
        if not file.lower().endswith(".arxml"):
            continue

        full_path = os.path.join(root_dir, file)
        rel_path = os.path.relpath(full_path, ARXML_DIR)

        print(f"Processing: {rel_path}")

        try:
            tree = etree.parse(full_path)
            root = tree.getroot()
        except Exception as e:
            results.append([
                rel_path, file, "PARSE_ERROR",
                "", "", str(e), "FAIL"
            ])
            continue

        for rule in rules:
            rule_id = rule.get("rule_id", "UNKNOWN")
            desc = rule.get("description", "")
            xpath = normalize_xpath(rule.get("xpath", ""))
            condition = rule.get("condition", "")
            expected = rule.get("expected", "")
            mandatory = rule.get("mandatory", True)

            try:
                elements = root.xpath(xpath)
            except Exception:
                results.append([
                    rel_path, file, rule_id, desc,
                    xpath, expected, "", "INVALID_XPATH"
                ])
                continue

            if not elements:
                status = "FAIL" if mandatory else "SKIP"
                results.append([
                    rel_path, file, rule_id, desc,
                    xpath, expected, "", status
                ])
                continue

            for elem in elements:
                actual = (elem.text or "").strip()
                status = "PASS" if evaluate_condition(actual, expected, condition) else "FAIL"

                results.append([
                    rel_path, file, rule_id, desc,
                    xpath, expected, actual, status
                ])

# ============== REPORT ==================
df = pd.DataFrame(results, columns=[
    "ARXML Path",
    "ARXML File",
    "Rule ID",
    "Description",
    "XPath",
    "Expected",
    "Actual",
    "Status"
])

os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
df.to_excel(OUTPUT_FILE, index=False)

print(f"Validation completed. Report generated: {OUTPUT_FILE}")
