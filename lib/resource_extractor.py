"""Extract and normalize structured resources from assistant responses."""

import re

TYPE_LABELS = {
    "form": "Download Form",
    "office": "Visit Office",
    "law": "View Law",
}

_DASH_LINE = re.compile(
    r"^-\s*(FORM|OFFICE|LAW)\s*:\s*(.+)$",
    re.IGNORECASE,
)
_RESOURCES_HEADER = re.compile(r"^\s*RESOURCES\s*:\s*$", re.IGNORECASE)
_SINHALA_MARKER = re.compile(r"සම්පත්:\s*(.+?)(?:\n|$)")
_RESOURCE_INLINE = re.compile(
    r"RESOURCE:\s*(?:(FORM|OFFICE|LAW)\s*[:\-]\s*)?(.+?)(?=\n|RESOURCE:|$)",
    re.IGNORECASE,
)


def _split_name_url(value: str) -> tuple[str, str | None]:
    if "|" in value:
        name, url = value.split("|", 1)
        name = name.strip()
        url = url.strip() or None
        return name, url
    return value.strip(), None


def _infer_type(name: str) -> str:
    lower = name.lower()
    office_hints = ("office", "department", "ministry", "division", "secretariat", "පාර්ලි")
    law_hints = ("law", "act", "ordinance", "regulation", "statute", "නීතිය")
    if any(hint in lower for hint in office_hints):
        return "office"
    if any(hint in lower for hint in law_hints):
        return "law"
    return "form"


def _make_resource(resource_type: str, name: str, url: str | None) -> dict | None:
    resource_type = (resource_type or "").lower()
    name = (name or "").strip()
    if resource_type not in TYPE_LABELS or not name:
        return None
    return {
        "type": resource_type,
        "name": name,
        "url": url,
        "label": TYPE_LABELS[resource_type],
    }


def _parse_dash_line(line: str) -> dict | None:
    match = _DASH_LINE.match(line.strip())
    if not match:
        return None
    resource_type = match.group(1).lower()
    name, url = _split_name_url(match.group(2).strip())
    return _make_resource(resource_type, name, url)


def _parse_resources_section(text: str) -> list[dict]:
    results: list[dict] = []
    in_section = False

    for line in text.splitlines():
        stripped = line.strip()
        if _RESOURCES_HEADER.match(stripped):
            in_section = True
            continue
        if not in_section:
            continue
        if not stripped:
            if results:
                break
            continue
        parsed = _parse_dash_line(stripped)
        if parsed:
            results.append(parsed)
        elif results and not stripped.startswith("-"):
            break

    return results


def _parse_freeform_marker(content: str, explicit_type: str | None = None) -> dict | None:
    content = (content or "").strip()
    if not content:
        return None

    typed_match = re.match(
        r"^(FORM|OFFICE|LAW)\s*:\s*(.+)$",
        content,
        re.IGNORECASE,
    )
    if typed_match:
        resource_type = typed_match.group(1).lower()
        name, url = _split_name_url(typed_match.group(2).strip())
        return _make_resource(resource_type, name, url)

    name, url = _split_name_url(content)
    resource_type = (explicit_type or _infer_type(name)).lower()
    return _make_resource(resource_type, name, url)


def _parse_sinhala_markers(text: str) -> list[dict]:
    results: list[dict] = []
    for match in _SINHALA_MARKER.finditer(text):
        parsed = _parse_freeform_marker(match.group(1))
        if parsed:
            results.append(parsed)
    return results


def _parse_resource_inline(text: str) -> list[dict]:
    results: list[dict] = []
    for match in _RESOURCE_INLINE.finditer(text):
        explicit_type = match.group(1).lower() if match.group(1) else None
        parsed = _parse_freeform_marker(match.group(2), explicit_type)
        if parsed:
            results.append(parsed)
    return results


def _dedupe(resources: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    unique: list[dict] = []
    for item in resources:
        key = (item.get("type"), item.get("name"), item.get("url"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def strip_resources_from_reply(text: str) -> str:
    if not text or not isinstance(text, str):
        return ""

    try:
        lines: list[str] = []
        in_resources = False

        for line in text.splitlines():
            stripped = line.strip()
            if _RESOURCES_HEADER.match(stripped):
                in_resources = True
                continue
            if in_resources:
                if _DASH_LINE.match(stripped) or not stripped:
                    if not stripped:
                        in_resources = False
                    continue
                in_resources = False

            if stripped.startswith("සම්පත්:"):
                continue

            cleaned = _RESOURCE_INLINE.sub("", line).rstrip()
            if cleaned.strip():
                lines.append(cleaned)

        result = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
        return result
    except Exception:
        return text.strip()


def extract_resources(text: str) -> list[dict]:
    if not text or not isinstance(text, str):
        return []

    try:
        resources: list[dict] = []
        resources.extend(_parse_resources_section(text))
        resources.extend(_parse_sinhala_markers(text))
        resources.extend(_parse_resource_inline(text))
        return _dedupe(resources)
    except Exception:
        return []


if __name__ == "__main__":
    sample = """
Here is how to register your business.

RESOURCES:
- FORM: Business Registration Form | https://gov.lk/forms/br1
- OFFICE: Department of Registrar of Companies | Colombo 10
- LAW: Companies Act No. 7 of 2007 | https://gov.lk/laws/companies-act

සම්පත්: FORM: ජනමත පෙත්සම | https://gov.lk/forms/petition

You can also visit RESOURCE: Municipal Council - Gampaha for local permits.
RESOURCE: LAW: Municipal Councils Ordinance | https://gov.lk/laws/mco
"""

    extracted = extract_resources(sample)
    print(f"Extracted {len(extracted)} resources:\n")
    for resource in extracted:
        print(resource)
