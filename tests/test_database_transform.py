import csv
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "scripts"))

from database_transform import CSVConverter, ConfigError, DataError, TableLoader, YAMLConfigParser


class DatabaseTransformTests(unittest.TestCase):
    def test_converts_with_yaml_aliases_and_skips_unmapped_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "input.csv"
            yaml_path = tmp_path / "mapping.yaml"

            input_path.write_text(
                "Genus,Species,trait,trait_ref,count,default_ref\n"
                "Aus,bus,XY|unknown,ref-a,4,default-a\n"
                "Cus,dus,other,ref-b,not-a-number,default-b\n",
                encoding="utf-8",
            )
            yaml_path.write_text(
                """
file:
  format: csv
  header: true
  default reference header: default_ref
taxonomy:
  genus:
    header: Genus
  species_epithet:
    header: Species
attributes:
  - column_name: trait
    reference: trait_ref
    destination:
      key: sex_chromosome_formula
      separator: "|"
      import:
        - XY
      ignore:
        - other
  - column_name: count
    destination:
      key: chromosome_number
      import:
        - intigers
""",
                encoding="utf-8",
            )

            converter = CSVConverter(input_path, yaml_path)
            rows = converter.convert()

        self.assertEqual(
            rows,
            [
                {
                    "species": "Aus bus",
                    "key": "sex_chromosome_formula",
                    "value": "XY",
                    "reference": "ref-a",
                },
                {
                    "species": "Aus bus",
                    "key": "chromosome_number",
                    "value": "4",
                    "reference": "default-a",
                },
            ],
        )
        self.assertEqual(converter.report.unmapped_values_count, 2)
        self.assertEqual(converter.report.ignored_values, 1)

    def test_missing_configured_columns_fail_in_strict_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "input.csv"
            yaml_path = tmp_path / "mapping.yaml"

            input_path.write_text("species,present,reference\nAus bus,value,ref\n", encoding="utf-8")
            yaml_path.write_text(
                """
file:
  format: csv
  header: true
  default reference header: reference
taxonomy:
  species:
    header: species
attributes:
  - column_name: missing
    destination:
      key: trait
      import:
        - value
""",
                encoding="utf-8",
            )

            with self.assertRaises(DataError):
                CSVConverter(input_path, yaml_path)

    def test_yaml_tabs_get_friendly_config_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            yaml_path = Path(tmp) / "bad.yaml"
            yaml_path.write_text("file:\n\tformat: csv\n", encoding="utf-8")

            with self.assertRaisesRegex(ConfigError, "spaces, not tabs"):
                YAMLConfigParser(yaml_path)

    def test_write_output_always_uses_four_import_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "input.csv"
            yaml_path = tmp_path / "mapping.yaml"
            output_path = tmp_path / "output.csv"

            input_path.write_text("species,trait,reference\nAus bus,value,ref\n", encoding="utf-8")
            yaml_path.write_text(
                """
file:
  format: csv
  header: true
  default reference header: reference
taxonomy:
  species:
    header: species
attributes:
  - column_name: trait
    destination:
      key: trait
      import:
        - string
""",
                encoding="utf-8",
            )

            converter = CSVConverter(input_path, yaml_path)
            converter.convert()
            converter.write_output(output_path)

            with output_path.open(newline="", encoding="utf-8") as handle:
                reader = csv.reader(handle)
                header = next(reader)

        self.assertEqual(header, ["species", "key", "value", "reference"])

    def test_reads_simple_xlsx_without_optional_excel_dependency(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "input.xlsx"
            yaml_path = tmp_path / "mapping.yaml"
            make_minimal_xlsx(
                input_path,
                [
                    ["species", "trait", "reference"],
                    ["Aus bus", "value", "ref"],
                ],
            )
            yaml_path.write_text(
                """
file:
  format: xlsx
  header: true
  default reference header: reference
taxonomy:
  species:
    header: species
attributes:
  - column_name: trait
    destination:
      key: trait
      import:
        - value
""",
                encoding="utf-8",
            )

            converter = CSVConverter(input_path, yaml_path)
            rows = converter.convert()

        self.assertEqual(rows[0]["species"], "Aus bus")
        self.assertEqual(rows[0]["value"], "value")

    def test_legacy_xls_has_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.xls"
            input_path.write_bytes(b"not a supported workbook")

            with self.assertRaisesRegex(DataError, "legacy .xls files are not supported"):
                TableLoader(input_path, {"format": "xls"}).load()

    def test_fail_on_unmapped_does_not_write_output_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "input.csv"
            yaml_path = tmp_path / "mapping.yaml"
            output_path = tmp_path / "output.csv"
            report_path = tmp_path / "report.json"

            input_path.write_text("species,trait,reference\nAus bus,unexpected,ref\n", encoding="utf-8")
            yaml_path.write_text(
                """
file:
  format: csv
  header: true
  default reference header: reference
taxonomy:
  species:
    header: species
attributes:
  - column_name: trait
    destination:
      key: trait
      import:
        - expected
""",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parents[1] / "scripts" / "database_transform.py"),
                    str(input_path),
                    str(yaml_path),
                    str(output_path),
                    "--report",
                    str(report_path),
                    "--fail-on-unmapped",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output_path.exists())
            self.assertTrue(report_path.exists())


def make_minimal_xlsx(path, rows):
    shared_strings = []
    shared_lookup = {}
    sheet_rows = []
    for row_number, row in enumerate(rows, start=1):
        cells = []
        for col_number, value in enumerate(row, start=1):
            if value not in shared_lookup:
                shared_lookup[value] = len(shared_strings)
                shared_strings.append(value)
            cells.append(
                f'<c r="{column_name(col_number)}{row_number}" t="s"><v>{shared_lookup[value]}</v></c>'
            )
        sheet_rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
</Types>""",
        )
        archive.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>
</workbook>""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f"""<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<sheetData>{''.join(sheet_rows)}</sheetData>
</worksheet>""",
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            f"""<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{len(shared_strings)}" uniqueCount="{len(shared_strings)}">
{''.join(f'<si><t>{value}</t></si>' for value in shared_strings)}
</sst>""",
        )


def column_name(number):
    letters = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


if __name__ == "__main__":
    unittest.main()
