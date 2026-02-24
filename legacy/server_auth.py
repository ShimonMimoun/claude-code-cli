from claude_code_internal.servers.auth import app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("claude_code_internal.servers.auth:app", host="0.0.0.0", port=8001, reload=True)

