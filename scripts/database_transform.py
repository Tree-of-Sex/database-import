import yaml
import csv
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
import logging


# Set up logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


@dataclass
class ConversionRule:
    """Represents a single attribute conversion rule."""
    column_name: str
    column_reference: Optional[str]
    destinations: List[Dict[str, Any]]
    ignore_values: List[str]
    separator: str


class YAMLConfigParser:
    """Parse and validate YAML configuration files."""
    
    def __init__(self, yaml_path: str):
        """Load and parse YAML configuration file."""
        with open(yaml_path, 'r') as f:
            self.config = yaml.safe_load(f)
        
        self.validate_config()
    
    def validate_config(self) -> None:
        """Validate required fields in configuration."""
        required_sections = ['file', 'taxonomy', 'attributes']
        for section in required_sections:
            if section not in self.config:
                raise ValueError(f"Missing required section: {section}")
    
    def get_file_config(self) -> Dict[str, Any]:
        """Get file configuration."""
        return self.config['file']
    
    def get_taxonomy_config(self) -> Dict[str, Any]:
        """Get taxonomy configuration."""
        return self.config['taxonomy']
    
    def get_attributes(self) -> List[Dict[str, Any]]:
        """Get attributes configuration."""
        return self.config['attributes']
    
    def get_default_reference_header(self) -> str:
        """Get default reference column header."""
        file_config = self.get_file_config()
        return file_config.get('default reference header', 'Reference')


