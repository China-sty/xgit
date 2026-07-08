import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from schemas.state import ChatRequest
from agents.simple_agent import SimpleAgent

# Initialize FastAPI application
# We use FastAPI here because it natively supports async/await and provides auto-generated OpenAPI docs.
app = FastAPI(title="Developer Knowledge Agent API")

# Initialize the simple state-machine agent.
# We instantiate this once at startup to avoid re-initializing the LLM client on every request.
agent = SimpleAgent()

@app.post("/chat")
def chat_endpoint(req: ChatRequest):
    """
    Main endpoint for handling user queries about code authorship and bug responsibility.
    Takes a natural language query, runs it through the agent's pipeline (extract -> query -> synthesize),
    and returns both the final answer and the intermediate evidence for traceability.
    """
    # 🌟 新增的流式响应分支
    if req.stream:
        return StreamingResponse(
            agent.stream({"query": req.query}),
            media_type="text/event-stream"
        )
        
    # 传统的同步阻塞响应分支
    result = agent.invoke({"query": req.query})
    
    # Return structured data instead of just the answer text.
    # This allows frontend/clients to display the evidence to users, building trust in the LLM's conclusion.
    return {
        "entity_extracted": result["entity"],
        "evidence": result["evidence"],
        "answer": result["answer"]
    }

if __name__ == "__main__":
    # Start the server listening on all interfaces.
    print("Agent Server is starting on 0.0.0.0:3000...")
    uvicorn.run(app, host="0.0.0.0", port=3000)
