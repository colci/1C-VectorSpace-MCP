import tempfile
from pathlib import Path

from metadata_parsers import parse_event_subscription_xml


EVENT_SUBSCRIPTION_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Meta xmlns="http://v8.1c.ru/8.3/MDClasses" xmlns:v8="http://v8.1c.ru/8.1/data/core">
  <EventSubscription uuid="subscription-uuid">
    <Properties>
      <Name>BeforeWriteProducts</Name>
      <Synonym>
        <v8:item>
          <v8:content>Перед записью товаров</v8:content>
        </v8:item>
      </Synonym>
      <Comment>Smoke parser check</Comment>
      <Source>
        <Type>CatalogRef.Products</Type>
        <Type>CatalogRef.Products</Type>
        <TypeSet>DocumentRef.Sales</TypeSet>
      </Source>
      <Event>BeforeWrite</Event>
      <Handler>CommonModule.ProductsEvents.BeforeWrite</Handler>
    </Properties>
  </EventSubscription>
</Meta>
"""


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="metadata-parser-smoke-") as tmpdir:
        xml_file = Path(tmpdir) / "EventSubscription.xml"
        xml_file.write_text(EVENT_SUBSCRIPTION_XML, encoding="utf-8")

        parsed = parse_event_subscription_xml(xml_file)
        assert parsed is not None
        assert parsed["name"] == "BeforeWriteProducts"
        assert parsed["synonym"] == "Перед записью товаров"
        assert parsed["comment"] == "Smoke parser check"
        assert parsed["event"] == "BeforeWrite"
        assert parsed["handler"] == "CommonModule.ProductsEvents.BeforeWrite"
        assert parsed["handler_module"] == "CommonModule.ProductsEvents"
        assert parsed["handler_method"] == "BeforeWrite"
        assert parsed["sources"] == ["CatalogRef.Products", "DocumentRef.Sales"]
        assert parsed["uuid"] == "subscription-uuid"
        assert "Тип метаданных: EventSubscription" in parsed["card_text"]

        broken_file = Path(tmpdir) / "Broken.xml"
        broken_file.write_text("<Meta>", encoding="utf-8")
        assert parse_event_subscription_xml(broken_file) is None

        unrelated_file = Path(tmpdir) / "Catalog.xml"
        unrelated_file.write_text("<Meta><Catalog /></Meta>", encoding="utf-8")
        assert parse_event_subscription_xml(unrelated_file) is None

    print("[metadata-parser-smoke] success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
