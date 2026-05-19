# database-import

Tool for integrating existing databases into the Tree of Sex database.

## What is the idea

We want to integrate other databases in a smart way - in a way, that we could do it again. In a way, that would be compatible with all the ways others store data.

The idea is to create config-files, that would map other databases onto Tree of Sex database terminology (defined by a dictionary at the moment, and by the ToS Ontology in not-so-distant future).

## YAML formating proposal

Formating of the `v4` of the YAML.

```
file:
  format: csv
  header: true
  comment: "#"
  name: TOS_data_2024-11-18_invertebrates.csv
  submitter: "Tree of Sex 1" # source in GoaT
  # source_date: 2021-03-03
  default reference header: "citation" # ignore if attributed have specific refernce values

taxonomy:
  taxon_id:
    header: ncbi_taxon_id
  species:
    header: species
  family:
    header: family

attributes:
  - column_name: "Imported column name"
    column_reference: "Name of a column with references"
    destination:
      key: "name of the destination column"
      separator: "|" # allows for multiple records per row, separated by the listed symbol
      import_values: # listed values that will be imported together with translation to what they should be imported as
        - "source value": "destination value"
        - "Important Feat": "important feature" # or maybe magic "all" / "ints" / other types?
      ignore:
        - "Uninportant value" # specific values to ignore whem processing, overrides all
        - "Any other value not worth recording"
    destination:
      key: "The other key that gets information from this"
      import_values:
        - "Important Feat 2": "important feature in a different destination"
```

The full example can be found here: [TOS_data_2024-11-18_invertebrates2ToS1.1.0.yaml](database_import_files/TOS_data_2024-11-18_invertebrates2ToS1.1.0.yaml), and the database file is [here](https://www.treeofsex.org/resources/data.invert.csv). I tried to make sure it covers every possible situation.

Changelog:
 - `v4` - adding taxonomy reading consistent with GoaT
 - `v3` - reformating lists so they make sense; adding magic values on import
 - `v1` and `v2` - first drafts of the structure largely inspired by GoaT import files 

## Imported databases in ToS 1.1.0

Maintained here: https://docs.google.com/spreadsheets/d/1dz-JoZ-aO-CBbRQoKntHEFgrdBCx3nLfmJXUXoAy4vI/edit?gid=0#gid=0

### Transforming and checking an import

Install the small Python dependency first:

```bash
python3 -m pip install -r requirements.txt
```

Run the converter with a JSON report and fail the run if any non-empty,
non-ignored source values are not imported:

```bash
python3 scripts/database_transform.py \
  data/_Database_files/3-ToS1/data.invert.csv \
  database_import_files/TOS_data_2024-11-18_invertebrates2ToS1.1.0.yaml \
  output_reformatted_db/TOS_data_invertebrates_ToS1.1.0_formatted.csv \
  --report output_reformatted_db/TOS_data_invertebrates_ToS1.1.0_report.json \
  --fail-on-unmapped
```

The output table always has four columns:

```text
species,key,value,reference
```

The report is the main sanity check. It lists row counts, unique imported keys,
reference coverage, empty values, ignored values, and all unmapped source
values. For each unmapped value, decide whether to:

- add it to `import` when it should be imported as-is,
- add a mapping such as `"source value": "Tree of Sex value"`,
- add it to `ignore` when it is deliberately not imported, or
- clean the source data if the value is a typo or mixed-format entry.

Useful validation flags:

- `--validate-only` checks the YAML and source data without writing the output CSV.
- `--fail-on-unmapped` exits with an error without writing the output CSV when any non-empty value is neither imported nor ignored.
- `--allow-missing-columns` downgrades missing configured columns to warnings during YAML drafting.
