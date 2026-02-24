from claude_code_internal.servers.llm_gateway import app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("claude_code_internal.servers.llm_gateway:app", host="0.0.0.0", port=8002, reload=True)