class CSVConverter:
    """Convert CSV files using YAML configuration."""
    
    def __init__(self, csv_path: str, yaml_path: str):
        """
        Initialize converter.
        
        Args:
            csv_path: Path to input CSV file
            yaml_path: Path to YAML configuration file
        """
        self.csv_path = csv_path
        self.yaml_path = yaml_path
        
        # Load configuration
        self.config_parser = YAMLConfigParser(yaml_path)
        self.file_config = self.config_parser.get_file_config()
        self.taxonomy_config = self.config_parser.get_taxonomy_config()
        self.attributes = self.config_parser.get_attributes()
        self.default_ref_header = self.config_parser.get_default_reference_header()
        
        # Load CSV data
        self.csv_data = self.load_csv()
        self.headers = list(self.csv_data[0].keys()) if self.csv_data else []
        
        # Conversion results
        self.output_rows = []
    
    def load_csv(self) -> List[Dict[str, str]]:
        """Load CSV file respecting configuration."""
        file_config = self.file_config
        
        with open(self.csv_path, 'r') as f:
            # Handle comments if specified
            if file_config.get('comment'):
                lines = [line for line in f if not line.startswith(file_config['comment'])]
                f.seek(0)
                f = iter(lines)
            
            reader = csv.DictReader(f, skipinitialspace=True)
            data = list(reader)
        
        logger.info(f"Loaded {len(data)} rows from {self.csv_path}")
        return data
    
    def parse_conversion_rule(self, attr: Dict[str, Any]) -> ConversionRule:
        """
        Parse a single attribute conversion rule.
        
        Args:
            attr: Attribute configuration dictionary
            
        Returns:
            ConversionRule object
        """
        column_name = attr.get('column_name')
        if not column_name:
            raise ValueError("Missing 'column_name' in attribute")
        
        # Handle multiple destinations (can be a list or a single dict)
        destinations = attr.get('destination', [])
        if isinstance(destinations, dict):
            destinations = [destinations]
        
        column_reference = attr.get('column_reference')
        ignore_values = attr.get('ignore', [])
        separator = attr.get('separator', '|')
        
        return ConversionRule(
            column_name=column_name,
            column_reference=column_reference,
            destinations=destinations,
            ignore_values=ignore_values,
            separator=separator
        )
    
    def get_reference_value(self, row: Dict[str, str], rule: ConversionRule) -> str:
        """
        Get reference value for a row.
        
        Args:
            row: CSV row data
            rule: Conversion rule
            
        Returns:
            Reference value
        """
        # Column-specific reference takes precedence
        if rule.column_reference and rule.column_reference in row:
            return row[rule.column_reference]
        
        # Fall back to default reference header
        if self.default_ref_header in row:
            return row[self.default_ref_header]
        
        return ""
    
    def translate_value(self, value: str, import_values: Optional[List] = None) -> Optional[str]:
        """
        Translate a value according to import_values mapping.
        
        Args:
            value: Original value
            import_values: List of mappings or special types
            
        Returns:
            Translated value or None if should be ignored
        """
        if not import_values:
            return value
        
        for mapping in import_values:
            if isinstance(mapping, dict):
                # Direct mapping: {"source": "destination"}
                for source, dest in mapping.items():
                    if source == value:
                        return dest
                    # Check for special types
                    if source == "ints" and value.isdigit():
                        return value
                    if source == "all":
                        return dest
            elif isinstance(mapping, str):
                # If it's just a string, it might be a special type indicator
                if mapping == "ints" and value.isdigit():
                    return value
        
        # If no mapping found, return original value
        return value
    
    def get_species(self, row: Dict[str, str]) -> Optional[str]:
        """Extract species name from row."""
        species_config = self.taxonomy_config.get('species', {})
        species_header = species_config.get('header')
        
        if species_header and species_header in row:
            return row[species_header]
        
        genus_config = self.taxonomy_config.get('genus', {})
        genus_header = genus_config.get('header')
        species_epithet_config = self.taxonomy_config.get('species_epithet', {})
        species_epithet_header = species_epithet_config.get('header')

        if genus_header and genus_header in row and species_epithet_header and species_epithet_header in row:
            return f"{row[genus_header]} {row[species_epithet_header]}"

        return None
    
    def convert(self) -> List[Dict[str, str]]:
        """
        Perform the conversion.
        
        Returns:
            List of output rows with columns: species, key, value, reference
        """
        output_rows = []
        
        # Get all explicitly configured source columns
        configured_columns = set()
        for attr in self.attributes:
            configured_columns.add(attr.get('column_name'))
        
        logger.info(f"Processing configured columns: {sorted(configured_columns)}")
        
        for row_idx, row in enumerate(self.csv_data):
            species = self.get_species(row)
            
            if not species:
                logger.warning(f"Row {row_idx + 2}: No species found, skipping")
                continue
            
            # Process each attribute
            for attr in self.attributes:
                rule = self.parse_conversion_rule(attr)
                
                # Check if source column exists in CSV
                if rule.column_name not in row:
                    logger.debug(f"Row {row_idx + 2}: Column '{rule.column_name}' not found in CSV, skipping")
                    continue
                
                source_value = row[rule.column_name].strip()
                
                # Skip empty values
                if not source_value:
                    continue
                
                # Skip ignored values
                if source_value in rule.ignore_values:
                    continue
                
                # Get reference
                reference = self.get_reference_value(row, rule)
                
                # Handle separator (multiple values in one cell)
                values = [v.strip() for v in source_value.split(rule.separator)]
                
                # Process each destination
                for destination in rule.destinations:
                    dest_key = destination.get('key')
                    if not dest_key:
                        continue
                    
                    import_values = destination.get('import_values')
                    ignore = destination.get('ignore', [])
                    
                    # Process each value
                    for val in values:
                        # Skip if in ignore list
                        if val in ignore:
                            continue
                        
                        # Translate value
                        translated_val = self.translate_value(val, import_values)
                        
                        # Skip if translation failed
                        if translated_val is None:
                            continue
                        
                        output_rows.append({
                            'species': species,
                            'key': dest_key,
                            'value': translated_val,
                            'reference': reference
                        })
        
        self.output_rows = output_rows
        logger.info(f"Converted to {len(output_rows)} output rows")
        return output_rows
    
    def write_output(self, output_path: str) -> None:
        """
        Write converted data to output CSV file.
        
        Args:
            output_path: Path to output CSV file
        """
        if not self.output_rows:
            logger.warning("No output rows to write")
            return
        
        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            fieldnames = ['species', 'key', 'value', 'reference']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self.output_rows)
        
        logger.info(f"Output written to {output_path}")
    
    def get_report(self) -> Dict[str, Any]:
        """Get conversion report."""
        return {
            'input_rows': len(self.csv_data),
            'output_rows': len(self.output_rows),
            'species_count': len(set(r['species'] for r in self.output_rows)),
            'unique_keys': sorted(set(r['key'] for r in self.output_rows)),
            'rows_with_reference': sum(1 for r in self.output_rows if r['reference']),
            'rows_without_reference': sum(1 for r in self.output_rows if not r['reference']),
        }


def main():
    """Example usage."""
    if len(sys.argv) < 3:
        print("Usage: python csv_converter.py <input.csv> <config.yaml> [output.csv]")
        print("\nExample:")
        print("  python csv_converter.py data.csv config.yaml output.csv")
        sys.exit(1)
    
    csv_path = sys.argv[1]
    yaml_path = sys.argv[2]
    output_path = sys.argv[3] if len(sys.argv) > 3 else "output.csv"
    
    try:
        # Run conversion
        converter = CSVConverter(csv_path, yaml_path)
        converter.convert()
        converter.write_output(output_path)
        
        # Print report
        report = converter.get_report()
        print("\n" + "="*60)
        print("CONVERSION REPORT")
        print("="*60)
        print(f"Input rows: {report['input_rows']}")
        print(f"Output rows: {report['output_rows']}")
        print(f"Species found: {report['species_count']}")
        print(f"Unique keys: {len(report['unique_keys'])}")
        print(f"  Keys: {', '.join(report['unique_keys'])}")
        print(f"Rows with reference: {report['rows_with_reference']}")
        print(f"Rows without reference: {report['rows_without_reference']}")
        print("="*60)
        
    except Exception as e:
        logger.error(f"Conversion failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()