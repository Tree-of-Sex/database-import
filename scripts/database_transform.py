#!/usr/bin/env python3
"""Transform source database tables into Tree of Sex import rows.

The output schema is always:

    species,key,value,reference

A mapping YAML describing where species names, references, source columns,
allowed values, ignored values, and destination keys live in each input
database must be provided. The converter is intentionally strict enough 
to make incomplete YAMLs visible before they are used for a real import.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
import zipfile
import yaml
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree

OUTPUT_FIELDS = ["species", "key", "value", "reference"]
INTEGER_TOKENS = {"int", "ints", "integer", "integers", "intiger", "intigers", "intger", "intgers"}
POSITIVE_INTEGER_TOKENS = {"positive integer", "positive integers", "positive int", "positive ints"}
INTEGER_RANGE_TOKENS = {"integer range", "integer ranges", "int range", "int ranges", "range", "ranges"}
NUMBER_TOKENS = {"number", "numeric", "float", "double"}
STRING_TOKENS = {"string", "str", "text", "all"}
BLANK_VALUES = {"", "na", "n/a", "none", "null", "nan"}

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


class ConfigError(ValueError):
    """Raised when a YAML mapping is missing required structure."""


class DataError(ValueError):
    """Raised when the source data cannot be loaded or validated."""


@dataclass(frozen=True)
class DestinationRule:
    key: str
    import_values: list[Any] = field(default_factory=list)
    ignore_values: set[str] = field(default_factory=set)
    separators: tuple[str, ...] = ("|",)


@dataclass(frozen=True)
class AttributeRule:
    column_name: str
    column_reference: str | None
    destinations: tuple[DestinationRule, ...]
    ignore_values: set[str] = field(default_factory=set)


@dataclass
class ConversionReport:
    input_rows: int = 0
    output_rows: int = 0
    species_count: int = 0
    rows_with_reference: int = 0
    rows_without_reference: int = 0
    empty_values: int = 0
    ignored_values: int = 0
    unmapped_values_count: int = 0
    rows_without_species: int = 0
    missing_reference_values: int = 0
    unique_keys: set[str] = field(default_factory=set)
    source_columns_seen: Counter[str] = field(default_factory=Counter)
    source_columns_imported: Counter[str] = field(default_factory=Counter)
    unmapped_values: Counter[str] = field(default_factory=Counter)
    imported_values_by_key: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_rows": self.input_rows,
            "output_rows": self.output_rows,
            "species_count": self.species_count,
            "unique_keys": sorted(self.unique_keys),
            "rows_with_reference": self.rows_with_reference,
            "rows_without_reference": self.rows_without_reference,
            "missing_reference_values": self.missing_reference_values,
            "empty_values": self.empty_values,
            "ignored_values": self.ignored_values,
            "unmapped_values_count": self.unmapped_values_count,
            "rows_without_species": self.rows_without_species,
            "source_columns_seen": dict(sorted(self.source_columns_seen.items())),
            "source_columns_imported": dict(sorted(self.source_columns_imported.items())),
            "unmapped_values": dict(sorted(self.unmapped_values.items())),
            "top_imported_values_by_key": {
                key: dict(counter.most_common(25))
                for key, counter in sorted(self.imported_values_by_key.items())
            },
        }


class YAMLConfigParser:
    """Parse and validate a database-import YAML file."""

    def __init__(self, yaml_path: str | Path):
        self.yaml_path = Path(yaml_path)
        try:
            with self.yaml_path.open("r", encoding="utf-8") as handle:
                self.config = load_yaml(handle)
        except yaml.YAMLError as exc:
            raise ConfigError(format_yaml_error(self.yaml_path, exc)) from exc
        if not isinstance(self.config, dict):
            raise ConfigError(f"{self.yaml_path}: YAML must contain a mapping at the top level")
        self.validate_config_shape()

    def validate_config_shape(self) -> None:
        required_sections = ["file", "taxonomy", "attributes"]
        for section in required_sections:
            if section not in self.config:
                raise ConfigError(f"{self.yaml_path}: missing required section '{section}'")

        if not isinstance(self.config["attributes"], list):
            raise ConfigError(f"{self.yaml_path}: 'attributes' must be a list")

        if not self.species_headers:
            raise ConfigError(
                f"{self.yaml_path}: taxonomy must define either species.header or genus.header "
                "and species_epithet.header"
            )

        for idx, attr in enumerate(self.config["attributes"], start=1):
            if not isinstance(attr, dict):
                raise ConfigError(f"{self.yaml_path}: attributes[{idx}] must be a mapping")
            if not attr.get("column_name"):
                raise ConfigError(f"{self.yaml_path}: attributes[{idx}] is missing column_name")
            destinations = attr.get("destination")
            if not destinations:
                raise ConfigError(
                    f"{self.yaml_path}: attribute '{attr.get('column_name')}' is missing destination"
                )
            if isinstance(destinations, dict):
                destinations = [destinations]
            if not isinstance(destinations, list):
                raise ConfigError(
                    f"{self.yaml_path}: destination for '{attr.get('column_name')}' must be a mapping or list"
                )
            for dest_idx, destination in enumerate(destinations, start=1):
                if not isinstance(destination, dict) or not destination.get("key"):
                    raise ConfigError(
                        f"{self.yaml_path}: destination {dest_idx} for "
                        f"'{attr.get('column_name')}' is missing key"
                    )

    @property
    def file_config(self) -> dict[str, Any]:
        return self.config["file"]

    @property
    def taxonomy_config(self) -> dict[str, Any]:
        return self.config["taxonomy"]

    @property
    def default_reference_header(self) -> str:
        return self.file_config.get("default reference header", "Reference")

    @property
    def species_headers(self) -> list[str]:
        species_header = self.taxonomy_config.get("species", {}).get("header")
        genus_header = self.taxonomy_config.get("genus", {}).get("header")
        epithet_header = self.taxonomy_config.get("species_epithet", {}).get("header")
        if species_header:
            return [species_header]
        if genus_header and epithet_header:
            return [genus_header, epithet_header]
        return []

    def parse_rules(self) -> list[AttributeRule]:
        return [self._parse_attribute(attr) for attr in self.config["attributes"]]

    def _parse_attribute(self, attr: dict[str, Any]) -> AttributeRule:
        destinations = attr["destination"]
        if isinstance(destinations, dict):
            destinations = [destinations]

        return AttributeRule(
            column_name=str(attr["column_name"]),
            column_reference=attr.get("column_reference") or attr.get("reference"),
            destinations=tuple(self._parse_destination(attr, destination) for destination in destinations),
            ignore_values=as_string_set(attr.get("ignore", [])),
        )

    def _parse_destination(self, attr: dict[str, Any], destination: dict[str, Any]) -> DestinationRule:
        import_values = destination.get("import_values", destination.get("import", []))
        return DestinationRule(
            key=str(destination["key"]),
            import_values=as_list(import_values),
            ignore_values=as_string_set(destination.get("ignore", [])),
            separators=as_separators(destination.get("separator", attr.get("separator", "|"))),
        )


class TableLoader:
    """Load source database tables into a list of string dictionaries."""

    def __init__(self, input_path: str | Path, file_config: dict[str, Any]):
        self.input_path = Path(input_path)
        self.file_config = file_config
        configured_format = str(file_config.get("format") or "").lower()
        suffix_format = self.input_path.suffix.lstrip(".").lower()
        self.format = configured_format or suffix_format
        if suffix_format in {"csv", "tsv", "xlsx", "xls"} and configured_format and configured_format != suffix_format:
            logger.warning(
                f"{self.input_path}: YAML declares format '{configured_format}' but file extension is '{suffix_format}'; using extension",
            )
            self.format = suffix_format

    def load(self) -> list[dict[str, str]]:
        if self.format == "csv":
            return self._load_delimited(",")
        if self.format == "tsv":
            return self._load_delimited("\t")
        if self.format == "xlsx":
            return self._load_xlsx()
        if self.format == "xls":
            raise DataError(
                f"{self.input_path}: legacy .xls files are not supported directly. "
                "Save the source file as .xlsx or CSV and update file.format in the YAML."
            )
        raise DataError(f"Unsupported input format '{self.format}' for {self.input_path}")

    def _load_delimited(self, delimiter: str) -> list[dict[str, str]]:
        comment = self.file_config.get("comment")
        try:
            with self.input_path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows: Iterable[str] = handle
                if comment:
                    rows = (line for line in handle if not line.startswith(str(comment)))
                reader = csv.DictReader(rows, delimiter=delimiter, skipinitialspace=True)
                if not reader.fieldnames:
                    raise DataError(f"{self.input_path}: no header row found")
                return [clean_row(row) for row in reader]
        except UnicodeDecodeError as exc:
            raise DataError(
                f"{self.input_path}: could not read as text. Check file.format in the YAML "
                "or convert the source file to CSV/TSV."
            ) from exc

    def _load_xlsx(self) -> list[dict[str, str]]:
        with zipfile.ZipFile(self.input_path) as archive:
            sheet_path = first_sheet_path(archive)
            shared_strings = read_shared_strings(archive)
            sheet_xml = ElementTree.fromstring(archive.read(sheet_path))

        rows: list[list[str]] = []
        namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        for row in sheet_xml.findall(".//x:sheetData/x:row", namespace):
            values: list[str] = []
            for cell in row.findall("x:c", namespace):
                column_index = column_index_from_cell_reference(cell.attrib.get("r", ""))
                while len(values) <= column_index:
                    values.append("")
                values[column_index] = read_cell_value(cell, shared_strings, namespace)
            rows.append(values)

        if not rows:
            raise DataError(f"{self.input_path}: no rows found in first worksheet")

        headers = [header.strip() for header in rows[0]]
        if not any(headers):
            raise DataError(f"{self.input_path}: no header row found in first worksheet")

        records = []
        for row in rows[1:]:
            padded = row + [""] * (len(headers) - len(row))
            records.append({header: stringify(value) for header, value in zip(headers, padded) if header})
        return records


class CSVConverter:
    """Convert source rows using a YAML mapping."""

    def __init__(self, input_path: str | Path, yaml_path: str | Path, strict: bool = True):
        self.input_path = Path(input_path)
        self.yaml_path = Path(yaml_path)
        self.strict = strict
        self.config_parser = YAMLConfigParser(self.yaml_path)
        self.rules = self.config_parser.parse_rules()
        self.rows = TableLoader(self.input_path, self.config_parser.file_config).load()
        self.headers = set(self.rows[0].keys()) if self.rows else set()
        self.output_rows: list[dict[str, str]] = []
        self.report = ConversionReport(input_rows=len(self.rows))
        self.validate_against_input()

    def validate_against_input(self) -> None:
        missing_columns: set[str] = set()
        missing_columns.update(header for header in self.config_parser.species_headers if header not in self.headers)

        for rule in self.rules:
            if rule.column_name not in self.headers:
                missing_columns.add(rule.column_name)
            if rule.column_reference and rule.column_reference not in self.headers:
                missing_columns.add(rule.column_reference)

        default_ref = self.config_parser.default_reference_header
        rules_without_specific_ref = [rule.column_name for rule in self.rules if not rule.column_reference]
        if rules_without_specific_ref and default_ref not in self.headers:
            missing_columns.add(default_ref)

        if missing_columns:
            message = f"{self.input_path}: missing configured columns: {sorted(missing_columns)}"
            if self.strict:
                raise DataError(message)
            logger.warning(message)

    def convert(self) -> list[dict[str, str]]:
        species_seen: set[str] = set()

        for row_number, row in enumerate(self.rows, start=2):
            species = self.get_species(row)
            if not species:
                self.report.rows_without_species += 1
                logger.debug("Row %s: no species found, skipping", row_number)
                continue
            species_seen.add(species)

            for rule in self.rules:
                source_value = row.get(rule.column_name, "").strip()
                if is_blank(source_value):
                    self.report.empty_values += 1
                    continue

                self.report.source_columns_seen[rule.column_name] += 1
                if source_value in rule.ignore_values:
                    self.report.ignored_values += 1
                    continue

                reference = self.get_reference_value(row, rule)
                if not reference:
                    self.report.missing_reference_values += 1

                for destination in rule.destinations:
                    for value in split_values(source_value, destination.separators):
                        if is_blank(value):
                            self.report.empty_values += 1
                            continue
                        if value in destination.ignore_values:
                            self.report.ignored_values += 1
                            continue

                        translated_value = translate_value(value, destination.import_values)
                        if translated_value is None:
                            self.report.unmapped_values_count += 1
                            self.report.unmapped_values[f"{rule.column_name} -> {destination.key}: {value}"] += 1
                            continue

                        output_row = {
                            "species": species,
                            "key": destination.key,
                            "value": translated_value,
                            "reference": reference,
                        }
                        self.output_rows.append(output_row)
                        self.report.source_columns_imported[rule.column_name] += 1
                        self.report.imported_values_by_key[destination.key][translated_value] += 1
                        self.report.unique_keys.add(destination.key)

        self.report.output_rows = len(self.output_rows)
        self.report.species_count = len(species_seen)
        self.report.rows_with_reference = sum(1 for row in self.output_rows if row["reference"])
        self.report.rows_without_reference = self.report.output_rows - self.report.rows_with_reference
        return self.output_rows

    def get_species(self, row: dict[str, str]) -> str | None:
        taxonomy = self.config_parser.taxonomy_config
        species_header = taxonomy.get("species", {}).get("header")
        if species_header:
            return blank_to_none(row.get(species_header, "").strip())

        genus_header = taxonomy.get("genus", {}).get("header")
        epithet_header = taxonomy.get("species_epithet", {}).get("header")
        genus = row.get(genus_header, "").strip() if genus_header else ""
        epithet = row.get(epithet_header, "").strip() if epithet_header else ""
        return blank_to_none(f"{genus} {epithet}".strip())

    def get_reference_value(self, row: dict[str, str], rule: AttributeRule) -> str:
        if rule.column_reference:
            return row.get(rule.column_reference, "").strip()
        return row.get(self.config_parser.default_reference_header, "").strip()

    def write_output(self, output_path: str | Path) -> None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
            writer.writeheader()
            writer.writerows(self.output_rows)
        logger.info("Output written to %s", output_path)

    def write_report(self, report_path: str | Path) -> None:
        report_path = Path(report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open("w", encoding="utf-8") as handle:
            json.dump(self.report.as_dict(), handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        logger.info("Report written to %s", report_path)


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def load_yaml(handle: Any) -> Any:
    return yaml.safe_load(handle)


def format_yaml_error(path: Path, exc: yaml.YAMLError) -> str:
    mark = getattr(exc, "problem_mark", None)
    location = ""
    if mark is not None:
        location = f" line {mark.line + 1}, column {mark.column + 1}"

    problem = getattr(exc, "problem", None) or str(exc).splitlines()[0]
    hint = ""
    if "cannot start any token" in problem or "\\t" in problem or "\t" in str(exc):
        hint = " YAML indentation must use spaces, not tabs."

    return f"{path}:{location}: invalid YAML: {problem}.{hint}"


def first_sheet_path(archive: zipfile.ZipFile) -> str:
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    namespace = {
        "x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    }
    first_sheet = workbook.find(".//x:sheets/x:sheet", namespace)
    if first_sheet is None:
        raise DataError("Excel workbook does not contain any worksheets")

    relationship_id = first_sheet.attrib.get(f"{{{namespace['r']}}}id")
    if not relationship_id:
        return "xl/worksheets/sheet1.xml"

    relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    for relationship in relationships.findall("rel:Relationship", namespace):
        if relationship.attrib.get("Id") == relationship_id:
            target = relationship.attrib["Target"].lstrip("/")
            return target if target.startswith("xl/") else f"xl/{target}"

    raise DataError(f"Could not resolve first worksheet relationship {relationship_id}")


def read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []

    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    shared_xml = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    strings = []
    for item in shared_xml.findall("x:si", namespace):
        strings.append("".join(text.text or "" for text in item.findall(".//x:t", namespace)))
    return strings


def column_index_from_cell_reference(reference: str) -> int:
    letters = "".join(char for char in reference if char.isalpha())
    if not letters:
        return 0
    index = 0
    for letter in letters.upper():
        index = index * 26 + (ord(letter) - ord("A") + 1)
    return index - 1


def read_cell_value(cell: ElementTree.Element, shared_strings: list[str], namespace: dict[str, str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(text.text or "" for text in cell.findall(".//x:t", namespace)).strip()

    value = cell.find("x:v", namespace)
    if value is None or value.text is None:
        return ""

    raw_value = value.text
    if cell_type == "s":
        try:
            return shared_strings[int(raw_value)].strip()
        except (IndexError, ValueError):
            return raw_value.strip()

    return raw_value.strip()


def as_string_set(value: Any) -> set[str]:
    return {str(item).strip() for item in as_list(value)}


def as_separators(value: Any) -> tuple[str, ...]:
    separators = tuple(str(item) for item in as_list(value) if str(item))
    return separators or ("|",)


def clean_row(row: dict[str, Any]) -> dict[str, str]:
    return {stringify(key): stringify(value) for key, value in row.items() if key is not None}


def stringify(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def blank_to_none(value: str) -> str | None:
    return None if is_blank(value) else value


def is_blank(value: str) -> bool:
    return value.strip().lower() in BLANK_VALUES


def split_values(value: str, separators: tuple[str, ...]) -> list[str]:
    if not separators:
        return [value.strip()]
    pattern = "|".join(re.escape(separator) for separator in separators)
    return [part.strip() for part in re.split(pattern, value) if part.strip()]


def translate_value(value: str, import_values: list[Any]) -> str | None:
    """Return the imported value, or None when the value is not allowed."""
    if not import_values:
        return value

    for rule in import_values:
        if isinstance(rule, dict):
            for source, destination in rule.items():
                source_text = stringify(source)
                if source_text == "all":
                    return stringify(destination)
                if source_text == value:
                    return stringify(destination)
                typed_value = coerce_type_token(value, source_text)
                if typed_value is not None:
                    return typed_value
        else:
            token = stringify(rule)
            if token == value:
                return value
            typed_value = coerce_type_token(value, token)
            if typed_value is not None:
                return typed_value

    return None


def coerce_type_token(value: str, token: str) -> str | None:
    normalized_token = token.strip().lower()
    if normalized_token in STRING_TOKENS:
        return value
    if normalized_token in INTEGER_TOKENS:
        return coerce_integer(value)
    if normalized_token in POSITIVE_INTEGER_TOKENS:
        integer = coerce_integer(value)
        if integer is not None and int(integer) > 0:
            return integer
        return None
    if normalized_token in INTEGER_RANGE_TOKENS:
        return coerce_integer_range(value)
    if normalized_token in NUMBER_TOKENS:
        if re.fullmatch(r"-?(?:\d+(?:\.\d*)?|\.\d+)", value):
            return value
        return None
    return None


def coerce_integer(value: str) -> str | None:
    if re.fullmatch(r"-?\d+", value):
        return value
    if re.fullmatch(r"-?\d+\.0+", value):
        return value.split(".", maxsplit=1)[0]
    return None


def coerce_integer_range(value: str) -> str | None:
    parts = re.split(r"\s*[-–]\s*", value)
    if len(parts) == 1:
        return coerce_integer(parts[0])
    if len(parts) == 2:
        start = coerce_integer(parts[0])
        end = coerce_integer(parts[1])
        if start is not None and end is not None:
            return f"{start}-{end}"
    return None


def print_report(report: ConversionReport) -> None:
    data = report.as_dict()
    print("\n" + "=" * 60)
    print("CONVERSION REPORT")
    print("=" * 60)
    print(f"Input rows: {data['input_rows']}")
    print(f"Output rows: {data['output_rows']}")
    print(f"Species found: {data['species_count']}")
    print(f"Unique keys: {len(data['unique_keys'])}")
    print(f"  Keys: {', '.join(data['unique_keys'])}")
    print(f"Rows with reference: {data['rows_with_reference']}")
    print(f"Rows without reference: {data['rows_without_reference']}")
    print(f"Empty values: {data['empty_values']}")
    print(f"Ignored values: {data['ignored_values']}")
    print(f"Unmapped values: {data['unmapped_values_count']}")
    print(f"Rows without species: {data['rows_without_species']}")
    if data["unmapped_values"]:
        print("First 10 unmapped values:")
        for value, count in list(data["unmapped_values"].items())[:10]:
            print(f"  {count}x {value}")
    print("=" * 60)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a source database table into Tree of Sex species/key/value/reference rows."
    )
    parser.add_argument("input", help="Input database table (.csv, .tsv, or .xlsx)")
    parser.add_argument("yaml", help="YAML mapping file")
    parser.add_argument("output", nargs="?", help="Output CSV path")
    parser.add_argument("--report", help="Optional JSON conversion report path")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the YAML against the input table without writing converted rows",
    )
    parser.add_argument(
        "--allow-missing-columns",
        action="store_true",
        help="Warn instead of failing when the YAML refers to missing input columns",
    )
    parser.add_argument(
        "--fail-on-unmapped",
        action="store_true",
        help="Exit with an error if any non-empty, non-ignored source values are not imported",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    try:
        converter = CSVConverter(args.input, args.yaml, strict=not args.allow_missing_columns)
        converter.convert()
        print_report(converter.report)

        if args.report:
            converter.write_report(args.report)

        if args.fail_on_unmapped and converter.report.unmapped_values_count:
            raise DataError(
                f"{converter.report.unmapped_values_count} source values were not imported; "
                "output CSV was not written. Inspect the report and update the YAML import/ignore lists"
            )

        if not args.validate_only:
            output_path = args.output or "output.csv"
            converter.write_output(output_path)

    except (ConfigError, DataError) as exc:
        logger.error("%s", exc)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
