# from services.invoice_parser import InvoiceParser
# from config import mistral_semaphore
# import asyncio
# from models import InvoiceData


# invoice_p = InvoiceParser()

# gst_issue_pdf = "/home/soham/Documents/orbtl/Hypro/test/20260120_044351170_iOS.pdf"
# wierd_pdf = "/home/soham/Documents/orbtl/Hypro/test/20260120_044342560_iOS.jpg.pdf"
# wrong_subtotal = "/home/soham/Documents/orbtl/Hypro/test/20260120_044417729_iOS.pdf"
# is_it_issue = "/home/soham/Documents/orbtl/Hypro/test/20260120_044433018_iOS.pdf"
# ten = "/home/soham/Documents/orbtl/Hypro/10.pdf"
# hund = "/home/soham/Documents/orbtl/Hypro/100.pdf" 
# twelve = "/home/soham/Documents/orbtl/Hypro/118.pdf"


# async def one():
#     boolean, invoice_d, n = await invoice_p.parse_invoice(twelve)
    
#     if invoice_d:
#         print(invoice_d.model_dump_json(indent=4))
        

# if __name__ == "__main__":
#     asyncio.run(one())




import asyncio
from pathlib import Path
from services.invoice_parser import InvoiceParser

# Base folder
BASE_DIR = Path("/home/soham/Documents/orbtl/Hypro-2/input")
OUTPUT_DIR = Path("/home/soham/Documents/orbtl/Hypro-2/output")

invoice_parser = InvoiceParser()


async def process_pdf(pdf_path: Path):
    try:
        print(f"Processing: {pdf_path.name}")

        success, invoice_d, _ = await invoice_parser.parse_invoice(str(pdf_path))

        if invoice_d:
            json_output = invoice_d.model_dump_json(indent=4)

            output_file = OUTPUT_DIR / f"{pdf_path.stem}.json"

            with open(output_file, "w") as f:
                f.write(json_output)

            print(f"Saved: {output_file.name}")
        else:
            print(f"⚠️ Failed to parse: {pdf_path.name}")

    except Exception as e:
        print(f"❌ Error processing {pdf_path.name}: {e}")


async def main():
    # Create output directory if it doesn't exist
    OUTPUT_DIR.mkdir(exist_ok=True)

    # Get all PDFs in folder (non-recursive)
    pdf_files = [f for f in BASE_DIR.glob("*.pdf") if f.is_file()]

    print(f"Found {len(pdf_files)} PDFs")

    # Sequential processing (safe if your parser uses semaphores)
    for pdf in pdf_files:
        await process_pdf(pdf)


if __name__ == "__main__":
    asyncio.run(main())