import ollama
from pydantic import BaseModel
from typing import List, Optional

# 1. Define Pydantic Schema
class Book(BaseModel):
    """Information about a book."""
    title: str
    author: str
    genre: str
    year: Optional[int] = None

# For reliable results, you should also include tips in your prompt and set temperature to 0.
prompt_content = """
Tell me about the book 'The Hitchhiker's Guide to the Galaxy'. 
Only return a JSON object that matches the provided schema. 
Do not include any commentary or extra text outside the JSON.
"""

# 2. & 3. Generate JSON schema and pass it to the 'format' parameter
try:
    response = ollama.chat(
        model='gemma2:2b', # Use a capable model (e.g., Llama 3.1, Qwen3)
        messages=[{'role': 'user', 'content': prompt_content}],
        format=Book.model_json_schema(), # Pass the schema here
        options={'temperature': 0} # Set temperature low for deterministic output
    )

    # The response content will be a JSON string
    json_output = response['message']['content']

    # 4. Parse and validate the response into a Pydantic object
    book_object = Book.model_validate_json(json_output)
    
    print("Successfully parsed Pydantic object:")
    print(f"Title: {book_object.title}")
    print(f"Author: {book_object.author}")
    print(f"Genre: {book_object.genre}")
    print(f"Year: {book_object.year}")

except Exception as e:
    print(f"Error processing response: {e}")
    # You might implement fallback or retry logic here
