from services.invoice_parser import InvoiceParser
from config import mistral_semaphore
import asyncio
from models import InvoiceData


invoice_p = InvoiceParser()

gst_issue_pdf = "/home/soham/Documents/orbtl/Hypro/test/20260120_044351170_iOS.pdf"
wierd_pdf = "/home/soham/Documents/orbtl/Hypro/test/20260120_044342560_iOS.jpg.pdf"
wrong_subtotal = "/home/soham/Documents/orbtl/Hypro/test/20260120_044417729_iOS.pdf"
is_it_issue = "/home/soham/Documents/orbtl/Hypro/test/20260120_044433018_iOS.pdf"

async def one():
    boolean, invoice_d, n = await invoice_p.parse_invoice(is_it_issue)
    
    if invoice_d:
        print(invoice_d.model_dump_json(indent=4))
        

if __name__ == "__main__":
    asyncio.run(one())