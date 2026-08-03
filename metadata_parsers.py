import xml.etree.ElementTree as ET
from pathlib import Path


def xml_local_name(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def get_child(element: ET.Element, child_name: str) -> ET.Element | None:
    for child in element:
        if xml_local_name(child.tag) == child_name:
            return child
    return None


def get_child_text(element: ET.Element, child_name: str) -> str:
    child = get_child(element, child_name)
    return (child.text or "").strip() if child is not None else ""


def extract_localized_string(element: ET.Element | None) -> str:
    if element is None:
        return ""
    for item in element.iter():
        if xml_local_name(item.tag) == "content":
            text = (item.text or "").strip()
            if text:
                return text
    return (element.text or "").strip()


def parse_event_subscription_xml(filepath: Path) -> dict | None:
    try:
        root = ET.parse(filepath).getroot()
    except (ET.ParseError, OSError):
        return None

    subscription = next(
        (child for child in root if xml_local_name(child.tag) == "EventSubscription"),
        None,
    )
    if subscription is None:
        return None

    properties = get_child(subscription, "Properties")
    if properties is None:
        return None

    name = get_child_text(properties, "Name")
    event = get_child_text(properties, "Event")
    handler = get_child_text(properties, "Handler")
    synonym = extract_localized_string(get_child(properties, "Synonym"))
    comment = get_child_text(properties, "Comment")

    sources: list[str] = []
    source_element = get_child(properties, "Source")
    if source_element is not None:
        for item in source_element:
            if xml_local_name(item.tag) not in {"Type", "TypeSet"}:
                continue
            source = (item.text or "").strip()
            if source and source not in sources:
                sources.append(source)

    handler_parts = handler.split(".")
    handler_module = ".".join(handler_parts[:2]) if len(handler_parts) >= 2 else ""
    handler_method = ".".join(handler_parts[2:]) if len(handler_parts) >= 3 else ""

    lines = [
        "Тип метаданных: EventSubscription",
        f"Имя подписки: {name}",
        f"Синоним: {synonym}",
        f"Событие: {event}",
        f"Источники: {', '.join(sources)}",
        f"Обработчик: {handler}",
    ]
    if comment:
        lines.append(f"Описание: {comment}")

    return {
        "name": name,
        "synonym": synonym,
        "comment": comment,
        "event": event,
        "handler": handler,
        "handler_module": handler_module,
        "handler_method": handler_method,
        "sources": sources,
        "uuid": subscription.attrib.get("uuid", ""),
        "card_text": "\n".join(lines),
    }
