import json
from pathlib import Path
from typing import Union

# import your real parser
from coa_parser import parse_coa   # <-- change this


def test_parse_coa(
    pdf_path: Union[str, Path],
    output_dir: Union[str, Path] = None,
    debug: bool = False
) -> Path:
    """
    Parse a COA PDF and save output JSON.

    Args:
        pdf_path: Path to COA PDF
        output_dir: Optional directory to save JSON
        debug: Whether to enable debug mode in parser

    Returns:
        Path to saved JSON file
    """

    pdf_path = Path(pdf_path)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError("File must be a PDF")

    # Decide output directory
    if output_dir is None:
        output_dir = pdf_path.parent
    else:
        output_dir = Path(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / f"{pdf_path.stem}_parsed.json"

    print(f"Parsing: {pdf_path}")
    coa_output = parse_coa(str(pdf_path), debug=debug)

    print(f"Saving JSON to: {json_path}")

    with open(json_path, "w") as f:
        json.dump(
            coa_output.model_dump(mode="json"),
            f,
            indent=2,
            default=str
        )

    return json_path


if __name__ == "__main__":
    # Example usage
    pdf = "/home/soham/Documents/orbtl/Hypro/COA.pdf"
    result_path = test_parse_coa(pdf)
    print("Saved at:", result_path)