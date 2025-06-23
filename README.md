# database-import

everything for integrating other databases into the Tree of Sex database.

## What is the idea

We want to integrate other databases in a smart way - in a way, that we could do it again. In a way, that would be compatible with all the ways others store data.

The idea is to create config-files, that would map other databases onto Tree of Sex database terminology (defined by a dictionary at the moment, and by the ToS Ontology in not-so-distant future).

## YAML formating proposal

Formating of the `v3` of the YAML:

```
file:
  format: csv
  header: true
  comment: "#"
  name: TOS_data_2024-11-18_invertebrates.csv
  submitter: "Tree of Sex 1" # source in GoaT
  # source_date: 2021-03-03
  default reference header: "citation" # ignore if attributed have specific refernce values

attributes:
  # - column_name: "Imported column name"
  #   column_reference: "Name of a column with references"
  #   destination:
  #     key: "name of the destination column"
  #     separator: "|" # allows for multiple records per row, separated by the listed symbol
  #     import_values: # listed values that will be imported together with translation to what they should be imported as
  #       - "source value": "destination value"
  #       - "Important Feat": "important feature" # or maybe magic "all" / "ints" / other types?
  #     ignore:
  #       - "Uninportant value" # specific values to ignore whem processing, overrides all
  #       - "Any other value not worth recording"
  #   destination:
  #     key: "The other key that gets information from this"
  #     import_values:
  #       - "Important Feat 2": "important feature in a different destination"
```

The full example can be found here: [TOS_data_2024-11-18_invertebrates2ToS1.1.0.yaml](database_import_files/TOS_data_2024-11-18_invertebrates2ToS1.1.0.yaml), and the database file is [here](https://www.treeofsex.org/resources/data.invert.csv). I tried to make sure it covers every possible situation.

What people thing? WOuld this work?

## Imported databases in ToS 1.1.0

Maintained here: https://docs.google.com/spreadsheets/d/1dz-JoZ-aO-CBbRQoKntHEFgrdBCx3nLfmJXUXoAy4vI/edit?gid=0#gid=0
