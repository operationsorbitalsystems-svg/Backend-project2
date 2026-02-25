from services.bedrock import call_bedrock
import asyncio

sys_prompt = "Say hello"
prompt = "say hello"

async def main():
    answer, t = await call_bedrock(sys_prompt, prompt)
    print("Answer:", answer)
    print("Time:", t)

if __name__ == "__main__":
    asyncio.run(main())
    
    
