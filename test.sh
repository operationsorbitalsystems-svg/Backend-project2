# 1. Create session
BATCH_ID=$(curl -s -X POST http://localhost:8000/api/sessions | jq -r '.batch_id')

# 2. Upload files (replace paths)
curl -X POST "http://localhost:8000/api/sessions/$BATCH_ID/upload" \
  -F "coa_file=@/home/soham/Documents/orbtl/Chart of Accounts.pdf" \
  -F "files=@/home/soham/Documents/orbtl/test2/61 TURINTON.pdf" | jq

# 3. Check status
curl -s "http://localhost:8000/api/sessions/$BATCH_ID/status" | jq

# 4. Debug COA - Check flat_list of ledgers
curl -s "http://localhost:8000/api/sessions/$BATCH_ID/status" | jq '.coa_data.flat_list'


echo "curl -s "http://localhost:8000/api/sessions/$BATCH_ID/status" | jq"



